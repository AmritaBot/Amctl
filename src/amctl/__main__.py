from .cli import cli
from .templating import discover_templates


def main() -> None:
    discover_templates()
    cli()


if __name__ == "__main__":
    main()
