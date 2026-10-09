"""The MCP server, exercised through a real client session over stdio."""
import json

import duckdb

from mcp_client import run_session


def test_discovery_exposes_two_tools_and_no_sql(defective_db, tmp_path):
    tools, _ = run_session(defective_db, str(tmp_path / "log.jsonl"), [])
    names = {t.name for t in tools}
    assert names == {"list_metrics", "query_metric"}
    params = {t.name: set(t.input_schema.get("properties", {})) for t in tools}
    assert params["query_metric"] == {"metric_id", "start_month", "end_month", "slice_by"}
    assert not any("sql" in p.lower() for ps in params.values() for p in ps)


def test_registry_lists_definitions_versions_and_contracts(defective_db, tmp_path):
    _, [(err, reg)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [("list_metrics", {})])
    assert not err
    by_id = {m["metric_id"]: m for m in reg["metrics"]}
    conv = by_id["trial_conversion_rate"]
    assert conv["status"] == "implemented" and conv["version"] == 1 and conv["window_days"] == 42
    assert conv["allowed_slices"] == ["total", "cats_on_plan", "referred"]
    assert by_id["contribution_margin"]["status"] == "contract"
    assert by_id["contribution_margin"]["missing_inputs"]


def test_conversion_through_mcp_equals_the_mart(defective_db, tmp_path):
    _, [(err, ans)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07"})])
    assert not err and ans["status"] == "ok"
    con = duckdb.connect(defective_db, read_only=True)
    conv, elig = con.execute("""
        select sum(case when converted_in_window then 1 else 0 end), count(*)
        from main_marts.fct_trials
        where is_window_complete and cohort_month between '2025-04-01' and '2026-07-01'""").fetchone()
    con.close()
    tot = ans["range_total"]
    assert (tot["numerator"], tot["denominator"]) == (conv, elig)
    assert abs(tot["value"] - conv / elig) < 1e-12
    # the range total is a ratio of sums, not an average of monthly rates
    rates = [r["value"] for r in ans["rows"] if r["published"]]
    assert abs(tot["value"] - sum(rates) / len(rates)) > 1e-6


def test_slice_outside_the_list_is_refused_and_logged(defective_db, tmp_path):
    log = tmp_path / "log.jsonl"
    _, [(err, text)] = run_session(defective_db, str(log), [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01",
                          "end_month": "2026-06", "slice_by": "postcode"})])
    assert err and "not allowed" in text
    entry = json.loads(log.read_text().strip().splitlines()[-1])
    assert entry["outcome"] == "refused" and entry["args"]["slice_by"] == "postcode"


def test_contracts_unknown_metrics_bad_ranges_are_refused(defective_db, tmp_path):
    _, res = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "contribution_margin", "start_month": "2026-01", "end_month": "2026-02"}),
        ("query_metric", {"metric_id": "revenue; drop table x", "start_month": "2026-01", "end_month": "2026-02"}),
        ("query_metric", {"metric_id": "churn", "start_month": "2026-01", "end_month": "2026-02"}),
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2024-01", "end_month": "2026-09"}),
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-1", "end_month": "2026-02"}),
    ])
    assert all(err for err, _ in res)
    assert "contract only" in res[0][1]
    assert "registry id" in res[1][1]
    assert "unknown metric" in res[2][1]
    assert "at most 24 months" in res[3][1]
    assert "YYYY-MM" in res[4][1]


def test_small_cells_are_suppressed_and_no_personal_data_leaves(defective_db, tmp_path):
    _, [(err, ans)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07",
                          "slice_by": "cats_on_plan"})])
    assert not err
    hidden = [r for r in ans["rows"] if not r["published"]]
    assert hidden, "the 3+ cats slice should fall under the minimum group size somewhere"
    assert all(r["value"] is None and r["numerator"] is None for r in hidden)
    blob = json.dumps(ans)
    assert "@" not in blob and "ACC-" not in blob and "shopify:" not in blob


def test_every_answer_carries_version_window_cutoff_and_quality(defective_db, tmp_path):
    _, [(err, ans)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-01", "end_month": "2026-09",
                          "slice_by": "order_kind"})])
    assert not err
    assert ans["definition_version"] == 1
    assert ans["window"]["start_month"] == "2026-01"
    assert ans["cutoff_utc"].startswith("2026-09-30 21:00")
    assert ans["quality"]["availability"] == "available"
    # the defective scenario has open completeness exceptions, and the answer says so
    assert ans["quality"]["open_reconciliation_exceptions"] == 3


def _true_cell(db, metric, slice_name, period, value):
    con = duckdb.connect(db, read_only=True)
    row = con.execute("select numerator, denominator from main_metrics.metric_values where metric_id = ? "
                      "and slice_name = ? and period = ? and slice_value = ?",
                      [metric, slice_name, period, value]).fetchone()
    con.close()
    return row


def test_hidden_cells_cannot_be_recovered_by_subtraction(defective_db, tmp_path):
    """The three cases found in review: referred 2026-06, cats 3+ in 2026-06, one-off revenue 2026-09.
    The attacker asks for the total and for the split of the same month and subtracts."""
    cases = [("trial_conversion_rate", "referred", "2026-06", "yes"),
             ("trial_conversion_rate", "cats_on_plan", "2026-06", "3+"),
             ("net_revenue", "order_kind", "2026-09", "one_off")]
    calls = []
    for metric, sl, month, _ in cases:
        calls += [("query_metric", {"metric_id": metric, "start_month": month, "end_month": month}),
                  ("query_metric", {"metric_id": metric, "start_month": month, "end_month": month, "slice_by": sl})]
    _, res = run_session(defective_db, str(tmp_path / "log.jsonl"), calls)
    for i, (metric, sl, month, target) in enumerate(cases):
        (e1, total), (e2, split) = res[2 * i], res[2 * i + 1]
        assert not e1 and not e2
        t = total["rows"][0]
        assert t["published"], "the month total itself is large enough to publish"
        hidden = [r for r in split["rows"] if not r["published"]]
        assert target in {r["slice_value"] for r in hidden}
        assert len(hidden) >= 2, f"{metric}/{sl}/{month}: a single hidden cell is recoverable"
        visible = [r for r in split["rows"] if r["published"]]
        guess = t["numerator"] - sum(r["numerator"] for r in visible)
        truth = _true_cell(defective_db, metric, sl, f"{month}-01", target)[0]
        assert guess != truth, f"{metric}/{sl}/{month}: total minus visible cells gives the hidden cell"
        assert split["range_total"]["published"] is False and "numerator" not in split["range_total"]
        assert all(r["pending"] is None for r in split["rows"])


def test_no_split_ever_leaves_exactly_one_hidden_cell(defective_db, tmp_path):
    combos = [("trial_conversion_rate", "cats_on_plan"), ("trial_conversion_rate", "referred"),
              ("net_revenue", "order_kind"), ("active_subscriptions", "cats_on_plan")]
    _, res = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": m, "start_month": "2025-04", "end_month": "2026-09", "slice_by": s})
        for m, s in combos])
    for (m, s), (err, ans) in zip(combos, res):
        assert not err
        per_period = {}
        for r in ans["rows"]:
            per_period.setdefault(r["period"], []).append(r)
        for period, cells in per_period.items():
            hidden = sum(1 for c in cells if not c["published"])
            # one hidden cell is only acceptable when it is the whole period (then the total is hidden too)
            assert hidden != 1 or len(cells) == 1, (m, s, period)


def test_range_total_hidden_when_any_month_is_small_and_numbers_are_numbers(defective_db, tmp_path):
    _, [(e1, rev), (e2, small)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-01", "end_month": "2026-09"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-08", "end_month": "2026-09"}),
    ])
    assert not e1 and not e2
    assert isinstance(rev["range_total"]["value"], float)
    assert all(isinstance(r["value"], float) for r in rev["rows"])
    # September has no trial with a closed window yet: the range total is withheld, not computed around it
    assert small["range_total"]["published"] is False and small["range_total"]["value"] is None


def test_unwritable_call_log_blocks_the_answer_and_says_why(defective_db, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    _, [(err, text)] = run_session(defective_db, str(blocker / "log.jsonl"), [("list_metrics", {})])
    assert err and "not writable" in text
