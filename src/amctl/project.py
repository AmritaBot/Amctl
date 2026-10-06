"""Project metadata helpers — read-only TOML access for
``[tool.amctl.project]`` and ``[tool.amctl.scripts]`` sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Matches the leading name of a PEP 508 requirement string.
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

#: Matches a ``name = "..."`` entry in ``uv.lock``.
_LOCK_NAME = re.compile(r'^name\s*=\s*"([^"]+)"', re.MULTILINE)


def normalize_name(name: str) -> str:
    """Normalize a distribution name following PEP 503.

    Args:
        name: Raw distribution name.

    Returns:
        Lowercase name with runs of ``-``, ``_`` and ``.`` collapsed to ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def find_pyproject(start_dir: Path | None = None) -> Path | None:
    """Walk up from *start_dir* (default cwd) looking for ``pyproject.toml``.

    Args:
        start_dir: Directory to start searching from (default: cwd).

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


def _find_pyproject(start_dir: Path | None = None) -> Path | None:
    """Deprecated alias of :func:`find_pyproject`."""
    return find_pyproject(start_dir)


def _load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file using :mod:`tomli`."""
    import tomli

    with open(path, "rb") as f:
        return tomli.load(f)


def _amctl_section(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``[tool.amctl]`` table of a parsed pyproject, if any."""
    tools = data.get("tool")
    if not isinstance(tools, dict):
        return None
    section = tools.get("amctl")
    return section if isinstance(section, dict) else None


def read_project_meta(
    start_dir: Path | None = None,
) -> dict[str, str] | None:
    """Read ``[tool.amctl.project]`` from the nearest ``pyproject.toml``.

    This only reports what the marker section literally says.  Use
    :func:`detect_project` when the marker may be absent but the project is
    still managed by amctl.

    Args:
        start_dir: Directory to start searching from (default: cwd).

    Returns:
        A dict with keys ``"project-type"`` and ``"version"`` if the section
        exists, otherwise ``None``.
    """
    pyproject = find_pyproject(start_dir)
    if pyproject is None:
        return None

    section = _amctl_section(_load_toml(pyproject))
    if section is None:
        return None
    project = section.get("project")
    if not isinstance(project, dict):
        return None
    return {str(k): str(v) for k, v in project.items()}


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
    pyproject = find_pyproject(start_dir)
    if pyproject is None:
        return {}

    section = _amctl_section(_load_toml(pyproject))
    if section is None:
        return {}
    scripts = section.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items()}


def declared_dependencies(
    start_dir: Path | None = None,
) -> set[str]:
    """Collect the normalized names of every dependency a project declares.

    Covers ``[project.dependencies]``, ``[project.optional-dependencies]``,
    ``[dependency-groups]`` and ``[tool.uv].dev-dependencies``.

    Args:
        start_dir: Directory to start searching from (default: cwd).

    Returns:
        A set of PEP 503 normalized distribution names.
    """
    pyproject = find_pyproject(start_dir)
    if pyproject is None:
        return set()

    data = _load_toml(pyproject)
    specs: list[str] = []

    project = data.get("project")
    if isinstance(project, dict):
        specs.extend(_as_str_list(project.get("dependencies")))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                specs.extend(_as_str_list(group))

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group in groups.values():
            specs.extend(_as_str_list(group))
            if isinstance(group, list):
                specs.extend(
                    str(item["include-group"])
                    for item in group
                    if isinstance(item, dict) and "include-group" in item
                )

    tools = data.get("tool")
    if isinstance(tools, dict):
        uv = tools.get("uv")
        if isinstance(uv, dict):
            specs.extend(_as_str_list(uv.get("dev-dependencies")))

    names: set[str] = set()
    for spec in specs:
        match = _REQUIREMENT_NAME.match(spec)
        if match:
            names.add(normalize_name(match.group(1)))
    return names


def _as_str_list(value: Any) -> list[str]:
    """Coerce a TOML array into a list of strings, ignoring other types."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _lock_pins_amctl(root: Path) -> bool:
    """Report whether ``uv.lock`` next to *root* pins the ``amctl`` package."""
    lock = root / "uv.lock"
    if not lock.is_file():
        return False
    try:
        text = lock.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(normalize_name(m) == "amctl" for m in _LOCK_NAME.findall(text))


@dataclass(frozen=True)
class ProjectInfo:
    """What amctl could work out about the current project.

    Attributes:
        project_type: Template name, or ``""`` when it could not be determined.
        version: Project version, or ``""`` when absent.
        source: ``"marker"`` when read from ``[tool.amctl.project]``,
            ``"inferred"`` when deduced from other evidence.
        root: Directory holding the ``pyproject.toml``.
    """

    project_type: str
    version: str
    source: str
    root: Path


def _infer_project_type(deps: set[str]) -> str:
    """Guess the template a project came from, from its declared dependencies.

    Only a single unambiguous match counts.  The self-referential
    ``amctl_template`` is skipped: every project that depends on amctl would
    otherwise match it, and projects generated from that template always carry
    the marker anyway.

    Args:
        deps: Normalized names of the project's declared dependencies.

    Returns:
        Template name, or ``""`` when the guess would not be unique.
    """
    if not deps:
        return ""
    try:
        from amctl.templating import TemplateManager
    except Exception:
        return ""

    matches: list[str] = []
    for name, templ_cls in TemplateManager().get_templs().items():
        core = normalize_name(str(getattr(templ_cls, "__core_package__", "") or ""))
        if not core or core == "amctl":
            continue
        if core in deps:
            matches.append(name)
    return matches[0] if len(matches) == 1 else ""


def detect_project(
    start_dir: Path | None = None,
) -> ProjectInfo | None:
    """Work out whether the current directory belongs to an amctl project.

    The ``[tool.amctl.project]`` marker wins when present.  Without it a
    project still counts as amctl-managed if it declares ``amctl`` as a
    dependency, defines ``[tool.amctl.scripts]``, or has a ``uv.lock`` that
    pins amctl.

    Args:
        start_dir: Directory to start searching from (default: cwd).

    Returns:
        A :class:`ProjectInfo`, or ``None`` when nothing points at amctl.
    """
    pyproject = find_pyproject(start_dir)
    if pyproject is None:
        return None

    meta = read_project_meta(start_dir)
    if meta is not None:
        return ProjectInfo(
            project_type=meta.get("project-type", ""),
            version=meta.get("version", ""),
            source="marker",
            root=pyproject.parent,
        )

    deps = declared_dependencies(start_dir)
    has_scripts = bool(read_project_scripts(start_dir))
    if (
        "amctl" not in deps
        and not has_scripts
        and not _lock_pins_amctl(pyproject.parent)
    ):
        return None

    data = _load_toml(pyproject)
    project = data.get("project")
    version = ""
    if isinstance(project, dict) and project.get("version") is not None:
        version = str(project["version"])

    return ProjectInfo(
        project_type=_infer_project_type(deps),
        version=version,
        source="inferred",
        root=pyproject.parent,
    )
