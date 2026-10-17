# ReCRATE reviewer on-ramp — design

**Date:** 2026-10-18
**Status:** Awaiting approval
**Scope:** Front-end flow change inside `app/app.py` (Streamlit), plus a small backend
change to report crate-build progress (see below).

## Intent and why

ReCRATE's users are **peer reviewers / domain experts** who come to check whether a
paper's reasoning and results actually hold up. Today they land on a dense two-card
screen (chat + workstation with Validate/Reuse/Record) that assumes they already know
what a research object is. We want a focused on-ramp that walks a reviewer through three
plain steps before the full workstation appears:

1. **Paste the preprint link.**
2. **Generate (or upload) the RO-Crate, and review its files.**
3. **Validate the output** — the existing chat + workstation.

The goal is that a reviewer who has never seen the tool is working within seconds and
understands, at each moment, what they are looking at and what to do next.

## Goals

- A centered **Welcome** screen: "Welcome to ReCRATE," one headline, one input for a
  preprint link (DOI / arXiv / link). This replaces today's `start()` landing view.
- A centered **Generate & review** screen that:
  - shows a **real live loading state** while the crate is built from the link — a
    progress bar + task checklist where each step ticks when it genuinely completes
    (requires a backend progress hook, see below);
  - offers an **upload** alternative ("or upload a crate you already have" — .zip /
    `ro-crate-metadata.json`);
  - shows the resulting **file tree** so the reviewer can see every file, with a **flag**
    on anything suspect (e.g. a method missing a result);
  - presents a **"Looks good — validate output →"** gate (plus a "Something's off" path).
- The **looks-good** action lands the reviewer in the **existing** chat + workstation on
  the **Validate** section (the flow already sets `ss._goto = "validate"`).
- Both new screens use ReCRATE's current visual language: warm off-white background,
  teal accent (`#1f7a6b`), existing CSS tokens and the `ReCRATE` wordmark. A 1→2→3
  progress indicator across the top of screens 1–2.

## Non-goals

- No change to **Reuse** and **Record** — they stay exactly as they are, reached from the
  workstation after validation as they are today.
- No change to the **chat + workstation** itself (screen 3) beyond being entered via the
  new gate.
- No backend/API changes **beyond** a progress-reporting hook threaded through the crate
  builder (`agentify` / the pipeline it drives) so screen 2 can show real per-step
  progress. Everything else reuses existing functions (`identify`, `fetch`/`from_upload`,
  `build`, the crate file listing, `agent.diagnosis()`). No change to what the pipeline
  actually does — only the emission of progress events as it runs.
- No new brand name — it stays **ReCRATE** (user confirmed "proCREATE" was a riff).
- No multi-page routing framework; stays a single Streamlit page driven by session state.

## Anti-goals (what would make this a failure even if it technically works)

- The reviewer reaching the workstation **without** having had a chance to eyeball the
  crate's files first — the review gate is the point of screen 2.
- Losing the ability to generate a crate **from a link** or to **upload** an existing one;
  both entry paths must survive.
- A redesign that looks bolted-on / off-brand relative to the existing teal/off-white UI.
- Regressing the existing examples, chat, or Validate/Reuse/Record behavior.

## Current state (as built)

- Single Streamlit page, `app/app.py` (~1345 lines). Layout chooses between `start()` and
  `workstation(agent)` based on `ss.get("agent_dir")` (see ~lines 1305, 1334–1345).
- `start()` (lines 1230–1264) is today's landing: "Recreate a piece of research," a form
  taking a DOI/link, a file uploader, examples, and a "What you'll get" row.
- `build(trusted)` (lines 308–330) runs `agentify(...)` inside `st.spinner(...)`, loads the
  agent, posts a chat summary, and sets `ss._goto = "validate"` — i.e. generation already
  flows straight into the workstation with no review pause.
- `identify()` / `fetch()` / `from_upload()` (`src/recrate/sources.py`) resolve links and
  uploads; the crate's files live under `agent.crate_dir` with `ro-crate-metadata.json`;
  `agent.diagnosis()` surfaces readiness/findings.

## The parts of the how that are decided

- **Welcome direction:** Option A — straight to the link, centered. One headline, one
  input, nothing else. (Chosen over the "set the stage with 3 steps" variant.)
- **Screen 2 is a real stop in the flow**, not a pass-through. Generation no longer flows
  silently into the workstation; it pauses on the review screen until the reviewer clicks
  the looks-good gate. (This is the one behavioral change to `build()`'s current
  straight-to-validate path.)
- **Upload lives on screen 2**, alongside generate, as the "or upload a crate you already
  have" branch — not on the welcome screen.
- **State:** introduce a small flow-state marker (e.g. an `ss` stage flag) distinguishing
  Welcome → Generate/Review → Workstation, so the review screen can render between a crate
  being built and `agent_dir` driving the workstation. Exact variable naming left to the
  builder; it should sit alongside the existing `ss.agent_dir` / `ss.ro` / `ss._goto`
  conventions rather than replacing them.
- **Real build progress (backend hook):** `agentify` (and the pipeline steps it runs —
  fetch paper, resolve code repo, extract methods/parameters, package metadata, validate)
  must emit progress events as each step completes. The front end subscribes and ticks the
  checklist on screen 2 against real progress. The mechanism (a callback parameter, a
  shared progress object like the existing `ss.tools.progress` used by `ChatTools`, or a
  small event stream) is the builder's call, but it must surface genuine step completion,
  not a timer. The pipeline's behavior and outputs are unchanged.
- **Examples honor the review gate:** `open_example()` routes through screen 2 like the
  link and upload paths, rather than jumping straight to the workstation, so a reviewer
  always sees the crate's files before validating. (Settled call.)
- **Chat card hidden on screens 1–2:** the currently-unconditional two-column page root
  becomes stage-conditional — a single centered column on Welcome and Generate/Review,
  with the left chat card restored only at the workstation (screen 3). The top-bar
  Connect/engine control may stay visible; the chat-driven build path is simply not the
  entry on stages 1–2 (the new link input is). (Settled call.)
- **Styling:** reuse the existing injected CSS tokens (`--accent`, `--card`, `--line`,
  `--ink`, `--muted`, etc.) and wordmark; add a centered-column wrapper and a 1→2→3
  progress bar. No new CSS framework.

## Left to the builder

- Exact session-state variable(s) and how the layout dispatcher chooses among the three
  stages.
- Where to split `build()` so generation and the review pause are separable (e.g. build →
  land on review screen → confirm → enter workstation), while preserving the chat summary
  message and `ss._goto = "validate"` on entry.
- How to render the file tree on screen 2 (reuse the existing file-tree rendering used in
  the Record section if practical) and which conditions raise a flag (start from
  `agent.diagnosis()` findings; a method missing a result is one example).
- Whether the "Something's off" path returns to Welcome, reveals diagnosis detail, or opens
  chat — pick the simplest that lets the reviewer act, and note the choice.
- Where the examples are offered as an entry point (Welcome screen vs. elsewhere), given
  that they now route through screen 2's gate once chosen.
- The exact progress-hook mechanism (callback vs. shared progress object vs. event stream)
  and the granularity of the step list surfaced to the reviewer.

## Verification

- `streamlit run app/app.py` launches; Welcome shows centered with one input.
- Pasting a known arXiv/DOI link shows real per-step progress ticking as the build runs,
  then lands on screen 2 with a file tree and the looks-good gate (not straight into the
  workstation).
- Uploading a crate `.zip` / `ro-crate-metadata.json` lands on the same review screen.
- Clicking looks-good enters the existing chat + workstation on Validate; Reuse and Record
  are unchanged.
- An existing example opens onto screen 2's review gate, then end-to-end into the
  workstation.
- The chat card is absent on Welcome/Generate and present at the workstation.
