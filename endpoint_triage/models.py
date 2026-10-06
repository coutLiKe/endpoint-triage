"""Data models shared by the collectors, the findings engine, and the reporters.

Collectors produce CheckResult objects, the findings engine turns them into
Finding objects, and reporters render a Report. Keeping these as plain
dataclasses means every layer agrees on the same structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Outcome of a single diagnostic check."""

    OK = "ok"                    # The check ran and produced a result.
    FAILED = "failed"            # The check ran but failed (error, bad output, or test failed).
    UNAVAILABLE = "unavailable"  # The platform cannot provide this (missing command, unsupported OS).
    SKIPPED = "skipped"          # Deliberately not run (e.g. no gateway to ping).


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {"INFO": 1, "WARNING": 2, "CRITICAL": 3}[self.value]


@dataclass
class CheckResult:
    id: str
    title: str
    status: Status
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Extra troubleshooting detail (raw stderr, tracebacks). Only written to
    # reports when the tool runs with --debug.
    debug: str | None = None

    @classmethod
    def ok(cls, id: str, title: str, data: dict[str, Any]) -> CheckResult:
        return cls(id, title, Status.OK, data=data)

    @classmethod
    def failed(cls, id: str, title: str, error: str, debug: str | None = None,
               data: dict[str, Any] | None = None) -> CheckResult:
        return cls(id, title, Status.FAILED, data=data or {}, error=error, debug=debug)

    @classmethod
    def unavailable(cls, id: str, title: str, error: str, debug: str | None = None) -> CheckResult:
        return cls(id, title, Status.UNAVAILABLE, error=error, debug=debug)

    @classmethod
    def skipped(cls, id: str, title: str, reason: str) -> CheckResult:
        return cls(id, title, Status.SKIPPED, error=reason)

    def to_dict(self, include_debug: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
        }
        if include_debug:
            result["debug"] = self.debug
        return result


@dataclass
class Finding:
    severity: Severity
    title: str
    explanation: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "title": self.title,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


# Without these, the scan cannot honestly call the endpoint healthy, so a
# missing or failed core check makes the overall status UNKNOWN.
CORE_CHECK_IDS = ("system.os", "resources.memory", "resources.disks", "network.interfaces")

# Report sections in display order: (key, heading).
SECTIONS = [
    ("system", "System Information"),
    ("resources", "Resource Usage"),
    ("network", "Network Configuration"),
    ("connectivity", "Connectivity Tests"),
    ("updates", "Operating System Updates"),
]


@dataclass
class Report:
    tool_version: str
    os_name: str
    generated_at: datetime
    duration_seconds: float
    checks: dict[str, list[CheckResult]]
    findings: list[Finding] = field(default_factory=list)
    # The settings this scan ran with (targets, skipped checks), so a reader
    # knows exactly what was tested.
    options: dict[str, Any] = field(default_factory=dict)

    def all_checks(self) -> list[CheckResult]:
        return [check for key, _ in SECTIONS for check in self.checks.get(key, [])]

    def get(self, check_id: str) -> CheckResult | None:
        for check in self.all_checks():
            if check.id == check_id:
                return check
        return None

    def highest_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=lambda s: s.rank)

    def incomplete_core_checks(self) -> list[str]:
        incomplete = []
        for check_id in CORE_CHECK_IDS:
            check = self.get(check_id)
            if check is None or check.status != Status.OK:
                incomplete.append(check_id)
        return incomplete

    def overall_status(self) -> str:
        """Nagios-style result: CRITICAL > UNKNOWN > WARNING > OK.

        INFO findings never change the result. UNKNOWN means core
        diagnostics are missing, so "OK" would be a claim the scan can't back up.
        """
        highest = self.highest_severity()
        if highest == Severity.CRITICAL:
            return "CRITICAL"
        if self.incomplete_core_checks():
            return "UNKNOWN"
        if highest == Severity.WARNING:
            return "WARNING"
        return "OK"
