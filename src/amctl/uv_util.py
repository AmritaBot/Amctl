import os
import shlex

_uv_check = os.popen("uv --version")
_uv_check.read()
if _uv_check.close() is not None:
    raise RuntimeError("uv is not available, please install it first")


class UvOperator:
    """Wrapper around the ``uv`` CLI using :func:`os.popen` with shell escaping.

    Usage::

        uv = UvOperator(cwd="/path/to/project")
        out = uv.add("httpx")
        print(out)
    """

    def __init__(self, cwd: str | None = None, uv_path: str = "uv"):
        """Initialise the wrapper.

        Args:
            cwd: Working directory where ``uv`` commands are executed.
                 Pass ``None`` (the default) to use the current directory.
            uv_path: Path or name of the ``uv`` executable (default ``"uv"``).
        """
        self._cwd = cwd
        self._uv = uv_path

    def _build_cmd(self, *parts: str) -> str:
        """Build a single shell command string from positional *parts*.

        Each part is shell-escaped and a ``cd`` prefix is prepended when
        *cwd* was set.
        """
        cmd = " ".join(shlex.quote(p) for p in parts)
        if self._cwd is not None:
            cmd = f"cd {shlex.quote(self._cwd)} && {cmd}"
        return cmd

    def _run(self, *parts: str) -> str:
        """Execute ``uv <parts...>`` and return stdout as a string.

        Raises:
            RuntimeError: When the exit code is non-zero (stderr is embedded
                in the message).
        """
        cmd = self._build_cmd(self._uv, *parts)
        with os.popen(cmd) as stream:
            output = stream.read()
        exit_code = stream.close()
        if exit_code is not None:
            raise RuntimeError(f"'{cmd}' failed (exit {exit_code}):\n{output}")
        return output

    def init(self, path: str = ".", **kwargs: str) -> str:
        """Run ``uv init [--<key> <value> ...] <path>``.

        Args:
            path: Project path (default ``"."``).
            **kwargs: Extra flags passed as ``--key=value``.  A truthy
                      value with a single underscore is converted to
                      ``--key-with-hyphens``.
        """
        flags = []
        for k, v in kwargs.items():
            flag = f"--{k.replace('_', '-')}"
            if v is not True:
                flag += f"={v}"
            flags.append(flag)
        return self._run("init", *flags, path)

    def add(self, *packages: str, dev: bool = False) -> str:
        """Run ``uv add [--dev] <package>...``.

        Args:
            packages: One or more package specifiers.
            dev: Add as a dev-dependency.
        """
        args = ["add"]
        if dev:
            args.append("--dev")
        return self._run(*args, *packages)

    def remove(self, *packages: str) -> str:
        """Run ``uv remove <package>...``."""
        return self._run("remove", *packages)

    def sync(self, **kwargs: str) -> str:
        """Run ``uv sync [--<key>=<value> ...]``.

        Args:
            **kwargs: Extra flags passed as ``--key=value``.
        """
        flags = [
            f"--{k.replace('_', '-')}={v}"
            if v is not True
            else f"--{k.replace('_', '-')}"
            for k, v in kwargs.items()
        ]
        return self._run("sync", *flags)

    def lock(self, **kwargs: str) -> str:
        """Run ``uv lock [--<key>=<value> ...]``.

        Args:
            **kwargs: Extra flags passed as ``--key=value``.
        """
        flags = [
            f"--{k.replace('_', '-')}={v}"
            if v is not True
            else f"--{k.replace('_', '-')}"
            for k, v in kwargs.items()
        ]
        return self._run("lock", *flags)
    def export(self, **kwargs: str) -> str:
        """Run ``uv export [--<key>=<value> ...]``.

        Args:
            **kwargs: Extra flags passed as ``--key=value``.
        """
        flags = [
            f"--{k.replace('_', '-')}={v}"
            if v is not True
            else f"--{k.replace('_', '-')}"
            for k, v in kwargs.items()
        ]
        return self._run("export", *flags)

    def version(self, **kwargs: str) -> str:
        """Run ``uv version [--<key>=<value> ...]``.

        Args:
            **kwargs: Extra flags passed as ``--key=value``.
        """
        flags = [
            f"--{k.replace('_', '-')}={v}"
            if v is not True
            else f"--{k.replace('_', '-')}"
            for k, v in kwargs.items()
        ]
        return self._run("version", *flags)

    def run(self, *args: str) -> str:
        """Run ``uv run <args>...``."""
        return self._run("run", *args)

    def python(self, *args: str) -> str:
        """Run ``uv python <args>...``."""
        return self._run("python", *args)
    def venv(self, *args: str) -> str:
        """Run ``uv venv <args>...``."""
        return self._run("venv", *args)

    def build(self, *args: str) -> str:
        """Run ``uv build <args>...``."""
        return self._run("build", *args)

    def cache(self, *args: str) -> str:
        """Run ``uv cache <args>...``."""
        return self._run("cache", *args)

    def tool(self, *args: str) -> str:
        """Run ``uv tool <args>...``."""
        return self._run("tool", *args)

    def self_(self, *args: str) -> str:
        """Run ``uv self <args>...``.

        The method is named ``self_`` (trailing underscore) to avoid
        clashing with the Python keyword.
        """
        return self._run("self", *args)

    def publish(self, *args: str) -> str:
        """Run ``uv publish <args>...``."""
        return self._run("publish", *args)
