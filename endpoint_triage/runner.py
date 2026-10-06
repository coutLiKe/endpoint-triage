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

# Read-only safety net: the runner only executes these commands, and only
# from these fixed system locations. PATH is never searched, so a fake
# "ping.exe" or "powershell.exe" planted in a user-writable folder (or next
# to the tool on Windows) cannot be run in place of the real one.
POSIX_COMMAND_PATHS = {
    # macOS
    "sw_vers": ("/usr/bin/sw_vers",),
    "sysctl": ("/usr/sbin/sysctl", "/sbin/sysctl"),
    "vm_stat": ("/usr/bin/vm_stat",),
    "ifconfig": ("/sbin/ifconfig",),
    "route": ("/sbin/route",),
    "scutil": ("/usr/sbin/scutil",),
    "softwareupdate": ("/usr/sbin/softwareupdate",),
    # macOS + Linux
    "df": ("/bin/df", "/usr/bin/df"),
    "ping": ("/sbin/ping", "/bin/ping", "/usr/bin/ping", "/usr/sbin/ping"),
    # Linux
    "ip": ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"),
    "apt": ("/usr/bin/apt",),
    "dnf": ("/usr/bin/dnf",),
}
WINDOWS_COMMAND_PATHS = {
    "ping": (r"System32\PING.EXE",),
    "powershell": (r"System32\WindowsPowerShell\v1.0\powershell.exe",),
}
ALLOWED_COMMANDS = frozenset(POSIX_COMMAND_PATHS) | frozenset(WINDOWS_COMMAND_PATHS)

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
            return f"command not found in trusted system locations: {name}"
        if self.error == TIMEOUT:
            return f"{name} timed out"
        if self.error == OS_ERROR:
            return f"could not run {name}: {self.stderr.strip()}"
        message = _first_line(self.stderr) or _first_line(self.stdout) or "no output"
        return f"{name} exited with code {self.returncode}: {message}"


RunFunc = Callable[..., CommandResult]


def resolve_executable(name: str) -> str | None:
    """Return the trusted absolute path of an allow-listed command, or None."""
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidates = [os.path.join(system_root, p) for p in WINDOWS_COMMAND_PATHS.get(name, ())]
    else:
        candidates = list(POSIX_COMMAND_PATHS.get(name, ()))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def run_command(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run a command without a shell and capture its output.

    Never raises for ordinary failures: a missing command, a timeout, or a
    non-zero exit code are all reported through the returned CommandResult.
    """
    if not args or args[0] not in ALLOWED_COMMANDS:
        raise ValueError(f"Refusing to run command not on the allow-list: {args[:1]}")

    executable = resolve_executable(args[0])
    if executable is None:
        log.debug("%s not found in trusted system locations", args[0])
        return CommandResult(args, None, error=NOT_FOUND)

    env = None
    if os.name != "nt":
        # Force English, C-locale output so parsers see predictable text.
        env = {**os.environ, "LC_ALL": "C"}

    log.debug("Running: %s", [executable, *args[1:]])
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [executable, *args[1:]],
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
    # The encoding line is wrapped in try/catch because Constrained Language
    # Mode (AppLocker/WDAC) forbids setting it; the scripts still work without it.
    prelude = (
        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }; "
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
