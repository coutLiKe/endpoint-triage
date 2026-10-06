"""The single place where the tool runs operating-system commands.

Every collector receives a `run` function (this module's `run_command` by
default). Tests pass in a fake `run` instead, so no test depends on the
machine it runs on. This is dependency injection in its simplest form.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from endpoint_triage.models import CheckResult

log = logging.getLogger(__name__)

# Read-only safety net: the runner refuses to execute anything not on this
# list. Every command here is used only in a read-only way by the collectors.
ALLOWED_COMMANDS = frozenset({
    # macOS
    "sw_vers", "sysctl", "vm_stat", "ifconfig", "route", "scutil", "softwareupdate",
    # macOS + Linux
    "df", "ping",
    # Linux
    "ip", "apt", "dnf",
    # Windows (ping is shared)
    "powershell",
})

DEFAULT_TIMEOUT = 15

NOT_FOUND = "not_found"
TIMEOUT = "timeout"
OS_ERROR = "os_error"


@dataclass
class CommandResult:
    args: list[str]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    # None when the process ran (even if it exited non-zero); otherwise
    # NOT_FOUND, TIMEOUT or OS_ERROR, meaning the process never finished.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    def describe_failure(self) -> str:
        name = self.args[0] if self.args else "command"
        if self.error == NOT_FOUND:
            return f"command not found: {name}"
        if self.error == TIMEOUT:
            return f"{name} timed out"
        if self.error == OS_ERROR:
            return f"could not run {name}: {self.stderr.strip()}"
        message = _first_line(self.stderr) or _first_line(self.stdout) or "no output"
        return f"{name} exited with code {self.returncode}: {message}"


RunFunc = Callable[..., CommandResult]


def run_command(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run a command without a shell and capture its output.

    Never raises for ordinary failures: a missing command, a timeout, or a
    non-zero exit code are all reported through the returned CommandResult.
    """
    if not args or args[0] not in ALLOWED_COMMANDS:
        raise ValueError(f"Refusing to run command not on the allow-list: {args[:1]}")

    env = None
    if os.name != "nt":
        # Force English, C-locale output so parsers see predictable text.
        env = {**os.environ, "LC_ALL": "C"}

    log.debug("Running: %s", args)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.debug("Command not found: %s", args[0])
        return CommandResult(args, None, error=NOT_FOUND)
    except subprocess.TimeoutExpired:
        log.debug("Command timed out after %ss: %s", timeout, args[0])
        return CommandResult(args, None, error=TIMEOUT)
    except OSError as exc:
        log.debug("OS error running %s: %s", args[0], exc)
        return CommandResult(args, None, stderr=str(exc), error=OS_ERROR)

    log.debug("%s exited %s in %.2fs", args[0], completed.returncode, time.monotonic() - started)
    return CommandResult(args, completed.returncode, completed.stdout or "", completed.stderr or "")


def run_powershell(script: str, run: RunFunc = run_command, timeout: float = 30) -> CommandResult:
    """Run a PowerShell snippet. Scripts are expected to print JSON."""
    prelude = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$ProgressPreference = 'SilentlyContinue'; "
        "$ErrorActionPreference = 'Stop'; "
    )
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", prelude + script],
        timeout=timeout,
    )


def read_text_file(path: str) -> str:
    """Read a small text file (e.g. /proc/meminfo). Raises OSError on failure."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def command_failure(check_id: str, title: str, result: CommandResult) -> CheckResult:
    """Convert a failed CommandResult into the matching CheckResult."""
    debug = f"command: {result.args}\nreturncode: {result.returncode}\nstderr: {result.stderr.strip()}"
    if result.error == NOT_FOUND:
        return CheckResult.unavailable(check_id, title, result.describe_failure(), debug=debug)
    return CheckResult.failed(check_id, title, result.describe_failure(), debug=debug)


def unsupported_os(check_id: str, title: str, os_name: str) -> CheckResult:
    return CheckResult.unavailable(check_id, title, f"not supported on {os_name or 'this OS'}")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
