"""Exporting the research object's RO-Crate: a valid RO-Crate, saying how it was obtained, the original unchanged."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.export import export_crate

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
VALIDATOR = shutil.which("rocrate-validator") or str(Path(sys.executable).parent / "rocrate-validator")
pytestmark = pytest.mark.skipif(not (EXAMPLE / "crate").exists(), reason="WorkflowHub example not present")


def test_export_the_crate(tmp_path):
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    before = (a.crate_dir / "ro-crate-metadata.json").read_text()
    z = export_crate(a.path, tmp_path / "out.zip")
    with zipfile.ZipFile(z) as f:
        names = set(f.namelist())
        meta = json.loads(f.read("ro-crate-metadata.json"))
        f.extractall(tmp_path / "x")
    assert {"ro-crate-metadata.json", "ro-crate-preview.html"} <= names
    how = next(e for e in meta["@graph"] if e["@id"] == "#how-this-crate-was-made")
    assert how["@type"] == "CreateAction" and "Galaxy Workflow RO-Crate" in how["description"]
    assert (a.crate_dir / "ro-crate-metadata.json").read_text() == before  # the research object is unchanged


@pytest.mark.network
@pytest.mark.skipif(not Path(VALIDATOR).exists(), reason="needs rocrate-validator (pip install roc-validator)")
def test_the_exported_crate_validates(tmp_path):
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    with zipfile.ZipFile(export_crate(a.path, tmp_path / "out.zip")) as f:
        f.extractall(tmp_path / "x")
    out = tmp_path / "v.json"
    subprocess.run([VALIDATOR, "validate", "-p", "ro-crate-1.1", "--no-paging", "-f", "json", "-o", str(out),
                    str(tmp_path / "x")], capture_output=True, timeout=600)
    assert json.loads(out.read_text())["passed"]


@pytest.mark.network
@pytest.mark.skipif(not Path(VALIDATOR).exists(), reason="needs rocrate-validator (pip install roc-validator)")
def test_the_crate_is_validated_when_created_and_again_when_it_changes(tmp_path, monkeypatch):
    from recrate.export import crate_validation

    monkeypatch.delenv("RECRATE_NO_VALIDATE", raising=False)
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    v = json.loads((a.path / "agent.json").read_text())["crate_validation"]  # stored at creation, before any export
    assert v["passed"] and v["profile"] == "workflow-ro-crate-1.0" and v["required"]["passed_checks"] > 0
    assert crate_validation(a.path)["when"] == v["when"]  # unchanged crate: not validated again
    (a.crate_dir / "notes.txt").write_text("a change")
    again = crate_validation(a.path)
    assert again["fingerprint"] != v["fingerprint"] and again["passed"]  # changed: validated again


def test_incidental_files_do_not_change_the_crate_fingerprint(tmp_path):
    """Finder's .DS_Store and Python's bytecode appear on their own; they must not make a validated crate look changed
    (which re-ran the validator, a minute or more, while the app drew its page)."""
    from recrate.export import crate_fingerprint

    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    before = crate_fingerprint(a.path)
    (a.crate_dir / ".DS_Store").write_bytes(b"\x00" * 10)
    (a.crate_dir / "workflows").mkdir(exist_ok=True)
    (a.crate_dir / "workflows" / "__pycache__").mkdir()
    (a.crate_dir / "workflows" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\x00" * 99)
    assert crate_fingerprint(a.path) == before
    (a.crate_dir / "notes.txt").write_text("a real change")
    assert crate_fingerprint(a.path) != before


def test_the_stored_validation_is_shown_without_running_the_validator(tmp_path, monkeypatch):
    """run=False never starts the validator: the stored result comes back, marked stale once the crate changed."""
    import recrate.export as export

    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    manifest = json.loads((a.path / "agent.json").read_text())
    manifest["crate_validation"] = {"passed": True, "profile": "workflow-ro-crate-1.0", "fingerprint": export.crate_fingerprint(a.path),
                                    "required": {"passed": True, "checks": 1, "passed_checks": 1, "issues": []},
                                    "recommended": {"passed": True, "checks": 1, "passed_checks": 1, "issues": []}, "when": "2026-01-01T00:00:00+00:00"}
    (a.path / "agent.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(export, "validate_crate", lambda *_: (_ for _ in ()).throw(AssertionError("validator ran")))
    assert export.crate_validation(a.path, run=False)["when"] == "2026-01-01T00:00:00+00:00"
    (a.crate_dir / "notes.txt").write_text("a change")
    v = export.crate_validation(a.path, run=False)
    assert v["stale"] and v["when"] == "2026-01-01T00:00:00+00:00"
