"""The harness itself must fail when an anomaly is missed, added, duplicated or misclassified."""
import os
import subprocess
from collections import Counter

from harness.check import HERE, compare, load_expected, observed

EXPECTED_FILES = [f"{sc}_{kind}" for sc in ("clean", "defective")
                  for kind in ("items.csv", "identity_candidates.csv", "warnings.txt")]


def _seen(root, scenario):
    return observed(os.path.join(root, "warehouse", f"{scenario}.duckdb"),
                    os.path.join(root, "target", scenario, "run_results.json"))


def test_defective_scenario_matches_the_hand_written_list(root):
    assert compare(load_expected("defective"), _seen(root, "defective")) == []


def test_expectation_files_are_committed_and_read(root):
    tracked = subprocess.run(["git", "ls-files", "harness/expected"], cwd=root, capture_output=True,
                             text=True, check=True).stdout.split()
    for name in EXPECTED_FILES:
        assert f"harness/expected/{name}" in tracked, f"{name} is not committed"
        assert os.path.exists(os.path.join(HERE, "expected", name))
    items, _, warns = load_expected("defective")
    assert sum(items.values()) >= 10 and len(warns) == 8


def test_a_missed_anomaly_fails(root):
    problems = compare(load_expected("defective"), _seen(root, "clean"))
    assert any(p.startswith("MISSED") and "PO-D1-B2" in p for p in problems)
    assert any("warning from test warn_completeness_missing_in_landing" in p for p in problems)


def test_an_extra_or_misclassified_anomaly_fails(root):
    items, cands, warns = load_expected("defective")
    items = Counter(items)
    dup = next(k for k in items if k[1] == "PO-D2-B1")
    del items[dup]
    d3 = next(k for k in items if k[1] == "PO-D3-B1")
    del items[d3]
    items[d3[:4] + (-349,)] += 1
    problems = compare((items, cands, warns), _seen(root, "defective"))
    assert any(p.startswith("EXTRA") and "PO-D2-B1" in p for p in problems)
    assert any(p.startswith("WRONG") and "PO-D3-B1" in p for p in problems)


def test_a_second_occurrence_of_the_same_key_fails(root):
    expected = load_expected("defective")
    o_items, o_cands, o_warns, broken = _seen(root, "defective")
    o_items = Counter(o_items)
    again = next(k for k in o_items if k[1] == "PO-D1-B2")
    o_items[again] += 1          # the same exception reported twice
    problems = compare(expected, (o_items, o_cands, o_warns, broken))
    assert any(p.startswith("EXTRA") and "PO-D1-B2" in p for p in problems)
