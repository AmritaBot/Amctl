"""Project metadata helpers — read-only TOML access for
``[tool.amctl.project]`` and ``[tool.amctl.scripts]`` sections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _find_pyproject(start_dir: Path | None = None) -> Path | None:
    """Walk up from *start_dir* (default cwd) looking for ``pyproject.toml``.

    Returns:
        ``Path`` to the file or ``None`` if not found.
    """
    current = Path.cwd() if start_dir is None else start_dir.resolve()
    while True:
        candidate = current / "pyproject.toml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:  # reached filesystem root
            return None
        current = parent


def _load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file using :mod:`tomli`."""
    import tomli

    with open(path, "rb") as f:
        return tomli.load(f)


def read_project_meta(
    start_dir: Path | None = None,
) -> dict[str, str] | None:
    """Read ``[tool.amctl.project]`` from the nearest ``pyproject.toml``.

    Args:
        start_dir: Directory to start searching from (default: cwd).

    Returns:
        A dict with keys ``"project-type"``, ``"version"``, ``"created_at"``
        if the section exists, otherwise ``None``.
    """
    pyproject = _find_pyproject(start_dir)
    if pyproject is None:
        return None

    data = _load_toml(pyproject)
    tools = data.get("tool")
    if not isinstance(tools, dict):
        return None
    amctl_section = tools.get("amctl")
    if not isinstance(amctl_section, dict):
        return None
    section = amctl_section.get("project")
    if not isinstance(section, dict):
        return None
    return {str(k): str(v) for k, v in section.items()}


def read_project_scripts(
    start_dir: Path | None = None,
) -> dict[str, str]:
    """Read ``[tool.amctl.scripts]`` from the nearest ``pyproject.toml``.

    Args:
        start_dir: Directory to start searching from (default: cwd).

    Returns:
        A dict mapping script name → shell command string.  Empty dict
        if the section is not present.
    """
    pyproject = _find_pyproject(start_dir)
    if pyproject is None:
        return {}

    data = _load_toml(pyproject)
    tools = data.get("tool")
    if not isinstance(tools, dict):
        return {}
    amctl_section = tools.get("amctl")
    if not isinstance(amctl_section, dict):
        return {}
    section = amctl_section.get("scripts")
    if not isinstance(section, dict):
        return {}
    return {str(k): str(v) for k, v in section.items()}
