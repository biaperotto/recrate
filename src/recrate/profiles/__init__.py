"""Registry of domain profiles.

``generic`` works for any workflow; ``bioimaging`` adds calibration-aware checks. Other fields (genomics, single-cell,
spatial omics, time series) are sketched in docs/domain-profiles.md.
"""

from __future__ import annotations

from .base import FAIL, OK, WARN, Adjustment, CheckItem, CheckReport, DomainProfile
from .bioimaging import BioimagingProfile
from .generic import GenericProfile

PROFILES: dict[str, type[DomainProfile]] = {
    BioimagingProfile.name: BioimagingProfile,
    GenericProfile.name: GenericProfile,
}


def get_profile(name: str) -> DomainProfile:
    """A known profile, or the generic one for any other field (it checks presence and format only)."""
    return PROFILES.get(name or "generic", GenericProfile)()


__all__ = [
    "OK", "WARN", "FAIL", "Adjustment", "CheckItem", "CheckReport", "DomainProfile",
    "BioimagingProfile", "GenericProfile", "PROFILES", "get_profile",
]
