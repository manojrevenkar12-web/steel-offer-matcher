"""The gate: a value left open is not wrong; a value decided wrongly is a regression."""

from steelmatch.agents.extractor import Extraction, ReviewItem
from steelmatch.domain.spec import LineSpec, Policy
from steelmatch.review.gate import judge

CASE = {"case_id": "c1", "expected": {"lines": [{"line": 1, "grade_id": "S235JR", "form": "coil", "thickness_mm": 3.0,
                                                 "width_mm": 1500.0, "length_mm": None, "quantity_t": 20.0}]}}


def line(**kw):
    base = dict(line=1, form="coil", finish="hot_rolled", grade_id="S235JR", thickness_mm=3.0, width_mm=1500, quantity_t=20)
    return LineSpec(**(base | kw))


def test_exact():
    r = judge(CASE, Extraction("ready", [line()], Policy()))
    assert r.exact and not r.wrong_values


def test_open_value_is_not_wrong():
    r = judge(CASE, Extraction("review", [], Policy(), [ReviewItem(1, "thickness_mm", "not stated")]))
    assert not r.exact and not r.wrong_values and r.review


def test_wrong_value_is_counted():
    r = judge(CASE, Extraction("ready", [line(width_mm=1250)], Policy()))
    assert r.wrong_values == ["1.width_mm"]


def test_the_accepted_baseline_has_no_wrong_values():
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "evals" / "baseline.json"
    if p.exists():
        data = json.loads(p.read_text())
        assert data["passed"] and data["wrong_values"] == 0
