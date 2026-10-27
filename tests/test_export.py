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
