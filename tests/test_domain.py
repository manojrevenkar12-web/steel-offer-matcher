from datetime import date

import pytest

from steelmatch.domain.dates import delivery_by
from steelmatch.domain.fit import check, rank
from steelmatch.domain.spec import LineSpec, Policy, StockItem
from steelmatch.domain.units import dims, read_field, readings

from .conftest import TODAY


@pytest.mark.parametrize("token,expected", [
    ("1,5", [1.5]), ("1.500", [1500.0, 1.5]), ("1.250,5", [1250.5]), ("12", [12.0]), ("2.5", [2.5]), ("20 000", [20000.0]),
])
def test_readings(token, expected):
    assert readings(token) == expected


@pytest.mark.parametrize("field,quote,value", [
    ("width_mm", "Breite 1.500 mm", 1500.0),
    ("thickness_mm", "1,5 x 1.500 x C", 1.5),
    ("width_mm", "1,5 x 1.500 x C", 1500.0),
    ("quantity_t", "ca. 20.000 kg", 20.0),
    ("quantity_t", "25 to", 25.0),
    ("price_eur_t", "Zielpreis 0,72 €/kg", 720.0),
    ("length_mm", "3,0 x 1250 x 2500 mm", 2500.0),
])
def test_read_field(field, quote, value):
    assert read_field(field, quote).value == value


def test_ambiguous_quantity_goes_to_review():
    r = read_field("quantity_t", "2 Coils à 12 t")
    assert r.value is None and r.ambiguous


def test_dims_marks_coil():
    assert dims("2,00 x 1250 x C") == {"thickness_mm": 2.0, "width_mm": 1250.0, "form": "coil"}


def test_delivery_dates():
    assert delivery_by("Lieferung KW 50", TODAY) == (date(2026, 12, 11), True)
    assert delivery_by("bis 04.12.", TODAY) == (date(2026, 12, 4), True)
    assert delivery_by("delivery asap", TODAY) == (None, True)
    assert delivery_by("Mitte Dezember", TODAY) == (None, False)


@pytest.mark.parametrize("text,grade,via", [
    ("S235JR+AR", "S235JR", "id"), ("st 37-2", "S235JR", "alias"), ("1.0038", "S235JR", "number"),
    ("S235 JR / 1.0038", "S235JR", "id"), ("S 355 J2 + N", "S355J2", "alias"), ("V2A", "1.4301", "alias"),
    ("1.4301 2B", "1.4301", "number"), ("DX51D+Z275", "DX51D", "id"),
])
def test_identifiers_decide(kb, text, grade, via):
    r = kb.resolve(text)
    assert (r.grade_id, r.via) == (grade, via)


def test_names_only_propose(kb):
    r = kb.resolve("St37")
    assert r.grade_id is None and "S235JR" in r.proposals
    comparable = kb.resolve("ASTM A36")
    assert comparable.grade_id is None and comparable.via == "comparable"
    assert kb.resolve("S235JR oder S355J2").grade_id is None


def test_learned_alias_resolves(kb):
    assert kb.with_learned("St37", "S235JR").resolve("ST 37").grade_id == "S235JR"


def test_upgrade_rules(kb):
    assert kb.upgrade_kind("S235JR", "S355J2") == "upgrade"
    assert kb.upgrade_kind("S355J2", "S235JR") == "no"
    assert kb.upgrade_kind("S355J2", "S355JR") == "no"  # weaker impact toughness
    assert kb.upgrade_kind("1.4301", "1.4404") == "upgrade"
    assert kb.upgrade_kind("1.4404", "1.4301") == "no"
    assert kb.upgrade_kind("DC01", "DX51D") == "no"


def spec(**kw):
    base = dict(form="sheet", finish="hot_rolled", grade_id="S235JR", thickness_mm=3.0, width_mm=1250, length_mm=2500,
                quantity_t=20, delivery_country="DE")
    return LineSpec(**(base | kw))


def item(**kw):
    base = dict(item_id="i1", supplier_id="s", form="coil", finish="hot_rolled", grade_id="S235JR", grade_text="S235JR",
                thickness_mm=3.0, width_mm=1250, weight_t=22, price_eur_t=640, country="DE", origin="DE", certificate="3.1")
    return StockItem(**(base | kw))


def fit(s, it, policy=None, settings=None, kb=None):
    from steelmatch.domain.grades import GradeKB
    from steelmatch.settings import load

    return check(s, policy or Policy(), it, kb or GradeKB.load(), (settings or load()).fit, TODAY)


def test_coil_cut_to_sheets_passes_and_is_costed():
    f = fit(spec(), item())
    assert f.status == "pass" and "cut to length" in f.processing
    assert f.landed_eur_t == 640 + 22 + 28


def test_wider_coil_is_slit_when_trim_is_small():
    f = fit(spec(form="coil", width_mm=600, length_mm=None), item(width_mm=1250))
    assert f.status == "pass" and "slitting" in f.processing and f.usable_t < 22


def test_slitting_with_large_trim_fails():
    f = fit(spec(form="coil", width_mm=1250, length_mm=None), item(width_mm=1500))
    assert f.status == "fail"


def test_upgrade_needs_permission():
    assert fit(spec(), item(grade_id="S355J2")).status == "review"
    assert fit(spec(), item(grade_id="S355J2"), Policy(upgrades_ok=True)).status == "pass"


def test_hard_failures():
    assert fit(spec(), item(quality="secondary")).status == "fail"
    assert fit(spec(), item(certificate="2.2"), Policy(certificate="3.1")).status == "fail"
    assert fit(spec(), item(origin="RU"), Policy(upgrades_ok=True)).status == "fail"
    assert fit(spec(), item(thickness_mm=3.5)).status == "fail"
    assert fit(spec(form="coil", length_mm=None), item(form="sheet", length_mm=2500)).status == "fail"


def test_unknown_origin_with_exclusion_goes_to_review():
    assert fit(spec(), item(origin=None), Policy(excluded_origins=["CN"])).status == "review"


def test_alternative_thickness_counts():
    s = spec(thickness_mm=2.5, alternatives=[{"thickness_mm": 3.0}])
    assert fit(s, item()).status == "pass"


def test_price_just_over_target_is_review_far_over_is_fail():
    assert fit(spec(max_price_eur_t=680), item()).status == "review"
    assert fit(spec(max_price_eur_t=600), item()).status == "fail"


def test_partial_lot_is_offered_and_flagged():
    f = fit(spec(), item(weight_t=10))
    assert f.status == "pass" and f.partial


def test_rank_prefers_cheaper_landed_then_exact_grade():
    a = fit(spec(), item(item_id="a", price_eur_t=660))
    b = fit(spec(), item(item_id="b", price_eur_t=640))
    c = fit(spec(), item(item_id="c", grade_id="S355J2", price_eur_t=600), Policy(upgrades_ok=True))
    assert [f.item_id for f in rank([a, b, c])] == ["c", "b", "a"]
