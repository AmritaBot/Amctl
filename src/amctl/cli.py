"""CLI entry points for amctl.

Commands
--------
create    – Scaffold a new project from a template.
list      – List available templates.
info      – Show detailed information about a template.
tmpl      – Template‑specific sub‑commands.
man       – Run project scripts from ``[tools.amctl.scripts]``.
fix       – Restore missing / corrupted project files.
self      – Manage amctl itself (cache, template updates).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import click
import colorama

from amctl.colors import ColorLog
from amctl.license import LICENSES
from amctl.project import read_project_meta, read_project_scripts
from amctl.templating import TemplateManager
from amctl.uv_util import UvOperator
from amctl.version_resolver import (
    _core_package_for,
    _intersect_python,
    cache_file_path,
    clear_cache,
    fetch_pypi_info,
    fetch_pypi_releases,
    load_cache,
    resolve_versions,
    save_cache,
)

#  Helpers


def _parse_dynamic_fields(ctx: click.Context) -> dict[str, Any]:
    """Parse ``--key=val`` / ``--key val`` / ``--flag`` from ``ctx.args``.

    Returns a dict of field_name → value.  Boolean flags become ``True``.
    """
    fields: dict[str, Any] = {}
    args = list(ctx.args)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                key, val = key.split("=", 1)
                fields[key] = val
                i += 1
            else:
                # look ahead: next token is a value if it doesn't start with --
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    fields[key] = args[i + 1]
                    i += 2
                else:
                    fields[key] = True  # bare --flag
                    i += 1
        else:
            i += 1
    return fields


def _pick_python(constraint: str) -> str | None:
    """Pick a Python version matching *constraint* (e.g. ``>=3.10,<3.12``).

    Uses ``uv python list`` to find the highest available version within
    the range.  Returns a string like ``"3.11"`` or ``None``.
    """
    try:
        out = subprocess.run(
            ["uv", "python", "list", "--only-installed"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    best = None
    best_ver = 0.0
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line.startswith("cpython-"):
            continue
        # cpython-3.11.14-linux-x86_64-gnu  →  3.11
        parts = line.split("-")
        if len(parts) < 2:
            continue
        try:
            ver = float(parts[1])
        except ValueError:
            continue
        if _pyver_matches(ver, constraint) and ver > best_ver:
            best_ver = ver
            best = parts[1]
    return best


def _pyver_matches(ver: float, constraint: str) -> bool:
    """Check if *ver* (e.g. 3.11) satisfies *constraint* (e.g. ``>=3.10,<3.12``)."""
    if not constraint:
        return True
    for part in constraint.split(","):
        part = part.strip()
        if part.startswith(">="):
            try:
                if ver < float(part[2:]):
                    return False
            except ValueError:
                pass
        elif part.startswith("<="):
            try:
                if ver > float(part[2:]):
                    return False
            except ValueError:
                pass
        elif part.startswith(">"):
            try:
                if ver <= float(part[1:]):
                    return False
            except ValueError:
                pass
        elif part.startswith("<"):
            try:
                if ver >= float(part[1:]):
                    return False
            except ValueError:
                pass
    return True


def _choose_version(
    templ_cls: type,
    frozen: bool = False,
) -> tuple[str | None, str | None]:
    """Fold‑UX version picker + Python constraint resolution.

    Shows a compact summary.  Typing ``?`` expands the full list.
    Returns ``(version, python_constraint)`` or ``(None, None)``.
    When *frozen* is ``True``, Python resolution is skipped entirely.
    """
    result = resolve_versions(templ_cls)
    versions = result.versions
    if not versions:
        return None, None

    ver_list = [v for v, _ in versions]

    # source label
    source_labels = {
        "pypi": "",
        "cache": "[cached]",
        "cache-expired": "[cached, may be outdated]",
        "fallback": "[local fallback, may be outdated]",
    }
    label = source_labels.get(result.source, "")

    latest = ver_list[0] if ver_list else ""
    pre_count = sum(
        1 for v in ver_list if "rc" in v or "dev" in v or "alpha" in v or "beta" in v
    )
    total = len(ver_list)
    py_map = dict(versions)

    ColorLog.question(
        f"Available versions for '{templ_cls.__template_name__}'"
        + (f" {label}" if label else "")
    )
    click.echo(f"    latest: {latest}")
    if pre_count:
        click.echo(f"    + {pre_count} pre-release(s) · {total} version(s) total")
    else:
        click.echo(f"    + {total} version(s) total")
    click.echo('    (type "?" to browse all, or enter a version directly)')
    click.echo()

    choice = click.prompt(
        "Choose version",
        default="?",
        type=str,
        show_default=True,
    ).strip()

    if choice == "?":
        # expand full list
        click.echo()
        for idx, v in enumerate(ver_list, 1):
            py_req = py_map.get(v, "")
            mark = " (latest)" if idx == 1 else ""
            extra = f"  [Python {py_req}]" if py_req else ""
            click.echo(f"  [{idx}] {v}{mark}{extra}")

        choice = click.prompt(
            "Choose version (number or version string)",
            default=latest,
            type=str,
            show_default=True,
        ).strip()

    if choice in py_map:
        return _maybe_resolve(choice, py_map, templ_cls, frozen)

    # try 1‑based index
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(ver_list):
            chosen = ver_list[idx]
            return _maybe_resolve(chosen, py_map, templ_cls, frozen)
    except ValueError:
        pass

    ColorLog.warn(f"'{choice}' is not a valid version, using latest.")
    return _maybe_resolve(latest, py_map, templ_cls, frozen)


def _maybe_resolve(
    version: str,
    py_map: dict[str, str],
    templ_cls: type,
    frozen: bool,
) -> tuple[str | None, str | None]:
    """Resolve Python constraint when *frozen* is ``False``, otherwise skip."""
    if frozen:
        return version, None
    return _resolve_python_version(version, py_map, templ_cls)


def _resolve_python_version(
    version: str,
    python_requires: dict[str, str],
    templ_cls: type,
) -> tuple[str | None, str | None]:
    """Resolve Python version constraint via exhaustive search over versions.

    Tries *version* first; on conflict walks older versions (
    descending) looking for a compatible pair.  Returns
    ``(version, python_constraint)`` or ``(version, None)``.
    """
    # try the requested version first
    py_req = python_requires.get(version, "")
    templ_py = getattr(templ_cls, "__python_requires__", "") or ""

    constraint = _intersect_python(templ_py, py_req)
    if constraint is not None:
        return version, constraint

    # conflict — try older versions
    ver_list = list(python_requires.keys())
    try:
        idx = ver_list.index(version)
    except ValueError:
        idx = 0

    for v in ver_list[idx + 1 :]:
        py_req2 = python_requires.get(v, "")
        constraint2 = _intersect_python(templ_py, py_req2)
        if constraint2 is not None:
            ColorLog.warn(
                f"'{version}' conflicts with Python {py_req}; "
                f"falling back to '{v}' ({constraint2})."
            )
            return v, constraint2

    # no viable combination
    ColorLog.error(
        f"No compatible version found for '{templ_cls.__template_name__}':\n"
        f"  template requires: {templ_py or '(none)'}\n"
        f"  core package '{templ_cls.__core_package__}'"
        + (f" ({len(ver_list)} versions)" if ver_list else " (no versions)")
    )
    if py_req:
        ColorLog.error(f"  '{version}' requires Python {py_req} (conflict)")
    ColorLog.info("Use --frozen to skip Python version selection and let uv handle it.")
    raise click.Abort()


def _collect_fields(templ_cls: type, cli_fields: dict[str, Any]) -> dict[str, Any]:
    """Merge CLI‑provided fields with interactive prompts for missing required ones."""

    result: dict[str, Any] = {}
    for fname, fdef in templ_cls.__tmpl_fields__.items():
        if fname in cli_fields:
            raw = cli_fields[fname]
        else:
            raw = fdef.default

        # coerce via field type
        if raw is not None and not isinstance(raw, fdef.type):
            try:
                raw = fdef.type(raw)
            except (ValueError, TypeError):
                pass

        # prompt if required and still missing/default-ish
        if fdef.required and (raw is None or raw == fdef.default):
            ColorLog.question(f"{fdef.description or fname}:")
            prompt_kwargs: dict[str, Any] = {"type": fdef.type}
            if fdef.choices:
                prompt_kwargs["type"] = click.Choice(fdef.choices)
            raw = click.prompt(
                f"  {fname}",
                default=fdef.default or "",
                show_default=bool(fdef.default),
                **prompt_kwargs,
            )

        result[fname] = raw
    return result


def _choose_license() -> str | None:
    """Prompt the user to pick a license or skip.

    Returns a license key (``"MIT"``, …) or ``None`` when the user
    chooses not to generate a LICENSE file.
    """
    ColorLog.question("Choose a license for your project:")
    keys = list(LICENSES.keys())
    for idx, key in enumerate(keys, 1):
        click.echo(f"  [{idx}] {key}")

    click.echo(f"  [{len(keys) + 1}] None (skip)")

    choice = click.prompt(
        "License",
        default=str(len(keys) + 1),
        type=str,
        show_default=True,
        show_choices=False,
    ).strip()

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(keys):
            return keys[idx]
    except ValueError:
        pass

    if choice in keys:
        return choice

    return None


def _resolve_name(name_opt: str | None) -> str:
    """Resolve project name: --name option > interactive prompt."""
    if name_opt:
        return name_opt.strip()
    ColorLog.question("Please enter a name for your project: ")
    return click.prompt("Project name", type=str).strip()


#  CLI root


@click.group()
def cli() -> None:
    """CLI for Project.Amrita"""
    colorama.init()
    ColorLog.set_level_from_env()


#  create


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
@click.option("--type", "-t", default=None, help="Template type to use.")
@click.option(
    "--version",
    "-V",
    default=None,
    help="Template version (default: latest).",
)
@click.option(
    "--force-version",
    default=None,
    help="Force a specific version (even if not in the known list).",
)
@click.option("--name", "-n", default=None, help="Project name.")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Overwrite target directory if it exists.",
)
@click.option(
    "--output",
    "-o",
    default=".",
    help="Output directory (default: current directory).",
)
@click.option(
    "--frozen",
    is_flag=True,
    help="Skip Python version selection and .python-version.",
)
@click.pass_context
def create(
    ctx: click.Context,
    type: str | None,
    version: str | None,
    force_version: str | None,
    name: str | None,
    force: bool,
    output: str,
    frozen: bool,
) -> None:
    """Create a new project from a template.

    \b
    Examples:
      amctl create -t amrita_core -n myapp
      amctl create -t amcore -n myapp --description="My App"
      amctl create -n myapp -t amcore -V 2.0 -o /tmp
      amctl create -n myapp -t amcore --force-version 3.0-beta
    """
    mgr = TemplateManager()

    # select template
    if type is None:
        ColorLog.question("Please enter a project type: ")
        type = click.prompt(
            "Project type",
            default="",
            type=str,
            show_default=False,
        ).strip()

        if not type:
            ColorLog.warn("No template specified. Available types:")
            for tname in mgr.get_templs():
                desc = mgr.get_templ(tname).__template_description__
                line = f"  - {tname}"
                if desc:
                    line += f"  — {desc}"
                click.echo(line)
            return

    templ_cls = mgr.safe_get_templ(type)
    if templ_cls is None:
        ColorLog.error(f"Unknown project type '{type}'")
        raise click.Abort()

    # select version
    py_constraint: str | None = None
    if force_version:
        version = force_version
    elif version is None:
        version, py_constraint = _choose_version(templ_cls, frozen=frozen)
    else:
        # validate against resolved list
        result = resolve_versions(templ_cls)
        ver_set = {v for v, _ in result.versions}
        if version not in ver_set:
            ColorLog.warn(
                f"Version '{version}' not found in known versions.  "
                f"Use --force-version to override."
            )
            version, _ = _choose_version(templ_cls, frozen=frozen)

    # resolve name
    project_name = _resolve_name(name)

    # collect fields
    cli_fields = _parse_dynamic_fields(ctx)
    fields = _collect_fields(templ_cls, cli_fields)

    # target dir
    output_path = Path(output).resolve()
    target = output_path / project_name

    if target.exists():
        if force:
            ColorLog.warn(f"Removing existing directory: {target}")
            shutil.rmtree(target)
        else:
            ColorLog.error(
                f"Directory '{target}' already exists. Use --force to overwrite."
            )
            raise click.Abort()

    # create
    ColorLog.info(
        f"Creating project '{project_name}' of type '{type}'"
        + (f" (v{version})" if version else "")
        + "..."
    )

    templ = templ_cls()
    templ.on_create(target, name=project_name, version=version, **fields)

    # interactive license selection
    chosen_license = _choose_license()
    if chosen_license is not None:
        license_text = LICENSES.get(chosen_license, "")
        if license_text.strip():
            (target / "LICENSE").write_text(license_text + "\n", encoding="utf-8")
            ColorLog.info("  Generated LICENSE")

    ColorLog.success(f"Project '{project_name}' created at {target}")
    click.echo()
    click.echo(f"  cd {project_name}")

    # write .python-version if resolved (skip when frozen)
    if py_constraint and version and not frozen:
        py_ver = _pick_python(py_constraint)
        if py_ver:
            (target / ".python-version").write_text(py_ver + "\n", encoding="utf-8")
            ColorLog.info(f"  Python {py_ver} ({py_constraint})")

    # offer to install dependencies
    install = (
        click.prompt(
            "Install dependencies now?",
            default="Y",
            show_default=True,
            type=str,
        )
        .strip()
        .upper()
    )
    if install == "Y":
        ColorLog.info("Running uv sync...")
        uv = UvOperator(cwd=str(target))
        try:
            uv.sync()
            ColorLog.success("Dependencies installed.")
        except RuntimeError as e:
            ColorLog.error(f"uv sync failed: {e}")


#  list


def _format_version_list(versions: list[tuple[str, str]], short: bool = True) -> str:
    if not versions:
        return "—"
    ver_list = [v for v, _ in versions]
    if short and len(ver_list) > 5:
        return ", ".join(ver_list[:5]) + f", … ({len(ver_list)} total)"
    return ", ".join(ver_list)


@cli.command("list")
@click.option("-v", "--verbose", is_flag=True, help="Show full version lists.")
def list_templates(verbose: bool) -> None:
    """List all available templates."""
    mgr = TemplateManager()
    templs = mgr.get_templs()

    if not templs:
        click.echo("No templates registered.")
        return

    click.echo("Available templates:")
    click.echo("─" * 60)
    for tname, tcls in templs.items():
        desc = tcls.__template_description__
        result = resolve_versions(tcls)
        versions_str = _format_version_list(result.versions, short=not verbose)
        nfields = len(tcls.__tmpl_fields__)
        click.echo(f"  {tname}")
        if desc:
            click.echo(f"    {desc}")
        click.echo(f"    versions: {versions_str}  |  fields: {nfields}")
        click.echo()


#  info


@cli.command("info")
@click.argument("type", required=True)
def info_cmd(type: str) -> None:
    """Show detailed information about a template."""
    mgr = TemplateManager()
    templ_cls = mgr.safe_get_templ(type)

    if templ_cls is None:
        ColorLog.error(f"Unknown template: '{type}'")
        raise click.Abort()

    click.echo(f"Template: {templ_cls.__template_name__}")
    click.echo(f"  Description: {templ_cls.__template_description__ or '—'}")

    result = resolve_versions(templ_cls)
    source_labels = {
        "pypi": "",
        "cache": "[cached]",
        "cache-expired": "[cached, may be outdated]",
        "fallback": "[local fallback]",
    }
    label = source_labels.get(result.source, "")
    ver_list = [v for v, _ in result.versions]
    versions_str = ", ".join(ver_list) if ver_list else "—"
    click.echo(f"  Versions: {versions_str} {label}".rstrip())
    click.echo()

    if templ_cls.__tmpl_fields__:
        click.echo("  Fields:")
        click.echo(
            f"  {'Name':<20} {'Type':<10} {'Default':<15} {'Required':<10} Description"
        )
        click.echo(
            f"  {'─' * 18:<20} {'─' * 8:<10} {'─' * 13:<15} {'─' * 8:<10} {'─' * 11}"
        )
        for fname, fdef in templ_cls.__tmpl_fields__.items():
            ftype = fdef.type.__name__
            default = str(fdef.default) if fdef.default is not None else "—"
            required = "yes" if fdef.required else "no"
            click.echo(
                f"  {fname:<20} {ftype:<10} {default:<15} {required:<10} {fdef.description}"
            )
    else:
        click.echo("  Fields: —")

    click.echo()
    click.echo("  Template files:")
    files = templ_cls.get_expected_files()
    for f in sorted(files):
        click.echo(f"    {f}")


#  tmpl  (dynamic sub‑group)


class TmplGroup(click.Group):
    """Dynamic group for ``amctl tmpl <type> [<cmd> ...]``.

    Each registered template becomes a sub‑group whose commands come
    from ``templ.get_tmpl_commands()``.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(TemplateManager().get_templs().keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        mgr = TemplateManager()
        templ_cls = mgr.safe_get_templ(cmd_name)
        if templ_cls is None:
            return None

        templ = templ_cls()
        sub_cmds = templ.get_tmpl_commands()

        if sub_cmds:
            # return a sub‑group with the template's commands
            group = click.Group(
                name=cmd_name,
                help=f"Commands for template '{cmd_name}'.",
                commands=sub_cmds,
            )
            return group

        # no registered tmpl commands → show field summary
        @click.command(name=cmd_name, help=f"Show field summary for '{cmd_name}'.")
        @click.pass_context
        def _show_fields(c: click.Context) -> None:
            click.echo(f"Template: {cmd_name}")
            click.echo(f"  {templ_cls.__template_description__ or '—'}")
            if not templ_cls.__tmpl_fields__:
                click.echo("  No fields defined.")
            else:
                click.echo("  Fields:")
                for fname, fdef in templ_cls.__tmpl_fields__.items():
                    req = " (required)" if fdef.required else ""
                    click.echo(
                        f"    {fname}: {fdef.type.__name__}"
                        + (f" = {fdef.default}" if fdef.default is not None else "")
                        + req
                    )
                    if fdef.description:
                        click.echo(f"      {fdef.description}")

        return _show_fields


@cli.command(cls=TmplGroup)
def tmpl() -> None:
    """Template‑specific commands.  Usage: amctl tmpl <type> [cmd ...]"""
    pass


#  man  (dynamic group: project scripts)


class ManGroup(click.Group):
    """Dynamic group for ``amctl man <script> [-- extra_args]``.

    Reads ``[tools.amctl.scripts]`` from the nearest ``pyproject.toml``
    and exposes each key as a sub‑command.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        scripts = read_project_scripts()
        return list(scripts.keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        scripts = read_project_scripts()
        if cmd_name not in scripts:
            return None

        script_cmd = scripts[cmd_name]
        cwd = self._project_root()

        @click.command(
            name=cmd_name,
            context_settings={"ignore_unknown_options": True},
            help=f"Run: {script_cmd}",
        )
        @click.argument("extra", nargs=-1, type=click.UNPROCESSED)
        def _run_script(extra: tuple[str, ...]) -> None:
            full_cmd = script_cmd
            if extra:
                full_cmd += " " + " ".join(extra)
            ColorLog.info(f"Running: {full_cmd}")
            subprocess.run(full_cmd, shell=True, cwd=cwd)

        return _run_script

    @staticmethod
    def _project_root() -> str | None:
        """Find the project root directory."""
        from amctl.project import _find_pyproject

        pp = _find_pyproject()
        return str(pp.parent) if pp else None

    def invoke(self, ctx: click.Context) -> Any:
        meta = read_project_meta()
        if meta is None:
            ColorLog.error(
                "Not inside an amctl project "
                "(no [tools.amctl.project] found in pyproject.toml or pyproject.toml is missing)."
            )
            ctx.exit(1)
        return super().invoke(ctx)


@cli.command(cls=ManGroup)
def man() -> None:
    """Run project scripts defined in [tools.amctl.scripts]."""
    pass


#  fix


def _parse_exclude(raw: str | None) -> set[str]:
    """Parse ``--exclude`` into a set of paths.

    Accepts a JSON array ``'["a","b"]'`` or a plain string ``"a"``.
    Returns an empty set when *raw* is ``None``.
    """
    if not raw:
        return set()
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x) for x in parsed}
        except json.JSONDecodeError:
            pass
    return {raw}


@cli.command("fix")
@click.option(
    "--check",
    is_flag=True,
    help="Only check for missing files; do not restore them.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Skip confirmation and restore all missing files at once.",
)
@click.option(
    "--exclude",
    default=None,
    help=(
        "Files to exclude from restoration.  "
        'Accepts a JSON array (\'["a","b"]\') or a plain path string.'
    ),
)
def fix_cmd(check: bool, force: bool, exclude: str | None) -> None:
    """Restore missing / corrupted project files from the template.

    By default an interactive prompt lets you accept, reject, or
    customise the list of files to restore.  Use ``--force`` to skip
    the prompt and restore everything (subject to ``--exclude``).
    """
    meta = read_project_meta()
    if meta is None:
        ColorLog.error(
            "Not inside an amctl project. (no [tools.amctl.project] found in pyproject.toml or pyproject.toml is missing)"
        )
        raise click.Abort()

    project_type = meta.get("project-type", "")
    project_version = meta.get("version", "")

    mgr = TemplateManager()
    templ_cls = mgr.safe_get_templ(project_type)
    if templ_cls is None:
        ColorLog.error(f"Unknown template: '{project_type}'")
        raise click.Abort()

    # find project root
    from amctl.project import _find_pyproject

    pp = _find_pyproject()
    if pp is None:
        ColorLog.error("No pyproject.toml found.")
        raise click.Abort()
    project_dir = pp.parent

    templ = templ_cls()
    ctx = templ._build_context(name=project_dir.name, version=project_version or None)

    # compute expected file set (rendering Jinja2 names)
    raw_expected = templ_cls.get_expected_files(project_version or None)
    expected_set: set[str] = set()
    for raw in raw_expected:
        if "{{" in raw or "{%" in raw:
            from jinja2 import BaseLoader, Environment

            tpl = Environment(loader=BaseLoader()).from_string(raw)
            rendered = tpl.render(**ctx)
        else:
            rendered = raw
        expected_set.add(rendered)

    # compute existing file set
    existing: set[str] = set()
    for f in project_dir.rglob("*"):
        if f.is_file() and ".git" not in f.parts:
            existing.add(str(f.relative_to(project_dir)))

    missing = expected_set - existing

    # LICENSE is not part of the template file set, but we check it
    # separately:  warn when the LICENSE file is absent.
    license_file_absent = not (project_dir / "LICENSE").is_file()

    if not missing and not license_file_absent:
        ColorLog.success("Project is healthy — no missing files.")
        return

    if license_file_absent:
        ColorLog.warn("LICENSE file is missing (not auto-restored — add it manually).")
        click.echo()

    missing_sorted = sorted(missing)

    if not missing_sorted:
        return

    # plan display
    click.echo()
    ColorLog.info(f"Plan: restore {len(missing_sorted)} missing file(s):")
    for f in missing_sorted:
        click.echo(f"  {f}")
    click.echo()

    if check:
        return

    # apply exclusions
    excluded = _parse_exclude(exclude)
    if excluded:
        missing_sorted = [f for f in missing_sorted if f not in excluded]
        if not missing_sorted:
            ColorLog.info("All missing files excluded — nothing to do.")
            return
        ColorLog.info(f"After exclusions: {len(missing_sorted)} file(s) to restore.")

    # interactive confirmation
    if not force:
        choice = (
            click.prompt(
                "Accept this plan? [Y]es / [N]o / [M]odify",
                default="Y",
                show_default=True,
                type=str,
            )
            .strip()
            .upper()
        )

        if choice == "N":
            ColorLog.warn("Restoration cancelled.")
            return

        if choice == "M":
            # Let user select which files to keep (inverse = check those to restore)
            click.echo()
            ColorLog.question("For each file, enter [Y] to restore or [N] to skip.")
            selected: list[str] = []
            for f in missing_sorted:
                ans = (
                    click.prompt(
                        f"  Restore '{f}'?",
                        default="Y",
                        show_default=True,
                        type=str,
                    )
                    .strip()
                    .upper()
                )
                if ans == "Y":
                    selected.append(f)
            missing_sorted = selected
            if not missing_sorted:
                ColorLog.info("No files selected — nothing to restore.")
                return

        elif choice != "Y":
            ColorLog.warn(f"Unknown choice '{choice}' — cancelled.")
            return

    # restore
    ColorLog.info("Restoring missing files...")
    from jinja2 import BaseLoader
    from jinja2 import Environment as J2Env

    from amctl.renderer import TemplateRenderer

    renderer = TemplateRenderer()
    restored = 0
    for rel in missing_sorted:
        found = False
        tmpl_dir = templ.get_template_dir(project_version or None)
        for item in tmpl_dir.rglob("*"):
            if item.is_file() and not item.name.startswith("__"):
                raw_rel = str(item.relative_to(tmpl_dir))
                if raw_rel.endswith(".tmpl"):
                    raw_rel = raw_rel[:-5]
                if "{{" in raw_rel or "{%" in raw_rel:
                    rendered = (
                        J2Env(loader=BaseLoader()).from_string(raw_rel).render(**ctx)
                    )
                else:
                    rendered = raw_rel
                if rendered == rel:
                    dst = project_dir / rel
                    renderer.render_file(item, dst, ctx)
                    found = True
                    restored += 1
                    break
        if not found:
            ColorLog.warn(f"  Cannot restore '{rel}' — template source not found")
    ColorLog.success(f"Restored {restored} file(s).")


#  self  (amctl management commands)


@cli.group("self")
def self_group() -> None:
    """Manage amctl itself (cache, template updates)."""
    pass


# self cache show


@self_group.group("cache")
def cache_group() -> None:
    """Manage the version cache."""
    pass


@cache_group.command("show")
def cache_show() -> None:
    """Display the contents of the version cache."""
    cache_path = cache_file_path()
    if not cache_path.is_file():
        click.echo(f"Cache file not found: {cache_path}")
        return

    data = load_cache()
    if not data:
        click.echo(f"Cache is empty: {cache_path}")
        return

    click.echo(f"Cache: {cache_path}  (24h TTL)")
    click.echo()
    for name, entry in sorted(data.items()):
        versions = entry.get("versions", [])
        updated = entry.get("updated_at", "?")
        click.echo(f"  {name}")
        click.echo(
            f"    versions: {', '.join(str(v) for v in versions) if versions else '—'}"
        )
        click.echo(f"    updated:  {updated}")
        click.echo()


@cache_group.command("clean")
def cache_clean() -> None:
    """Delete the version cache."""
    cache_path = cache_file_path()
    clear_cache()
    ColorLog.success(f"Cache cleared: {cache_path}")


@cache_group.command("fresh")
def cache_fresh() -> None:
    """Refresh the version cache from PyPI for all registered templates."""
    mgr = TemplateManager()
    templs = mgr.get_templs()
    if not templs:
        click.echo("No templates registered.")
        return

    data = load_cache()
    updated = skipped = 0
    for tname, tcls in sorted(templs.items()):
        pkg = _core_package_for(tcls)
        if not pkg:
            click.echo(f"  {tname}  →  (no core package)  [SKIP]")
            skipped += 1
            continue
        versions = fetch_pypi_releases(pkg)
        if versions:
            from datetime import datetime, timezone

            data[tname] = {
                "versions": dict(versions),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            ver_keys = [v for v, _ in versions]
            click.echo(
                f"  {tname}  →  {', '.join(ver_keys[:5])}{' …' if len(ver_keys) > 5 else ''}  [OK]"
            )
            updated += 1
        else:
            click.echo(f"  {tname}  →  (PyPI unreachable)  [SKIP]")
            skipped += 1

    # Always persist — creates .amctl dir + file even when all skipped.
    save_cache(data)
    ColorLog.success(f"Cache refreshed: {updated} updated, {skipped} skipped.")


# self tmpl-upd


@self_group.command("tmpl-upd")
def tmpl_upd() -> None:
    """Check for updates to installed amctl-template-* packages."""
    # Check if we're in a venv
    try:
        venv = Path.cwd() / ".venv"
        if not venv.is_dir():
            # maybe we're inside the venv itself
            if "VIRTUAL_ENV" in os.environ:
                pass
            else:
                ColorLog.warn("Not in a virtual environment — nothing to check.")
                return
    except Exception:
        pass

    mgr = TemplateManager()
    templs = mgr.get_templs()
    found_any = False

    for tname, tcls in sorted(templs.items()):
        pkg = _core_package_for(tcls)
        if not pkg:
            continue
        # check local version via importlib.metadata
        try:
            local_ver = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            continue
        found_any = True

        info = fetch_pypi_info(pkg)
        if info is None:
            click.echo(f"  {pkg}  {local_ver}  (PyPI unreachable)")
            continue

        latest = info.get("info", {}).get("version", "?")
        if latest != local_ver:
            click.echo(f"  {pkg}  {local_ver} → {latest}  ⬆ update available")
            click.echo(f"     Run: uv add {pkg}@{latest}")
        else:
            click.echo(f"  {pkg}  {local_ver} (latest)  ✓ up to date")

    if not found_any:
        click.echo("No amctl-template-* packages found in the environment.")


@cli.command("hello", hidden=True)
@click.option("-v", is_flag=True, hidden=True)
def _amrita(v: bool) -> None:
    click.echo("Hello!" if not v else "Hello, world!")
