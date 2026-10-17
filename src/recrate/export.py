"""Export the method crate as a WorkflowHub-ingestible ``.crate.zip``.

The Workflow RO-Crate profile 1.0 requires the crate to be zipped with
``ro-crate-metadata.json`` directly at the zip root (so the whole archive is the
RO-Crate Root), and recommends the ``.crate.zip`` extension so WorkflowHub
recognises it. See https://w3id.org/workflowhub/workflow-ro-crate/1.0.

This packages the ``crate/`` folder as-is (the recipe), not the run output.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .crate import METADATA_FILE, CrateError, resolve_crate_dir

CRATE_ZIP_SUFFIX = ".crate.zip"


def _target(crate_dir: Path, out: Path) -> Path:
    """Resolve the output path and force the WorkflowHub ``.crate.zip`` extension."""
    if out.is_dir():
        return out / f"{crate_dir.name}{CRATE_ZIP_SUFFIX}"
    name = out.name
    if name.endswith(CRATE_ZIP_SUFFIX):
        return out
    # strip a lone .zip so "mycrate.zip" becomes "mycrate.crate.zip"
    if name.endswith(".zip"):
        name = name[: -len(".zip")]
    return out.with_name(name + CRATE_ZIP_SUFFIX)


def export_crate_zip(crate: str | Path, out: str | Path) -> Path:
    """Zip a crate folder (or a paper agent's ``crate/``) into a ``.crate.zip``.

    ``out`` may be a directory (the archive is named after the crate) or a file
    path; either way the result ends in ``.crate.zip``. Returns the archive path.
    """
    crate_dir = resolve_crate_dir(crate)
    if not (crate_dir / METADATA_FILE).exists():
        raise CrateError(f"No {METADATA_FILE} in {crate_dir}; nothing to export.")

    out_zip = _target(crate_dir, Path(out))
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in crate_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            # relative to the crate root, so ro-crate-metadata.json lands at the zip root
            z.write(f, f.relative_to(crate_dir).as_posix())
    return out_zip
