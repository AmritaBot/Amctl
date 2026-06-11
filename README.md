# Amctl

**CLI for Project Amrita** — scaffold, manage, and maintain Python projects
with the Amrita ecosystem.

[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://pypi.org/project/amctl/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Installation

```bash
uv tool install amctl
# or
pip install amctl
```

## Quick Start

```bash
# List available templates
amctl list

# Create a new project
amctl create -t amrita_core -n myapp
cd myapp
uv sync
```

---

## Commands

### `amctl create`

Scaffold a new project from a template.

```bash
amctl create -t amrita_core -n myapp
amctl create -t amrita_core -n myapp --description="My awesome project"
amctl create -t amrita_core -n myapp -V 0.8.0 -o /tmp

# Bypass Python version resolution
amctl create -t amrita_core -n myapp --frozen

# Force a specific version not in the known list
amctl create -t amrita_core -n myapp --force-version 3.0-beta
```

| Option            | Description                                         |
| ----------------- | --------------------------------------------------- |
| `-t`, `--type`    | Template type (e.g. `amrita_core`)                  |
| `-n`, `--name`    | Project name                                        |
| `-V`, `--version` | Template version (default: latest)                  |
| `-o`, `--output`  | Output directory (default: `.`)                     |
| `-f`, `--force`   | Overwrite existing directory                        |
| `--force-version` | Use any version string, bypassing validation        |
| `--frozen`        | Skip Python version selection and `.python-version` |

Template-specific fields (declared via `__tmpl_fields__`) are passed as
dynamic CLI flags:

```bash
amctl create -t amrita_core -n myapp --description="Hello" --port=8080
```

After scaffolding, you are prompted to choose a license interactively:

```
[?] Choose a license for your project:
  [1] MIT
  [2] Apache-2.0
  [3] GPL-3.0
  [4] None (skip)
License [4]: _
```

A `LICENSE` file is written to the project root when a known license is
selected.

---

### `amctl list`

List all registered templates.

```bash
amctl list            # compact (first 5 versions shown)
amctl list -v         # verbose — full version lists
```

---

### `amctl info`

Show detailed information about a template.

```bash
amctl info amrita_core
```

Output includes description, available versions (with source labels),
declared fields (name/type/default/required/description), and the expected
template file tree.

---

### `amctl fix`

Check for missing template files and restore them.

```bash
amctl fix --check     # dry-run — report missing files only
amctl fix             # interactive [Y/N/M] confirmation
amctl fix --force     # restore all missing files without prompting
amctl fix --exclude '["README.md",".gitignore"]'   # skip specific files
```

| Option          | Description                           |
| --------------- | ------------------------------------- |
| `--check`       | Dry-run — show what would be restored |
| `--force`, `-f` | Skip confirmation, restore everything |
| `--exclude`     | JSON array or single path to exclude  |

---

### `amctl man`

Run project scripts defined in `pyproject.toml` under
`[tools.amctl.scripts]`.

```toml
# pyproject.toml
[tools.amctl.scripts]
lint = "uv run ruff check ."
test = "uv run pytest -v"
start = "uv run uvicorn myapp.main:app --reload"
```

```bash
amctl man lint
amctl man test -- --cov
amctl man start
```

Extra arguments after `--` are forwarded to the script.

---

### `amctl tmpl`

Template-specific sub-commands. Each registered template is a sub-group.

```bash
amctl tmpl amrita_core     # show field summary (if no commands registered)
```

Templates can register custom commands via `get_tmpl_commands()`.

---

### `amctl self`

Manage amctl itself.

```bash
amctl self cache show       # display version cache contents
amctl self cache fresh      # refresh cache from PyPI
amctl self cache clean      # delete the cache file
amctl self tmpl-upd         # check for template package updates
```

---

## Version Resolution

Amctl resolves available template versions through a **cache-first
fallback chain**:

```
fresh cache (24h TTL) → PyPI → expired cache (warn) → hardcoded fallback
```

**Cache location** (in priority order):

1. `$AMCTL_TMPL_CACHEPATH` — explicit path
2. `.venv/.amctl/versions_cache.json` — inside a virtualenv project
3. `~/.amctl/versions_cache.json` — global

**Environment variables**:

| Variable                    | Description                                  |
| --------------------------- | -------------------------------------------- |
| `AMCTL_TMPL_CACHEPATH`      | Custom cache directory                       |
| `AMCTL_TMPL_USECACHE=false` | Disable local cache (still uses PyPI)        |
| `AMCTL_TMPL_NOPYPI=true`    | Block all PyPI requests (air-gapped)         |
| `AMCTL_LOG_LEVEL=debug`     | Enable debug logging for network diagnostics |

Each version carries a `requires_python` constraint obtained from PyPI.
Amctl attempts to resolve a compatible Python interpreter using `uv
python list` and writes a `.python-version` file to the project root.

---

## Creating a Template

Templates are auto-discovered from `src/amctl/templ/<name>/`. Define a
class with the required dunder attributes:

```python
from amctl.templating import BaseTemplate, field

class MyTemplate(BaseTemplate):
    __template_name__ = "my_template"
    __template_description__ = "A custom project template"
    __core_package__ = "my-core-lib"         # PyPI package for version discovery
    __python_requires__ = ">=3.11"           # Python version constraint
    __versions__ = ("1.0",)                  # hardcoded fallback
    __tmpl_fields__ = {
        "description": field(default="desc"),
        "port": field(default=8000, type=int, required=True),
    }

    def on_create(self, project_dir, name, version=None, **fields):
        super().on_create(project_dir, name, version=version, **fields)

__template_export__ = MyTemplate
```

Template files go in the same directory and use the `.tmpl` extension
for Jinja2 rendering. Directory names containing `{{  }}` markers are
also rendered (e.g. `src/{{ name }}/__init__.py.tmpl`).

---

## Development

```bash
uv sync
uv run ruff check src/     # lint
uv run pyright src/        # type-check
uv run amctl --help
```

## License

MIT — see [LICENSE](LICENSE).
