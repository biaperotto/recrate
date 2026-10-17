# Counting and sizing nuclei in 2D fluorescence images with Fiji

ReCRATE demo authors. Demo Journal of Bioimage Methods (2026). https://doi.org/10.0000/recrate-demo-paper

## Abstract

We describe two simple, reusable methods for 2D fluorescence images of stained nuclei: a Fiji macro that
counts nuclei per field and reports their mean area, and a Python script that reports the nuclear size
distribution. On our dataset of six fields we find on average 22 nuclei per field with a mean
nuclear area of 107.5 µm².

## Data

Six fields of view, 256 × 256 pixels, single channel (nuclear stain), acquired at a pixel size of
0.65 µm. Images are calibrated TIFF files.

## Methods

### Nuclei counting (Fiji)

Images were thresholded with the Otsu method (dark background) and converted to masks. Particles were
analysed with Analyze Particles, keeping particles of at least 50 pixels to exclude debris. For each
field we report the number of nuclei and their mean area in µm². The macro is count_nuclei.ijm and was
run in Fiji 2.16.0.

### Nuclear size distribution (Python)

The same segmentation (Otsu threshold, 8-connected objects of at least 50 pixels) was done in Python with
scikit-image. For each field we report the median nuclear area and the fraction of nuclei larger than
150 µm². The script is size_distribution.py.

## Results

Across the six fields, the mean count was 22.0 nuclei per field and the mean nuclear area was
107.5 µm². The median nuclear area per field averaged 101.2 µm², and on average
17% of nuclei were larger than 150 µm². Per-field values are given in the supplementary tables.

## Data and code availability

Images, the Fiji macro, the Python script and per-field results are packaged as an RO-Crate.
