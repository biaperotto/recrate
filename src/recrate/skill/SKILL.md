---
name: recrate
description: Recreate a piece of published research as a validated ReCRATE research object. From a paper (PDF, DOI, arXiv link) or an RO-Crate, coordinate specialist subagents that read the paper and its code, describe each method so it can run, run it on the published data, compare with the published results, and independently verify every claim; deliver an RO-Crate, a report and an MCP agent. Use when asked to agentify a paper, make a paper's methods runnable, or reproduce or validate a paper with ReCRATE.
argument-hint: <paper PDF, DOI, arXiv link, RO-Crate, or ReCRATE agent folder>
---

# Recreate a piece of research with ReCRATE

You coordinate. Specialists do the reading, implementing and checking, in their own contexts, so that you keep
the overview and each claim is checked by someone who did not make it. Input: `$ARGUMENTS`.

The rules every agent follows, and that you enforce:

* **Nothing invented.** A parameter, a value or a published result enters the crate only with the sentence,
  table or file of the paper or its code that states it. What the paper doesn't state is reported as missing.
* **The published result is the test.** A method is done when `recrate validate` reproduces the published
  numbers, or names exactly what differs. Never loosen a tolerance to make a method pass; a tolerance follows
  how the paper reports (e.g. ±half a unit of its rounding).
* **The authors' code, untouched.** Wrap it, don't rewrite it. Re-implement only when no code exists, and say so.
* **Within reach.** If a method needs a GPU, a cluster, data larger than ~2 GB, or access that must be requested,
  describe what it needs and mark it not runnable here, rather than pretending.
* **Drafts stay drafts** (`"draft": true`) until a person confirms them.

## 0. Set up

1. Check ReCRATE: `recrate --version`. If missing: `pip install -e .` in the ReCRATE repository.
2. If `$ARGUMENTS` is already a ReCRATE agent folder (it has `agent.json`), use it. Otherwise create one:
   * a paper: `recrate agentify --paper <pdf> --fetch-code --out agents/<short-name>` (for a DOI or an arXiv
     link, download the PDF first, or open the app and paste the link);
   * an RO-Crate: `recrate agentify --crate <crate> --out agents/<short-name>`.
   This recreates the crate as of publication (date, software, code, cited data) and gives the paper's text in
   `<agent>/paper/paper.md`.
3. Make `<agent>/work/`. Read `<agent>/agent.json` and `<agent>/environment.json`.

## 1. Read and survey, in parallel

Launch these two specialists **in the same message**, as general-purpose subagents. Each prompt is the file
named below (in this skill's `agents/` folder), with `{AGENT}` replaced by the agent folder's absolute path.

* **paper reader** (`agents/paper-reader.md`) → `work/methods.json`: each method, its parameters with quotes,
  the data it was applied to, and the published results with where they are printed.
* **code and environment** (`agents/environment.md`) → `work/environment-review.json`: the code (cloned if
  linked), its entry points and dependencies, the data (where, size, licence, access), and compute needs.

When both are done, read their outputs. Decide, per method, **runnable here** or **not runnable here** (with the
reason). Tell the user the plan in a few lines before going on.

## 2. Implement, in parallel

For each runnable method, launch a **method implementer** (`agents/method-implementer.md`, with `{AGENT}` and
`{METHOD}` replaced), all in one message. Each writes its workflow under `<agent>/workflows/<method>/`, the
published results under `<agent>/expected/<method>.csv`, and its part of the description in
`work/method-<method>.json`, and runs its workflow once on the published data.

## 3. Assemble and check

1. Merge the parts into `<agent>/crate_description.json` (format: `python -c "import recrate.draft as d; print(d.__doc__)"`):
   the paper's title, DOI, licence, authors and `datePublished` at the top, `"draft": true`,
   `"paper_file": "paper/paper.md"`, and the methods. Paths are relative to the agent folder.
2. `recrate check-description <agent> <agent>/crate_description.json`. Fix every error (a quote that isn't in
   the paper, a published value that isn't printed in it, a missing file) by going back to the source, never by
   deleting the check.
3. `recrate draft <agent>/crate_description.json <agent>/crate` then `recrate validate <agent>`.

## 4. Verify, independently

Launch a fresh **verifier** (`agents/verifier.md`), not one of the implementers. It re-derives every parameter
and published value from the paper and code and reviews each run, writing `work/verification.json`. Apply its
corrections, then check, draft and validate again. At most 3 repair rounds per method; after that, keep the
method with its status and the reason.

## 5. Deliver

Write `<agent>/REPORT.md` for the user: per method, its status (reproduced, differs with exactly what, or not
runnable here with what it needs), what the paper doesn't state, and what a person must confirm before the draft
flag is removed. Then tell the user, briefly:

* the verdicts;
* to open the research object in the ReCRATE app (or press *Reload*), or to talk to it here:
  `claude mcp add <name> -- recrate serve <agent>` (absolute paths).
