from typing import ClassVar, Literal

from amctl.templating import BaseTemplate, TmplField, field

__template_ignore___: Literal[False] = False


class AmctlTemplate(BaseTemplate):
    """Minimal Python package scaffold template."""

    __template_name__: str = "amctl_template"
    __template_description__: str = (
        "Create a template for Amctl"
    )
    __core_package__: str = "amctl"
    __versions__: tuple[str, ...] = ("0.1.0",)
    __tmpl_fields__: ClassVar[dict[str, TmplField]] = {
        "description": field(
            default="My awesome project.",
            description="Short project description",
        ),
    }

    def on_create(self, project_dir, name, version=None, **fields):
        """Render template with amctl metadata embedded."""
        super().on_create(project_dir, name, version=version, **fields)


__template_export__ = AmctlTemplate
