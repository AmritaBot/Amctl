"""PyPI metadata cache.

The reusable half of :mod:`amctl.version_resolver`, which used to be tied to the
template system even though the cache itself has nothing to do with templates.
Splitting it out lets the CLI and other consumers -- AmritaBot's plugin store,
for instance -- share one implementation.

The cache is consulted first:

    cached and fresh  -> use the cache
    cached but stale  -> ask PyPI
        answered      -> write the answer back and use it
        unreachable   -> fall back to the stale entry, flagged as outdated
        nothing cached -> return nothing, the caller degrades on its own

Being out of date is not an error, it is a state worth showing::
class:`PyPIResult` carries ``source`` and ``outdated`` so the caller can decide
whether to tell the user.

Environment variables
---------------------
``AMCTL_PYPI_CACHEPATH`` : str | unset
    Cache directory.  Falls back to ``AMCTL_TMPL_CACHEPATH``, then to
    ``<cwd>/.venv/.amctl`` when ``.venv`` exists, then to ``~/.amctl``.
``AMCTL_PYPI_USECACHE`` : "false" | unset
    Set to ``"false"`` to disable the local cache.
``AMCTL_PYPI_NOPYPI`` : "true" | unset
    Set to ``"true"`` to block every PyPI request (air-gapped setups).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from amctl.colors import ColorLog

__all__ = [
    "CACHE_TTL",
    "DEFAULT_CACHE_NAME",
    "PyPIResult",
    "cache_dir",
    "cache_file_path",
    "clear_cache",
    "fetch_pypi_info",
    "fetch_pypi_releases",
    "load_cache",
    "resolve_meta",
    "save_cache",
]

CACHE_TTL = timedelta(hours=24)
DEFAULT_CACHE_NAME = "versions_cache.json"


@dataclass
class PyPIResult:
    """The outcome of one metadata lookup.

    ``source`` is one of ``"pypi"``, ``"cache"``, ``"cache-expired"`` or
    ``"miss"``.  A true ``outdated`` means the data may already be stale.
    """

    data: dict[str, Any] = field(default_factory=dict)
    source: str = "miss"
    updated_at: str | None = None
    outdated: bool = False


def cache_dir() -> Path:
    """Cache directory: ``AMCTL_PYPI_CACHEPATH`` -> ``AMCTL_TMPL_CACHEPATH``
    -> ``<cwd>/.venv/.amctl`` (when ``.venv`` exists) -> ``~/.amctl``."""
    for var in ("AMCTL_PYPI_CACHEPATH", "AMCTL_TMPL_CACHEPATH"):
        env = os.environ.get(var)
        if env:
            return Path(env)

    venv = Path.cwd() / ".venv"
    if venv.is_dir():
        return venv / ".amctl"

    return Path.home() / ".amctl"


def cache_file_path(name: str = DEFAULT_CACHE_NAME) -> Path:
    """Full path of a cache file.

    Args:
        name: File name.  Consumers that pick different names never collide --
            the CLI uses the default ``versions_cache.json``, other callers can
            pass their own.
    """
    return cache_dir() / name


def load_cache(name: str = DEFAULT_CACHE_NAME) -> dict[str, dict[str, Any]]:
    """Read the cache.

    Returns ``{}`` when the file is missing, corrupt or shaped wrong, and never
    raises.
    """
    path = cache_file_path(name)
    try:
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data  # type: ignore[return-value]
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_cache(data: dict[str, dict[str, Any]], name: str = DEFAULT_CACHE_NAME) -> None:
    """Write the cache atomically: a temporary file, then ``replace``."""
    path = cache_file_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    temp.replace(path)


def clear_cache(name: str = DEFAULT_CACHE_NAME) -> None:
    """Delete the cache file.  Silent when it does not exist."""
    try:
        cache_file_path(name).unlink(missing_ok=True)
    except OSError:
        pass


def cache_enabled() -> bool:
    """Whether the local cache is enabled."""
    return os.environ.get("AMCTL_PYPI_USECACHE", "").lower() != "false"


def pypi_disabled() -> bool:
    """Whether network requests are blocked."""
    return os.environ.get("AMCTL_PYPI_NOPYPI", "").lower() == "true"


def _fetch_json(url: str, timeout: int = 15) -> dict[str, Any] | None:
    """Fetch a JSON document.  Returns ``None`` on failure."""
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


def fetch_pypi_info(package_name: str, *, timeout: int = 15) -> dict[str, Any] | None:
    """Return PyPI's full ``info`` dict for ``package_name``, or ``None``."""
    return _fetch_json(f"https://pypi.org/pypi/{package_name}/json", timeout)


def fetch_pypi_releases(package_name: str) -> list[tuple[str, str]] | None:
    """Return ``[(version, requires_python), ...]``, newest first, or ``None``."""
    data = fetch_pypi_info(package_name)
    if data is None:
        return None
    releases = data.get("releases")
    if not isinstance(releases, dict) or not releases:
        return None
    global_py = str(data.get("info", {}).get("requires_python") or "")

    result: list[tuple[str, str]] = []
    for ver in sorted(releases.keys(), reverse=True):
        py_req = ""
        files = releases.get(ver)
        if isinstance(files, list):
            for f in files:
                if isinstance(f, dict) and f.get("requires_python"):
                    py_req = str(f["requires_python"])
                    break
        if not py_req:
            py_req = global_py
        result.append((ver, py_req))

    return result or None


def _meta_from_info(info: dict[str, Any]) -> dict[str, Any]:
    """Trim PyPI's ``info`` down to the fields we care about."""
    requires = info.get("requires_dist")
    return {
        "version": str(info.get("version") or ""),
        "desc": str(info.get("summary") or ""),
        "requires": [str(x) for x in requires] if isinstance(requires, list) else [],
        "requires_python": str(info.get("requires_python") or ""),
        "homepage": str(info.get("project_url") or info.get("home_page") or ""),
    }


def resolve_meta(
    package_name: str,
    *,
    name: str = DEFAULT_CACHE_NAME,
    ttl: timedelta = CACHE_TTL,
    timeout: int = 15,
) -> PyPIResult:
    """Resolve one distribution's metadata, consulting the cache first.

    The returned ``data`` looks like ``{"version": ..., "desc": ...,
    "requires": [...], "requires_python": ..., "homepage": ...}``.  It is empty
    when nothing could be resolved, and the caller is expected to degrade -- by
    not pinning a version, for instance.
    """
    now = datetime.now(timezone.utc)
    cached = load_cache(name).get(package_name) if cache_enabled() else None

    if isinstance(cached, dict):
        updated_raw = cached.get("updated_at", "")
        try:
            updated_at = datetime.fromisoformat(str(updated_raw))
            if now - updated_at < ttl:
                return PyPIResult(
                    data={k: v for k, v in cached.items() if k != "updated_at"},
                    source="cache",
                    updated_at=str(updated_raw),
                )
        except (ValueError, TypeError):
            pass

    if not pypi_disabled():
        info = fetch_pypi_info(package_name, timeout=timeout)
        if info is not None:
            meta = _meta_from_info(info.get("info") or {})
            if meta["version"]:
                data = load_cache(name)
                data[package_name] = {**meta, "updated_at": now.isoformat()}
                save_cache(data, name)
                return PyPIResult(data=meta, source="pypi", updated_at=now.isoformat())

    if isinstance(cached, dict):
        ColorLog.warn(
            f"Metadata for '{package_name}' may be outdated; using the cached copy."
        )
        return PyPIResult(
            data={k: v for k, v in cached.items() if k != "updated_at"},
            source="cache-expired",
            updated_at=str(cached.get("updated_at") or "") or None,
            outdated=True,
        )

    return PyPIResult(source="miss", outdated=True)
