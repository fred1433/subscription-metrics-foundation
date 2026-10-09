"""The MCP server, exercised through a real client session over stdio."""
import json
import os
import shutil
import subprocess
import sys

import duckdb
import pytest

from mcp_client import run_session

SPLITS = [("trial_conversion_rate", "cats_on_plan"), ("trial_conversion_rate", "referred"),
          ("net_revenue", "order_kind"), ("active_subscriptions", "cats_on_plan")]


def test_discovery_exposes_two_tools_and_no_sql(defective_db, tmp_path):
    tools, _ = run_session(defective_db, str(tmp_path / "log.jsonl"), [])
    assert {t.name for t in tools} == {"list_metrics", "query_metric"}
    params = {t.name: set(t.input_schema.get("properties", {})) for t in tools}
    assert params["query_metric"] == {"metric_id", "start_month", "end_month", "slice_by"}
    assert not any("sql" in p.lower() for ps in params.values() for p in ps)


def test_registry_lists_definitions_versions_entities_and_contracts(defective_db, tmp_path):
    _, [(err, reg)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [("list_metrics", {})])
    assert not err
    by_id = {m["metric_id"]: m for m in reg["metrics"]}
    conv = by_id["trial_conversion_rate"]
    assert conv["status"] == "implemented" and conv["version"] == "1-w42" and conv["window_days"] == 42
    assert "42 days" in conv["definition"]
    assert conv["allowed_slices"] == ["total", "cats_on_plan", "referred"]
    assert {by_id[m]["threshold_entity"] for m in ("trial_conversion_rate", "net_revenue", "active_subscriptions")} \
        == {"trial", "order", "subscription"}
    assert "UK VAT included" in by_id["net_revenue"]["definition"]
    assert by_id["contribution_margin"]["status"] == "contract" and by_id["contribution_margin"]["missing_inputs"]


def test_conversion_through_mcp_equals_the_mart(defective_db, tmp_path):
    _, [(err, ans)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07"})])
    assert not err and ans["status"] in ("ok", "degraded")
    con = duckdb.connect(defective_db, read_only=True)
    conv, elig = con.execute("""
        select sum(case when converted_in_window then 1 else 0 end), count(*)
        from main_marts.fct_trials
        where is_window_complete and cohort_month between '2025-04-01' and '2026-07-01'""").fetchone()
    con.close()
    tot = ans["range_total"]
    assert (tot["numerator"], tot["denominator"]) == (conv, elig)
    assert abs(tot["value"] - conv / elig) < 1e-12
    rates = [r["value"] for r in ans["rows"] if r["published"]]
    assert abs(tot["value"] - sum(rates) / len(rates)) > 1e-6     # ratio of sums, not an average of rates


def _truth(db):
    con = duckdb.connect(db, read_only=True)
    rows = con.execute("select metric_id, slice_name, cast(period as varchar), slice_value, group_size "
                       "from main_metrics.metric_values").fetchall()
    con.close()
    return {(m, s, p, v): g for m, s, p, v, g in rows}


def test_residual_after_suppression_is_empty_or_large_enough(defective_db, tmp_path):
    """For every metric, split and month: total minus every visible cell is 0 units or at least 10."""
    truth = _truth(defective_db)
    _, res = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": m, "start_month": "2025-04", "end_month": "2026-09", "slice_by": s})
        for m, s in SPLITS])
    checked = 0
    for (m, s), (err, ans) in zip(SPLITS, res):
        assert not err
        periods = {}
        for r in ans["rows"]:
            periods.setdefault(r["period"], []).append(r)
        for period, cells in periods.items():
            total = truth[(m, "total", period, "all")]
            visible = sum(truth[(m, s, period, c["slice_value"])] for c in cells if c["published"])
            residual = total - visible
            hidden = [c for c in cells if not c["published"]]
            # when the month total is itself below the threshold it is hidden too, and so is every cell
            if total < 10:
                assert all(not c["published"] for c in cells), (m, s, period)
            else:
                assert residual == 0 or residual >= 10, (m, s, period, residual)
            assert len(hidden) != 1 or len(cells) == 1, (m, s, period)
            checked += 1
    assert checked > 50


def test_replay_of_the_review_calls(defective_db, tmp_path):
    """July 2025 by number of cats: 2 cats and 3+ are small, together still under 10, so '1' is hidden too.
    Also the three earlier cases: referred 2026-06, 3+ cats 2026-06, one-off revenue 2026-09."""
    truth = _truth(defective_db)
    cases = [("trial_conversion_rate", "cats_on_plan", "2025-07"), ("trial_conversion_rate", "referred", "2026-06"),
             ("trial_conversion_rate", "cats_on_plan", "2026-06"), ("net_revenue", "order_kind", "2026-09")]
    calls = []
    for m, s, month in cases:
        calls += [("query_metric", {"metric_id": m, "start_month": month, "end_month": month}),
                  ("query_metric", {"metric_id": m, "start_month": month, "end_month": month, "slice_by": s})]
    _, res = run_session(defective_db, str(tmp_path / "log.jsonl"), calls)
    for i, (m, s, month) in enumerate(cases):
        (e1, total), (e2, split) = res[2 * i], res[2 * i + 1]
        assert not e1 and not e2
        visible = [r for r in split["rows"] if r["published"]]
        hidden = [r for r in split["rows"] if not r["published"]]
        assert hidden
        if total["rows"][0]["published"]:
            guess_units = truth[(m, "total", f"{month}-01", "all")] - sum(
                truth[(m, s, f"{month}-01", r["slice_value"])] for r in visible)
            assert guess_units == 0 or guess_units >= 10
        assert split["range_total"]["published"] is False and "numerator" not in split["range_total"]
        assert all(r["pending"] is None for r in split["rows"])
    july = {r["slice_value"]: r["published"] for r in res[1][1]["rows"]}
    assert july.get("1") is False, "July 2025: '1 cat' must be hidden as well"


def test_refusals_never_echo_the_refused_value(defective_db, tmp_path):
    log = tmp_path / "log.jsonl"
    email = "jane.doe@example.com"
    _, res = run_session(defective_db, str(log), [
        ("query_metric", {"metric_id": email, "start_month": "2026-01", "end_month": "2026-02"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01", "end_month": "2026-02",
                          "slice_by": email}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": email, "end_month": "2026-02"}),
    ])
    assert all(err for err, _ in res)
    assert "slice_by" in res[1][1] and "not allowed" in res[1][1]
    for _, text in res:
        assert email not in text and "jane" not in text
    assert email not in log.read_text() and "jane" not in log.read_text()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["args"] for e in entries] == [{"refused_argument": "metric_id"}, {"refused_argument": "slice_by"},
                                             {"refused_argument": "start_month"}]


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
    assert "registry id" in res[1][1] and "drop table" not in res[1][1]
    assert "unknown metric" in res[2][1]
    assert "at most 24 months" in res[3][1]
    assert "YYYY-MM" in res[4][1]


def test_failed_checks_serve_degraded_with_named_exceptions(root, defective_db, tmp_path):
    _, [(e1, rev), (e2, conv), (e3, act)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-01", "end_month": "2026-09",
                          "slice_by": "order_kind"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01", "end_month": "2026-06"}),
        ("query_metric", {"metric_id": "active_subscriptions", "start_month": "2026-01", "end_month": "2026-06"}),
    ])
    assert not (e1 or e2 or e3)
    assert rev["status"] == "degraded" and rev["rows"]
    failures = " ".join(rev["quality"]["reconciliation_failures"])
    for key in ("PO-D1-B2", "PO-D2-B1", "PO-D3-B1"):
        assert key in failures
    assert conv["status"] == "degraded" and "PO-F3B-T" in " ".join(conv["quality"]["reconciliation_failures"])
    assert act["status"] == "ok" and act["quality"]["reconciliation_failures"] == []
    clean = os.path.join(root, "warehouse", "clean.duckdb")
    _, [(e4, clean_rev)] = run_session(clean, str(tmp_path / "log2.jsonl"), [
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-01", "end_month": "2026-09"})])
    assert not e4 and clean_rev["status"] == "ok"


def test_small_cells_suppressed_and_no_personal_data_leaves(defective_db, tmp_path):
    _, [(err, ans)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07",
                          "slice_by": "cats_on_plan"})])
    assert not err
    hidden = [r for r in ans["rows"] if not r["published"]]
    assert hidden and all(r["value"] is None and r["numerator"] is None for r in hidden)
    blob = json.dumps(ans)
    assert "@" not in blob and "ACC-" not in blob and "shopify:" not in blob


def test_every_answer_carries_version_window_cutoff_and_numbers(defective_db, tmp_path):
    _, [(e1, rev), (e2, small)] = run_session(defective_db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "net_revenue", "start_month": "2026-01", "end_month": "2026-09"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-08", "end_month": "2026-09"}),
    ])
    assert not e1 and not e2
    assert rev["definition_version"] == "2" and rev["window"]["start_month"] == "2026-01"
    assert rev["cutoff_utc"].startswith("2026-09-30 21:00")
    assert isinstance(rev["range_total"]["value"], float)
    assert all(isinstance(r["value"], float) for r in rev["rows"])
    assert small["range_total"]["published"] is False and small["range_total"]["value"] is None


def test_unwritable_call_log_blocks_the_answer_and_says_why(defective_db, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    _, [(err, text)] = run_session(defective_db, str(blocker / "log.jsonl"), [("list_metrics", {})])
    assert err and "not writable" in text


def test_conversion_window_has_one_source(root, defective_db, tmp_path):
    """Change the window once (dbt var): fct_trials, the registry, the MCP answer and the version follow."""
    db = str(tmp_path / "defective.duckdb")   # same file name: existing views refer to the catalog by it
    shutil.copy(defective_db, db)
    dbt = os.path.join(os.path.dirname(sys.executable), "dbt")
    res = subprocess.run([dbt, "run", "--profiles-dir", root, "--project-dir", root, "--target-path",
                          str(tmp_path / "t"), "--vars", "{trial_conversion_window_days: 28}", "--quiet",
                          "--select", "fct_trials", "metric_registry", "metric_values", "metric_quality"],
                         env={**os.environ, "DBT_DUCKDB_PATH": db}, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    _, [(e1, reg), (e2, ans)] = run_session(db, str(tmp_path / "log.jsonl"), [
        ("list_metrics", {}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01", "end_month": "2026-03"})])
    conv = next(m for m in reg["metrics"] if m["metric_id"] == "trial_conversion_rate")
    assert conv["window_days"] == 28 and conv["version"] == "1-w28" and "28 days" in conv["definition"]
    assert ans["window"]["conversion_window_days"] == 28 and ans["definition_version"] == "1-w28"
    con = duckdb.connect(db, read_only=True)
    assert con.execute("select distinct window_days from main_marts.fct_trials").fetchall() == [(28,)]
    con.close()
