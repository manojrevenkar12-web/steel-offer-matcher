"""The project's invariants, checked."""

import ast
import json
from pathlib import Path

from steeleval import evaluate
from steeleval.harness import build

ROOT = Path(__file__).resolve().parents[1]


def imports(package: str) -> set[str]:
    out = set()
    for f in (ROOT / "src" / package).rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                out.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                out.add(node.module.split(".")[0])
    return out


def test_the_platform_never_reads_the_world():
    assert not {"steelworld", "steeleval"} & imports("steelmatch")
    for f in (ROOT / "src" / "steelmatch").rglob("*.py"):
        assert "world.json" not in f.read_text()


def test_the_world_does_not_read_the_platform_or_its_settings():
    assert "steelmatch" not in imports("steelworld")
    for f in (ROOT / "src" / "steelworld").rglob("*.py"):
        text = f.read_text()
        assert "matcher.json" not in text and "models.json" not in text and "knowledge/" not in text


def test_same_luck_for_every_design():
    a, b = build(23), build(23)
    assert [r.message_id for r in a.month.rfqs] == [r.message_id for r in b.month.rfqs]
    assert [d.message["body"] for d in a.docs.values()] == [d.message["body"] for d in b.docs.values()]
    from steelmatch.docs.reader import table_rows

    rows = lambda w: [table_rows(s.data, s.filename) for s in w.lists[0][:4]]
    assert rows(a) == rows(b)  # spreadsheet bytes carry timestamps; their content does not


def test_settings_live_in_config():
    cfg = json.loads((ROOT / "config" / "matcher.json").read_text())
    for key in ("fit", "harness", "matching", "routes", "learning"):
        assert key in cfg


def test_heldout_seeds_are_disjoint_and_locked():
    cfg = json.loads((ROOT / "config" / "world.json").read_text())
    assert not set(cfg["dev_seeds"]) & set(cfg["heldout_seeds"])
    lock = ROOT / "config" / "heldout.lock"
    if (ROOT / "reports" / "heldout.json").exists():
        assert lock.read_text().strip() == evaluate.fingerprint(), "the platform changed after the held-out run"


def test_reports_are_generated():
    for name in ("dev.json",):
        p = ROOT / "reports" / name
        if p.exists():
            data = json.loads(p.read_text())
            assert set(data["mean"]) == set(evaluate.DESIGNS)
