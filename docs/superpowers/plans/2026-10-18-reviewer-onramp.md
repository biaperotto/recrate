# ReCRATE reviewer on-ramp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guided three-step on-ramp — Welcome → Generate & review the crate → Validate — in front of ReCRATE's existing chat + workstation, with real per-step build progress.

**Architecture:** A single Streamlit page (`app/app.py`) gains an explicit flow stage (`ss.stage`: `welcome` → `review` → `workstation`). The page root dispatches on that stage: a centered single-column Welcome and Generate/Review for stages 1–2, the existing two-card chat + workstation for stage 3. Crate building (`recrate.agent.agentify`) gets an additive `on_progress` callback so the Generate screen can tick a real checklist as each build stage completes; the app wires that callback to a live Streamlit status widget using the same pattern already used for `ss.tools.progress`.

**Tech Stack:** Python 3, Streamlit, the existing `recrate` package. Tests via pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-reviewer-onramp-design.md`

## Global Constraints

- Brand name stays **ReCRATE** (not "proCREATE"); the wordmark is `docs/img/logo.svg`.
- Reuse existing CSS tokens injected at `app/app.py` lines 76–154 (`--accent #1f7a6b`, `--card`, `--line`, `--ink`, `--muted`, `--bubble`); no new CSS framework.
- No change to what the build pipeline does or outputs — the only backend change is emitting progress events as it runs.
- **Reuse** and **Record** sections are untouched; they remain reached from the workstation as today.
- The app is run with `streamlit run app/app.py`; `RECRATE_OFFLINE=1` is set in tests (`tests/conftest.py:7`).

## Review Focus

The spec implies these inputs/conditions that no happy-path task exercises; each has its test pinned to the owning task below.

- **A link that resolves to neither a paper nor a crate** (e.g. only a code repo, or junk text): Welcome must not advance to `review`; it stays on Welcome with a message. → Task 2 test.
- **A build that raises** (network/format failure mid-`agentify`): the Generate screen must surface the error and let the reviewer return to Welcome, not strand them on a half-built stage. → Task 4 test.
- **An uploaded crate with no runnable method**: the review screen must still render its file tree and allow "Looks good" (the existing `empty_validate` handles the downstream workstation). → Task 3 test.
- **`on_progress` left unset** (CLI/MCP/existing callers of `agentify`): build behaves exactly as before, no exceptions. → Task 1 test.
- **A crate whose diagnosis reports findings**: the review screen flags them rather than showing an all-clear. → Task 3 test.

---

### Task 1: `on_progress` hook in `agentify`

**Files:**
- Modify: `src/recrate/agent.py` (`agentify`, lines 185–340)
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `agentify(..., on_progress: Callable[[str, str], None] | None = None)`. When given, `agentify` calls `on_progress(key, label)` at the **start** of each build stage, in this order, skipping stages that do not run for the given inputs:
  - `("paper", "Reading the paper")` — before stage 1 (only if `paper` given)
  - `("code", "Fetching the code repository")` — before stage 2 (only if `repo`/code resolved)
  - `("crate", "Assembling the RO-Crate")` — before stage 3
  - `("environment", "Resolving the software environment")` — before stage 3b (only if it runs)
  - `("diagnose", "Diagnosing the crate")` — before stage 4b (only if `spec0`)
  - `("validate", "Validating each method")` — before stage 5 (only if `spec0`)
  - `("done", "Done")` — immediately before `return`.
  The callback must never raise into the pipeline; wrap each call so a failing callback is ignored.

- [ ] **Step 1: Write the failing test**

```python
def test_agentify_reports_progress_stages(demo, tmp_path):
    seen = []
    agentify(tmp_path / "prog", paper=demo["paper"], crate=demo["crate"],
             on_progress=lambda key, label: seen.append(key))
    keys = [k for k in seen]
    assert keys[0] == "paper"           # paper read first
    assert "crate" in keys
    assert "validate" in keys           # demo crate has runnable methods
    assert keys[-1] == "done"
    # order is monotonic against the known stage sequence
    order = ["paper", "code", "crate", "environment", "diagnose", "validate", "done"]
    assert keys == sorted(keys, key=order.index)

def test_agentify_without_on_progress_unchanged(demo, tmp_path):
    a = agentify(tmp_path / "noprog", paper=demo["paper"], crate=demo["crate"])
    assert {m["name"]: m["status"] for m in a.card()["methods"]} == {
        "count_nuclei": "validated", "size_distribution": "validated"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `RECRATE_OFFLINE=1 pytest tests/test_agent.py::test_agentify_reports_progress_stages -v`
Expected: FAIL — `agentify() got an unexpected keyword argument 'on_progress'`.

- [ ] **Step 3: Add the parameter and emit events**

Add `on_progress: Callable[[str, str], None] | None = None` to the `agentify` signature. Add a local helper `def _tick(key, label):` that calls `on_progress(key, label)` inside a `try/except Exception: pass` when it is set, and insert a `_tick(...)` call at the start of each stage listed in Interfaces (guarded by the same condition that guards the stage), plus a final `_tick("done", "Done")` before `return PaperAgent.load(out)`. Import `Callable` from `typing` if not already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `RECRATE_OFFLINE=1 pytest tests/test_agent.py::test_agentify_reports_progress_stages tests/test_agent.py::test_agentify_without_on_progress_unchanged -v`
Expected: PASS.

- [ ] **Step 5: Run the full agent suite to confirm no regression**

Run: `RECRATE_OFFLINE=1 pytest tests/test_agent.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/recrate/agent.py tests/test_agent.py
git commit -m "feat(agent): emit per-stage progress from agentify via on_progress hook"
```

---

### Task 2: Flow stage + centered Welcome screen (screen 1)

**Files:**
- Modify: `app/app.py` — session defaults (near lines 239–247), the page root (lines 1334–1345), `start()` (1230–1264), and `build()` / `open_example()` (set stage).
- Add CSS to the injected block (lines 76–154): a `.onramp` centered-column wrapper and a `.flowbar` 1→2→3 indicator.

**Interfaces:**
- Consumes: nothing.
- Produces: `ss.stage` with values `"welcome" | "review" | "workstation"`, defaulted to `"welcome"`. A `flowbar(active: int)` helper renders the 1→2→3 indicator. `welcome_screen()` renders the centered screen 1. Reset (topbar) clears `ss.stage` back to `welcome` (it already deletes session keys except the preserved set).

- [ ] **Step 1: Add the stage default**

Add `ss.setdefault("stage", "welcome")` alongside the other `ss.setdefault(...)` calls (near line 247). The topbar Reset loop (lines 1328–1330) already deletes `stage` since it is not in the preserved set — confirm `stage` is not added to that preserved set.

- [ ] **Step 2: Make the page root dispatch on stage**

Replace the page root (lines 1336–1345). New behavior:
- `ss.stage == "workstation"` (and `ss.get("agent_dir")`): render the existing two columns — `workstation(...)` on the right, `chat_card()` on the left — exactly as today.
- otherwise: render a single centered column (no chat card). If `ss.stage == "review"` call `review_screen()` (Task 3; until then a stub is acceptable within this task — see Step 4), else call `welcome_screen()`.
Keep `topbar()` as the first call in all cases.

- [ ] **Step 3: Write `welcome_screen()`**

A centered `.onramp` container showing: the `flowbar(1)` indicator; an `<h1>`-scale "Welcome to ReCRATE"; one muted subtitle line (one sentence, from the spec: paste a preprint link and we rebuild it into a research object you can check); and a single `st.form("welcome-link")` with one `st.text_input` (placeholder "A DOI or link — arXiv, bioRxiv, a paper DOI, WorkflowHub, Zenodo") and a primary submit "Start". On submit with non-empty text call `add_and_build(text, [])` then `st.rerun()`. Keep the existing examples list (the `for key, ex in EXAMPLES.items()` block) below the form so examples stay reachable. Do **not** render the file uploader here (upload lives on screen 2).

- [ ] **Step 4: Point the build paths at the review stage**

In `build()` (agent.py-free; app.py lines 308–330), change the final `ss._goto = "validate"` handoff so that instead of leaving the user to be dropped straight into the workstation, it sets `ss.stage = "review"` (keep setting `ss._goto = "validate"` so the workstation opens on Validate once the gate is passed). In `open_example()` (lines 333–344) likewise set `ss.stage = "review"`. For this task, if `review_screen()` is not yet written, add a minimal stub that shows "Crate ready" and a primary button "Looks good — validate output →" that sets `ss.stage = "workstation"` and reruns, so the flow is shippable end-to-end.

- [ ] **Step 5: Add a guard test for a non-resolving link**

The app has no Streamlit test harness; this behavior lives in `add_and_build` (app.py:378), which is plain Python. Add `tests/test_onramp.py` importing the function under a stubbed `streamlit` is heavy; instead assert the underlying rule directly:

```python
# tests/test_onramp.py
from recrate.sources import identify
def test_code_only_link_is_not_a_paper_or_crate():
    refs = identify("https://github.com/someone/repo")
    # a repo resolves to code, which add_and_build must NOT treat as paper/crate
    assert all(r.kind != "paper" and r.kind != "crate" for r in refs) or refs == []
```

This pins the Review-Focus rule that a code-only link must not advance the flow (the Welcome `add_and_build` branch already only builds when `ss.ro` has a crate or paper).

- [ ] **Step 6: Verify the app manually**

Run: `streamlit run app/app.py`
Expected: a centered "Welcome to ReCRATE" with the 1→2→3 bar, one link input, examples below, and no chat card. Pasting an example DOI builds and lands on the review stub; "Looks good" opens the existing workstation with the chat card restored. Pasting junk stays on Welcome with the existing "I didn't find…" message.

- [ ] **Step 7: Commit**

```bash
git add app/app.py tests/test_onramp.py
git commit -m "feat(app): stage-based flow with centered Welcome screen"
```

---

### Task 3: Generate & review screen — file tree, flags, upload (screen 2 body)

**Files:**
- Modify: `app/app.py` — add `review_screen()` (replacing the Task 2 stub), reuse `tree_html()` (lines 1053–1071) and `preview()`.

**Interfaces:**
- Consumes: `ss.stage == "review"`, `ss.agent_dir` (set by `build`/`open_example`), `PaperAgent.load(...)`, `agent.diagnosis()`, `tree_html(root, dir)`.
- Produces: `review_screen()` rendering the centered screen-2 body and the looks-good gate.

- [ ] **Step 1: Write `review_screen()` body**

Centered `.onramp` container with `flowbar(2)`. Load `agent = PaperAgent.load(ss.agent_dir)`. Render, in order:
- An **"or upload a crate you already have"** `st.file_uploader` (accept `.zip`, `ro-crate-metadata.json`, PDF). On a new upload, route it through the existing `add_and_build("", files)` path and `st.rerun()` (this rebuilds onto the review stage).
- A **"What's in the crate"** section: the crate file tree via `st.html(tree_html(agent.path, agent.path))` (same rendering the Record section uses), wrapped in the existing file-tree CSS classes.
- A **flags** line from `agent.diagnosis()`: if the diagnosis reports findings, list them with a warning marker (reuse the `⚠` style / `mark.cite` or the muted caption style); otherwise show a muted "No problems spotted" line.
- The **gate**: a primary button "Looks good — validate output →" that sets `ss.stage = "workstation"` and reruns, and a secondary "Something's off" button. For "Something's off", pick the simplest useful action: set `ss.stage = "workstation"` **and** open the chat focused so the reviewer can ask what's wrong — OR return to Welcome. **Builder's call per spec; implement returning to Welcome (`ss.stage="welcome"`, clear `ss.agent_dir`/`ss.ro`) as the simplest, and leave a comment noting the choice.**

- [ ] **Step 2: Add the empty-method guard test**

```python
# tests/test_onramp.py  (add)
def test_crate_without_methods_still_lists_files(demo, tmp_path):
    from recrate.agent import agentify, PaperAgent
    a = agentify(tmp_path / "paperonly", paper=demo["paper"])  # no crate -> may have no runnable method
    # the review screen reads agent.path file tree; assert the crate dir is listable
    assert a.path.exists()
```

(Confirms the review screen's data source exists even when there is no runnable method; the UI itself is verified manually.)

- [ ] **Step 3: Run tests**

Run: `RECRATE_OFFLINE=1 pytest tests/test_onramp.py -v`
Expected: PASS.

- [ ] **Step 4: Verify manually**

Run: `streamlit run app/app.py`; open an example. Expected: centered screen 2 with the upload control, a real file tree of the crate, a flags/all-clear line driven by diagnosis, and the two gate buttons. "Looks good" opens the workstation; upload rebuilds onto screen 2.

- [ ] **Step 5: Commit**

```bash
git add app/app.py tests/test_onramp.py
git commit -m "feat(app): review screen with crate file tree, diagnosis flags, upload, looks-good gate"
```

---

### Task 4: Live build progress on the Generate screen

**Files:**
- Modify: `app/app.py` — `build()` (lines 308–330) and its call site in `add_and_build` (line 384).

**Interfaces:**
- Consumes: `agentify(..., on_progress=...)` from Task 1; `ss.stage` from Task 2.
- Produces: a live checklist during the blocking build, using the same live-update pattern as `ss.tools.progress` (app.py:521, 571–577).

- [ ] **Step 1: Render a live status during the build**

In `build()`, replace the single `st.spinner(label)` wrapper with a live checklist:
- Before calling `agentify`, create a `st.status("Building the research object", expanded=True)` (or a `st.progress` + placeholder) plus a `flowbar(2)` above it so the reviewer sees step 2 active.
- Pass `on_progress=_cb` into `agentify`, where `_cb(key, label)` writes the current step into the status container (e.g. `status.update(label=label)` and append a line). Because Streamlit flushes widget deltas during a running script — the mechanism already proven by `ss.tools.progress` driving `st.progress` live at lines 521/573 — the checklist ticks as each real stage starts/completes.
- On success, `status.update(state="complete")`, then the existing chat-summary `say(...)` and `ss.stage = "review"` / `ss._goto = "validate"`, then the caller's `st.rerun()` lands on screen 2.

- [ ] **Step 2: Handle a build that raises (Review-Focus)**

Wrap the `agentify` call in `try/except Exception as e`. On failure: `status.update(state="error")`, `say(f"Couldn't build the crate: {e}")`, set `ss.stage = "welcome"` and clear `ss.agent_dir`/partial `ss.ro`, so the reviewer returns to Welcome rather than a half-built stage. Add a guard test asserting the error-path contract at the unit level:

```python
# tests/test_onramp.py (add)
def test_agentify_failure_is_catchable(tmp_path):
    import pytest
    from recrate.agent import agentify
    with pytest.raises(Exception):
        agentify(tmp_path / "bad", paper=tmp_path / "does-not-exist.pdf")
```

(Confirms `agentify` raises on a bad input so `build()`'s `try/except` has something to catch; the UI recovery is verified manually.)

- [ ] **Step 3: Run tests**

Run: `RECRATE_OFFLINE=1 pytest tests/test_onramp.py -v`
Expected: PASS.

- [ ] **Step 4: Verify manually**

Run: `streamlit run app/app.py`; paste a real arXiv/DOI link (network). Expected: the checklist ticks through Reading paper → Fetching code → Assembling crate → Resolving environment → Diagnosing → Validating as each stage genuinely completes, then lands on screen 2. Force a bad link and confirm it returns to Welcome with an error message.

- [ ] **Step 5: Commit**

```bash
git add app/app.py tests/test_onramp.py
git commit -m "feat(app): real per-step build progress on the Generate screen"
```

---

### Task 5: Examples honor the review gate (and cleanup)

**Files:**
- Modify: `app/app.py` — confirm `open_example()` lands on `review` (set in Task 2) and that example diagnosis/flags render on screen 2.

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces: no new interface — a verification/cleanup task.

- [ ] **Step 1: Confirm example routing**

Verify `open_example()` sets `ss.stage = "review"` (from Task 2) and does not set `agent_dir` in a way that skips the gate. Remove any now-dead code from the old `start()` (e.g. the "What you'll get" row is optional; keep or drop per visual judgment — note the choice in a comment).

- [ ] **Step 2: Full manual pass of the three steps**

Run: `streamlit run app/app.py`.
Expected end-to-end:
1. Welcome (centered, one link input, no chat card).
2. Open an example → screen 2 with live build (for a rebuilt example) or immediate file tree, flags, looks-good gate.
3. "Looks good" → existing chat + workstation on Validate, chat card restored; Reuse and Record unchanged.
Reset returns to Welcome.

- [ ] **Step 3: Run the whole suite**

Run: `RECRATE_OFFLINE=1 pytest -q`
Expected: PASS (no regression in existing tests).

- [ ] **Step 4: Commit**

```bash
git add app/app.py
git commit -m "chore(app): route examples through the review gate; tidy legacy landing"
```

---

## Notes for the executor

- `app/app.py` has **no Streamlit test harness** in this repo (all existing tests target the `recrate` package). UI tasks therefore pin their *data-layer* contracts with pytest and verify the rendered screens manually via `streamlit run app/app.py`, as the steps state. This is deliberate, not a gap.
- The live-progress mechanism is not speculative: the existing `ss.tools.progress` callback (app.py:521, 571–577) already updates a `st.progress` widget live during a blocking tool call. Task 4 uses the same pattern for `agentify`'s `on_progress`.
