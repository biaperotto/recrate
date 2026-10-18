# Paper example: inferential reproducibility of linear regression

A preprint whose authors share their code, without an RO-Crate: the case where ReCRATE has the most to do.
Retrieved 2026-10-18.

| Path | What it is | Source | Licence |
| --- | --- | --- | --- |
| `paper.pdf` | Jones L, Barnett A, Hartel G, Vagenas D (2026). *An Empirical Assessment of Inferential Reproducibility of Linear Regression in Health and Biomedical Research Papers.* medRxiv, version 2 | [doi:10.64898/2026.04.07.26350296](https://doi.org/10.64898/2026.04.07.26350296) | CC BY-NC 4.0 |

What ReCRATE finds from the paper alone (`recrate agentify --paper paper.pdf --doi 10.64898/2026.04.07.26350296`):

* no published RO-Crate, so it recreates one as of publication (7 April 2026), with the licence and the authors
  (with ORCIDs) from the DOI;
* the authors' own code, [github.com/Lee-V-Jones/Reproducibility](https://github.com/Lee-V-Jones/Reproducibility)
  (cloned: 97 Quarto documents with R code), its archived release
  ([Zenodo 10.5281/zenodo.19448969](https://doi.org/10.5281/zenodo.19448969)) and the study protocol
  ([Zenodo 10.5281/zenodo.10620146](https://doi.org/10.5281/zenodo.10620146)), told apart from software they only
  cite (rplos);
* R 4.4.2, as the paper states, and 60 R packages from the code, each at its version as of publication (CRAN).

Its methods are not tools yet: **Make runnable** in the app (or `recrate build`) turns them into tools checked
against the published results. The code needs R and Quarto.
