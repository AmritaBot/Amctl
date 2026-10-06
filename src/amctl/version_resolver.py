"""Template version resolver built on :mod:`amctl.pypi_cache`.

Resolution chain (cache-first):

    cache enabled?
    ├─ yes → cache fresh?  → return cache
    │        └─ no → PyPI? → write cache, return pypi
    │                 └─ no → expired cache? → return + WARN
    │                         └─ no → __versions__ + WARN
    └─ no → PyPI (unless AMCTL_PYPI_NOPYPI) → return pypi
            └─ no → __versions__ + WARN

The cache and the PyPI plumbing live in :mod:`amctl.pypi_cache`; this module
keeps only the template-specific parts and re-exports the shared helpers so
existing imports keep working.

Environment variables
---------------------
AMCTL_TMPL_CACHEPATH : str | unset
    Legacy alias of ``AMCTL_PYPI_CACHEPATH``.
AMCTL_PYPI_USECACHE : "false" | unset
    Set to ``"false"`` to disable the local cache.
AMCTL_PYPI_NOPYPI : "true" | unset
    Set to ``"true"`` to block all PyPI requests (e.g. air‑gapped).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from amctl.colors import ColorLog
from amctl.pypi_cache import (
    CACHE_TTL,
    cache_enabled,
    cache_file_path,
    clear_cache,
    fetch_pypi_info,
    fetch_pypi_releases,
    load_cache,
    pypi_disabled,
    save_cache,
)

if TYPE_CHECKING:
    from amctl.templating import BaseTemplate

__all__ = [
    "CACHE_TTL",
    "VersionResult",
    "cache_file_path",
    "clear_cache",
    "fetch_pypi_info",
    "fetch_pypi_releases",
    "load_cache",
    "resolve_versions",
    "save_cache",
]


@dataclass
class VersionResult:
    """The outcome of a version resolution."""

    versions: list[tuple[str, str]]  # [(version, python_requires), ...]
    source: str  # "pypi" | "cache" | "cache-expired" | "fallback"
    updated_at: str | None = None
    outdated: bool = False


def _core_package_for(template_cls: type[BaseTemplate]) -> str | None:
    """Return the PyPI package name of *template_cls*'s core dependency,
    or ``None`` if the template does not declare one."""
    pkg: str = getattr(template_cls, "__core_package__", "")
    return pkg.strip() or None


def _intersect_python(*constraints: str) -> str | None:
    """Compute the intersection of Python version constraints.

    Only handles ``>=``, ``<=``, ``<``, ``>`` with pairwise bounds.
    Returns ``None`` when constraints are unresolvable.
    """
    lo = None
    hi = None

    for c in constraints:
        c = c.strip()
        if not c:
            continue
        for part in c.split(","):
            part = part.strip()
            if part.startswith(">="):
                v = _parse_pyver(part[2:].strip())
                if v is not None:
                    lo = v if lo is None else max(lo, v)
            elif part.startswith("<="):
                v = _parse_pyver(part[2:].strip())
                if v is not None:
                    hi = v if hi is None else min(hi, v)
            elif part.startswith(">"):
                v = _parse_pyver(part[1:].strip())
                if v is not None:
                    lo = v if lo is None else max(lo, v)
            elif part.startswith("<"):
                v = _parse_pyver(part[1:].strip())
                if v is not None:
                    hi = v if hi is None else min(hi, v - 0.01)

    if lo is None:
        return None
    if hi is not None and lo > hi:
        return None

    result = f">={lo:.1f}"
    if hi is not None:
        result += f",<{hi:.1f}"
    return result


def _parse_pyver(s: str) -> float | None:
    """Parse a Python version string like ``3.10`` into a float."""
    try:
        return float(s)
    except ValueError:
        return None


def resolve_versions(template_cls: type[BaseTemplate]) -> VersionResult:
    """Resolve available versions for a template class using the
    cache‑first fallback chain.

    Returns a :class:`VersionResult` whose ``outdated`` flag is ``True``
    when the returned data may be stale (expired cache or hardcoded fallback).
    """
    name = template_cls.__template_name__
    pypi_pkg = _core_package_for(template_cls)
    now = datetime.now(timezone.utc)

    cached: dict[str, Any] | None = None
    if cache_enabled():
        entry = load_cache().get(name)
        cached = entry if isinstance(entry, dict) else None
        if cached:
            cached_versions = cached.get("versions")
            if isinstance(cached_versions, dict):
                updated_raw = cached.get("updated_at", "")
                try:
                    updated_at = datetime.fromisoformat(updated_raw)
                    if now - updated_at < CACHE_TTL:
                        return VersionResult(
                            versions=[
                                (str(k), str(v)) for k, v in cached_versions.items()
                            ],
                            source="cache",
                            updated_at=updated_raw,
                            outdated=False,
                        )
                except (ValueError, TypeError):
                    pass  # parse failure → treat as expired

        # cache expired or missing → try PyPI
        if pypi_pkg and not pypi_disabled():
            pypi_versions = fetch_pypi_releases(pypi_pkg)
            if pypi_versions:
                data = load_cache()
                data[name] = {
                    "versions": dict(pypi_versions),
                    "updated_at": now.isoformat(),
                }
                save_cache(data)
                return VersionResult(
                    versions=pypi_versions,
                    source="pypi",
                    updated_at=now.isoformat(),
                    outdated=False,
                )

        # PyPI unreachable — use expired cache if available
        if cached:
            cached_versions = cached.get("versions")
            if isinstance(cached_versions, dict):
                ColorLog.warn(
                    f"Template '{name}' versions may be outdated.  "
                    f"Use 'amctl self cache fresh' to update or "
                    f"'--force-version' to pin."
                )
                return VersionResult(
                    versions=[(str(k), str(v)) for k, v in cached_versions.items()],
                    source="cache-expired",
                    updated_at=cached.get("updated_at"),
                    outdated=True,
                )

    elif pypi_pkg and not pypi_disabled():
        # cache disabled — still try PyPI unless explicitly blocked
        pypi_versions = fetch_pypi_releases(pypi_pkg)
        if pypi_versions:
            return VersionResult(
                versions=pypi_versions,
                source="pypi",
                updated_at=now.isoformat(),
                outdated=False,
            )

    # ultimate fallback: __versions__
    hardcoded = list(template_cls.__versions__) if template_cls.__versions__ else []
    fallback: list[tuple[str, str]] = [(v, "") for v in hardcoded]
    if hardcoded:
        ColorLog.warn(
            f"Template '{name}' versions may be outdated "
            f"(using hardcoded fallback).  "
            f"Use 'amctl self cache fresh' to update or "
            f"'--force-version' to pin."
        )
    return VersionResult(
        versions=fallback,
        source="fallback",
        updated_at=None,
        outdated=bool(hardcoded),
    )
