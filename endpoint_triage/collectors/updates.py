"""Pending operating-system updates (detection only; nothing is installed)."""

from __future__ import annotations

import json
import re

from endpoint_triage.models import CheckResult
from endpoint_triage.runner import (
    NOT_FOUND,
    RunFunc,
    command_failure,
    run_command,
    run_powershell,
    unsupported_os,
)

UPDATES_ID, UPDATES_TITLE = "updates.os", "Pending OS updates"

# Searching can take a while because the OS contacts its update service.
UPDATE_TIMEOUT = 180

# Windows Update Agent COM API. Searching works without admin rights; the
# script only searches and never downloads or installs.
WINDOWS_UPDATE_SCRIPT = (
    "$session = New-Object -ComObject Microsoft.Update.Session; "
    "$result = $session.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); "
    "ConvertTo-Json -Compress -InputObject @($result.Updates | ForEach-Object { "
    "[pscustomobject]@{ Title = $_.Title; RebootRequired = $_.RebootRequired } })"
)


def collect(os_name: str, run: RunFunc = run_command) -> list[CheckResult]:
    if os_name == "Darwin":
        return [_macos(run)]
    if os_name == "Windows":
        return [_windows(run)]
    if os_name == "Linux":
        return [_linux(run)]
    return [unsupported_os(UPDATES_ID, UPDATES_TITLE, os_name)]


def updates_data(source: str, names: list[str], restart_required: bool | None = None,
                 note: str | None = None) -> dict:
    data: dict = {"source": source, "pending_count": len(names), "updates": names,
                  "restart_required": restart_required}
    if note:
        data["note"] = note
    return data


# ---------------------------------------------------------------- parsers

def parse_softwareupdate(output: str) -> tuple[list[str], bool]:
    """Parse `softwareupdate -l`. Returns (update titles, restart required)."""
    if "No new software available" in output:
        return [], False
    labels = re.findall(r"^\s*\* (?:Label: )?(.+)$", output, flags=re.MULTILINE)
    titles = re.findall(r"Title: ([^,]+)", output)
    if not labels and not titles:
        raise ValueError("unrecognized softwareupdate output")
    return titles or labels, "Action: restart" in output or "[restart]" in output


def parse_apt_upgradable(output: str) -> list[str]:
    """Parse `apt list --upgradable` lines like
    "openssl/noble-updates 3.0.13-0ubuntu3.4 amd64 [upgradable from: 3.0.13-0ubuntu3.1]"."""
    return [line.split("/", 1)[0] for line in output.splitlines() if "[upgradable from:" in line]


def parse_dnf_check_update(output: str) -> list[str]:
    """Parse `dnf check-update` lines like "openssl.x86_64  1:3.0.7-28.el9  baseos"."""
    names = []
    for line in output.splitlines():
        if line.startswith("Obsoleting"):
            break  # the rest lists packages being replaced, not updates
        fields = line.split()
        if len(fields) == 3 and "." in fields[0]:
            names.append(fields[0].rsplit(".", 1)[0])
    return names


# --------------------------------------------------------------- per-OS

def _macos(run: RunFunc) -> CheckResult:
    result = run(["softwareupdate", "-l"], timeout=UPDATE_TIMEOUT)
    output = result.stdout + "\n" + result.stderr  # "No new software" is printed on stderr
    if not result.ok:
        return command_failure(UPDATES_ID, UPDATES_TITLE, result)
    try:
        names, restart = parse_softwareupdate(output)
    except ValueError as exc:
        return CheckResult.failed(UPDATES_ID, UPDATES_TITLE, f"update status could not be determined: {exc}",
                                  debug=output)
    return CheckResult.ok(UPDATES_ID, UPDATES_TITLE, updates_data("softwareupdate", names, restart))


def _windows(run: RunFunc) -> CheckResult:
    result = run_powershell(WINDOWS_UPDATE_SCRIPT, run=run, timeout=UPDATE_TIMEOUT)
    if not result.ok:
        return command_failure(UPDATES_ID, UPDATES_TITLE, result)
    try:
        items = json.loads(result.stdout) if result.stdout.strip() else []
        if isinstance(items, dict):
            items = [items]
        names = [item["Title"] for item in items]
        restart = any(item.get("RebootRequired") for item in items)
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult.failed(UPDATES_ID, UPDATES_TITLE, f"unexpected Windows Update output: {exc}",
                                  debug=result.stdout)
    return CheckResult.ok(UPDATES_ID, UPDATES_TITLE, updates_data("Windows Update Agent", names, restart))


def _linux(run: RunFunc) -> CheckResult:
    apt = run(["apt", "list", "--upgradable"], timeout=UPDATE_TIMEOUT)
    if apt.error != NOT_FOUND:
        if not apt.ok:
            return command_failure(UPDATES_ID, UPDATES_TITLE, apt)
        return CheckResult.ok(UPDATES_ID, UPDATES_TITLE, updates_data(
            "apt", parse_apt_upgradable(apt.stdout),
            note="based on the local package index; it may be stale if `apt update` has not run recently"))

    # -C: use the cached metadata only, so a non-root user never writes to
    # the dnf cache or contacts repositories.
    dnf = run(["dnf", "-C", "-q", "check-update"], timeout=UPDATE_TIMEOUT)
    if dnf.error == NOT_FOUND:
        return CheckResult.unavailable(UPDATES_ID, UPDATES_TITLE,
                                       "no supported package manager found (apt or dnf)")
    # dnf check-update exit codes: 0 = no updates, 100 = updates available, 1 = error.
    if dnf.error is None and dnf.returncode in (0, 100):
        return CheckResult.ok(UPDATES_ID, UPDATES_TITLE, updates_data(
            "dnf", parse_dnf_check_update(dnf.stdout) if dnf.returncode == 100 else [],
            note="based on cached repository metadata; it may be stale"))
    return command_failure(UPDATES_ID, UPDATES_TITLE, dnf)
