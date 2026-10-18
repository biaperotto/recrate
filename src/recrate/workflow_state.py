"""The /recrate skill's progress, recorded so it can be checked and resumed.

Paper2Agent's coordinator writes a marker per finished step and checks the sequence before delivering. ReCRATE
does the same in ``<agent>/work/state.json``: each stage, who did it (role and subagent id), when, and the
checksum of what it produced. ``check_state`` then verifies:

* **order**: no stage was done before the stages it depends on;
* **outputs**: each stage's required files exist, and the latest record of each file matches it (a file changed
  after it was recorded means a step was skipped);
* **independence**: the verifier is not one of the implementers;
* **the gates**: ``check-description`` passes on the current description, and validation ran after the last
  change to it.

It also says which stage comes next, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGES = ["setup", "read", "survey", "plan", "implement", "assemble", "check", "validate", "verify", "deliver"]
NEEDS = {"read": ["setup"], "survey": ["setup"], "plan": ["read", "survey"], "implement": ["plan"],
         "assemble": ["implement"], "check": ["assemble"], "validate": ["check"], "verify": ["validate"],
         "deliver": ["verify"]}
OUTPUTS = {"setup": ["agent.json"], "read": ["work/methods.json"], "survey": ["work/environment-review.json"],
           "assemble": ["crate_description.json"], "verify": ["work/verification.json"], "deliver": ["REPORT.md"]}
# A paper with no runnable method goes from plan straight to deliver (with the report saying why).
SHORTCUT = {"deliver": ["plan"]}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _path(agent_dir: Path) -> Path:
    return Path(agent_dir) / "work" / "state.json"


def load_state(agent_dir: str | Path) -> dict[str, Any]:
    p = _path(Path(agent_dir))
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"events": []}


def mark(agent_dir: str | Path, stage: str, role: str, agent_id: str = "", method: str | None = None,
         outputs: list[str] | None = None, note: str = "") -> dict[str, Any]:
    """Record that ``stage`` is done. ``outputs`` are paths relative to the agent folder (the stage's required
    files are always included)."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}; stages: {', '.join(STAGES)}")
    agent_dir = Path(agent_dir)
    files = list(dict.fromkeys(OUTPUTS.get(stage, []) + (outputs or [])))
    missing = [f for f in files if not (agent_dir / f).is_file()]
    if missing:
        raise FileNotFoundError(f"Stage {stage!r} is not done: missing {', '.join(missing)}")
    state = load_state(agent_dir)
    event = {"stage": stage, "role": role, "agent_id": agent_id, "method": method, "note": note,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "outputs": {f: _sha(agent_dir / f) for f in files}}
    state["events"].append(event)
    _path(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    _path(agent_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return event


def check_state(agent_dir: str | Path, run_gates: bool = True) -> dict[str, Any]:
    agent_dir = Path(agent_dir)
    events = load_state(agent_dir)["events"]
    problems: list[str] = []
    done: list[str] = []
    for e in events:
        needs = NEEDS.get(e["stage"], [])
        alt = SHORTCUT.get(e["stage"])
        if any(n not in done for n in needs) and not (alt and all(n in done for n in alt)):
            problems.append(f"{e['stage']} ({e['at']}) was recorded before "
                            f"{', '.join(n for n in needs if n not in done)}")
        done.append(e["stage"])

    latest: dict[str, tuple[str, str]] = {}
    for e in events:
        for f, h in e["outputs"].items():
            latest[f] = (h, e["stage"])
    for f, (h, stage) in latest.items():
        p = agent_dir / f
        if not p.is_file():
            problems.append(f"{f} (from {stage}) no longer exists")
        elif _sha(p) != h:
            problems.append(f"{f} changed after {stage} recorded it: record the stage that changed it, "
                            f"or redo what follows")

    implementers = {e["agent_id"] for e in events if e["stage"] == "implement" and e["agent_id"]}
    for e in events:
        if e["stage"] == "verify" and e["agent_id"] and e["agent_id"] in implementers:
            problems.append(f"the verifier ({e['agent_id']}) also implemented a method: verification must be "
                            "independent")
    if "verify" in done and not any(e["stage"] == "verify" and e["agent_id"] for e in events):
        problems.append("the verify stage has no subagent id, so its independence can't be checked")

    gates: list[str] = []
    desc = agent_dir / "crate_description.json"
    if run_gates and desc.exists():
        from .verify import check_description

        errors = [f for f in check_description(agent_dir, desc).findings if f.level == "error"]
        gates.append(f"check-description: {'passes' if not errors else f'{len(errors)} error(s)'}")
        problems += [f"check-description: {f}" for f in errors]
        validated = [e for e in events if e["stage"] == "validate"]
        if "check" in done and validated and desc.stat().st_mtime > datetime.fromisoformat(validated[-1]["at"]).timestamp() + 1:
            problems.append("crate_description.json changed after the last validation: draft and validate again")

    nxt = next((s for s in STAGES if s not in done and not (s in {"implement", "assemble", "check", "validate",
                                                                  "verify"} and "deliver" in done)), None)
    return {"ok": not problems, "done": list(dict.fromkeys(done)), "next": nxt, "problems": problems,
            "gates": gates, "events": len(events)}


def describe(r: dict[str, Any]) -> str:
    lines = [f"Stages done: {', '.join(r['done']) or 'none'}", f"Next: {r['next'] or 'nothing, delivered'}"]
    lines += r["gates"]
    lines += [f"✗ {p}" for p in r["problems"]] or (["· nothing recorded yet"] if not r["events"]
                                                    else ["✓ order, outputs and independence check out"])
    return "\n".join(lines)
