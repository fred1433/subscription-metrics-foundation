"""The harness itself must fail when an anomaly is missed, added or misclassified."""
import os

from harness.check import compare, load_expected, observed


def _seen(root, scenario):
    return observed(os.path.join(root, "warehouse", f"{scenario}.duckdb"),
                    os.path.join(root, "target", scenario, "run_results.json"))


def test_defective_scenario_matches_the_hand_written_list(root):
    assert compare(load_expected("defective"), _seen(root, "defective")) == []


def test_a_missed_anomaly_fails(root):
    # clean data checked against the defective expectations: every injected fault is "missed"
    problems = compare(load_expected("defective"), _seen(root, "clean"))
    assert any(p.startswith("MISSED") and "PO-D1-B2" in p for p in problems)
    assert any("warning from test warn_completeness_missing_in_landing" in p for p in problems)


def test_an_extra_or_misclassified_anomaly_fails(root):
    items, cands, warns = load_expected("defective")
    items = dict(items)
    del items[("completeness", "PO-D2-B1")]
    items[("completeness", "PO-D3-B1")] = ("amount_mismatch", "exception", -349)
    problems = compare((items, cands, warns), _seen(root, "defective"))
    assert any(p.startswith("EXTRA") and "PO-D2-B1" in p for p in problems)
    assert any(p.startswith("WRONG") and "PO-D3-B1" in p for p in problems)
