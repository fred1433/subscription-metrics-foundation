"""Render every figure shown in README.md from the built warehouses, the dbt run results and a live MCP session.

  --write   replace the generated blocks in README.md
  --check   fail if README.md differs from what this run produces (used in CI)
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pytests"))
from mcp_client import run_session  # noqa: E402

DB = {s: os.path.join(ROOT, "warehouse", f"{s}.duckdb") for s in ("clean", "defective")}
CATEGORY = {
    "missing_in_landing": "paid in the platform, never landed",
    "duplicate_in_landing": "one paid order pushed twice",
    "amount_mismatch": "landed total differs from the charge",
    "within_arrival_tolerance": "paid shortly before the cutoff, still in transit",
    "scheduled_not_charged": "renewal due today, not charged yet: not missing revenue",
    "non_commercial_blank_order": "blank order created by a card update (motif, not amount)",
    "zero_price_replacement_kept": "free replacement: kept, it shipped and has a cost",
    "card_check_reversed": "73p card check, reversed: never revenue",
    "refund_recorded_not_paid": "refund recorded, money not sent yet",
    "duplicate_connector_row": "same campaign-day delivered twice by the connector",
    "second_trial_probable_household": "second trial, same card and address: sale kept, flagged",
}


def gbp(p):
    if p is None:
        return ""
    sign = "-" if p < 0 else ""
    return f"{sign}£{abs(p) / 100:,.2f}"


def fmt(unit, v):
    return gbp(v) if unit == "pence" else f"{v:,}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


EFFECT = {
    "missing_in_landing": "missing from the warehouse",
    "duplicate_in_landing": "in the warehouse twice",
    "amount_mismatch": "in the warehouse, wrong amount",
    "within_arrival_tolerance": "not in the warehouse yet",
    "scheduled_not_charged": "not revenue yet",
    "non_commercial_blank_order": "in the warehouse, out of order counts",
    "zero_price_replacement_kept": "in the warehouse, kept",
    "card_check_reversed": "never in revenue",
    "refund_recorded_not_paid": "not deducted yet",
    "duplicate_connector_row": "removed in staging",
    "second_trial_probable_household": "in the warehouse, flagged only",
    "line_total_mismatch": "order total differs from its lines",
}
LABEL = {"order_count": "Orders", "completeness": "Completeness", "lines_to_order_total": "Lines to order",
         "order_total_to_cash": "Order to cash", "ad_spend": "Ad spend", "trial_eligibility": "Trials"}


def tolerance_hours():
    with open(os.path.join(ROOT, "dbt_project.yml")) as f:
        return int(re.search(r"arrival_tolerance_hours:\s*(\d+)", f.read()).group(1))


def block_reconciliation():
    con = duckdb.connect(DB["defective"], read_only=True)
    s = con.execute("""select check_name, unit, source_value, warehouse_value, difference, explained,
                              unexplained, unexplained_gross, exceptions from main_reconciliation.rec_summary
                       order by case check_name when 'order_count' then 1 when 'completeness' then 2
                       when 'lines_to_order_total' then 3 when 'order_total_to_cash' then 4
                       when 'ad_spend' then 5 else 6 end""").fetchall()
    items = con.execute("""select check_name, item_key, category, classification, amount_pence
                           from main_reconciliation.rec_exceptions
                           order by case classification when 'exception' then 0 else 1 end, check_name, item_key"""
                        ).fetchall()
    con.close()
    con = duckdb.connect(DB["clean"], read_only=True)
    clean = con.execute("select count(*), sum(exceptions), sum(unexplained_gross) "
                        "from main_reconciliation.rec_summary where status = 'pass'").fetchone()
    con.close()
    comp = next(r for r in s if r[0] == "completeness")
    diff, unex, gross = comp[4], comp[6], comp[7]
    transit = [(k, a) for c, k, cat, cl, a in items if cat == "within_arrival_tolerance"]
    short = {"missing_in_landing": "never landed", "duplicate_in_landing": "pushed twice",
             "amount_mismatch": "landed too low" }
    exc = [f"`{k}` {gbp(a)} {short.get(cat, cat)}" for c, k, cat, cl, a in items
           if cl == "exception" and c == "completeness"]
    others = [LABEL[c] for c, *_ , ue, ug, ex in s if c != "completeness" and ue == 0]
    lead = (f"**In short:** the warehouse holds {gbp(abs(diff))} {'less' if diff < 0 else 'more'} than the "
            f"subscription platform charged. {'One order' if len(transit) == 1 else f'{len(transit)} orders'} worth "
            f"{gbp(-sum(a for _, a in transit))} {'was' if len(transit) == 1 else 'were'} paid within the {tolerance_hours()}-hour arrival tolerance and not landed yet (explained). The rest is "
            f"pinned to {len(exc)} named orders ({'; '.join(exc)}): {gbp(unex)} net, {gbp(gross)} gross, because a "
            f"duplicate must not hide a missing order. {', '.join(others)} close with nothing unexplained; their "
            f"exceptions are flagged, not money missing.\n\n")
    rows = [(LABEL[c], fmt(u, sv), fmt(u, wv), fmt(u, d), fmt(u, ue), fmt(u, ug), ex)
            for c, u, sv, wv, d, e, ue, ug, ex in s]
    t1 = table(["Check", "Source", "Warehouse", "Gap", "Unexplained, net", "Unexplained, gross", "Exceptions"], rows)
    t2 = table(["", "Item", "What it is", "Amount", "Effect"],
               [("**exception**" if cl == "exception" else "explained", f"`{k}`", CATEGORY.get(cat, cat),
                 gbp(a) if a else "", EFFECT.get(cat, "")) for c, k, cat, cl, a in items])
    return (lead + t1 + "\n\nGap is warehouse minus source. Line by line: for completeness items the amount is "
            "warehouse minus source; for the others it is the amount involved, and Effect says where it sits.\n\n"
            + t2 + f"\n\nSame checks on the clean scenario: {clean[0]} of 6 pass, {clean[1]} exceptions, "
            f"{gbp(clean[2])} unexplained.")


def block_conversion():
    con = duckdb.connect(DB["defective"], read_only=True)
    rows = con.execute("""select cast(period as varchar), numerator, denominator, value, pending_count
                          from main_metrics.metric_values
                          where metric_id = 'trial_conversion_rate' and slice_name = 'total'
                            and period >= '2026-01-01' order by period""").fetchall()
    cut = con.execute("select cast(max(checked_at_utc) as varchar) from main_metrics.metric_quality").fetchone()[0]
    con.close()
    t = table(["Trial cohort", "Converted", "Trials with a closed window", "Conversion", "Still in their window"],
              [(p[:7], n, d, f"{v:.1%}" if v is not None else "not yet measurable", pend)
               for p, n, d, v, pend in rows])
    return ("Trials whose first subscription box was paid within the window, over trials whose window has closed.\n\n"
            + t + f"\n\nWindow: 42 days from the trial (a demo policy, to be agreed). Cutoff: {cut} UTC. "
            "A trial cancelled before its first box stays in the denominator; trials whose window is still open "
            "are counted apart, never as failures.")


def block_journey():
    con = duckdb.connect(DB["defective"], read_only=True)
    ev = con.execute("""select event_date_london, string_agg(event_type, ', ' order by event_at_utc)
                        from main_staging.stg_platform__subscription_events
                        where subscription_id = 'SUB-J' and event_date_london <= '2026-06-25'
                        group by 1""").fetchall()
    events = {d: e for d, e in ev}
    states = con.execute("""select state_date, subscription_state, first_box_paid_known, expected_renewal_date
                            from main_marts.fct_subscription_state_daily where subscription_id = 'SUB-J'
                            and state_date in ('2026-06-01','2026-06-06','2026-06-07','2026-06-20','2026-06-21',
                                               '2026-06-22','2026-06-24') order by 1""").fetchall()
    blank = con.execute("select order_kind, is_commercial from main_marts.fct_orders "
                        "where platform_order_id = 'PO-J-BLANK'").fetchone()
    con.close()
    import datetime as dt
    j0 = dt.date(2026, 6, 1)
    rows = [(f"J{(d - j0).days}", d.isoformat(), events.get(d, "(none)"), st,
             "yes" if fb else "no", r.isoformat()) for d, st, fb, r in states]
    return (table(["Day", "Date", "Events that day", "State known that evening", "First box paid", "Next renewal expected"], rows)
            + f"\n\nThe J6 blank order lands as `{blank[0]}`, commercial = {str(blank[1]).lower()}. "
              "Its 73p card check is reversed and listed in the order-to-cash check.")


_SAME = {}


def same_number():
    if _SAME:
        return _SAME["v"]
    sql_path = os.path.join(ROOT, "target", "analyses-defective", "compiled", "subscription_metrics", "analyses",
                            "metabase_trial_conversion.sql")
    with open(sql_path) as f:
        report_sql = re.sub(r"--.*", "", f.read())
    con = duckdb.connect(DB["defective"], read_only=True)
    conv, elig = con.execute(f"select sum(converted), sum(trials_with_closed_window) from ({report_sql}) as r "
                             "where cohort_month between '2025-04-01' and '2026-07-01'").fetchone()
    con.close()
    log = os.path.join(ROOT, "target", "readme_mcp_calls.jsonl")
    _, res = run_session(DB["defective"], log, [
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2025-04", "end_month": "2026-07",
                          "slice_by": "postcode"}),
        ("query_metric", {"metric_id": "contribution_margin", "start_month": "2026-01", "end_month": "2026-06"}),
    ])
    _SAME["v"] = (conv, elig, res)
    return _SAME["v"]


def block_same_number():
    conv, elig, res = same_number()
    tot = res[0][1]["range_total"]
    t = table(["Path", "Converted", "Trials with a closed window", "Conversion"], [
        ("Report query `analyses/metabase_trial_conversion.sql`", conv, elig, f"{conv / elig:.4%}"),
        ("Agent: `query_metric('trial_conversion_rate', '2025-04', '2026-07')`", tot["numerator"],
         tot["denominator"], f"{tot['value']:.4%}"),
    ])
    same = "identical" if (conv, elig) == (tot["numerator"], tot["denominator"]) else "DIFFERENT"
    return (t + f"\n\nBoth paths: {same}. What the agent gets when it asks for something it should not:\n\n"
            f"```text\nslice_by='postcode'         -> {res[1][1]}\nmetric 'contribution_margin' -> {res[2][1]}\n```")


def block_runs():
    rows = []
    for sc in ("clean", "defective"):
        with open(os.path.join(ROOT, "target", sc, "run_results.json")) as f:
            res = json.load(f)["results"]
        tests = [r for r in res if r["unique_id"].startswith("test.")]
        models = [r for r in res if r["unique_id"].startswith("model.")]
        count = lambda st: sum(1 for r in tests if r["status"] == st)
        rows.append((sc, len(models), len(tests), count("pass"), count("warn"), count("fail") + count("error")))
    return table(["Scenario", "Models built", "dbt tests", "Pass", "Warn (expected)", "Fail"], rows)


def block_proof():
    con = duckdb.connect(DB["defective"], read_only=True)
    st = dict(con.execute("""select cast(state_date as varchar), subscription_state from main_marts.fct_subscription_state_daily
                             where subscription_id = 'SUB-J' and state_date in ('2026-06-20', '2026-06-22', '2026-06-24')""").fetchall())
    exc = con.execute("""select item_key, amount_pence from main_reconciliation.rec_exceptions
                         where check_name = 'completeness' and classification = 'exception' order by item_key""").fetchall()
    con.close()
    conv, elig, res = same_number()
    tot = res[0][1]["range_total"]
    same = (conv, elig) == (tot["numerator"], tot["denominator"])
    return "\n".join([
        "**What the run shows** (synthetic data, every figure produced by the build):",
        "",
        f"- A first box moved to day 20 and paid after a failed charge stays pending: `{st['2026-06-20']}` on day 19, "
        f"`{st['2026-06-22']}` on day 21 (not churn), `{st['2026-06-24']}` on day 23, the conversion day.",
        "- Gaps carry names, not just totals: the completeness gap is pinned to "
        + ", ".join(f"`{k}` ({gbp(a)})" for k, a in exc) + ".",
        f"- Same number twice: trial conversion April 2025 to July 2026 is {conv}/{elig} through the report query and "
        f"{tot['numerator']}/{tot['denominator']} through the MCP server{'' if same else ' (MISMATCH)'}.",
    ])


BLOCKS = {"proof": block_proof, "reconciliation": block_reconciliation, "conversion": block_conversion, "journey": block_journey,
          "same-number": block_same_number, "runs": block_runs}


def render(text: str) -> str:
    for name, fn in BLOCKS.items():
        pat = re.compile(rf"(<!-- generated:{name} -->\n)(?:.*?\n)?(<!-- /generated:{name} -->)", re.S)
        if not pat.search(text):
            raise SystemExit(f"README is missing the block {name}")
        body = fn()
        text = pat.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)
    return text


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    path = os.path.join(ROOT, "README.md")
    with open(path) as f:
        current = f.read()
    fresh = render(current)
    if a.write:
        with open(path, "w") as f:
            f.write(fresh)
        print("README.md updated from this run")
        return 0
    if fresh != current:
        sys.stdout.writelines(difflib.unified_diff(current.splitlines(True), fresh.splitlines(True), "README.md", "this run"))
        print("README.md does not match this run: run `make readme`")
        return 1
    print("README.md figures match this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
