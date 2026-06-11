import enum
import os

import click
from colorama import Fore, Style


class LogLevel(enum.IntEnum):
    """Ordered log levels for filtering output verbosity.

    Priority (ascending):
        ``DEBUG`` < ``INFO`` < ``SUCCESS`` ≡ ``WARNING`` < ``ERROR``

    ``SUCCESS`` and ``WARNING`` share the same integer value and therefore
    compare as equal.  The **default** level is ``INFO``.
    """

    DEBUG = 0
    INFO = 1
    SUCCESS = 2
    WARNING = 2
    ERROR = 3

    @staticmethod
    def from_str(name: str) -> "LogLevel":
        """Return the ``LogLevel`` member matching *name* (case‑insensitive).

        Common aliases are accepted::

            "warn"        → WARNING
            "err" / "fatal" / "critical" → ERROR

        Args:
            name: A string like ``"INFO"``, ``"debug"``, ``"error"``, etc.

        Returns:
            The corresponding ``LogLevel`` member.

        Raises:
            KeyError: If *name* does not match any known level or alias.
        """
        key = name.strip().upper()
        aliases: dict[str, "LogLevel"] = {
            "WARN": LogLevel.WARNING,
            "ERR": LogLevel.ERROR,
            "FATAL": LogLevel.ERROR,
            "CRITICAL": LogLevel.ERROR,
        }
        if key in aliases:
            return aliases[key]
        return LogLevel[key]


@staticmethod
def filter(level: LogLevel):
    """Decorator that suppresses output when *level* is below threshold.

    Usage::

        @filter(LogLevel.DEBUG)
        @staticmethod
        def debug(message: str): ...

    Args:
        level: The ``LogLevel`` this output method corresponds to.
    """

    def decorator(func):
        def wrapper(message: str):
            if ColorLog.should_log(level):
                func(message)

        return wrapper

    return decorator


class ColorLog:
    """Utility class for printing colored log messages to the console.

    The active log *threshold* is stored as a class attribute in
    :attr:`_level` and defaults to :attr:`LogLevel.INFO`.  Messages
    below the threshold are suppressed.
    """

    _level: LogLevel = LogLevel.INFO

    @staticmethod
    def set_level(level: LogLevel) -> LogLevel:
        """Set the current log threshold and return it.

        Args:
            level: The new minimum ``LogLevel``.

        Returns:
            The newly set ``LogLevel``.
        """
        ColorLog._level = level
        return level

    @staticmethod
    def get_level() -> LogLevel:
        """Return the current log threshold."""
        return ColorLog._level

    @staticmethod
    def set_level_from_env(
        env_var: str = "AMCTL_LOG_LEVEL",
        default: LogLevel = LogLevel.INFO,
    ) -> LogLevel:
        """Read *env_var* and set the log threshold accordingly.

        The environment value is parsed via :meth:`LogLevel.from_str`.
        If the variable is missing or cannot be parsed the *default*
        (``INFO``) is used instead.

        Args:
            env_var: Name of the environment variable to read.
            default:  Fallback ``LogLevel`` when the env var is
                      unset or invalid.

        Returns:
            The resolved ``LogLevel``.
        """
        raw = os.environ.get(env_var)
        if raw is None:
            ColorLog._level = default
            return default
        raw = raw.upper()
        try:
            parsed = LogLevel.from_str(raw)
            ColorLog._level = parsed
            return parsed
        except KeyError:
            ColorLog._level = default
            return default

    @staticmethod
    def should_log(level: LogLevel) -> bool:
        """Return ``True`` if *level* meets the current threshold."""
        return level >= ColorLog._level

    @staticmethod
    @filter(LogLevel.WARNING)
    def warn(message: str):
        """Return a message with a yellow warning prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized warning message string.
        """
        click.echo(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {message}")

    @staticmethod
    @filter(LogLevel.INFO)
    def info(message: str):
        """Return a message with a green info prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized info message string.
        """
        click.echo(f"{Fore.GREEN}[+]{Style.RESET_ALL} {message}")

    @staticmethod
    @filter(LogLevel.ERROR)
    def error(message: str):
        """Return a message with a red error prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized error message string.
        """
        click.echo(f"{Fore.RED}[-]{Style.RESET_ALL} {message}")

    @staticmethod
    @filter(LogLevel.INFO)
    def question(message: str):
        """Return a message with a blue question prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized question message string.
        """
        click.echo(f"{Fore.BLUE}[?]{Style.RESET_ALL} {message}")

    @staticmethod
    @filter(LogLevel.SUCCESS)
    def success(message: str):
        """Return a message with a green success prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized success message string.
        """
        click.echo(f"{Fore.GREEN}[=]{Style.RESET_ALL} {message}")

    @staticmethod
    @filter(LogLevel.DEBUG)
    def debug(message: str):
        """Return a message with a cyan debug prefix.

        Args:
            message: The message text to colorize.

        Returns:
            The colorized debug message string.
        """
        click.echo(f"{Fore.CYAN}[*]{Style.RESET_ALL} {message}")
