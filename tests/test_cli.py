from __future__ import annotations

from recrate.cli import main


def test_cli_end_to_end(demo, tmp_path, capsys):
    agent = str(tmp_path / "agent")
    mine = str(demo["my_images"])

    assert main(["agentify", "--paper", str(demo["paper"]), "--crate", str(demo["crate"]), "--out", agent]) == 0
    out = capsys.readouterr().out
    assert "count_nuclei [ImageJ Macro Language] readiness: ready" in out
    assert "Same numbers     passed       2/2 values within tolerance (paper-stated)" in out
    assert "size_distribution [Python] readiness: ready" in out
    assert "verdict: Reproduced: 2 of 2 method(s)" in out

    assert main(["ask", agent, "minimum particle size debris"]) == 0
    assert "50 pixels" in capsys.readouterr().out

    assert main(["check", agent, mine, "--method", "count_nuclei"]) == 0
    assert "min_size_px: 50.0 -> 12.5" in capsys.readouterr().out

    assert main(["run", agent, mine, "--method", "size_distribution", "--who", "Anna"]) == 0
    assert main(["record", agent]) == 0
    assert "reused on new data 1 time(s)" in capsys.readouterr().out
