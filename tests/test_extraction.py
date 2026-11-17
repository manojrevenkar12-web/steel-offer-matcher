"""The model reads and quotes; code decides every value from the quotes."""

from steelmatch.agents.extractor import LineClaim, PolicyClaim, RfqClaims, decide
from steelmatch.docs.reader import DocumentText, Segment

from .conftest import TODAY

EMAIL = """Sehr geehrte Damen und Herren,

wir benötigen ca. 24 to Coils warmgewalzt in St 37-2, 3,0 x 1.500 x C, alternativ auch 2,5 mm.
Gleichwertige Güte möglich. Mit APZ 3.1 nach EN 10204. Bitte keine Asienware.
Lieferung KW 50 frei Werk Dortmund.
"""


def doc(text=EMAIL):
    return DocumentText([Segment("email", text)])


def line(**kw):
    base = dict(form="coil", form_quote="Coils", finish="hot_rolled", finish_quote="warmgewalzt", grade="St 37-2",
                grade_quote="St 37-2", thickness_mm=3.0, thickness_quote="3,0 x 1.500 x C", width_mm=1500,
                width_quote="3,0 x 1.500 x C", quantity_t=24, quantity_quote="ca. 24 to")
    return LineClaim(**(base | kw))


def claims(**kw):
    base = dict(is_rfq=True, lines=[line()], policy=PolicyClaim(certificate="3.1", certificate_quote="Mit APZ 3.1 nach EN 10204.",
                excluded_origins=["CN", "IN"], origins_quote="Bitte keine Asienware."),
                delivery_country="DE", delivery_place_quote="frei Werk Dortmund", delivery_quote="Lieferung KW 50")
    return RfqClaims(**(base | kw))


def run(c, d=None, kb=None, settings=None):
    from steelmatch.domain.grades import GradeKB
    from steelmatch.settings import load

    return decide(c, d or doc(), kb or GradeKB.load(), (settings or load()).required_fields, TODAY)


def test_clean_extraction_is_ready():
    e = run(claims())
    assert e.status == "ready", e.review
    (l,) = e.lines
    assert (l.grade_id, l.thickness_mm, l.width_mm, l.quantity_t, l.form) == ("S235JR", 3.0, 1500.0, 24.0, "coil")
    assert e.policy.certificate == "3.1"
    assert {"CN", "IN", "KR", "VN"} <= set(e.policy.excluded_origins)
    assert l.delivery_by.isocalendar().week == 50


def test_numbers_come_from_the_quote_not_the_model():
    e = run(claims(lines=[line(thickness_mm=1500, width_mm=3.0, quantity_t=24000)]))
    (l,) = e.lines
    assert (l.thickness_mm, l.width_mm, l.quantity_t) == (3.0, 1500.0, 24.0)


def test_invented_quote_is_dropped_and_sent_to_review():
    e = run(claims(lines=[line(thickness_quote="3 mm thick")]))
    assert e.status == "review" and e.invalid_quotes == 1
    assert any(r.field == "thickness_mm" for r in e.review)


def test_equivalent_is_not_permission_for_a_higher_grade():
    c = claims()
    c.policy.upgrades_ok, c.policy.upgrades_quote = True, "Gleichwertige Güte möglich."
    assert run(c).policy.upgrades_ok is False


def test_hallucinated_certificate_goes_to_review():
    c = claims()
    c.policy.certificate, c.policy.certificate_quote = "3.2", "Abnahmeprüfzeugnis 3.2"
    e = run(c)
    assert e.policy.certificate == "none" and any(r.field == "certificate" for r in e.review)


def test_unknown_designation_proposes_and_reviews():
    d = doc(EMAIL.replace("St 37-2", "St37"))
    e = run(claims(lines=[line(grade="St37", grade_quote="St37")]), d)
    assert e.status == "review" and e.unresolved_grades == ["St37"]
    item = next(r for r in e.review if r.field == "grade")
    assert "S235JR" in item.proposals


def test_alternative_thickness_is_kept():
    c = claims(lines=[line(alternatives=[{"kind": "thickness_mm", "value": 2.5, "quote": "alternativ auch 2,5 mm"}])])
    assert run(c).lines[0].alternatives == [{"thickness_mm": 2.5}]


def test_not_an_rfq():
    assert run(RfqClaims(is_rfq=False)).status == "not_rfq"


def test_transcribed_numbers_need_confirmation():
    from steelmatch.domain.grades import GradeKB
    from steelmatch.settings import load

    d = DocumentText([Segment("email", "Anfrage als Foto anbei. Lieferung KW 50 frei Werk Dortmund."),
                      Segment("scan.png (transcribed)", "1. ca. 24 to Coils warmgewalzt St 37-2 3,0 x 1.500 x C")])
    e = decide(claims(policy=PolicyClaim()), d, GradeKB.load(), load().required_fields, TODAY, {"scan.png (transcribed)"})
    assert e.status == "review" and any("transcribed" in r.reason for r in e.review)
