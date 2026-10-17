"""Registry of domain profiles.

Only the bioimaging profile is implemented. Other fields (genomics, single-cell,
spatial omics, time series) are sketched in docs/domain-profiles.md.
"""

from __future__ import annotations

from .base import FAIL, OK, WARN, Adjustment, CheckItem, CheckReport, DomainProfile
from .bioimaging import BioimagingProfile

PROFILES: dict[str, type[DomainProfile]] = {
    BioimagingProfile.name: BioimagingProfile,
}


def get_profile(name: str) -> DomainProfile:
    try:
        return PROFILES[name]()
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown domain profile {name!r}. Known profiles: {known}") from None


__all__ = [
    "OK", "WARN", "FAIL", "Adjustment", "CheckItem", "CheckReport", "DomainProfile",
    "BioimagingProfile", "PROFILES", "get_profile",
]
