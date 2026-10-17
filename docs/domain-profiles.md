# Domain profiles

A domain profile lists, for one field, the properties data must share with a method's original data for the reuse to be fair, where to read them, and what to do on a mismatch (adjust, warn, or stop and ask).

| Field | What must match | Typical silent error | Status |
| --- | --- | --- | --- |
| Bioimaging | Pixel size and unit, channels, 2D/3D, consistent calibration across files | Size thresholds off by the scaling factor | Implemented |
| Genomics | Reference genome build, coordinate convention (0/1-based) | GRCh37 positions run against GRCh38 | Sketch |
| Single-cell | Raw vs normalised counts, species, gene ID type | Normalising data that is already normalised | Sketch |
| Spatial omics | Coordinate units, spot vs cell resolution | Neighbourhood radius at the wrong scale | Sketch |
| Time series | Sampling rate, units | Filters tuned to a different frequency | Sketch |

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

Register it in `recrate/profiles/__init__.py` and declare it in the crate with the root property `domain profile`.

## Bioimaging profile details

* Reads calibration from OME-TIFF (`PhysicalSizeX`, `PhysicalSizeXUnit`) or ImageJ TIFF tags (`XResolution` + ImageJ `unit`). DPI-style resolution in inches or centimetres is treated as no calibration.
* Units are normalised (`um`, `micron`, `µm` → µm).
* Pixel size within 5 % counts as matching.
* Mixed pixel sizes in one folder fail: split them into consistent sets.
* Missing calibration fails: size-based results cannot be compared.
* Parameters with `scaling = inverse_pixel_area` are multiplied by (paper pixel size / your pixel size)²; `inverse_pixel_size` by the plain ratio.

Next steps: read OME-Zarr (local and remote) and OMERO images in place; compare Fiji and plugin versions with those declared in the crate; compare masks against the paper's (Dice/IoU) when the crate includes them.
