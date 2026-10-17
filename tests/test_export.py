"""Export the method crate as a WorkflowHub-ingestible .crate.zip.

WorkflowHub requires the Workflow RO-Crate zipped with ro-crate-metadata.json
directly at the zip root, and recommends the .crate.zip extension.
"""

from __future__ import annotations

import zipfile

import pytest

from recrate.crate import CrateError
from recrate.export import export_crate_zip


def test_metadata_at_zip_root_and_extension(agent):
    out = export_crate_zip(agent.crate_dir, agent.path / "out")
    assert out.name.endswith(".crate.zip")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "ro-crate-metadata.json" in names  # at the root, not nested
    # the workflow file(s) travel too
    assert any(n.endswith((".py", ".ijm")) for n in names)


def test_accepts_agent_folder_and_forces_extension(agent, tmp_path):
    out = export_crate_zip(agent.path, tmp_path / "mycrate.zip")  # resolves agent -> crate/
    assert out.name == "mycrate.crate.zip"
    with zipfile.ZipFile(out) as z:
        assert "ro-crate-metadata.json" in z.namelist()


def test_errors_when_no_crate(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CrateError):
        export_crate_zip(tmp_path / "empty", tmp_path / "x.crate.zip")
