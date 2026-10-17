"""Generate a methods paragraph from a run crate.

Everything in the paragraph comes from the run crate, so it states exactly what
ran: software versions, parameters (including adjustments), calibration, and
where the record lives. Edit the wording; keep the facts.
"""

from __future__ import annotations

from pathlib import Path

from .runcrate import entity_index, load_run_crate


def methods_paragraph(run_dir: Path) -> str:
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    run = idx["#run"]
    wf = idx.get(run["instrument"]["@id"], {})
    software = [idx[s["@id"]] for s in wf.get("softwareRequirements", []) if s["@id"] in idx]
    sw_txt = ", ".join(f"{s['name']} {s.get('softwareVersion', '')}".strip() for s in software) or "the declared software"
    lang = idx.get(wf.get("programmingLanguage", {}).get("@id", ""), {}).get("name", "a workflow")

    params, adjusted = [], []
    n_images = 0
    for o in run.get("object", []):
        e = idx.get(o["@id"], {})
        if e.get("@type") == "File":
            n_images += 1
        elif e.get("@type") == "PropertyValue":
            params.append(f"{e['name']} = {e['value']}")
            if "description" in e:
                d = e["description"]
                adjusted.append(f"{e['name']} was {d[0].lower()}{d[1:]}")

    pixel = next((d for d in (idx.get(f"#check-{i}", {}) for i in range(20)) if d.get("name") == "Pixel size"), None)
    pixel_txt = ""
    if pixel:
        observed = pixel["description"].split("data: ", 1)[-1].split(";")[0]
        pixel_txt = f" Images had a pixel size of {observed}."

    paper = root.get("citation", {}).get("@id")
    based = root.get("isBasedOn", {}).get("@id")
    text = (
        f"{n_images} image(s) were analysed with the workflow {wf.get('name', '')} ({lang}) "
        f"{'from ' + paper if paper else 'from the published method'}, run with {sw_txt}. "
        f"Parameters: {'; '.join(params)}.{pixel_txt}"
    )
    if adjusted:
        text += " " + " ".join(adjusted)
    verdict = idx.get("#verdict")
    if verdict:
        text += f" Reproduction against the reported results: {verdict['description']}."
    text += (f" The full run, including inputs, parameters, software versions and outputs, is recorded as a "
             f"Workflow Run RO-Crate based on {based} and generated with ReCRATE.")
    return text
