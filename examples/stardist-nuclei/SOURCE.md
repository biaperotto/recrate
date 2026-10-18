# StarDist nuclei: where everything comes from

**Paper.** Schmidt U, Weigert M, Broaddus C, Myers G. *Cell Detection with Star-convex Polygons.* MICCAI 2018.
Preprint: [arXiv:1806.03535](https://arxiv.org/abs/1806.03535). The PDF is not stored here: arXiv's licence
(non-exclusive distribution) does not let us redistribute it. ReCRATE fetches it from arXiv when the example is opened
(into `paper-download/`, ignored by git).

**Code and model.** [stardist](https://github.com/stardist/stardist) 0.9.2 (BSD-3-Clause), with the model trained in the
paper, `2D_paper_dsb2018`, downloaded by stardist on first use. The two scripts in `workflows/` are ours (CC0): they call
stardist with the paper's thresholds (`prob_thresh` 0.417819, `nms_thresh` 0.5) and follow ReCRATE's script contract.

**Reproduce: the paper's test data.** `crate/data/stardist-ap/` holds the 50 test images and ground-truth masks of the
DSB2018 subset used in the paper (from the stardist release `dsb2018.zip`; the images come from the Kaggle 2018 Data
Science Bowl, BBBC038, CC0). `crate/expected/stardist-ap.csv` is the DSB2018 row of the paper's Table 1 (average
precision at IoU 0.5 to 0.9).

What ReCRATE finds: **3 of 9 values identical**, the other 6 higher or lower by at most 0.006
(for example 0.8648 against 0.8641 at IoU 0.5). The tolerance is the paper's rounding (±0.00005), so the verdict is
"differences found", not "reproduced". The likely cause is newer software (TensorFlow 2.21 and stardist 0.9.2 against
the 2018 versions); the conclusion of Table 1 does not change.

**Reuse: a second dataset of the same kind.** [BBBC039](https://bbbc.broadinstitute.org/BBBC039) (Broad Bioimage
Benchmark Collection, CC0): 200 images of U2OS nuclei stained with Hoechst, 520 × 696 pixels, 16-bit, with
ground truth. Same modality (widefield fluorescence, a nuclear stain), different cells, lab and microscope. The app
suggests its two links (`images.zip`, `masks.zip`) under **Reuse → From a link**; they are downloaded once into
`~/.cache/recrate/datasets/`. Its masks colour touching nuclei with different values (1 to 3, red channel); the scripts
read each connected region of one value as one nucleus.

Note: DSB2018 drew in part on Broad (BBBC) image collections, so BBBC039 may overlap with the training data and a good score
on it is not a fully independent test.

What ReCRATE finds on BBBC039, with the paper's model and thresholds unchanged: average precision 0.912 at IoU 0.5 and
0.299 at 0.9 (the paper reports 0.864 and 0.119 on DSB2018), and 22,438 nuclei found against 23,615 in the ground
truth (mean AP at IoU 0.5 per image 0.899).
