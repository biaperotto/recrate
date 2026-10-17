# Contributing

* `pip install -e ".[dev]"` and `pytest` before opening a pull request.
* New domain profile: see [docs/domain-profiles.md](docs/domain-profiles.md). Add a test with a small synthetic dataset that shows the silent error the profile catches.
* New runner: subclass `recrate.runners.base.Runner`, return a `RunResult`, and record software versions.
* Keep run crates conformant with Process Run Crate / Workflow Run Crate; add new information as standard schema.org terms where possible.
