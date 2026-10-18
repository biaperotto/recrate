# Claims Milestone 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a paper into a verified `claims.json` — every quantitative claim extracted with a
verbatim anchor, checked by a non-model gate, and pointed at data pinned to an exact commit.

**Architecture:** Four new focused modules (`claims.py` the data model and JSON I/O,
`claim_extract.py` the model-driven extraction, `claim_gate.py` the non-model verification gate,
`pinning.py` data location and commit pinning), plus fixes to the existing fetcher so anchors are
validated against text that matches the page they link to. No computation and no crate yet —
Milestone 1 ends with a `claims.json` whose every row is either gate-passed and `Not checked`,
or `Not checkable` with a reason.

**Tech Stack:** Python 3.12, stdlib + `anthropic` (extraction only, optional), pandas/openpyxl
already present, pytest. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-18-claims-section-design.md`

## Global Constraints

- Tests run offline. `tests/conftest.py` already sets `RECRATE_OFFLINE=1`; no test may touch the
  network. HTTP is faked with `tests/httpfake.py`'s `FakeHTTP` (`monkeypatch.setattr("recrate.http.get", fake.get)`).
- Nothing about Jones et al. may be hard-coded in `src/`. Demo values live only in `tests/`.
- A claim's `result` is never blank: one of `Reproduced`, `Mismatch`, `Not checked`, `Not checkable`.
- A claim that fails the gate is `Not checkable` with a one-line reason. It never reaches `Reproduced`.
- The model never sets a value that reaches a verdict unchecked (`verify.check_description` is the precedent).
- Keep all existing tests passing (120 passed, 3 skipped at time of writing).

## Review Focus

Five failure modes the spec implies that no task's happy path exercises. Each has a test added to
the task that owns the code.

1. **A paper with no data availability statement, or one that resolves to nothing.** Must produce
   `Not checkable` with a reason, not an exception. → Task 6.
2. **A model-extracted anchor that does not occur in the paper.** The commonest hallucination;
   must be caught by the gate and downgraded, not trusted. → Task 5.
3. **A valid DOI that ends in a letter or contains a colon.** The trailing-`doi:` fix must not
   truncate legitimate DOIs. → Task 1.
4. **A paper available only as PDF** (no HTML full text). Anchors must still be gate-checked
   against whatever text we have, and the row must say the link may not highlight. → Task 2.
5. **Malformed or partial model output** (not JSON, missing fields). Extraction must raise a clear
   error rather than emit half-built claims. → Task 4.

---

### Task 1: Stop the DOI capture swallowing `doi:`

medRxiv PDF text reads `…preprint doi: https://doi.org/10.64898/2026.04.07.26350296; this version…`.
Both DOI regexes allow `:` as a trailing character, so they capture `10.64898/2026.04.07.26350296doi:`.
The live agent folder records exactly that.

**Files:**
- Modify: `src/recrate/sources.py:37` (`DOI`)
- Modify: `src/recrate/paper.py:15` (`DOI_RE`)
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sources.identify()` and `paper.read_paper()` return DOIs with no trailing
  `doi:` / `doi` / punctuation. Every later task depends on a clean DOI.

- [ ] **Step 1: Write the failing tests**

```python
def test_doi_from_medrxiv_pdf_text_drops_the_watermark():
    t = "medRxiv preprint 10.64898/2026.04.07.26350296doi: this version posted"
    assert [r.id for r in identify(t)] == ["10.64898/2026.04.07.26350296"]

def test_doi_ending_in_a_letter_is_not_truncated():      # Review Focus 3
    assert [r.id for r in identify("see 10.1371/journal.pone.0123456x here")] \
        == ["10.1371/journal.pone.0123456x"]

def test_doi_containing_a_colon_is_kept():               # Review Focus 3
    assert [r.id for r in identify("10.1234/abc:def ")] == ["10.1234/abc:def"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_sources.py -k doi -v`
Expected: the first fails with the captured DOI ending `doi:`.

- [ ] **Step 3: Fix both regexes**

Strip a trailing `doi:`/`doi` token (case-insensitive) after matching, rather than widening the
character class — a colon is legal inside a DOI, so excluding `:` everywhere would break the third
test. Apply the same strip in `paper.py:119` where `.rstrip(".")` already runs.

- [ ] **Step 4: Run to verify they pass, and nothing regressed**

Run: `python3 -m pytest tests/test_sources.py tests/test_fetch.py tests/test_locate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/sources.py src/recrate/paper.py tests/test_sources.py
git commit -m "fix: DOI capture no longer swallows a trailing doi: watermark"
```

---

### Task 2: Prefer medRxiv/bioRxiv HTML full text over PDF

`reading.from_europepmc` covers JATS for indexed articles; medRxiv preprints are not in Europe PMC.
Today this paper yields `paper_text: "PDF text"`, and anchors validated against PDF text will not
match the HTML page they link to.

**Files:**
- Modify: `src/recrate/fetch.py` (`fetch_paper`, after the `citation_pdf_url` branch)
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `fetch.classify_link() -> (kind, value)`, already returns `"biorxiv"` for these links.
- Produces: `FetchResult.paper.text` holding HTML-derived full text for biorxiv/medrxiv links,
  and `FetchResult.message` naming the source. Tasks 4 and 5 validate anchors against this text.

- [ ] **Step 1: Write the failing test**

```python
def test_medrxiv_prefers_full_html_over_pdf(monkeypatch, tmp_path):
    fake = FakeHTTP()
    fake.add(MEDRXIV_LANDING, html=MEDRXIV_PAGE)          # advertises citation_pdf_url
    fake.add(MEDRXIV_LANDING + ".full", html=MEDRXIV_FULL)  # body text lives here
    monkeypatch.setattr("recrate.http.get", fake.get)
    r = fetch.fetch_paper(MEDRXIV_LANDING, tmp_path)
    assert "Sixty-eight papers reported that data were available" in r.paper.text
    assert r.full_text is True

def test_falls_back_to_pdf_when_no_html_full_text(monkeypatch, tmp_path):  # Review Focus 4
    # only the landing page + citation_pdf_url resolve; .full 404s
    ...
    assert r.full_text is True and "PDF" in r.message
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -k medrxiv -v`
Expected: FAIL — the `.full` page is never requested.

- [ ] **Step 3: Implement `_biorxiv_full_text(landing_url: str, cache_dir) -> str | None` in `src/recrate/fetch.py`**

For `kind == "biorxiv"`, request `<landing>.full` before the PDF branch and parse it with the
existing `_MetaParser` (its `.text` already strips script/style). Use it when it yields at least
`MIN_FULLTEXT_CHARS` (3000, already defined); otherwise fall through to the existing PDF path
unchanged. Set `message` to name which source was used.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/fetch.py tests/test_fetch.py
git commit -m "feat: fetch medRxiv/bioRxiv HTML full text in preference to the PDF"
```

---

### Task 3: The claim model and `claims.json`

**Files:**
- Create: `src/recrate/claims.py`
- Test: `tests/test_claims.py`

**Interfaces:**
- Consumes: nothing.
- Produces, used by every later task:
  - `RESULTS = ("Reproduced", "Mismatch", "Not checked", "Not checkable")`
  - `@dataclass Claim` with fields: `id: str`, `kind: str` (`"number"` | `"figure"`),
    `claim: str`, `section: str`, `anchor: str`, `paper_says: str`,
    `data_gives: str | None = None`, `result: str = "Not checked"`, `why_not: str = ""`,
    `data_source: dict | None = None`, `computation: dict | None = None`,
    `tolerance: dict | None = None`
  - `Claim.to_json() -> dict` / `Claim.from_json(d: dict) -> Claim`
  - `save_claims(claims: list[Claim], path: Path) -> None`
  - `load_claims(path: Path) -> list[Claim]`
  - `Claim.not_checkable(reason: str) -> Claim` — returns a copy with
    `result="Not checkable"` and `why_not=reason`

- [ ] **Step 1: Write the failing tests**

```python
def test_claim_round_trips_through_json(tmp_path):
    c = Claim(id="c1", kind="number", claim="Sixty-eight papers said data were available.",
              section="Results", anchor="data were available", paper_says="68")
    save_claims([c], tmp_path / "claims.json")
    assert load_claims(tmp_path / "claims.json") == [c]

def test_result_defaults_to_not_checked_and_is_never_blank():
    assert Claim(id="c1", kind="number", claim="x", section="Results",
                 anchor="a", paper_says="68").result == "Not checked"

def test_not_checkable_carries_its_reason():
    c = Claim(id="c1", kind="number", claim="x", section="Results",
              anchor="a", paper_says="68").not_checkable("data not shared")
    assert (c.result, c.why_not) == ("Not checkable", "data not shared")

def test_save_rejects_an_unknown_result(tmp_path):
    c = Claim(id="c1", kind="number", claim="x", section="R", anchor="a",
              paper_says="68", result="Probably")
    with pytest.raises(ValueError):
        save_claims([c], tmp_path / "claims.json")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_claims.py -v`
Expected: FAIL with "No module named 'recrate.claims'".

- [ ] **Step 3: Implement `src/recrate/claims.py` with the interfaces above**

`save_claims` validates `result in RESULTS` for every claim and raises `ValueError` naming the
offending id. JSON is written with `indent=2, ensure_ascii=False`, matching the repo's other writers.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_claims.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/claims.py tests/test_claims.py
git commit -m "feat: claim model and claims.json read/write"
```

---

### Task 4: Extract claims from the paper

**Files:**
- Create: `src/recrate/claim_extract.py`
- Create: `tests/fixtures/README.md` (states each fixture's source and licence)
- Create: `tests/fixtures/paper_text.txt` (a few paragraphs of the demo paper's full text, the
  text anchors are checked against)
- Create: `tests/fixtures/claims_response.json` (a recorded model response, hand-trimmed, whose
  anchors occur verbatim in `paper_text.txt`)
- Test: `tests/test_claim_extract.py`

**Interfaces:**
- Consumes: `claims.Claim`, `claims.RESULTS` (Task 3); `drafter.parse_json` for lenient JSON.
- Produces: `extract_claims(paper_text: str, *, respond=None) -> list[Claim]`.
  `respond` is an injectable callable taking the prompt and returning the model's raw string;
  it defaults to the Anthropic call. Tests pass a stub that replays the fixture, which is how
  extraction is covered offline.

- [ ] **Step 1: Write the failing tests**

```python
def test_extracts_claims_with_anchors_from_a_recorded_response():
    text = (FIXTURES / "paper_text.txt").read_text()
    claims = extract_claims(text, respond=lambda _p: (FIXTURES / "claims_response.json").read_text())
    assert len(claims) >= 3
    c = next(c for c in claims if c.paper_says == "68")
    assert c.anchor in text and c.section and c.kind == "number"
    assert c.result == "Not checked"          # extraction never sets a verdict

def test_malformed_model_output_raises(monkeypatch):      # Review Focus 5
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: "not json at all")

def test_claim_missing_a_required_field_raises():         # Review Focus 5
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: '{"claims":[{"claim":"x"}]}')

def test_requires_an_api_key_only_when_no_respond_given(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        extract_claims("some paper")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_claim_extract.py -v`
Expected: FAIL with "No module named 'recrate.claim_extract'".

- [ ] **Step 3: Implement `extract_claims` in `src/recrate/claim_extract.py`**

Follow `drafter.draft_description`'s shape: module-level `INSTRUCTIONS` prompt, import `anthropic`
lazily, raise `RuntimeError` when `ANTHROPIC_API_KEY` is unset *and* no `respond` was given. Parse
with `drafter.parse_json`; raise `ValueError` when the payload is not JSON, has no `claims` list,
or any entry lacks `claim`, `section`, `anchor`, `paper_says`. Every claim comes back
`result="Not checked"` — extraction never sets a verdict. Ids are `c1`, `c2`, … in document order.

The prompt must require: one short sentence in reCRATE's own words (not copied); an `anchor` of a
few words occurring **verbatim** in the supplied text; the value exactly as printed, keeping its
rounding and CI format.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_claim_extract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/claim_extract.py tests/test_claim_extract.py tests/fixtures/
git commit -m "feat: extract claims with verbatim anchors from a paper"
```

---

### Task 5: The non-model verification gate

Nothing a model wrote may reach a verdict unchecked. This mirrors `verify.check_description`.

**Files:**
- Create: `src/recrate/claim_gate.py`
- Test: `tests/test_claim_gate.py`

**Interfaces:**
- Consumes: `claims.Claim` (Task 3); `verify._norm` and `verify._number_in` for whitespace-,
  hyphenation- and case-insensitive matching (reuse, do not reimplement).
- Produces: `gate(claims: list[Claim], paper_text: str, data_root: Path | None = None) -> list[Claim]`
  returning a new list where any claim failing a check is `Not checkable` with its reason.

Checks, in order, first failure wins:
1. anchor occurs in the paper text → else `"anchor not found in the paper's text"`
2. `paper_says` occurs in the paper text → else `"the reported value is not printed in the paper"`
3. when `data_source` is set: its `file` exists under `data_root`, and its `columns` are present
   in that file → else `"data file not found: <name>"` / `"column not in the data: <name>"`

- [ ] **Step 1: Write the failing tests**

```python
PAPER = "Results\nSixty-eight (68) papers reported that data were available."

def test_a_claim_whose_anchor_is_in_the_paper_passes():
    out = gate([_c(anchor="data were available", paper_says="68")], PAPER)
    assert out[0].result == "Not checked" and out[0].why_not == ""

def test_a_hallucinated_anchor_is_not_checkable():        # Review Focus 2
    out = gate([_c(anchor="ninety-nine papers", paper_says="68")], PAPER)
    assert out[0].result == "Not checkable"
    assert "anchor" in out[0].why_not

def test_a_value_not_printed_in_the_paper_is_not_checkable():
    out = gate([_c(anchor="data were available", paper_says="71")], PAPER)
    assert out[0].result == "Not checkable"

def test_anchor_matching_ignores_whitespace_and_case():
    out = gate([_c(anchor="DATA   were\navailable", paper_says="68")], PAPER)
    assert out[0].result == "Not checked"

def test_a_missing_data_file_is_not_checkable(tmp_path):
    c = _c(anchor="data were available", paper_says="68",
           data_source={"file": "missing.xlsx", "columns": ["X"]})
    assert gate([c], PAPER, tmp_path)[0].result == "Not checkable"

def test_the_gate_never_promotes_a_claim():
    c = _c(anchor="data were available", paper_says="68")
    assert gate([c], PAPER)[0].result != "Reproduced"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_claim_gate.py -v`
Expected: FAIL with "No module named 'recrate.claim_gate'".

- [ ] **Step 3: Implement `gate` in `src/recrate/claim_gate.py`**

Anchor matching compares `verify._norm(anchor)` against `verify._norm(paper_text)`. Value matching
uses `verify._number_in` for numeric values and the normalised substring test otherwise, so a CI
string like `21% (8%, 48%)` is matched as printed. Column presence is read with pandas for
`.xlsx`/`.csv`; a file type it cannot open is `Not checkable` with that reason, not a crash.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_claim_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/claim_gate.py tests/test_claim_gate.py
git commit -m "feat: non-model gate so an extracted claim cannot reach a verdict unchecked"
```

---

### Task 6: Find the data and pin it to an exact version

**Files:**
- Create: `src/recrate/pinning.py`
- Test: `tests/test_pinning.py`

**Interfaces:**
- Consumes: `agent._clone` as the model for git use (shell out to `git`); `sources.identify` for
  finding repo/archive links in the availability statement.
- Produces:
  - `@dataclass PinnedSource`: `url: str`, `kind: str` (`"git"` | `"zenodo"`), `version: str`
    (a 40-char SHA for git), `path: Path`
  - `pin_git(url: str, dest: Path, ref: str | None = None) -> PinnedSource` — clones, checks out
    `ref` when given, and records the resolved SHA from `git rev-parse HEAD`
  - `file_record(path: Path, upstream: str, license: str | None = None) -> dict` with keys
    `name`, `sha256`, `size`, `url`, `license`
  - `data_links(paper_text: str) -> list[str]` — repo/archive URLs found in the availability
    statement, most specific first

- [ ] **Step 1: Write the failing tests**

Build a real git repo in `tmp_path` (`git init`, write a file, commit) so the test is offline.

```python
def test_pin_git_records_the_resolved_commit(tmp_path):
    origin = _make_repo(tmp_path / "origin", {"data/x.csv": "a,b\n1,2\n"})
    p = pin_git(str(origin), tmp_path / "work")
    assert len(p.version) == 40 and (p.path / "data" / "x.csv").exists()

def test_pin_git_checks_out_a_named_commit(tmp_path):
    origin, first = _make_repo_two_commits(tmp_path / "origin")
    p = pin_git(str(origin), tmp_path / "work", ref=first)
    assert p.version == first

def test_file_record_hashes_the_file(tmp_path):
    f = tmp_path / "x.csv"; f.write_text("a,b\n1,2\n")
    r = file_record(f, upstream="https://example.org/x.csv", license="CC-BY-NC-SA-4.0")
    assert r["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert r["size"] == f.stat().st_size and r["license"] == "CC-BY-NC-SA-4.0"

def test_data_links_finds_a_github_repo():
    assert data_links("Data and code: https://github.com/o/r") == ["https://github.com/o/r"]

def test_no_data_statement_yields_no_links():              # Review Focus 1
    assert data_links("The authors declare no competing interests.") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_pinning.py -v`
Expected: FAIL with "No module named 'recrate.pinning'".

- [ ] **Step 3: Implement `src/recrate/pinning.py` with the interfaces above**

`pin_git` clones (full clone when `ref` is given, since `--depth 1` cannot check out an arbitrary
SHA), then `git rev-parse HEAD`. Raise `RuntimeError` with git's stderr tail on failure, as
`agent._clone` does. `data_links` reuses `sources.REPO` and `sources.identify` rather than a new
regex.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_pinning.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/pinning.py tests/test_pinning.py
git commit -m "feat: locate a paper's data and pin it to an exact commit, with hashes"
```

---

### Task 7: `recrate claims` — run extraction and the gate, write `claims.json`

**Files:**
- Create: `src/recrate/claim_run.py`
- Modify: `src/recrate/cli.py` (new `cmd_claims`, new subparser, and the usage block at the top)
- Test: `tests/test_claims_cli.py`

**Interfaces:**
- Consumes: `claim_extract.extract_claims` (Task 4), `claim_gate.gate` (Task 5),
  `pinning.data_links`/`pin_git` (Task 6), `claims.save_claims` (Task 3).
- Produces: `run_claims(agent_dir: Path, *, respond=None, online: bool = True) -> list[Claim]`,
  writing `<agent_dir>/claims.json`. Milestone 2 calls this before computing.

Behaviour: read the agent's paper text from `<agent_dir>/paper/paper.md`; extract; gate; when
`online` and the paper names a data repo, pin it under `<agent_dir>/data/` and record each file;
save. A paper with no resolvable data still writes `claims.json`, every row `Not checkable` with
`"data not shared"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_writes_claims_json_for_an_agent(tmp_path, agent):
    out = run_claims(agent.path, respond=_replay, online=False)
    assert (agent.path / "claims.json").exists()
    assert all(c.result in RESULTS for c in load_claims(agent.path / "claims.json"))

def test_no_data_statement_still_writes_every_row(tmp_path, agent):   # Review Focus 1
    claims = run_claims(agent.path, respond=_replay_no_data, online=False)
    assert claims and all(c.result == "Not checkable" for c in claims)
    assert all(c.why_not for c in claims)

def test_cli_claims_command(tmp_path, agent, capsys):
    assert main(["claims", str(agent.path)]) == 0
    assert "claims.json" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_claims_cli.py -v`
Expected: FAIL — no `recrate.claim_run`, no `claims` subcommand.

- [ ] **Step 3: Implement `run_claims` and wire the CLI**

Add `cmd_claims(a)` beside the other `cmd_*` functions and register a `claims` subparser taking
`agent`, `--no-search` (skip data pinning) and `--doi`, following the shape of the existing
`agentify` subparser. Print one line per claim and the path written, in the style of
`cmd_check_description`.

- [ ] **Step 4: Run to verify they pass, and the suite is green**

Run: `python3 -m pytest -q`
Expected: PASS — the pre-existing 120 passed / 3 skipped, plus the new tests.

- [ ] **Step 5: Commit**

```bash
git add src/recrate/claim_run.py src/recrate/cli.py tests/test_claims_cli.py
git commit -m "feat: recrate claims — extract, gate and pin, writing claims.json"
```

---

## Done when

`recrate claims <agent>` writes a `claims.json` in which every row carries a verbatim anchor that
occurs in the fetched text, a value printed in the paper, and either a pinned data source or a
stated reason it is `Not checkable` — with the full suite green and offline.

Milestone 2 adds the generated check script, the computation, and the demo fixture that reproduces
the 18 published values.
