"""Template system for amctl — :class:`BaseTemplate` and :class:`TemplateManager`.

Templates are discovered via :mod:`amctl.templ` sub-packages and registered
automatically via ``__init_subclass__``.  Each template declares its metadata
(name, description, versions, custom fields) through class-level dunder
attributes and hook methods.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, ClassVar

import click

from amctl.colors import ColorLog

if TYPE_CHECKING:
    from amctl.version_resolver import VersionResult


#  Field descriptor


class TmplField:
    """Descriptor for a user-facing template field.

    Attributes:
        default: Default value (``None`` means no default).
        type: Callable for type-coercion (e.g. ``int``, ``str``, ``float``).
        description: Human-readable description for ``--help`` / prompts.
        required: If ``True``, the CLI will prompt when the value is missing.
        choices: Optional list of accepted values.
    """

    __slots__ = ("choices", "default", "description", "required", "type")

    def __init__(
        self,
        *,
        default: Any = None,
        type: Callable[[Any], Any] = str,
        description: str = "",
        required: bool = False,
        choices: list[str] | None = None,
    ) -> None:
        self.default = default
        self.type = type
        self.description = description
        self.required = required
        self.choices = choices


# convenience factory (reads like ``dataclasses.field()``)
def field(
    *,
    default: Any = None,
    type: Callable[[Any], Any] = str,
    description: str = "",
    required: bool = False,
    choices: list[str] | None = None,
) -> TmplField:
    """Create a :class:`TmplField` with the given parameters."""
    return TmplField(
        default=default,
        type=type,
        description=description,
        required=required,
        choices=choices,
    )


#  BaseTemplate


class BaseTemplate(ABC):
    """Base class for all project templates.

    Subclasses **must** define :attr:`__template_name__`.  They are
    automatically registered with the singleton :class:`TemplateManager`
    via ``__init_subclass__`` (unless ``__abstract__`` or
    ``__no_register__`` is set).

    Class attributes
    ----------------
    __template_name__ : str
        Unique template identifier (e.g. ``"amrita_core"``).
    __template_description__ : str
        Short description shown in ``amctl list`` / ``amctl info``.
    __versions__ : tuple[str, ...]
        **Hardcoded fallback** — used only when cache and PyPI are both
        unavailable.  The version resolver (cache → PyPI → fallback) is
        the primary source; this attribute is the last resort.
    __tmpl_fields__ : dict[str, TmplField]
        User‑facing fields that ``amctl create`` exposes as ``--key=val``.
    __override__ : bool
        Allow overriding a previously registered template with the same name.
    __abstract__ : bool
        If ``True`` the class is treated as abstract and not registered.
    __no_register__ : bool
        If ``True`` the class is skipped during registration.
    __pypi_package__ : str
        PyPI package name of the template's **core dependency**
        (e.g. ``"amrita-core"``).  The version resolver uses this to
        discover available versions from PyPI.  Leave empty (the
        default) when the template has no external core library.
    __python_requires__ : str
        Template‑level Python version constraint (e.g. ``">=3.11"``).
        Intersected with each version's ``requires_python`` to find
        a compatible pair.  Leave empty for no constraint.
    module : ClassVar[ModuleType]
        Set automatically by the importer to the sub‑package module.
    """

    __template_name__: str
    __template_description__: str = ""
    __versions__: tuple[str, ...] = ()
    __tmpl_fields__: ClassVar[dict[str, TmplField]] = {}
    __override__: bool = False
    __abstract__: bool = False
    __no_register__: bool = False
    __core_package__: str = ""
    __python_requires__: str = ""
    module: ClassVar[ModuleType]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if not getattr(cls, "__abstract__", False) and not getattr(
            cls, "__no_register__", False
        ):
            TemplateManager().register_templ(cls)

    # identity

    @classmethod
    def get_template_name(cls) -> str:
        """Return the template's unique name."""
        return cls.__template_name__

    @classmethod
    def get_latest_version(cls) -> str | None:
        """Return the newest available version, or ``None``.

        Uses the version resolver (cache → PyPI → hardcoded fallback).
        """
        from amctl.version_resolver import resolve_versions

        result = resolve_versions(cls)
        return result.versions[0][0] if result.versions else None

    @classmethod
    def get_available_versions(cls) -> "VersionResult":
        """Return the full version resolution result.

        See :func:`amctl.version_resolver.resolve_versions` for details.
        """
        from amctl.version_resolver import resolve_versions

        return resolve_versions(cls)

    # filesystem helpers

    @classmethod
    def get_template_dir(cls, version: str | None = None) -> Path:
        """Return the on‑disk template directory for *version*.

        If *version* is given and a ``v{version}`` sub‑directory exists
        underneath the template's module directory, that sub‑directory
        is returned.  Otherwise the module directory itself is returned.
        """
        base = Path(getattr(cls.module, "__file__", "")).resolve().parent
        if version is not None:
            version_dir = base / f"v{version}"
            if version_dir.is_dir():
                return version_dir
        return base

    @classmethod
    def get_expected_files(cls, version: str | None = None) -> list[str]:
        """Return a list of relative paths (strings) that this template
        is expected to produce for the given *version*.

        The default implementation walks :meth:`get_template_dir` and
        collects every file (stripping ``.tmpl`` suffix), skipping
        Python module files (``__init__.py``, ``*.pyc``).
        Override to customise.
        """
        tmpl_dir = cls.get_template_dir(version)
        files: list[str] = []
        for item in sorted(tmpl_dir.rglob("*")):
            if item.name == "__pycache__":
                continue
            if item.is_dir():
                continue
            if item.name == "__init__.py" or item.name.endswith(".pyc"):
                continue
            rel = item.relative_to(tmpl_dir)
            name = str(rel)
            if name.endswith(".tmpl"):
                name = name[:-5]
            files.append(name)
        return files

    # lifecycle hooks

    def _build_context(
        self, name: str, version: str | None, **fields: Any
    ) -> dict[str, Any]:
        """Assemble the Jinja2 context dict for rendering.

        Subclasses may override to control which fields are passed
        to the template engine.
        """
        ctx: dict[str, Any] = {
            "name": name,
            "version": version or "",
            "project_type": self.__template_name__,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        ctx.update(fields)
        return ctx

    def on_create(
        self,
        project_dir: Path,
        name: str,
        version: str | None = None,
        **fields: Any,
    ) -> None:
        """Create a new project from this template.

        The default implementation renders the common template directory
        first, then overlays version‑specific files (if a version
        sub‑directory exists).

        Args:
            project_dir: Target project directory.
            name: Project name.
            version: Template version (``None`` = latest).
            **fields: Additional field values from ``--field=val`` CLI args.
        """
        from amctl.renderer import TemplateRenderer

        renderer = TemplateRenderer()
        ctx = self._build_context(name, version, **fields)

        # 1) render common (all‑version) files
        common_dir = self.get_template_dir()
        renderer.render_dir(common_dir, project_dir, ctx)

        # 2) overlay version‑specific files
        if version is not None:
            ver_dir = self.get_template_dir(version)
            if ver_dir != common_dir:
                renderer.render_dir(ver_dir, project_dir, ctx)

    # CLI extension points

    def get_tmpl_commands(self) -> dict[str, click.Command]:
        """Return sub‑commands registered under ``amctl tmpl <type>``.

        Override in subclasses to expose template‑specific CLI commands.
        """
        return {}


#  TemplateManager (singleton)


class TemplateManager:
    """Singleton registry of all known template classes."""

    __instance = None
    __inited = False
    _templ_class: ClassVar[dict[str, type[BaseTemplate]]]

    def __new__(cls) -> "TemplateManager":  # noqa: PYI034
        if cls.__instance is None:
            cls._templ_class = {}
            cls.__instance = super().__new__(cls)

        return cls.__instance

    def __init__(self) -> None:
        if not TemplateManager.__inited:
            super().__init__()
            TemplateManager.__inited = True

    # query

    def get_templs(self) -> dict[str, type[BaseTemplate]]:
        """Return a copy of all registered template classes."""
        return self._templ_class

    def safe_get_templ(self, tmpl_name: str) -> type[BaseTemplate] | None:
        """Return the template class for *tmpl_name*, or ``None``."""
        return self._templ_class.get(tmpl_name)

    def get_templ(self, tmpl_name: str) -> type[BaseTemplate]:
        """Return the template class for *tmpl_name*.

        Raises:
            ValueError: If no such template is registered.
        """
        if tmpl_name not in self._templ_class:
            raise ValueError(f"No template found for '{tmpl_name}'")
        return self._templ_class[tmpl_name]

    # registration

    def register_templ(self, templ: type[BaseTemplate]) -> None:
        """Register a template class."""
        tmpl_name = templ.get_template_name()
        override = getattr(templ, "__override__", False)

        if not isinstance(tmpl_name, str):
            return

        existing = self._templ_class.get(tmpl_name)
        if existing is not None:
            if not override:
                return ColorLog.error(
                    f"Project template '{tmpl_name}' at {templ.__module__} is already registered "
                    f"by {existing.__name__}, it will be skipped. "
                )
            ColorLog.warn(
                f"Template '{tmpl_name}' overridden: "
                f"{existing.__name__} → {templ.__name__}"
            )

        self._templ_class[tmpl_name] = templ
