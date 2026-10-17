from __future__ import annotations

from recrate.cli import main


def test_cli_end_to_end(demo, tmp_path, capsys):
    runs = str(tmp_path / "runs")
    crate, mine = str(demo["crate"]), str(demo["my_images"])

    assert main(["inspect", crate]) == 0
    assert main(["reproduce", crate, "--runner", "python", "--runs-dir", runs, "--who", "Anna"]) == 0
    assert "Reproduced" in capsys.readouterr().out

    assert main(["check", crate, mine]) == 0
    assert "min_size_px: 50.0 -> 12.5" in capsys.readouterr().out

    assert main(["run", crate, mine, "--runner", "python", "--runs-dir", runs, "--who", "Anna"]) == 0
    assert main(["record", crate, "--runs-dir", runs]) == 0
    assert "Reproduced 1 time(s)" in capsys.readouterr().out
