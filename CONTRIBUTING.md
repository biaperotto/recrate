# Contributing

* `pip install -e ".[dev]"`, then `ruff check src app tests`, `mypy` and `pytest` before opening a pull request (CI runs the three).
* New ecosystem (a workflow type from WorkflowHub): add an adapter in `recrate.adapters` that reads the community's own conventions for parameters, example data and expected outputs, writes a ReCRATE crate with an invocation recipe (`docs/crate-spec.md`), and test it with a small synthetic crate that runs with the engine installed.
* New domain profile: see [docs/domain-profiles.md](docs/domain-profiles.md). Add a test with a small synthetic dataset that shows the silent error the profile catches.
* New runner: subclass `recrate.runners.base.Runner`, return a `RunResult`, and record software versions.
* Keep run crates conformant with Process Run Crate / Workflow Run Crate; add new information as standard schema.org terms where possible.
