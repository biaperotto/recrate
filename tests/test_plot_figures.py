"""Figures drawn by code from a file, chosen by name (what Paper2Agent's chat does with ad hoc plotting code), and
recorded in the run crate with the data they came from. The model picks what to plot; it never draws."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from recrate.figures import PLOT_KINDS, make_run_figure, plot_table, read_table  # noqa: E402

ROWS = [{"species": "Adelie", "sex": "male", "mass": 4043.5, "flipper": 192.4},
        {"species": "Adelie", "sex": "female", "mass": 3368.8, "flipper": 187.8},
        {"species": "Gentoo", "sex": "male", "mass": 5484.8, "flipper": 221.5},
        {"species": "Gentoo", "sex": "female", "mass": 4679.7, "flipper": 212.7},
        {"species": "Chinstrap", "sex": "male", "mass": 3939.0, "flipper": 199.9},
        {"species": "Chinstrap", "sex": "female", "mass": 3527.2, "flipper": 191.7}]


def test_every_plot_kind_draws_from_the_rows():
    for kind in PLOT_KINDS:
        x, y = ("flipper", "mass") if kind in {"scatter", "line"} else ("species", "mass")
        png, note = plot_table(ROWS, kind, x=x, y=y, group="sex", title=f"{kind} test")
        assert png[:4] == b"\x89PNG" and note.startswith("6 rows"), kind
    png, note = plot_table(ROWS, "bar", x="species", y="mass")  # repeated categories are averaged, and said
    assert "means" in note


def test_a_plot_names_what_it_cannot_do():
    with pytest.raises(ValueError, match="No column 'weight'"):
        plot_table(ROWS, "bar", x="species", y="weight")
    with pytest.raises(ValueError, match="not numeric"):
        plot_table(ROWS, "bar", x="species", y="sex")
    with pytest.raises(ValueError, match="Unknown plot kind"):
        plot_table(ROWS, "pie", x="species", y="mass")
    with pytest.raises(ValueError, match="groups"):
        plot_table([{"g": str(i), "v": i} for i in range(9)], "hist", y="v", group="g")


def test_read_table_turns_cells_into_numbers(tmp_path):
    (tmp_path / "t.csv").write_text("item,count,note\na,\"1,234\",x\nb,NA,y\n")
    rows = read_table(tmp_path / "t.csv")
    assert rows[0]["count"] == 1234.0 and rows[1]["count"] is None and rows[0]["note"] == "x"


def test_a_figure_from_a_run_is_recorded_with_its_spec_and_data(agent, tmp_path):
    m = next(x for x in agent.methods if x.run_crate)
    run = Path(m.run_crate)
    path = make_run_figure(run, "bar", {"source": "results", "x": "image", "y": "count", "title": "Nuclei per image"})
    assert path.exists() and path.parent == run / "outputs" / "figures" and path.name == "bar-count-by-image.png"
    g = json.loads((run / "ro-crate-metadata.json").read_text())["@graph"]
    by_id = {e["@id"]: e for e in g}
    img = by_id["outputs/figures/bar-count-by-image.png"]
    spec = json.loads(by_id[img["additionalProperty"][0]["@id"]]["value"])  # what was plotted, redrawable
    assert spec == {"kind": "bar", "source": "outputs/results.csv", "x": "image", "y": "count", "group": None,
                    "title": "Nuclei per image"}
    action = next(e for e in g if e.get("@id") == "#figure-bar-count-by-image-png")
    assert action["@type"] == "CreateAction" and action["result"] == {"@id": img["@id"]}
    assert action["object"] == [{"@id": "outputs/results.csv"}]
    # a table from outside the run (the user's data, a paper table) is copied in, so the record holds what was plotted
    (tmp_path / "penguins.csv").write_text("species,sex,mass\n" + "\n".join(
        f"{r['species']},{r['sex']},{r['mass']}" for r in ROWS) + "\n")
    path2 = make_run_figure(run, "bar", {"source": str(tmp_path / "penguins.csv"), "x": "species", "y": "mass",
                                         "group": "sex"})
    assert path2.name == "bar-mass-by-species-sex.png" and (run / "outputs" / "figures" / "data" / "penguins.csv").exists()
    g = json.loads((run / "ro-crate-metadata.json").read_text())["@graph"]
    action = next(e for e in g if e.get("@id") == "#figure-bar-mass-by-species-sex-png")
    assert action["object"] == [{"@id": "outputs/figures/data/penguins.csv"}]
    with pytest.raises(ValueError, match="No column"):
        make_run_figure(run, "scatter", {"source": "results", "x": "image", "y": "nope"})


def _content(result):
    if isinstance(result, tuple):  # mcp 1.x: (content, structured)
        result = result[0]
    return getattr(result, "content", result)


def test_mcp_make_figure_returns_the_image_inline_and_run_tools_do_too(agent):
    pytest.importorskip("mcp")
    from recrate.mcp_server import build_server

    server = build_server(agent.path)
    m = next(x for x in agent.methods if x.run_crate)

    content = _content(asyncio.run(server.call_tool("make_figure", {
        "run_crate": m.run_crate, "kind": "hist", "source": "results", "y": "count"})))
    kinds = [getattr(c, "type", "") for c in content]
    assert kinds == ["text", "image"], kinds  # the JSON reply, then the picture itself
    payload = json.loads(content[0].text)
    assert payload["artifacts"][0]["path"].endswith("hist-count.png")

    with pytest.raises(Exception, match="No table|Error executing tool"):  # an unknown table is a tool error
        asyncio.run(server.call_tool("make_figure", {"kind": "line", "source": "table:Table 9", "x": "a", "y": "b"}))

    content = _content(asyncio.run(server.call_tool("reproduce", {"method": m.id, "who": "test"})))
    kinds = [getattr(c, "type", "") for c in content]
    assert kinds[0] == "text" and "image" in kinds  # a run's figures and overlays come back as images
    rep = json.loads(content[0].text)
    assert rep["run_crate"] and all(Path(a["path"]).is_absolute() for a in rep["artifacts"])
