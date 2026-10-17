"""Registry of domain profiles.

``generic`` works for any workflow (files, formats, table columns); ``bioimaging`` adds calibration-aware checks;
``timeseries`` reads sampling rates and channels. Other fields (genomics, single-cell, spatial omics) are sketched
in docs/domain-profiles.md. Every profile uses the same scaling rules (``base.rescale``).
"""

from __future__ import annotations

from .base import FAIL, OK, WARN, Adjustment, CheckItem, CheckReport, DomainProfile
from .bioimaging import BioimagingProfile
from .generic import GenericProfile
from .timeseries import TimeseriesProfile

PROFILES: dict[str, type[DomainProfile]] = {
    BioimagingProfile.name: BioimagingProfile,
    GenericProfile.name: GenericProfile,
    TimeseriesProfile.name: TimeseriesProfile,
}


def get_profile(name: str) -> DomainProfile:
    """A known profile, or the generic one for any other field (it checks presence and format only)."""
    return PROFILES.get(name or "generic", GenericProfile)()


__all__ = [
    "OK", "WARN", "FAIL", "Adjustment", "CheckItem", "CheckReport", "DomainProfile",
    "BioimagingProfile", "GenericProfile", "TimeseriesProfile", "PROFILES", "get_profile",
]
