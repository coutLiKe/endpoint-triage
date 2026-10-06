"""Shared test utilities: a fake command runner and fixture loading."""

from __future__ import annotations

from pathlib import Path

from endpoint_triage.runner import NOT_FOUND, CommandResult

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult([], returncode, stdout, stderr)


def fail(returncode: int = 1, stderr: str = "boom", stdout: str = "") -> CommandResult:
    return CommandResult([], returncode, stdout, stderr)


def not_found() -> CommandResult:
    return CommandResult([], None, error=NOT_FOUND)


class FakeRunner:
    """Stands in for runner.run_command.

    `responses` maps a substring of the command line (e.g. "vm_stat" or
    "Win32_LogicalDisk") to the CommandResult to return. The first matching
    key wins; unknown commands behave as "command not found".
    Every call is recorded in `calls` so tests can assert what was run.
    """

    def __init__(self, responses: dict[str, CommandResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: float = 0) -> CommandResult:
        self.calls.append(args)
        command_line = " ".join(args)
        for key, result in self.responses.items():
            if key in command_line:
                return CommandResult(args, result.returncode, result.stdout, result.stderr, result.error)
        return CommandResult(args, None, error=NOT_FOUND)


def fake_files(files: dict[str, str]):
    """Return a read_file function backed by a dict; missing paths raise OSError."""

    def read_file(path: str) -> str:
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    return read_file
