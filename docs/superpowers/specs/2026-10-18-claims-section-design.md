# Claims: every checkable claim a paper makes, checked

**Status:** design, awaiting approval
**Date:** 2026-10-18

## Intent

Today reCRATE answers "did the methods in this crate run and reproduce?" It cannot answer the
question a reader or reviewer actually asks: **"the paper says 68 papers shared their data —
is that true of the data they shared?"**

Claims answers that, claim by claim, for any paper. Each checkable claim becomes a row: what
the paper says, what the shared data gives, whether those agree, and the exact file, columns
and computation behind the answer — pinned so anyone can re-run it.

Claims cover **numbers and figures**. A figure is a claim too: the paper shows a plot, and the
question is whether running the authors' own code on the shared data produces that plot.

### Claims is not a new section. It is what Reproduce becomes.

Verifying a claim *is* reproducing: you run something, then you compare. Two sections would
produce **two competing verdicts** — the card's headline is computed today from method ladders
(`verdict_sentence` over `agent.methods`; the tab reads `Reproduce · n_ok/len(methods)`), and a
second claims tally beside it would leave a reader with "Reproduced 1/1" next to "14/18 claims
reproduced" and nothing saying which is the answer. A reproducibility tool that gives two
answers to "did this hold up?" has failed at its one job.

They also already share machinery. The verdict ladder is `runs → numbers → conclusion(person)`.
A claim check is exactly that: did the computation run, do the numbers match at the paper's
precision, does a person accept the conclusion. Figures use the same rungs — the run is the
Docker render, the person call is the match.

So claims become the rows of the Reproduce section, and the section's verdict is computed over
claims. A runnable workflow is the *mechanism* that produces outputs; claims are what get
verified against the paper.

### Why now

The repo can already fetch a paper, find its data, build an RO-Crate and run workflows. What's
missing is the layer tying a *sentence in the paper* to a *number in a spreadsheet*. Without it,
"reproducible" stays a property of code rather than of the paper's assertions.

## Goals

- For any paper, list every checkable quantitative claim with a verdict that is never blank.
- Never mark a claim reproduced without having run the computation.
- Never hide a mismatch: show both values and how the number was produced.
- Make the resulting RO-Crate self-sufficient — enough on its own to re-run every check.
- Reproduce the paper's **figures** by running the authors' own method, pinned, in Docker.
- One headline verdict for the paper, computed over claims.
- Keep the existing suite green, and keep tests offline.

## Non-goals

- Judging whether a reproduced figure *matches* the paper. A person decides that.
- Re-implementing the authors' analysis in Python when their own code exists.
- Parsing numbers out of rendered R output (see "What the authors' method yields").
- Checking qualitative claims, or claims with no shared data. Those are `Not checkable` with a
  reason, never silently dropped.

## Anti-goals

Things that would make this a failure even if it technically worked:

- A row reading `Reproduced` because a computation was skipped, defaulted or guessed.
- A claim-to-data mapping invented by the model when the data dictionary doesn't support it.
- A reported value the model hallucinated flowing through to a green verdict.
- Pixel-diffing figures and reporting a similarity score as if it were a verdict.
- Numbers typed into the UI. The UI renders `claims.json` and nothing else.
- A crate that references a data file it does not contain.
- Two verdicts for one paper.

## Constraints

- **Offline tests.** The whole suite runs with no network. Docker-backed checks are opt-in and
  never run in the offline suite.
- **Licence.** The demo data is CC BY-NC-SA 4.0. Every bundled file records its licence and
  credits the authors.
- **RO-Crate 1.1.** Terms outside the 1.1 context (`sha256`, `creditText`, …) go in a context
  extension mapped to their schema.org IRI.
- **Generality.** Nothing about Jones et al. may be hard-coded in the product. The demo's
  expected values live only in the test fixture.
- **Model discipline.** The repo's rule stands: the model never sets a verdict, a tolerance or a
  published value without a non-model check behind it (`verify.check_description` is the
  precedent). See "The verification gate".

## The claim

| Column | What it shows |
|---|---|
| Claim | One short sentence in plain words, written by reCRATE, not copied from the paper |
| Where in the paper | Section name plus a text-fragment link `<paper url>#:~:text=<anchor>`, built from words occurring verbatim in the fetched full text; falls back to the section link if no anchor matches |
| Paper says | The value as the paper reports it, with its rounding and CI format |
| Data gives | The value reCRATE computed, formatted the same way |
| Result | `Reproduced` \| `Mismatch` \| `Not checked` \| `Not checkable` — never blank |
| Data source | Link to the exact file pinned to a commit or record version, plus sheet, columns and filter |
| Why not checked | One line, only for `Not checked` / `Not checkable` |

Expanding a row shows the computation that ran — the generated script, the columns, filter,
aggregation and tolerance — and, for figures, the two images side by side.

## Two execution modes

**Independent recomputation (numbers).** Descriptive quantities — counts, percentages,
proportions with CIs, cross-tabulations — are recomputed directly from the shared data. This is
a *stronger* check than re-running the authors' script, because it tests the number rather than
the code that produced it, and it is what `check_claims_jones2026.py` does. Comparison semantics
come straight from it: compare **as formatted strings at the paper's reported precision**. Counts
match exactly; a rounded percentage or CI matches when the recomputed value rounds to the
reported one, using the paper's own CI method when it names one (Wilson, here). The repo's
existing numeric tolerance applies only to continuous values.

**The authors' own method (figures).** A figure is reproduced by running the authors' code,
pinned, in Docker — never by redrawing the plot in Python, which would be a different method.

> *My call:* applying independent recomputation to simple descriptive numbers is my reading of
> "use the same method as the paper", which was said about figures. It matches the user's own
> reference script. If they want the authors' code behind every number, these two modes collapse
> into one.

### How a computation is expressed and run

**Generated Python per claim, not a declarative spec interpreted by reCRATE.** The 18 reference
checks are not expressible in any small vocabulary — they include a Wilson CI, `groupby(paper_ID)
.any()` then a count of papers, `nunique()` over a filtered frame, `value_counts()` over three
named categories, and composite formatted outputs (`"14/32 (44%)"`, `"30 / 9 of 11"`,
`"1 / 40 / 56"`). An interpreter over a closed op set would mark most real claims `Not checkable`.

So reCRATE emits a check script — the shape of `check_claims_jones2026.py` — and runs it with
`python -I`, behind the repo's existing gate that downloaded or generated code does not run
without the person's say-so. The declarative fields (columns, filter, aggregation) are recorded
in `claims.json` for display and provenance; they are not the execution mechanism.

**Self-sufficiency bar:** the crate re-runs with `python check.py .` and pandas + openpyxl
alone. Installing reCRATE is never required to verify a crate.

### What the authors' method yields

Figures, and machine facts about the run — nothing else. Lifting `0.34 (0.07, 0.69)` out of
rendered R output generically is its own research project, and hard-coding it to Jones is barred.

Consequence for the Bayesian claim: it stays **`Not checked`**, reason *"needs a Docker run with
brms/Stan; reCRATE does not parse numbers out of rendered R output"*. What the opt-in Docker run
actually delivers is the regenerated **MCMC diagnostic figure** (`mcmc_combo(...)` → `ggsave`),
judged by a person like any other figure. That keeps the opt-in check meaningful without
inventing an output parser.

## Figures

**The paper's `.qmd` is the method.** This needs saying because reCRATE's own drafted crate for
this paper currently has zero methods — that is a gap in our drafting step, not a property of
the paper. The authors' two Quarto documents contain 17+ R chunks that compute the paper's
numbers and draw its figures.

**Figures are generated, then embedded.** The pattern is `ggplot(...)` → `ggsave("figures/x.png")`
→ `knitr::include_graphics('figures/x.png')`. Running the code regenerates the PNG. Confirmed in
the demo repo: `swimmerB` (estimates per paper), `p1` (the simulated example), `p2` (residuals),
and the `mcmc_combo` diagnostic. The flowcharts (`myflow.png`, `flowinferential.png`) appear only
as includes with no generating code — they are hand-drawn, so they are `Not checkable` with that
reason, and the N counts they display get checked numerically instead.

**Run the figure's chunks, not the whole document.** A full render also fits the brms model
(hours of Stan) and may pull the repo's 297 MB `Papers/` corpus. reCRATE runs the minimal chunk
set a figure depends on.

**The verdict is a person's.** reCRATE shows the paper's figure beside the regenerated one and
records the researcher's decision — who, when, a note — reusing `review_conclusion`. No
pixel-diff, no similarity threshold.

reCRATE still states the machine facts, which are not matters of opinion: did the run succeed,
did it emit the expected file. **A figure that fails to generate is a failure**, reported without
anyone's judgement. A figure that generated but is unreviewed is `Not checked`, reason "awaiting
review". Only "does this match the paper" is the human's. Numbers are never sent for human review.

> *My call:* a figure run produces a Workflow Run Crate like any other run, so
> `runcrate.review_conclusion`'s `AssessAction` applies unchanged and the verdict feeds the same
> ladder and headline sentence. No second review store.

## The verification gate

Claims let a model set the "Paper says" value and the data mapping, which the repo's existing
discipline forbids doing unchecked. The precedent is `verify.check_description`: Claude drafts,
then a **non-model** pass re-checks quotes word-for-word against the paper and every published
value against the text, and the crate stays `draft: true` until a person confirms.

The same gate applies here, and it runs before any claim can reach a verdict:

- the anchor string must occur verbatim in the fetched full text;
- the "Paper says" value must occur in the paper text;
- the mapped file, sheet and columns must exist in the pinned data.

A claim failing any of these is `Not checkable` with that reason. It never reaches `Reproduced`.
Model output does not flow straight to a green verdict.

> *My call:* extraction runs through the `drafter.py` path (library call, `ANTHROPIC_API_KEY`)
> and is exposed as its own CLI command. It is not forced into every `agentify` run.

## Pipeline

1. **Read the paper.** Full text through the existing fetcher, preferring JATS/HTML over PDF.
   Record the version (e.g. medRxiv v2).
2. **Extract claims.** Every quantitative claim: counts, percentages, CIs, effect sizes, test
   results. Store section, a verbatim anchor, the reported value and its precision. Anchors, not
   long passages.
3. **Find the data.** Follow the data availability statement to the repo or archive and resolve
   it to a fixed version — a commit SHA for git, a record version for Zenodo. Download; record
   each file's sha256, size and upstream URL.
4. **Map each claim to data**, using the data dictionaries. Record file, sheet, columns, filter,
   aggregation. **No mapping means `Not checkable` with a reason** — never a silent guess.
5. **Verification gate** (above).
6. **Compute and compare**, per the two modes.
7. **Build the RO-Crate in the same run.**
8. **Self-sufficiency check.**

### Prerequisite: the fetcher

Folded into milestone 1, because anchors are meaningless without it. Today this paper yields
`paper_text: "PDF text"` and a corrupt DOI — `https://doi.org/10.64898/2026.04.07.26350296doi:`,
a trailing `doi:` swallowed by the DOI regex. Anchors validated against PDF-derived text that
does not match the medRxiv HTML page make the acceptance assertion self-referential: it would
pass while the links fail to highlight. So: fetch medRxiv HTML full text, and fix the DOI capture.

## The RO-Crate

Enough on its own to re-run every check:

- the paper as `ScholarlyArticle` (DOI, version, authors with ORCIDs where available);
- every data file a claim uses, as a `File` with `sha256`, `encodingFormat`, `license`, and
  `isBasedOn` pointing at the pinned upstream URL;
- each claim as a schema.org `Claim` with `text`, `appearance` (the paper), `isBasedOn` (its data
  file), and its result in `creativeWorkStatus`;
- the check script as `SoftwareSourceCode`, its output, and a `CreateAction` (Process Run Crate
  profile) with `instrument` = script, `object` = data files, `result` = output, start/end times
  and `actionStatus`;
- the authors' own code and software versions (from session info, lockfiles, environment files)
  as linked entities, even where reCRATE cannot run them;
- for figure claims: the Docker image reference and the file it was derived from, the generated
  image, the authors' committed reference image where one exists, and the human verdict.

**Self-sufficiency check.** Fail the run if any claim's `isBasedOn` file is missing from the
crate, any `hasPart` file is missing on disk, or any file in the crate directory is not listed.
Reported as its own line in the UI, not folded into the claim rows.

> *My calls:* this is a third crate kind in the agent folder, alongside the input `crate/` and
> the run crates in `recrate/`; it does not replace `paper_crate.py`'s output. It must pass
> `rocrate-validator` in CI exactly as the run crates do (`tests/test_rocrate_validator.py`). The
> self-sufficiency rule is scoped to the claims crate directory, so the agent folder's `paper/`,
> `code/` and `environment.json` are not in its scope. `build_crate.py` is absorbed into the
> product and then deleted; `check_claims_jones2026.py` stays as a reference until milestone 2's
> fixture reproduces its 18 results, then goes too.

### Docker

> *My call:* the image is built from a Dockerfile generated from the paper's session info —
> `rocker/r-ver:4.4.2` plus a dated Posit Package Manager snapshot to pin package versions, plus
> Quarto. For the demo that means brms 2.22.0, ggplot2 4.0.1, dplyr 1.1.4, lme4 1.1-37,
> readxl 1.4.5, tidyverse 2.0.0, DiagrammeR, forestplot, bayesplot, posterior. The generated
> Dockerfile and the resulting image digest are recorded in the crate.

## Outputs

- `claims.json` — one object per claim with every field above, plus `id`, `computation`, `tolerance`.
- the RO-Crate directory and a zip of it.
- `claim-check-output.txt` — one line per claim: PASS / FAIL / NOT CHECKED, paper value vs data value.

The UI reads `claims.json`. No numbers are typed into the UI.

## UI

Claims become the body of the **Reproduce** section: the rows, numeric and figure together, with
the columns above, anchors as links, pinned data links, and expandable computation. Figure rows
show both images side by side with the review control. The section's headline verdict is computed
over claims; a method run contributes as the mechanism behind the claims it produces, not as a
separate tally.

The self-sufficiency result is its own line. `Appendix` keeps the paper's source, environment and
published-results tables, as it does today.

## Acceptance

Jones L, Barnett A, Hartel G, Vagenas D, *An Empirical Assessment of Inferential Reproducibility
of Linear Regression in Health and Biomedical Research Papers*, medRxiv
`10.64898/2026.04.07.26350296` v2. Data and code: `github.com/Lee-V-Jones/Reproducibility`, pinned
at `370e615`.

The 18 expected numeric values are the user's, reproduced by `check_claims_jones2026.py`. They
live in the fixture only, never in product code: `Reproducibility_wide` (availability,
computational and inferential counts, Wilson CI `21% (8%, 48%)`), `Reproducibility_long`
(model-level reproducibility, assumption violations, best-fit distribution), `compare_data_long`
(coefficient ranges, three change thresholds, significance shifts). The Bayesian row stays
`Not checked`.

Also assert: every row's anchor occurs in the fetched full text; every data source link carries
the pinned commit; the crate passes the self-sufficiency check and `rocrate-validator`; every
bundled file's sha256 matches.

> *My calls:* the four `.xlsx` (≈115 KB) are bundled into `tests/fixtures/` with licence and
> credit recorded per file, which keeps the suite offline. Extraction is covered offline by
> replaying a recorded model response as a golden file, so milestone 1 has a real exit criterion.
> Milestone 4 is done only when a Jones figure **actually regenerates** on a developer machine
> at least once — machinery never seen working does not count — while the offline suite never
> runs Docker. One second paper from `agents/` serves as a generality smoke test (extraction runs,
> verdicts are sane); there is no recall standard, so a missed claim is not a bug at v1.

## Rules

- Works for any paper. Demo values live only in the fixture.
- Never `Reproduced` without running the computation. If it can't be checked, say why.
- Never hide a mismatch: show both values and the computation.
- Respect the data licence; record it per file and credit the authors.
- Keep all existing tests passing; tests run offline.
- If an expected value looks wrong, stop and ask rather than change the fixture.

## Milestones

One PR each.

1. Fetcher prerequisite (medRxiv HTML full text, DOI capture fix); `claims.json` schema; claim
   extraction with anchors; the verification gate; data location and commit pinning.
2. Claim-to-data mapping, generated check script, computation and comparison; demo fixture
   reproduces the 18 results.
3. RO-Crate built in the same run, with the self-sufficiency check and validator conformance.
4. Docker execution of the authors' method: figure regeneration, the opt-in MCMC diagnostic.
5. Claims as the body of the Reproduce section, with side-by-side figures and the human verdict;
   one headline verdict computed over claims.

## Decided by the user

- A Claims capability covering numbers **and** figures.
- Claims belong **inside Reproduce** — verifying a claim is reproducing then verifying.
- Figures reproduced by running the authors' own method, in Docker, at the provided versions.
- The researcher decides whether a figure reproduced; the UI carries that.
- reCRATE still states machine facts; a figure that fails to generate is a failure.
- No human eyeball on numeric claims — those keep the automatic verdict.
- The Bayesian claim is an opt-in slow check, `Not checked` in the offline fixture.
- The paper's `.qmd` chunks are methods; the empty method list is reCRATE's drafting gap.
- Everything in the original specification: columns, pipeline, crate contents, outputs, rules
  and acceptance values.

## Left to the builder

- The `claims.json` schema in detail.
- The extraction prompt, and how anchors are chosen so they survive into an HTML page.
- How `Not checkable` reasons are worded.
- Which minimal chunk set a given figure depends on, and how that is determined from the `.qmd`.
