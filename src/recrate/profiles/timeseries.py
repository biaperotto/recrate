"""Time-series profile: signals recorded as tables, one row per time point.

Reads, from each CSV file in the data folder:

* the time column (the input's ``time column`` property, or the first column), in seconds;
* the sampling rate (1 / median interval), the duration and the channels (the other numeric columns).

It compares them with what the method's crate declares (``sampling rate`` in Hz, ``channels`` as a comma-separated
list of required columns). Parameters counted in samples (a smoothing window, a minimum peak distance) declare
``"scaling": "scales_with:sampling rate^1"`` and are rescaled so they keep their duration in seconds. This is the
same silent error as a filter size in pixels on images with another pixel size: the method runs, and the answer
changes.
"""

from __future__ import annotations

import csv
from pathlib import Path
from statistics import median
from typing import Any

from ..crate import MethodSpec
from .base import FAIL, OK, WARN, CheckItem, CheckReport, DomainProfile, Field, rescale, unstated_scale

RATE_REL_TOL = 0.05


def read_signal(path: Path, time_column: str | None = None) -> dict[str, Any]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    cols = list(rows[0]) if rows else []
    tcol = time_column or (cols[0] if cols else None)
    info: dict[str, Any] = {"file": Path(path).name, "columns": cols, "time_column": tcol, "rows": len(rows),
                            "sampling_rate": None, "duration": None}
    if not rows or tcol not in cols:
        return info
    try:
        t = [float(r[tcol]) for r in rows]
    except ValueError:
        return info
    dts = [b - a for a, b in zip(t, t[1:]) if b > a]
    if dts:
        info["sampling_rate"] = 1 / median(dts)
        info["duration"] = t[-1] - t[0]
    info["channels"] = [c for c in cols if c != tcol]
    return info


class TimeseriesProfile(DomainProfile):
    name = "timeseries"
    description = "Signals as tables: time column, sampling rate, channels; window parameters rescaled in time."
    field = Field(
        label="Recordings", data_noun="recordings", scale="sampling rate", scale_check="Sampling rate",
        scale_question="How does your sampling rate compare with the research's?",
        scale_ratio_label="Your sampling rate ÷ the research's",
        scale_help="2 means you record twice as many samples per second (e.g. 200 Hz against 100 Hz).")

    def inspect(self, data_path: Path) -> dict[str, Any]:
        return {"files": [read_signal(f) for f in sorted(Path(data_path).glob("*.csv"))]}

    def diagnose_inputs(self, spec: MethodSpec) -> list[str]:
        scaled = [p.name for p in spec.value_inputs if (p.scaling_rule or ("",))[0] == "sampling rate"]
        if scaled and "sampling rate" not in spec.data_input.properties:
            return [f"The research states no sampling rate, so {', '.join(scaled)} (in samples) can't be rescaled "
                    "for data recorded at another rate."]
        return []

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None,
              scale_ratio: float | None = None) -> CheckReport:
        data_path = Path(data_path)
        params = dict(params or spec.default_params())
        report = CheckReport(profile=self.name, data_path=data_path)
        d = spec.data_input
        files = sorted(data_path.glob("*.csv")) if data_path.is_dir() else [data_path]
        if not files:
            report.items.append(CheckItem("Signal files", "≥1 CSV", 0, FAIL, "No .csv files in this folder."))
            return report
        infos = [read_signal(f, d.prop("time column")) for f in files]
        report.observed = {"files": infos}
        report.items.append(CheckItem("Signal files", "≥1 CSV", len(files), OK))

        bad = [i["file"] for i in infos if i["sampling_rate"] is None]
        if bad:
            report.items.append(CheckItem("Time column", d.prop("time column") or "first column", "missing or not "
                                          "increasing", FAIL, f"No usable time column in: {', '.join(bad[:3])}."))
            return report
        report.items.append(CheckItem("Time column", d.prop("time column") or "first column", infos[0]["time_column"], OK))

        need = [c.strip() for c in str(d.prop("channels") or "").split(",") if c.strip()]
        if need:
            missing = sorted({c for i in infos for c in need if c not in i["columns"]})
            report.items.append(CheckItem("Channels", ", ".join(need), ", ".join(infos[0]["channels"]),
                                          FAIL if missing else OK,
                                          f"Missing: {', '.join(missing)}. Rename your columns or map them." if missing else ""))

        rates = [i["sampling_rate"] for i in infos]
        lo, hi = min(rates), max(rates)
        if hi - lo > RATE_REL_TOL * lo:
            report.items.append(CheckItem("Sampling rate", "one rate", f"{lo:g}–{hi:g} Hz", FAIL,
                                          "Files have different sampling rates: split them into consistent sets."))
            return report
        obs = sum(rates) / len(rates)
        exp = d.properties.get("sampling rate")
        if exp is None:
            unstated_scale(spec, params, report, self.field, scale_ratio, observed=f"{obs:g} Hz",
                           same_tol=RATE_REL_TOL)
        else:
            exp_hz = float(exp.value)
            if abs(obs - exp_hz) <= RATE_REL_TOL * exp_hz:
                report.items.append(CheckItem("Sampling rate", f"{exp_hz:g} Hz", f"{obs:g} Hz", OK))
            else:
                adj = rescale(spec, params, "sampling rate", obs / exp_hz,
                              f"sampling rate {obs:g} Hz vs {exp_hz:g} Hz in the paper")
                report.adjustments.extend(adj)
                report.items.append(CheckItem("Sampling rate", f"{exp_hz:g} Hz", f"{obs:g} Hz", WARN,
                                              "; ".join(f"{a.parameter} {a.old:g} → {a.new:g}" for a in adj)
                                              or "No parameter declares how it scales; results may not be comparable."))
        dur = sum(i["duration"] for i in infos) / len(infos)
        report.items.append(CheckItem("Duration", "", f"{dur:.4g} s per file", OK))
        return report
