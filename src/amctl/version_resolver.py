"""Template version resolver with PyPI remote + local cache + fallback.

Resolution chain (cache‑first):

    cache enabled?
    ├─ yes → cache fresh?  → return cache
    │        └─ no → PyPI? → write cache, return pypi
    │                 └─ no → expired cache? → return + WARN
    │                         └─ no → __versions__ + WARN
    └─ no → PyPI (unless AMCTL_TMPL_NOPYPI) → return pypi
            └─ no → __versions__ + WARN

Environment variables
---------------------
AMCTL_TMPL_CACHEPATH : str | unset
    Custom cache directory path.
AMCTL_TMPL_USECACHE : "false" | unset
    Set to ``"false"`` to disable the local cache.
AMCTL_TMPL_NOPYPI : "true" | unset
    Set to ``"true"`` to block all PyPI requests (e.g. air‑gapped).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from amctl.colors import ColorLog

if TYPE_CHECKING:
    from amctl.templating import BaseTemplate

CACHE_TTL = timedelta(hours=24)


#  Version info container


@dataclass
class VersionResult:
    """The outcome of a version resolution."""

    versions: list[tuple[str, str]]  # [(version, python_requires), ...]
    source: str  # "pypi" | "cache" | "cache-expired" | "fallback"
    updated_at: str | None = None
    outdated: bool = False


#  Cache path helpers


def _cache_dir() -> Path:
    """Determine the cache directory.

    1. ``AMCTL_TMPL_CACHEPATH`` env var (highest priority).
    2. ``.venv/.amctl`` when ``.venv/`` exists under cwd.
    3. ``~/.amctl`` otherwise.
    """
    env = os.environ.get("AMCTL_TMPL_CACHEPATH")
    if env:
        return Path(env)

    venv = Path.cwd() / ".venv"
    if venv.is_dir():
        return venv / ".amctl"

    return Path.home() / ".amctl"


def cache_file_path() -> Path:
    """Return the full path to the versions cache JSON file."""
    return _cache_dir() / "versions_cache.json"


#  Cache I/O


def load_cache() -> dict[str, dict[str, Any]]:
    """Load the versions cache from disk.  Returns ``{}`` on any error."""
    path = cache_file_path()
    try:
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data  # type: ignore[return-value]
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_cache(data: dict[str, dict[str, Any]]) -> None:
    """Persist *data* to the cache file, creating parent directories."""
    path = cache_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    temp.replace(path)


def clear_cache() -> None:
    """Delete the cache file if it exists."""
    path = cache_file_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


#  Feature flags


def _cache_enabled() -> bool:
    return os.environ.get("AMCTL_TMPL_USECACHE", "").lower() != "false"


def _pypi_disabled() -> bool:
    return os.environ.get("AMCTL_TMPL_NOPYPI", "").lower() == "true"


#  PyPI helpers


def _core_package_for(template_cls: type[BaseTemplate]) -> str | None:
    """Return the PyPI package name of *template_cls*'s core dependency,
    or ``None`` if the template does not declare one."""
    pkg: str = getattr(template_cls, "__core_package__", "")
    return pkg.strip() or None


def _fetch_json(url: str, timeout: int = 15) -> dict[str, Any] | None:
    """Fetch a JSON document from *url*.  Returns ``None`` on failure.

    Logs a DEBUG‑level message on error so network issues can be
    diagnosed with ``AMCTL_LOG_LEVEL=debug``.
    """
    import traceback

    try:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "amctl (https://github.com/AmritaBot/Amctl)",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        ColorLog.debug(f"Unexpected PyPI response type: {type(data).__name__}")
    except requests.exceptions.RequestException:
        ColorLog.debug(f"PyPI fetch failed for {url}:\n{traceback.format_exc()}")
    return None


def fetch_pypi_releases(package_name: str) -> list[tuple[str, str]] | None:
    """Return a ``dict[version, python_requires]`` for *package_name*.

    Retrieves ``requires_python`` from each release artifact (first file's
    ``requires_python``), falling back to ``info.requires_python`` for
    the package as a whole.  Returns ``None`` on failure.
    """
    data = _fetch_json(f"https://pypi.org/pypi/{package_name}/json")
    if data is None:
        return None
    releases = data.get("releases")
    if not isinstance(releases, dict):
        return None
    global_py = str(data.get("info", {}).get("requires_python") or "")

    result: list[tuple[str, str]] = []
    if not releases:
        return None
    # sort newest first
    for ver in sorted(releases.keys(), reverse=True):
        py_req = ""
        files = releases.get(ver)
        if isinstance(files, list) and files:
            for f in files:
                if isinstance(f, dict):
                    pr = f.get("requires_python")
                    if pr:
                        py_req = str(pr)
                        break
        if not py_req:
            py_req = global_py
        result.append((ver, py_req))

    return result if result else None


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


def fetch_pypi_info(package_name: str) -> dict[str, Any] | None:
    """Return the full PyPI info dict for *package_name*, or ``None``."""
    return _fetch_json(f"https://pypi.org/pypi/{package_name}/json")


#  Core resolver


def resolve_versions(template_cls: type[BaseTemplate]) -> VersionResult:
    """Resolve available versions for a template class using the
    cache‑first fallback chain.

    Returns a :class:`VersionResult` whose ``outdated`` flag is ``True``
    when the returned data may be stale (expired cache or hardcoded fallback).
    """
    name = template_cls.__template_name__
    pypi_pkg = _core_package_for(template_cls)
    now = datetime.now(timezone.utc)

    # cache path
    if _cache_enabled():
        cached = load_cache().get(name)
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
        if pypi_pkg and not _pypi_disabled():
            pypi_versions = fetch_pypi_releases(pypi_pkg)
            if pypi_versions:
                # write back to cache as dict (JSON-friendly)
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

    else:
        # cache disabled — still try PyPI unless explicitly blocked
        if pypi_pkg and not _pypi_disabled():
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
