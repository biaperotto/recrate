"""Build an RO-Crate 1.1 for Jones et al. (2026), "An Empirical Assessment of Inferential
Reproducibility of Linear Regression in Health and Biomedical Research Papers".

Usage: python build_crate.py <path-to-cloned-Lee-V-Jones/Reproducibility> <out-dir>

The crate:
  * describes the preprint, authors, related papers, protocol, code and software versions;
  * bundles the four data spreadsheets the headline numbers come from (CC BY-NC-SA 4.0);
  * lists the 18 headline claims as schema.org Claim entities;
  * records an independent re-check of those claims as a CreateAction
    (Process Run Crate profile), with the script and its output.
"""
import datetime
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys

REPO, OUT = sys.argv[1], sys.argv[2]
COMMIT = subprocess.check_output(["git", "-C", REPO, "rev-parse", "HEAD"], text=True).strip()
GH = "https://github.com/Lee-V-Jones/Reproducibility"
RAW = f"{GH}/blob/{COMMIT}"
PREPRINT = "https://doi.org/10.64898/2026.04.07.26350296"
QMD = ("An Empirical Assessment of Inferential Reproducibility of Linear Regression in Health "
       "and Biomedical Research Papers August 14.qmd")
CC = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
TODAY = datetime.date.today().isoformat()

DATA_FILES = {
    "Reproducibility_wide_with_dictionary.xlsx":
        "One row per sampled paper (95): data availability, computational and inferential "
        "reproducibility classifications, assumption checks. Sheet 'Data_Dictionary' defines columns.",
    "Reproducibility_long_with_dictionary.xlsx":
        "One row per assessed model (51 rows; 32 assessed for inferential reproducibility): "
        "assumption violations, distribution, inferential outcome.",
    "compare_data_long_with_dictionary.xlsx":
        "One row per coefficient range compared (97): 10%, 0.1 and 0.2 standardized-difference "
        "thresholds and significance change.",
    "model_data_long_with_dictionary.xlsx":
        "Original vs reproduced estimate agreement categories per model term (268 rows).",
}

# Headline claims from the preprint (v2) and the columns they come from.
CLAIMS = [
    ("Of 95 sampled papers, 68 reported that data were available.", "Reproducibility_wide"),
    ("Data were accessible for 43 papers.", "Reproducibility_wide"),
    ("20 papers were assessed for computational reproducibility.", "Reproducibility_wide"),
    ("8 papers were computationally reproduced.", "Reproducibility_wide"),
    ("14 papers were assessed for inferential reproducibility.", "Reproducibility_wide"),
    ("3 of the 14 papers were inferentially reproducible.", "Reproducibility_wide"),
    ("Paper-level inferential reproducibility was 21% (95% Wilson CI 8%, 48%).", "Reproducibility_wide"),
    ("14 of 32 models (44%) were inferentially reproducible.", "Reproducibility_long"),
    ("Residual normality was violated in 8 papers.", "Reproducibility_long"),
    ("Independence was violated in 8 papers.", "Reproducibility_long"),
    ("16 of 32 models exhibited independence violations.", "Reproducibility_long"),
    ("Linearity violations were found in 5 papers, affecting 8 models.", "Reproducibility_long"),
    ("A Gaussian distribution fit 28 models, Tweedie 3 and Inverse Gaussian 1.", "Reproducibility_long"),
    ("97 coefficient ranges were evaluated.", "compare_data_long"),
    ("30 ranges exceeded the 10% threshold; 9 of 11 papers.", "compare_data_long"),
    ("12 ranges exceeded a 0.1 standardized difference; 5 of 11 papers.", "compare_data_long"),
    ("2 ranges exceeded a 0.2 standardized difference; 2 of 11 papers.", "compare_data_long"),
    ("Of 97 p-values, 1 shifted significance, 40 stayed significant, 56 stayed non-significant.",
     "compare_data_long"),
    # Not checked by the bundled script (needs R + brms/Stan):
    ("After accounting for clustering by paper, the marginal probability of inferential "
     "reproducibility was 0.34 (95% CrI 0.07, 0.69); ICC 0.59 (0.05, 0.89).", None),
]


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ref(i):
    return {"@id": i}


if os.path.exists(OUT):
    shutil.rmtree(OUT)
os.makedirs(f"{OUT}/data")
for name in DATA_FILES:
    shutil.copy2(f"{REPO}/data/{name}", f"{OUT}/data/{name}")
shutil.copy2(os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_claims_jones2026.py"),
             f"{OUT}/check_claims_jones2026.py")

# Run the claim check inside the crate so the recorded output matches the bundled files.
start = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
run = subprocess.run([sys.executable, "-I", "check_claims_jones2026.py", "."],
                     cwd=OUT, capture_output=True, text=True)
end = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
with open(f"{OUT}/claim-check-output.txt", "w") as f:
    f.write(run.stdout + run.stderr)
passed = run.returncode == 0
import pandas  # noqa: E402  (only for its version)

g = []
g.append({
    "@id": "ro-crate-metadata.json", "@type": "CreativeWork",
    "conformsTo": ref("https://w3id.org/ro/crate/1.1"), "about": ref("./"),
})

data_ids = [f"data/{n}" for n in DATA_FILES]
claim_ids = [f"#claim-{i + 1:02d}" for i in range(len(CLAIMS))]
g.append({
    "@id": "./", "@type": "Dataset",
    "name": "RO-Crate: Inferential reproducibility of linear regression in health research (Jones et al. 2026)",
    "description": (
        "Research object for the medRxiv preprint 'An Empirical Assessment of Inferential "
        "Reproducibility of Linear Regression in Health and Biomedical Research Papers'. It bundles "
        "the study data behind the headline results, links the authors' R/Quarto code pinned to "
        f"commit {COMMIT[:7]}, lists the paper's headline claims, and records an independent "
        "re-check of 18 of them from the bundled data."),
    "datePublished": TODAY,
    "license": ref(CC),
    "conformsTo": [ref("https://w3id.org/ro/wfrun/process/0.5")],
    "keywords": ["inferential reproducibility", "linear regression", "meta-research",
                 "regression assumptions", "PLOS ONE", "health research"],
    "cite-as": PREPRINT,
    "citation": ref(PREPRINT),
    "mainEntity": ref(PREPRINT),
    "isBasedOn": [ref(GH), ref("https://doi.org/10.5281/zenodo.19448969")],
    "hasPart": [ref(i) for i in data_ids] + [ref("check_claims_jones2026.py"),
                                             ref("claim-check-output.txt"),
                                             ref(f"{RAW}/{QMD.replace(' ', '%20')}"),
                                             ref(f"{RAW}/Inferential_session_info.txt")],
    "mentions": [ref(c) for c in claim_ids] + [ref("#claim-check-run")],
})

# --- the paper and people --------------------------------------------------------------
orgs = [
    ("#qut-rmg", "Research Methods Group, School of Public Health and Social Work, Queensland University of Technology"),
    ("#qut-aushsi", "AusHSI, Centre for Healthcare Transformation, Queensland University of Technology"),
    ("#qimr-stats", "Statistics Unit, QIMR Berghofer Medical Research Institute"),
]
for oid, name in orgs:
    g.append({"@id": oid, "@type": "Organization", "name": name, "address": "Queensland, Australia"})
people = [
    ("https://orcid.org/0000-0002-5686-1155", "Lee Jones", ["#qut-rmg", "#qut-aushsi", "#qimr-stats"]),
    ("https://orcid.org/0000-0001-6339-0374", "Adrian Barnett", ["#qut-aushsi"]),
    ("#gunter-hartel", "Gunter Hartel", ["#qimr-stats"]),
    ("#dimitrios-vagenas", "Dimitrios Vagenas", ["#qut-rmg"]),
]
for pid, name, aff in people:
    p = {"@id": pid, "@type": "Person", "name": name, "affiliation": [ref(a) for a in aff]}
    if pid.startswith("https://orcid.org/0000-0002-5686-1155"):
        p["email"] = "lee.jones@qut.edu.au"
    g.append(p)
authors = [ref(p[0]) for p in people]

g.append({
    "@id": PREPRINT, "@type": "ScholarlyArticle",
    "name": "An Empirical Assessment of Inferential Reproducibility of Linear Regression in Health and Biomedical Research Papers",
    "author": authors, "publisher": ref("https://www.medrxiv.org/"),
    "identifier": "10.64898/2026.04.07.26350296", "version": "2",
    "url": "https://www.medrxiv.org/content/10.64898/2026.04.07.26350296v2",
    "creativeWorkStatus": "Preprint (not peer reviewed by a journal)",
    "abstract": ("Random sample of 95 health PLOS ONE 2019 papers using linear regression; 14 papers with "
                 "at least partially computationally reproduced models were reanalysed to test whether "
                 "addressing assumption violations changed the inference. Only 3 of 14 papers were "
                 "inferentially reproducible."),
    "isBasedOn": [ref(i) for i in data_ids],
    "citation": [ref("https://doi.org/10.1371/journal.pone.0299617"),
                 ref("https://doi.org/10.1371/journal.pone.0305150"),
                 ref("https://doi.org/10.64898/2026.04.07.26350286"),
                 ref("https://doi.org/10.5281/zenodo.10620146")],
    "review": ref("https://metaror.org/article/an-empirical-assessment-of-inferential-reproducibility-of-linear-regression-in-health-and-biomedical-research-papers/"),
})
g.append({"@id": "https://www.medrxiv.org/", "@type": "Organization", "name": "medRxiv"})
related = [
    ("https://doi.org/10.1371/journal.pone.0299617", "ScholarlyArticle",
     "Common misconceptions held by health researchers when interpreting linear regression assumptions, a cross-sectional study (PLOS ONE 2025; Stage 1)"),
    ("https://doi.org/10.1371/journal.pone.0305150", "ScholarlyArticle",
     "Linear regression reporting practices for health researchers, a cross-sectional meta-research study (PLOS ONE 2025; Stage 1)"),
    ("https://doi.org/10.64898/2026.04.07.26350286", "ScholarlyArticle",
     "Challenges in the Computational Reproducibility of Linear Regression Analyses: An Empirical Study (medRxiv preprint; Stage 2 part 1)"),
    ("https://doi.org/10.5281/zenodo.10620146", "CreativeWork",
     "Lee-V-Jones/statistical-quality: Protocol (study protocol)"),
]
for rid, typ, name in related:
    g.append({"@id": rid, "@type": typ, "name": name})
g.append({
    "@id": "https://metaror.org/article/an-empirical-assessment-of-inferential-reproducibility-of-linear-regression-in-health-and-biomedical-research-papers/",
    "@type": "Review", "name": "MetaROR open peer review of the preprint",
    "itemReviewed": ref(PREPRINT),
})

# --- code, licence, software -----------------------------------------------------------
g.append({
    "@id": GH, "@type": ["Dataset", "SoftwareSourceCode"],
    "name": "Lee-V-Jones/Reproducibility",
    "description": ("Authors' repository: data, per-paper reproduction code and HTML reports for 95 papers, "
                    "and Quarto sources for the computational and inferential manuscripts."),
    "codeRepository": GH, "version": COMMIT, "author": ref(people[0][0]),
    "license": ref(CC), "programmingLanguage": ref("#R"),
    "url": "https://lee-v-jones.github.io/Reproducibility/",
})
g.append({
    "@id": "https://doi.org/10.5281/zenodo.19448969", "@type": "Dataset",
    "name": "Zenodo archive of Lee-V-Jones/Reproducibility", "license": ref(CC),
})
g.append({
    "@id": f"{RAW}/{QMD.replace(' ', '%20')}", "@type": ["File", "SoftwareSourceCode"],
    "name": QMD,
    "description": "Quarto source that produces the manuscript, all numbers, tables and figures (needs R).",
    "programmingLanguage": ref("#R"), "encodingFormat": "text/markdown",
    "softwareRequirements": [ref("#R"), ref("#quarto")] + [ref(f"#r-{p}") for p, _ in []],
    "isPartOf": ref(GH),
})
g.append({
    "@id": f"{RAW}/Inferential_session_info.txt", "@type": "File",
    "name": "R sessionInfo() for the inferential manuscript",
    "description": "R 4.4.2 on x86_64 Windows 11; lists every attached and namespace-loaded package with versions.",
    "encodingFormat": "text/plain", "isPartOf": ref(GH),
})
g.append({"@id": CC, "@type": "CreativeWork", "name": "CC BY-NC-SA 4.0",
          "description": "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International"})
g.append({"@id": "#R", "@type": ["ComputerLanguage", "SoftwareApplication"], "name": "R",
          "version": "4.4.2", "url": "https://www.r-project.org/"})
g.append({"@id": "#quarto", "@type": "SoftwareApplication", "name": "Quarto", "url": "https://quarto.org/"})
r_pkgs = [("brms", "2.22.0", "Bayesian mixed-effects logistic model of reproducibility"),
          ("posterior", "1.6.1", "Posterior summaries and convergence diagnostics"),
          ("bayesplot", "1.12.0", "Bayesian diagnostic plots"),
          ("binom", "1.1-1.1", "Wilson confidence intervals"),
          ("DescTools", "0.99.60", "Frequencies"),
          ("readxl", "1.4.5", "Reading the data spreadsheets"),
          ("dplyr", "1.1.4", "Data handling"),
          ("tidyverse", "2.0.0", "Data handling"),
          ("knitr", "1.50", "Rendering")]
pkg_ids = []
for name, ver, use in r_pkgs:
    pid = f"#r-{name}"
    pkg_ids.append(pid)
    g.append({"@id": pid, "@type": "SoftwareApplication", "name": f"R package {name}", "version": ver,
              "description": use, "url": f"https://CRAN.R-project.org/package={name}"})
for e in g:
    if e["@id"] == f"{RAW}/{QMD.replace(' ', '%20')}":
        e["softwareRequirements"] = [ref("#R"), ref("#quarto")] + [ref(p) for p in pkg_ids]

# --- data files ------------------------------------------------------------------------
for name, desc in DATA_FILES.items():
    path = f"{OUT}/data/{name}"
    g.append({
        "@id": f"data/{name}", "@type": "File", "name": name, "description": desc,
        "encodingFormat": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "contentSize": str(os.path.getsize(path)), "sha256": sha256(path),
        "license": ref(CC), "author": authors,
        "isBasedOn": ref(f"{RAW}/data/{name}"),
        "creditText": "Jones L, Barnett A, Hartel G, Vagenas D (2026). github.com/Lee-V-Jones/Reproducibility",
    })
    g.append({"@id": f"{RAW}/data/{name}", "@type": "File", "name": f"{name} (upstream)",
              "isPartOf": ref(GH)})

# --- claims ----------------------------------------------------------------------------
for cid, (text, source) in zip(claim_ids, CLAIMS):
    c = {"@id": cid, "@type": "Claim", "name": cid[1:], "text": text, "appearance": ref(PREPRINT)}
    if source:
        c["isBasedOn"] = ref(f"data/{source}_with_dictionary.xlsx")
        c["creativeWorkStatus"] = "Reproduced from bundled data" if passed else "Check failed"
    else:
        c["creativeWorkStatus"] = "Not checked (needs R + brms/Stan)"
    g.append(c)

# --- the re-check run (Process Run Crate) ----------------------------------------------
g.append({
    "@id": "check_claims_jones2026.py", "@type": ["File", "SoftwareSourceCode", "SoftwareApplication"],
    "name": "Claim check script",
    "description": "Recomputes 18 headline numbers from the bundled spreadsheets, independently of the authors' R code.",
    "programmingLanguage": ref("#python"), "encodingFormat": "text/x-python",
    "softwareRequirements": [ref("#python"), ref("#pandas")],
    "sha256": sha256(f"{OUT}/check_claims_jones2026.py"),
})
g.append({"@id": "#python", "@type": ["ComputerLanguage", "SoftwareApplication"], "name": "Python",
          "version": sys.version.split()[0], "url": "https://www.python.org/"})
g.append({"@id": "#pandas", "@type": "SoftwareApplication", "name": "pandas",
          "version": pandas.__version__, "url": "https://pandas.pydata.org/"})
g.append({
    "@id": "claim-check-output.txt", "@type": "File", "name": "Claim check output",
    "description": "PASS/FAIL per claim, paper value vs value recomputed from data.",
    "encodingFormat": "text/plain", "sha256": sha256(f"{OUT}/claim-check-output.txt"),
})
g.append({
    "@id": "#claim-check-run", "@type": "CreateAction",
    "name": "Independent re-check of the preprint's headline claims",
    "instrument": ref("check_claims_jones2026.py"),
    "object": [ref(i) for i in data_ids],
    "result": ref("claim-check-output.txt"),
    "startTime": start, "endTime": end,
    "actionStatus": ref("http://schema.org/CompletedActionStatus" if passed
                        else "http://schema.org/FailedActionStatus"),
    "description": "18 of 19 listed claims checked; the Bayesian model claim was not re-run.",
})

crate = {"@context": ["https://w3id.org/ro/crate/1.1/context",
                      {"sha256": "http://schema.org/sha256", "creditText": "http://schema.org/creditText"}],
         "@graph": g}
with open(f"{OUT}/ro-crate-metadata.json", "w") as f:
    json.dump(crate, f, indent=2, ensure_ascii=False)

# --- minimal human-readable preview ----------------------------------------------------
rows = "".join(f"<tr><td>{html.escape(c['@id'][1:])}</td><td>{html.escape(c['text'])}</td>"
               f"<td>{html.escape(c['creativeWorkStatus'])}</td></tr>"
               for c in g if c.get("@type") == "Claim")
files = "".join(f"<li><a href='{html.escape(e['@id'])}'>{html.escape(e['@id'])}</a> — "
                f"{html.escape(e.get('description', ''))}</li>"
                for e in g if e["@id"] in [h["@id"] for h in g[1]["hasPart"]])
root = g[1]
with open(f"{OUT}/ro-crate-preview.html", "w") as f:
    f.write(f"""<!doctype html><meta charset="utf-8"><title>{html.escape(root['name'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 16px}}
table{{border-collapse:collapse;width:100%}}td{{border-top:1px solid #ccc;padding:4px 8px;vertical-align:top}}</style>
<script type="application/ld+json">{json.dumps(crate)}</script>
<h1>{html.escape(root['name'])}</h1><p>{html.escape(root['description'])}</p>
<p>Paper: <a href="{PREPRINT}">{PREPRINT}</a> · Code: <a href="{GH}">{GH}</a> @ {COMMIT[:7]} ·
Licence: <a href="{CC}">CC BY-NC-SA 4.0</a></p>
<h2>Files</h2><ul>{files}</ul><h2>Claims</h2><table>{rows}</table>""")
print("wrote", OUT, "| claim check passed:", passed)
