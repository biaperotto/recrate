---
name: recrate
description: Recreate a piece of published research as a validated ReCRATE research object. From a paper (PDF, DOI, arXiv link) or an RO-Crate, coordinate specialist subagents that read the paper and its code, describe each method so it can run, run it on the published data, compare with the published results, and independently verify every claim; deliver an RO-Crate, a report and an MCP agent. Use when asked to agentify a paper, make a paper's methods runnable, or reproduce or validate a paper with ReCRATE.
argument-hint: <paper PDF, DOI, arXiv link, RO-Crate, or ReCRATE agent folder> [quick]
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
* **The authors' code, untouched, in its own environment.** Wrap it, don't rewrite it; it runs in
  `<agent>/env` (`recrate env`), never in ReCRATE's. Re-implement only when no code exists, and say so.
* **Within reach.** If a method needs a GPU, a cluster, data larger than ~2 GB, or access that must be requested,
  describe what it needs and mark it not runnable here, rather than pretending.
* **Drafts stay drafts** (`"draft": true`) until a person confirms them.
* **Every stage is recorded.** After each stage, `recrate mark <agent> <stage> --role <role> [--id <subagent id>]`
  writes it to `work/state.json` with the checksums of what it produced. Pass each subagent's id (from its
  result), so the verifier's independence can be checked.

**Resuming.** If `<agent>/work/state.json` exists, run `recrate workflow-check <agent>` first: it lists the stages
done and the next one, and flags a file changed since it was recorded. Continue from the next stage; redo a stage
whose outputs changed.

## Quick mode

If `$ARGUMENTS` ends with the word `quick`, keep the whole run short (about 10 to 20 minutes), and say so in every
specialist's prompt by adding this paragraph to it:

> **Quick mode.** Work only on the methods named in the plan (at most 3). Fetch only the data those methods need, and
> nothing over 500 MB. Build the environment and check that the code's main modules load; do not run or render every
> script, notebook or report, only those the planned methods use. Do not survey tutorials.

In quick mode: plan at most **3 methods**, the ones the paper's main results (abstract, main tables) rest on; skip
tutorial-derived tools; allow **one** repair round per method. Everything else works as below (quotes, published
values, tolerances with sources, an independent verifier, the deliver checks). In `REPORT.md`, list what quick mode
left out, so a full run (`/recrate <agent>` without `quick`) can add it.

## 0. Set up

1. Check ReCRATE: `recrate --version`. If missing: `pip install -e .` in the ReCRATE repository.
2. If `$ARGUMENTS` is already a ReCRATE agent folder (it has `agent.json`), use it. Otherwise create one:
   * a paper: `recrate agentify --paper <pdf> --fetch-code --out agents/<short-name>` (for a DOI or an arXiv
     link, download the PDF first, or open the app and paste the link);
   * an RO-Crate: `recrate agentify --crate <crate> --out agents/<short-name>`.
   For a paper, `agentify` first looks for an RO-Crate already published for it (WorkflowHub, Zenodo, DataCite,
   links in the paper, its code repository; pass `--doi <the paper's DOI>` when you know it) and uses it if found;
   `agent.json` → `crate_search` says what was searched and found. Otherwise it recreates the crate as of
   publication (date, software, code, cited data). Either way the paper's text is in `<agent>/paper/paper.md`.
   If the specialists later find a published crate the search missed (e.g. in a repository the paper doesn't
   link), rebuild with `--crate` pointing at it.
3. Make `<agent>/work/`. Read `<agent>/agent.json` and `<agent>/environment.json`. If `<agent>/paper/index.md`
   exists, the paper has a reading package (tables as CSV, figures, supplement): point the specialists at it.
4. `recrate mark <agent> setup --role coordinator`.

## 1. Read and survey, in parallel

Launch these two specialists **in the same message**, as general-purpose subagents. Each prompt is the file
named below (in this skill's `agents/` folder), with `{AGENT}` replaced by the agent folder's absolute path.

* **paper reader** (`agents/paper-reader.md`) → `work/methods.json`: each method, its parameters with quotes,
  the data it was applied to, and the published results with where they are printed.
* **code and environment** (`agents/environment.md`) → `work/environment-review.json`: the code (cloned if
  linked), its entry points and dependencies, the data (where, size, licence, access), and compute needs.

When both are done, `recrate mark <agent> read --role paper-reader --id <id>` and
`recrate mark <agent> survey --role environment --id <id>`, then read their outputs. Plan the tools: every
method the paper reports results for (validated against those results), **plus** the code's tutorials and examples
that a user would want as a tool (as Paper2Agent extracts its tools; validated against the tutorial's recorded
outputs). Skip tutorials that only repeat a paper method. Decide, per method,
**runnable here** or **not runnable here**: the environment specialist checked each method's needs against
`recrate hardware`; a method whose needs don't fit is not runnable here, with exactly what is missing. Tell the
user the plan in a few lines, then `recrate mark <agent> plan --role coordinator --note "<runnable methods>"`.
If none is runnable, go to 5 (deliver).

## 2. Implement, in parallel

For each runnable method, launch a **method implementer** (`agents/method-implementer.md`, with `{AGENT}` and
`{METHOD}` replaced), all in one message. Each writes its workflow under `<agent>/workflows/<method>/`, the
published results under `<agent>/expected/<method>.csv`, and its part of the description in
`work/method-<method>.json`, and runs its workflow once on the published data. Workflows may be Python, R or
shell scripts. For each, `recrate mark <agent> implement --role method-implementer --id <id> --method <method>
--output work/method-<method>.json`.

## 3. Assemble and check

1. Merge the parts into `<agent>/crate_description.json` (format: `python -c "import recrate.draft as d; print(d.__doc__)"`):
   the paper's title, DOI, licence, authors and `datePublished` at the top, `"draft": true`,
   `"paper_file": "paper/paper.md"`, and the methods. Paths are relative to the agent folder.
   `recrate mark <agent> assemble --role coordinator`.
2. `recrate check-description <agent> <agent>/crate_description.json`. Fix every error (a quote that isn't in
   the paper, a published value that isn't printed in it, a missing file) by going back to the source, never by
   deleting the check.
   When it passes: `recrate mark <agent> check --role coordinator`.
3. `recrate draft <agent>/crate_description.json <agent>/crate` then `recrate validate <agent>`, then
   `recrate mark <agent> validate --role coordinator`.

## 4. Verify, independently

Launch a fresh **verifier** (`agents/verifier.md`), not one of the implementers. It re-derives every parameter
and published value from the paper and code and reviews each run, writing `work/verification.json`. Apply its
corrections, then check, draft and validate again (marking each stage again). At most 3 repair rounds per
method; after that, keep the method with its status and the reason. Then
`recrate mark <agent> verify --role verifier --id <the verifier's id>`: it must not be an implementer's id.

## 5. Deliver

Write `<agent>/REPORT.md` for the user: per method, its status (reproduced, differs with exactly what, or not
runnable here with what it needs), what the paper doesn't state, and what a person must confirm before the draft
flag is removed. Then, before telling the user anything is done:

1. `recrate mark <agent> deliver --role coordinator`, then `recrate workflow-check <agent>`. Fix every problem it
   names (a stage out of order, a file changed after it was checked, a verifier who was an implementer).
2. `recrate verify-server <agent> --reproduce`: launches the agent's MCP server over stdio, checks its tools and
   calls them, including `reproduce` for each method. It must pass.
3. `recrate package <agent>`: a zip with a `USAGE.md`, checked again after extracting it to a fresh folder.

Then tell the user, briefly:

* the verdicts;
* the package (`<agent>.zip`), to share or deposit;
* to open the research object in the ReCRATE app (or press *Reload*), or to talk to it here:
  `recrate install-mcp <agent> --client project` (or `--client desktop`), which registers it with absolute
  paths and checks that it starts.
