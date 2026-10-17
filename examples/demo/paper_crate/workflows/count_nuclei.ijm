#@ File (style="directory") input_dir
#@ File (style="directory") output_dir
#@ String (value="Otsu") threshold_method
#@ Double (value=50) min_size_px

// ReCRATE demo workflow: count nuclei in 2D single-channel fluorescence images.
// For every .tif in input_dir it thresholds the image, keeps particles of at least
// min_size_px pixels, and writes one row per image to output_dir/results.csv.
// Masks of the kept particles go to output_dir/masks/.
// Areas are reported in calibrated units (from the image's pixel size).

setBatchMode(true);
setOption("BlackBackground", true);
run("Set Measurements...", "area redirect=None decimal=4");

list = getFileList(input_dir);
maskDir = output_dir + File.separator + "masks";
File.makeDirectory(maskDir);
out = File.open(output_dir + File.separator + "results.csv");
print(out, "image,count,mean_area,area_unit,pixel_size,pixel_unit");

for (i = 0; i < list.length; i++) {
    name = list[i];
    if (!(endsWith(name, ".tif") || endsWith(name, ".tiff"))) continue;
    open(input_dir + File.separator + name);
    getPixelSize(unit, pw, ph);
    setAutoThreshold(threshold_method + " dark");
    run("Convert to Mask");
    run("Analyze Particles...", "size=" + min_size_px + "-Infinity pixel show=Masks clear");
    n = nResults;
    total = 0;
    for (r = 0; r < n; r++) total += getResult("Area", r);
    mean = 0;
    if (n > 0) mean = total / n;
    stem = replace(name, "\\.tiff?$", "");
    saveAs("Tiff", maskDir + File.separator + stem + "_mask.tif");
    print(out, name + "," + n + "," + d2s(mean, 4) + "," + unit + "^2," + d2s(pw, 6) + "," + unit);
    close("*");
}
File.close(out);
