"""Scenario harness: the build must find exactly the anomalies written down by hand, no more, no less.

For a scenario it compares
  1. the dbt tests that ended in WARN with harness/expected/<scenario>_warnings.txt
     (and fails on any test that ended in fail or error),
  2. every reconciliation item (check, key, category, classification, amount) with <scenario>_items.csv,
  3. the identity review queue with <scenario>_identity_candidates.csv.
Items are compared as occurrences with their count (a multiset), so a second occurrence of the same key
fails unless it is expected. A missed anomaly, an extra one or a misclassified one fails the run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))


def load_expected(scenario: str, expected_dir: str = os.path.join(HERE, "expected")):
    with open(os.path.join(expected_dir, f"{scenario}_items.csv")) as f:
        items = Counter((r["check_name"], r["item_key"], r["category"], r["classification"], int(r["amount_pence"]))
                        for r in csv.DictReader(f))
    with open(os.path.join(expected_dir, f"{scenario}_identity_candidates.csv")) as f:
        cands = {(r["customer_key_a"], r["customer_key_b"], r["matched_on"]) for r in csv.DictReader(f)}
    with open(os.path.join(expected_dir, f"{scenario}_warnings.txt")) as f:
        warns = {line.strip() for line in f if line.strip()}
    return items, cands, warns


def observed(db: str, run_results: str):
    con = duckdb.connect(db, read_only=True)
    items = Counter((r[0], r[1], r[2], r[3], int(r[4])) for r in con.execute(
        "select check_name, item_key, category, classification, amount_pence "
        "from main_reconciliation.rec_exceptions").fetchall())
    cands = {tuple(r) for r in con.execute(
        "select customer_key_a, customer_key_b, matched_on from main_intermediate.int_identity_candidates").fetchall()}
    con.close()
    with open(run_results) as f:
        results = json.load(f)["results"]
    warns, broken = set(), []
    for r in results:
        if not r["unique_id"].startswith("test."):
            if r["status"] not in ("success",):
                broken.append(f"{r['unique_id']}: {r['status']}")
            continue
        name = r["unique_id"].split(".")[2]
        if r["status"] == "warn":
            warns.add(name)
        elif r["status"] != "pass":
            broken.append(f"{name}: {r['status']}")
    return items, cands, warns, broken


def compare(expected, seen) -> list[str]:
    e_items, e_cands, e_warns = expected
    o_items, o_cands, o_warns, broken = seen
    problems = [f"test or model did not pass: {b}" for b in broken]
    missing = Counter(e_items) - Counter(o_items)
    extra = Counter(o_items) - Counter(e_items)
    for m in sorted(missing.elements()):
        twin = next((x for x in extra.elements() if x[:2] == m[:2]), None)
        if twin is not None:
            problems.append(f"WRONG    {m[0]} {m[1]} expected {m[2:]} found {twin[2:]}")
            extra[twin] -= 1
        else:
            problems.append(f"MISSED   {m[0]} {m[1]} expected {m[2:]}")
    for x in sorted((+extra).elements()):
        problems.append(f"EXTRA    {x[0]} {x[1]} found {x[2:]}")
    for c in sorted(e_cands - o_cands):
        problems.append(f"MISSED   identity candidate {c}")
    for c in sorted(o_cands - e_cands):
        problems.append(f"EXTRA    identity candidate {c}")
    for w in sorted(e_warns - o_warns):
        problems.append(f"MISSED   warning from test {w}")
    for w in sorted(o_warns - e_warns):
        problems.append(f"EXTRA    warning from test {w}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=["clean", "defective"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-results", required=True)
    a = ap.parse_args(argv)
    expected = load_expected(a.scenario)
    seen = observed(a.db, a.run_results)
    problems = compare(expected, seen)
    n_items, n_warn = sum(expected[0].values()), len(expected[2])
    if problems:
        print(f"[{a.scenario}] harness FAILED")
        for p in problems:
            print("  " + p)
        return 1
    print(f"[{a.scenario}] harness OK: {n_items} reconciliation items, {len(expected[1])} identity candidates, "
          f"{n_warn} expected warnings, all matched exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
