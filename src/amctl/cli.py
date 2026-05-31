import click
import colorama
from click import group

from amctl.colors import ColorLog
from amctl.templating import TemplateManager

p_type = type


@group()
def cli(*args, **kwargs):
    """
    CLI for Project.Amrita
    """
    colorama.init()
    ColorLog.set_level_from_env()


@cli.command()
@click.argument("type", required=False)
def create(type: str | None = None):
    """
    Create a new project
    """
    if not type:
        ColorLog.question("Please enter a type for your project: ")
        type = click.prompt(
            "Project type:",
            default="",
            type=str,
            err=True,
            prompt_suffix="",
            show_default=False,
        ).strip()
        if not type:
            ColorLog.warn("No template specified, available types are:")
            for t in TemplateManager().get_templs():
                click.echo(f"  - {t}")
            return
    if (templ := TemplateManager().safe_get_templ(type)) is None:
        ColorLog.error(f"Unknown project type '{type}'")
        raise click.Abort()
    ColorLog.question("Please enter a name for your project: ")
    name: str = click.prompt(
        "Project name:",
        type=str,
        err=True,
        prompt_suffix="",
        show_default=False,
    ).strip()
    ColorLog.info(f"Creating project '{name}' of type '{type}'...")
