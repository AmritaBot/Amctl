"""Jinja2 template rendering engine for amctl.

Uses :mod:`jinja2` to render ``.tmpl`` files into their final form,
automatically stripping the ``.tmpl`` suffix from output paths.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from amctl.colors import ColorLog


class TemplateRenderer:
    """Render ``.tmpl`` files using Jinja2.

    Usage::

        renderer = TemplateRenderer()
        renderer.render_file(src, dst, {"name": "myproject"})
        renderer.render_dir(template_dir, output_dir, {"name": "myproject"})
    """

    def __init__(self) -> None:
        self._env: Environment | None = None

    def _get_env(self, search_path: str) -> Environment:
        """Return a Jinja2 ``Environment`` with a ``FileSystemLoader`` for
        *search_path*.  Environments are cached per search path.
        """
        if self._env is not None:
            loader = self._env.loader
            assert isinstance(loader, FileSystemLoader)
            if str(loader.searchpath) == str(search_path):
                return self._env
        self._env = Environment(
            loader=FileSystemLoader(str(search_path)),
            keep_trailing_newline=True,
        )
        return self._env

    def _strip_tmpl(self, path: Path) -> Path:
        """Remove ``.tmpl`` suffix from *path*."""
        if path.suffix == ".tmpl":
            return path.with_suffix("")
        return path

    def render_file(
        self,
        src: Path,
        dst: Path,
        context: dict[str, Any],
    ) -> Path:
        """Render a single Jinja2 template file.

        Args:
            src: Source ``.tmpl`` file path.
            dst: Destination path (``.tmpl`` suffix is stripped automatically).
            context: Jinja2 template context variables.

        Returns:
            The written destination path.
        """
        dst = self._strip_tmpl(dst)
        search_path = str(src.parent)
        env = self._get_env(search_path)
        try:
            template = env.get_template(src.name)
        except TemplateNotFound:
            # If the template file is not found by the loader, just copy it
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            ColorLog.info(f"  Copied {dst}")
            return dst
        rendered = template.render(**context)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(rendered, encoding="utf-8")
        ColorLog.info(f"  Generated {dst}")
        return dst

    def _render_name(self, name: str, context: dict[str, Any]) -> str:
        """Render a file or directory *name* through Jinja2 if it contains
        ``{{`` / ``{%`` markers.
        """
        if "{{" in name or "{%" in name:
            from jinja2 import BaseLoader, Environment

            tpl = Environment(loader=BaseLoader()).from_string(name)
            return tpl.render(**context)
        return name

    def render_dir(
        self,
        src_dir: Path,
        dst_dir: Path,
        context: dict[str, Any],
    ) -> list[Path]:
        """Recursively render all files in *src_dir* into *dst_dir*.

        Files ending with ``.tmpl`` are rendered as Jinja2 templates and
        written without the ``.tmpl`` suffix.  All other files are copied
        verbatim.  Directory names containing Jinja2 markers (``{{ }}``)
        are also rendered.

        Args:
            src_dir: Source template directory.
            dst_dir: Destination directory.
            context: Jinja2 template context variables.

        Returns:
            List of generated/copied file paths (relative to *dst_dir*).
        """
        result: list[Path] = []
        # Walk with os.walk for Python 3.10 compat
        for root_str, _dirs, files in os.walk(str(src_dir)):
            root = Path(root_str)
            # Skip __pycache__ dirs
            if "__pycache__" in root.parts:
                continue
            rel_root = root.relative_to(src_dir)
            dst_root = dst_dir.joinpath(
                *(self._render_name(part, context) for part in rel_root.parts)
            )
            dst_root.mkdir(parents=True, exist_ok=True)

            for fname in files:
                # Skip Python module files from the template package
                if fname == "__init__.py" or fname.endswith(".pyc"):
                    continue
                src_file = root / fname
                rendered_fname = self._render_name(fname, context)
                dst_file = dst_root / rendered_fname

                if fname.endswith(".tmpl"):
                    self.render_file(src_file, dst_file, context)
                else:
                    shutil.copy2(src_file, dst_file)
                    ColorLog.info(f"  Copied {dst_file}")
                result.append(dst_file)

        return result
