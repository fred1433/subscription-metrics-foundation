"""Scenario harness: the build must find exactly the anomalies written down by hand, no more, no less.

For a scenario it compares
  1. the dbt tests that ended in WARN with harness/expected/<scenario>_warnings.txt
     (and fails on any test that ended in fail or error),
  2. every reconciliation item (check, key, category, classification, amount) with <scenario>_items.csv,
  3. the identity review queue with <scenario>_identity_candidates.csv.
A missed anomaly, an extra one or a misclassified one fails the run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))


def load_expected(scenario: str, expected_dir: str = os.path.join(HERE, "expected")):
    with open(os.path.join(expected_dir, f"{scenario}_items.csv")) as f:
        items = {(r["check_name"], r["item_key"]): (r["category"], r["classification"], int(r["amount_pence"]))
                 for r in csv.DictReader(f)}
    with open(os.path.join(expected_dir, f"{scenario}_identity_candidates.csv")) as f:
        cands = {(r["customer_key_a"], r["customer_key_b"], r["matched_on"]) for r in csv.DictReader(f)}
    with open(os.path.join(expected_dir, f"{scenario}_warnings.txt")) as f:
        warns = {line.strip() for line in f if line.strip()}
    return items, cands, warns


def observed(db: str, run_results: str):
    con = duckdb.connect(db, read_only=True)
    items = {(r[0], r[1]): (r[2], r[3], int(r[4])) for r in con.execute(
        "select check_name, item_key, category, classification, amount_pence "
        "from main_reconciliation.rec_exceptions").fetchall()}
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
    for k in sorted(e_items.keys() - o_items.keys()):
        problems.append(f"MISSED   {k[0]} {k[1]} expected {e_items[k]}")
    for k in sorted(o_items.keys() - e_items.keys()):
        problems.append(f"EXTRA    {k[0]} {k[1]} found {o_items[k]}")
    for k in sorted(e_items.keys() & o_items.keys()):
        if e_items[k] != o_items[k]:
            problems.append(f"WRONG    {k[0]} {k[1]} expected {e_items[k]} found {o_items[k]}")
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
    n_items, n_warn = len(expected[0]), len(expected[2])
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
