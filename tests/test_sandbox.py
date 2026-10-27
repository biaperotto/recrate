"""A paper's code runs confined: it can write to its run's folders, not to yours, and can't reach the network."""

from __future__ import annotations

import subprocess
import sys

import pytest

from recrate import sandbox


def run(code: str, out) -> subprocess.CompletedProcess:
    cmd, env = sandbox.wrap([sys.executable, "-c", code], [out])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env={**__import__("os").environ, **env})


@pytest.mark.skipif(sandbox.backend() is None, reason="no sandbox on this computer (sandbox-exec or bwrap)")
def test_writes_only_to_the_run_and_no_network(tmp_path):
    import uuid
    from pathlib import Path

    out = tmp_path / "out"
    home = Path.home() / f".recrate-sandbox-test-{uuid.uuid4().hex}"  # temporary folders are writable; your home isn't
    r = run(f"open({str(out / 'ok.txt')!r}, 'w').write('x')", out)
    assert r.returncode == 0 and (out / "ok.txt").exists()
    r = run(f"open({str(home)!r}, 'w').write('x')", out)
    written = home.exists()
    home.unlink(missing_ok=True)
    assert r.returncode != 0 and not written  # outside the run's folders: refused
    r = run("import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)", out)
    assert r.returncode != 0  # no network
    assert "no network" in sandbox.describe()


def test_no_sandbox_means_no_run_unless_chosen(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    monkeypatch.delenv("RECRATE_SANDBOX", raising=False)
    with pytest.raises(sandbox.SandboxUnavailable, match="unsandboxed"):
        sandbox.wrap(["true"], [tmp_path])
    monkeypatch.setenv("RECRATE_SANDBOX", "off")
    cmd, _ = sandbox.wrap(["true"], [tmp_path])
    assert cmd == ["true"] and "unsandboxed" in sandbox.describe()


def test_unknown_mode_is_refused(monkeypatch):
    monkeypatch.setenv("RECRATE_SANDBOX", "chroot")
    with pytest.raises(sandbox.SandboxUnavailable, match="Unknown"):
        sandbox.backend()
