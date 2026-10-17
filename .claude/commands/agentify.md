---
description: Agentify a paper that has no RO-Crate - read it and its code, draft the crate, validate, repeat
argument-hint: <paper agent folder made by `recrate agentify --paper ...`>
---

You are turning a scientific paper into a runnable paper agent with ReCRATE. The paper agent folder is
`$ARGUMENTS`. It contains `paper/paper.md` (the paper's text), `paper/paper.json` (DOI and code links) and,
if cloned, `code/` (the paper's repository). `crate/` may already hold an RO-Crate ReCRATE recreated from the
paper (its software as of publication in `crate/environment/`, its code and cited data), with no runnable method
yet: your job is to describe the methods so they can run. `recrate validate` keeps the dated environment.

Work like a careful co-author. Only state what the paper or code supports; never invent numbers.

1. **Read the paper.** Find each computational method a reader would want to apply to their own data, the
   data it was applied to (format, pixel size, channels, 2D/3D for images), the parameters and their values,
   and the results the paper reports for its own data.
2. **Get the code.** If `code/` is missing and `paper/paper.json` lists a repository, clone it into `code/`
   (`git clone --depth 1 <url> $ARGUMENTS/code`).
3. **Find a runnable entry point per method.** A Fiji macro (`.ijm`) or a script that takes an input folder.
   If a script needs a thin wrapper to follow ReCRATE's script contract (`--input_dir`, `--output_dir`, one
   `--<param>` per parameter, writes `results.csv`), write it in `$ARGUMENTS/code/recrate_wrappers/` and keep
   the paper's logic untouched. Prefer the paper's own example data and tutorials for example inputs.
4. **Find the paper's reported results** for that example data, as a per-item CSV (from supplementary tables,
   tutorial outputs or the repo). If you can only find summary numbers, write a small CSV with them and say so.
   **Record where each number came from.** The reported results and tolerances are the ground truth the
   validation verdict is judged against, so the user must be able to check them: note the exact location in
   the paper (e.g. "Table 1, p. 4", "Figure 3b", "Methods §2.3").
5. **Write the description** at `$ARGUMENTS/crate_description.json`, in the format documented in
   `src/recrate/draft.py` (run `python -c "import recrate.draft as d; print(d.__doc__)"`). Paths are relative to
   the agent folder. Set `"draft": true`. Declare `pixel size`, `channels`, `dimensions` for image inputs and
   `scaling` for size parameters in pixels. Set tolerances per reported measurement. **For every method with
   reported results, set `expected_source` to where in the paper those results are**, and set
   `tolerance_sources` for any tolerance you took from the paper. If a number is your own estimate rather than
   the paper's, say so in that source string (e.g. "estimated; not stated in paper") — never leave it blank to
   hide that.
6. **Build and validate:**
   ```bash
   recrate draft $ARGUMENTS/crate_description.json $ARGUMENTS/crate
   recrate validate $ARGUMENTS
   ```
7. **Repair.** If a method fails or differs, read the error and the run crate under `$ARGUMENTS/recrate/`, fix
   the environment, the wrapper or the description, and validate again. Stop after 6 attempts per method and
   leave it marked as not validated, with the reason in the method's description.
8. **Report** to the user: each method, its validation verdict, the source you recorded for each reported
   result (so they know what to check), anything you could not find in the paper (for example the pixel size),
   and what they should confirm before the draft flag is removed.

Then the user can run `recrate serve $ARGUMENTS` and connect it to Claude.
