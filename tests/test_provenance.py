"""Reported-results provenance: where in the paper a number came from.

A drafted crate carries LLM-extracted ground truth (the reported results and the
tolerances the verdict is judged against). These must cite their source in the
paper so a person can confirm them before the draft flag is removed.
"""

from __future__ import annotations

import json

from recrate.crate import load_crate
from recrate.diagnose import diagnose_crate
from recrate.draft import build_crate


def _desc(tmp):
    (tmp / "wf.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp / "expected.csv").write_text("image,count\na.tif,10\n", encoding="utf-8")
    (tmp / "images").mkdir()
    (tmp / "images" / "a.tif").write_bytes(b"II*\0")
    return {
        "name": "Provenance test paper",
        "profile": "bioimaging",
        "draft": True,
        "methods": [{
            "name": "count",
            "workflow": {"file": "wf.py", "language": "Python"},
            "inputs": [{"id": "in-images", "name": "input_dir", "type": "File", "format": "image/tiff"}],
            "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                         "tolerances": {"count": 0.05},
                         "tolerance_sources": {"count": "stated in Methods, §2.3"}}],
            "example_data": "images",
            "expected": "expected.csv",
            "expected_source": "Table 1 (p. 4)",
        }],
    }


def test_expected_source_round_trips(tmp_path):
    crate = build_crate(_desc(tmp_path), tmp_path / "crate", base_dir=tmp_path)
    spec = load_crate(crate)
    assert spec.expected_source == "Table 1 (p. 4)"


def test_tolerance_source_round_trips(tmp_path):
    crate = build_crate(_desc(tmp_path), tmp_path / "crate", base_dir=tmp_path)
    spec = load_crate(crate)
    assert spec.tolerance_sources() == {"count": "stated in Methods, §2.3"}
    assert spec.tolerances() == {"count": 0.05}  # unchanged


def test_source_surfaced_in_method_card(tmp_path):
    crate = build_crate(_desc(tmp_path), tmp_path / "crate", base_dir=tmp_path)
    card = load_crate(crate).summary()
    assert card["reported_results_source"] == "Table 1 (p. 4)"
    assert card["tolerance_sources"] == {"count": "stated in Methods, §2.3"}


def test_crate_stays_valid_without_source(tmp_path):
    d = _desc(tmp_path)
    del d["methods"][0]["expected_source"]
    del d["methods"][0]["outputs"][0]["tolerance_sources"]
    crate = build_crate(d, tmp_path / "crate", base_dir=tmp_path)
    spec = load_crate(crate)
    assert spec.expected_source is None
    assert spec.tolerance_sources() == {}
    # the metadata is still valid JSON with the expected File entity present
    meta = json.loads((crate / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    assert any(e.get("@id", "").startswith("expected/") for e in meta["@graph"])


def _unsourced(name): return "cite no source" in name.message.lower()


def test_diagnose_warns_when_draft_results_have_no_source(tmp_path):
    d = _desc(tmp_path)
    del d["methods"][0]["expected_source"]
    crate = build_crate(d, tmp_path / "crate", base_dir=tmp_path)
    diag = diagnose_crate(crate)
    hits = [f for f in diag.all_findings() if _unsourced(f)]
    assert hits and all(f.severity == "warning" for f in hits)


def test_diagnose_silent_when_source_present(tmp_path):
    crate = build_crate(_desc(tmp_path), tmp_path / "crate", base_dir=tmp_path)  # has expected_source
    diag = diagnose_crate(crate)
    assert not any(_unsourced(f) for f in diag.all_findings())
