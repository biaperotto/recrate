# Domain profiles

A domain profile lists, for one field, the properties data must share with a method's original data for the reuse to be fair, where to read them, and what to do on a mismatch (adjust, warn, or stop and ask).

| Field | What must match | Typical silent error | Status |
| --- | --- | --- | --- |
| Bioimaging | Pixel size and unit, channels, 2D/3D, bit depth, consistent calibration; object size after the run | Size thresholds off by the scaling factor | Implemented |
| Genomics | Reference genome build, coordinate convention (0/1-based) | GRCh37 positions run against GRCh38 | Sketch |
| Single-cell | Raw vs normalised counts, species, gene ID type | Normalising data that is already normalised | Sketch |
| Spatial omics | Coordinate units, spot vs cell resolution | Neighbourhood radius at the wrong scale | Sketch |
| Time series | Sampling rate, channels, time column | Windows in samples on data at another rate | Implemented (synthetic test) |

## Writing a profile

Subclass `recrate.profiles.base.DomainProfile`:

```python
class GenomicsProfile(DomainProfile):
    name = "genomics"

    def inspect(self, data_path):
        ...  # read the build from a VCF header (##reference=...), contig names, etc.

    def check(self, spec, data_path, params=None):
        report = CheckReport(profile=self.name, data_path=data_path)
        expected = spec.data_input.prop("reference build")   # declared in the crate
        observed = ...
        report.items.append(CheckItem("Reference build", expected, observed,
                                      OK if observed == expected else FAIL,
                                      "" if observed == expected else "Lift over first."))
        return report
```

Scaling is shared: a parameter declares `"scaling": "scales_with:<property>^<exponent>"`, and the profile calls
`base.rescale(spec, params, "<property>", yours / paper's, why)` once it has read that property from your files.

Two optional methods complete a profile:

* `diagnose_inputs(spec)`: gaps in what the crate declares, before any data is seen (shown as `ready-with-gaps`);
* `check_results(spec, results_csv, report, reference_csv, pixel_ratio)`: after a run on new data, whether the
  results look like the paper's (flagged, never blocked). The base class already compares the mean of every
  column the paper reports and warns when one is ten times larger or smaller.

Register it in `recrate/profiles/__init__.py` and declare it in the crate with the root property `domain profile`.
Nothing in the app, the CLI or the MCP server changes: they show whatever checks the profile returns.

## Bioimaging profile details

* Reads calibration from OME-TIFF (`PhysicalSizeX`, `PhysicalSizeXUnit`) or ImageJ TIFF tags (`XResolution` + ImageJ `unit`). DPI-style resolution in inches or centimetres is treated as no calibration.
* Units are normalised (`um`, `micron`, `µm` → µm).
* Pixel size within 5 % counts as matching.
* Mixed pixel sizes in one folder fail: split them into consistent sets.
* Missing calibration fails: size-based results cannot be compared.
* Parameters with `scaling = inverse_pixel_area` are multiplied by (paper pixel size / your pixel size)²; `inverse_pixel_size` by the plain ratio.
* Bit depth and intensity range are compared with the paper's own example images (a different bit depth, or a range more than 4× apart, warns).
* After a run: objects per area and mean object size are compared with a reproduction on the paper's data, in the paper's pixels (converted with the calibrations, or the pixel ratio you state). Density more than 3× apart, or object size more than 2× apart, warns.

Next steps: read OME-Zarr (local and remote) and OMERO images in place; compare Fiji and plugin versions with those declared in the crate; compare masks against the paper's (Dice/IoU) when the crate includes them.
