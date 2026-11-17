"""Stock lists: the model maps columns once per layout, code checks the mapping on the values."""

import csv
import io

from steelmatch.agents.stocklist import ColumnMap, fingerprint, header_row, read_rows, validate
from steelmatch.docs.reader import table_rows

from .conftest import TODAY

ROWS = [
    ["Werkstoff", "Dicke", "Breite", "Länge", "Menge (t)", "€/kg", "Prüfzeugnis", "Qualität"],
    ["S235JR", "3,00", "1500", "Coil", "18,40", "0,642", "3.1", "1. Wahl"],
    ["S355J2+N", "5,00", "2000", "6000", "9,10", "0,705", "3.1", "2. Wahl"],
    ["ST 37", "2,50", "1250", "Coil", "21,00", "0,618", "2.2", "1. Wahl"],
]
GOOD = ColumnMap(columns={"designation": "Werkstoff", "thickness": "Dicke", "width": "Breite", "length": "Länge",
                          "weight": "Menge (t)", "price": "€/kg", "certificate": "Prüfzeugnis", "quality": "Qualität"},
                 weight_unit="t", price_unit="eur_kg")


def test_good_mapping_reads_every_row(kb):
    assert validate(GOOD, ROWS[0], ROWS[1:]) == []
    items, skipped = read_rows(GOOD, ROWS[0], ROWS[1:], "S01", "DE", kb, TODAY, "S01_v0")
    assert not skipped
    a, b, c = items
    assert (a.form, a.grade_id, a.price_eur_t, a.weight_t) == ("coil", "S235JR", 642.0, 18.4)
    assert (b.form, b.length_mm, b.quality, b.grade_id) == ("sheet", 6000.0, "secondary", "S355J2")
    assert c.grade_id is None and c.grade_text == "ST 37"  # unknown designations are kept, not guessed


def test_swapped_columns_are_caught():
    bad = GOOD.model_copy(update={"columns": GOOD.columns | {"thickness": "Breite", "width": "Dicke"}})
    assert validate(bad, ROWS[0], ROWS[1:])  # thicknesses of 1500 mm are not read as thicknesses
    wl = GOOD.model_copy(update={"columns": GOOD.columns | {"width": "Länge", "length": "Breite"}})
    assert validate(wl, ROWS[0], ROWS[1:])


def test_wrong_units_are_caught():
    assert any("per tonne or per kilo" in p for p in validate(GOOD.model_copy(update={"price_unit": "eur_t"}), ROWS[0], ROWS[1:]))
    assert any("kg or t" in p for p in validate(GOOD.model_copy(update={"weight_unit": "kg"}), ROWS[0], ROWS[1:]))


def test_header_detection_and_fingerprint():
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows([["Lagerliste Mai"], *ROWS])
    rows = table_rows(buf.getvalue().encode(), "list.csv")
    h = header_row(rows)
    assert rows[h][0] == "Werkstoff"
    assert fingerprint(rows[h]) == fingerprint(list(ROWS[0]))
