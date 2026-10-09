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
    "within_arrival_tolerance": "paid 90 minutes before the cutoff, still in transit",
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


def block_reconciliation():
    con = duckdb.connect(DB["defective"], read_only=True)
    s = con.execute("""select check_name, compares, unit, source_value, warehouse_value, difference, explained,
                              unexplained, exceptions from main_reconciliation.rec_summary
                       order by case check_name when 'order_count' then 1 when 'completeness' then 2
                       when 'gross_to_net' then 3 when 'ad_spend' then 4 else 5 end""").fetchall()
    items = con.execute("""select check_name, item_key, category, classification, amount_pence
                           from main_reconciliation.rec_exceptions
                           order by case classification when 'exception' then 0 else 1 end, check_name, item_key"""
                        ).fetchall()
    con.close()
    con = duckdb.connect(DB["clean"], read_only=True)
    clean = con.execute("select count(*), sum(exceptions), sum(abs(unexplained)) "
                        "from main_reconciliation.rec_summary where status = 'pass'").fetchone()
    con.close()
    comp_row = next(r for r in s if r[0] == "completeness")
    diff, expl, unex = comp_row[5], comp_row[6], comp_row[7]
    short = {"missing_in_landing": "never landed", "duplicate_in_landing": "pushed twice",
             "amount_mismatch": "landed too low"}
    exc = [f"`{k}` {gbp(a)} {short.get(cat, cat)}" for c, k, cat, cl, a in items
           if cl == "exception" and c == "completeness"]
    lead = (f"**In short:** the warehouse holds {gbp(abs(diff))} {'less' if diff < 0 else 'more'} than the "
            f"subscription platform charged. That gap breaks down into one order paid 90 minutes before the cutoff and "
            f"still in transit ({gbp(expl)}, explained) and {len(exc)} named orders netting {gbp(unex)} "
            f"({'; '.join(exc)}). The other checks close, and each remaining exception has a key and a reason.\n\n")
    rows = [(c, comp, fmt(u, sv), fmt(u, wv), fmt(u, d), fmt(u, e) if c in ("order_count", "completeness") else "",
             fmt(u, ue), ex) for c, comp, u, sv, wv, d, e, ue, ex in s]
    t1 = table(["Check", "Compares", "Source", "Warehouse", "Difference", "Explained", "Unexplained", "Exceptions"], rows)
    t2 = table(["", "Check", "Item", "What it is", "Amount"],
               [("**exception**" if cl == "exception" else "explained", c, f"`{k}`", CATEGORY.get(cat, cat),
                 gbp(a) if a else "") for c, k, cat, cl, a in items])
    return (lead + t1 + "\n\nLine by line (amounts are warehouse minus source):\n\n" + t2 +
            f"\n\nSame checks on the clean scenario: {clean[0]} of 5 pass, {clean[1]} exceptions, "
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
    return (t + f"\n\nWindow: 42 days from the trial (a demo policy, to be agreed). Cutoff: {cut} UTC. "
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
              "Its 73p card check is reversed and listed in the gross-to-net check.")


def block_same_number():
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


BLOCKS = {"reconciliation": block_reconciliation, "conversion": block_conversion, "journey": block_journey,
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
