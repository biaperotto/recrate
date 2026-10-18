# Real-world example: nuclei segmentation and counting (WorkflowHub)

A published Workflow RO-Crate for fluorescence microscopy, with the public image data it was built from.
Use it to test ReCRATE on a crate that was not written for ReCRATE.

Retrieved 2026-10-17.

## What is here

| Path | What it is | Source | Licence |
| --- | --- | --- | --- |
| `crate/` | The RO-Crate exactly as published, unmodified | [WorkflowHub #771, version 2](https://workflowhub.eu/workflows/771?version=2) (`/ro_crate?version=2`) | MIT |
| `crate/segmentation-and-counting.ga` | The Galaxy workflow (v0.2, 2024-11-07) | [iwc-workflows/fluorescence-nuclei-segmentation-and-counting](https://github.com/iwc-workflows/fluorescence-nuclei-segmentation-and-counting) | MIT |
| `crate/segmentation-and-counting-tests.yml` | Planemo test: input image and expected outputs | same | MIT |
| `crate/test-data/` | Test input (200×200, 16-bit), expected label image, overlay, and count (**20 nuclei**) | same | MIT |
| `research-data/B2/` | 3 of the 9 full fields of well B2 (1344×1024, 16-bit, DAPI) | [Zenodo 10.5281/zenodo.3362976](https://doi.org/10.5281/zenodo.3362976) | CC-BY-4.0 |
| `fetch_data.sh` | Downloads whole wells from Zenodo (`./fetch_data.sh B2 B3`) | | |
| `reuse-bbbc039/` | 6 of BBBC039's 200 images (U2OS nuclei, Hoechst, 520×696, 16-bit) with their ground-truth masks in `ground-truth/`, for **Reuse** | [BBBC039](https://bbbc.broadinstitute.org/BBBC039), Broad Bioimage Benchmark Collection | CC0 |
| `make_half_resolution.py` | Makes `my-data-half-resolution/`: the B2 fields at half resolution (2×2 averaged), for the reuse example | derived from `research-data/B2` | CC-BY-4.0 |

## The workflow

Author: Leonid Kostrykin ([ORCID 0000-0003-1323-3762](https://orcid.org/0000-0003-1323-3762)). Based on the Galaxy
Training Network tutorial [Introduction to Image Analysis using Galaxy](https://training.galaxyproject.org/training-material/topics/imaging/tutorials/imaging-introduction/tutorial.html)
(Wollmann, Hiltemann, Kostrykin, Chiang Jurado). Tested continuously with Planemo on GitHub Actions (declared in the crate).

| Step | Galaxy tool (imgteam) | Version | Parameters |
| --- | --- | --- | --- |
| 1 | Filter 2-D image | 1.12.0+galaxy1 | Gaussian, size 3 |
| 2 | Histogram equalization | 0.18.1+galaxy0 | CLAHE |
| 3 | Threshold image | 0.18.1+galaxy3 | Otsu, dark background |
| 4 | Convert image format (bfconvert) | 6.7.0+galaxy3 | to PNG |
| 5 | Binary to label map | 0.5+galaxy0 | connected components |
| 6 | Overlay images | 0.0.4+galaxy4 | contours, red, labels shown |
| 7 | Count objects in label map | 0.0.5-2 | |

Method reference: Otsu, N. (1979). A threshold selection method from gray-level histograms. *IEEE Transactions on
Systems, Man, and Cybernetics* 9(1), 62–66. doi:10.1109/TSMC.1979.4310076

## The data

Gunkel, M. (2019). *Microscope Image Analysis Course Sept 2019 – Images siRNA Screen.* Zenodo.
doi:10.5281/zenodo.3362976. 380 fluorescence images of DAPI-stained HeLa nuclei, 42 wells of a 384-well plate,
each well treated with an siRNA, acquired with an Olympus ScanR system at 10× magnification.

There is no research article behind this dataset or workflow: the citable items are the Zenodo record, the
workflow (WorkflowHub / GitHub), the training tutorial, and Otsu (1979).

## Running it with ReCRATE

```bash
recrate agentify --crate examples/workflowhub-nuclei-segmentation/crate \
                 --out examples/workflowhub-nuclei-segmentation/paper_agent     # validated: 20 vs 20
recrate run examples/workflowhub-nuclei-segmentation/paper_agent \
            examples/workflowhub-nuclei-segmentation/research-data/B2           # 425, 413, 307 nuclei
```

The crate was not written for ReCRATE: its parameters live in the `.ga` file (`tool_state`), its example data
and expected results in the Planemo test, and it declares no input properties or tolerances. `recrate agentify`
recognises a Galaxy crate and derives a ReCRATE crate from those files (`src/recrate/galaxy.py`), leaving this
folder unchanged. The workflow runs through ports of the Galaxy imaging tools at the pinned versions (code read
from BMCV/galaxy-image-analysis as of 2024-11-07), in the workflow's own wiring; the port reproduces the
published count and label image exactly (`tests/test_galaxy.py`).

Still missing: a pixel size. Neither the test image nor the Zenodo images carry one and the record doesn't state
it, so sizes stay in pixels. Take it from the acquisition metadata rather than assuming one.

## Reuse on another lab's images: BBBC039

`reuse-bbbc039/` is what the example opens with under **Reuse**: the same 6 BBBC039 images as the StarDist example
(every 33rd in name order), from another lab and microscope, denser than the research images. With the workflow's
published settings unchanged it finds **472 nuclei against 627** in the ground truth (87/110, 101/142, 5/7, 106/136,
82/104, 91/128 per image). A smaller filter doesn't close the gap (filter size 2: 485; 1: 528): the workflow has no
step that splits touching nuclei, so on dense images neighbours merge. ReCRATE shows this rather than fixing it:
because the folder has a `ground-truth/` with masks named like the images, each run reports `count_true` next to
`count`, and the app plots them side by side.
