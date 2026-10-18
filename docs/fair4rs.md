# FAIR4RS, principle by principle

How ReCRATE meets the [FAIR principles for research software](https://doi.org/10.1038/s41597-022-01710-x), applied
where reuse happens: to the method being reused, and to each run. This describes the proof of concept, not a
certification.

| Principle | How ReCRATE meets it | Status |
| --- | --- | --- |
| **F1** persistent identifiers, per version | Starts from the paper's DOI and the workflow's WorkflowHub or Zenodo identifier, with its version; a preprint's version is kept apart from its DOI | Done |
| **F2, F3** rich metadata that includes the identifier | Each crate names the paper it cites and its authors (with ORCIDs from the DOI); each run crate names the crate it is `isBasedOn`, and the software and versions that ran | Done |
| **F4** metadata searchable and indexable | Every crate is JSON-LD; deposit to a registry is manual | Partial |
| **A1** retrievable by identifier, over open protocols | Sources are fetched by DOI or URL over HTTPS; a published crate is found from the paper's links, WorkflowHub, Zenodo, DataCite or its code | Done |
| **A1.2** authentication where needed | Data behind access requests is recorded, never fetched | Partial (`/recrate` agent) |
| **A2** metadata outlive the software | Each run crate keeps a copy of the workflow and all its metadata | Done |
| **I1** exchange data by community standards | Inputs declare their formats and properties (e.g. OME/ImageJ calibration); your files are checked against them | Done for images, tables, signals |
| **I2** qualified references to other objects | `isBasedOn`, `citation`, `exampleOfWork`, `conformsTo`; the authors' own code and data told apart from software they cite; follow-ups link the run they follow | Done |
| **R1** rich, accurate description | Parameters carry units, scaling rules, roles and the paper's sentence; outputs carry kinds and sourced tolerances | Done |
| **R1.1** clear licence | The paper's licence from its DOI metadata, in its crate and every run crate; "Licence not stated", said plainly, when none can be found | Done |
| **R1.2** detailed provenance | Who ran what, when, on which files (SHA-256), with which parameters and why, checked by whom | Done |
| **R2** references to the software used | Software and versions per run (every installed Python package); versions as of publication from PyPI, conda and CRAN, each with how it was determined | Done |
| **R3** community standards | RO-Crate 1.1, Workflow RO-Crate 1.0, Process and Workflow Run Crate 0.5. The research object's crate is validated when created; every run crate passes all 55 required [validator](https://github.com/crs4/rocrate-validator) checks (in CI), and 121 to 123 of 136 at the recommended level | Done |
