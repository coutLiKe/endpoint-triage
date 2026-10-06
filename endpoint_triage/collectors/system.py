"""System information: hostname, OS, version/build, architecture, uptime."""

from __future__ import annotations

import json
import platform
import re
import socket
from datetime import datetime, timedelta, timezone

from endpoint_triage.models import CheckResult
from endpoint_triage.runner import (
    RunFunc,
    command_failure,
    read_text_file,
    run_command,
    run_powershell,
    unsupported_os,
)

OS_ID, OS_TITLE = "system.os", "Operating system"
UPTIME_ID, UPTIME_TITLE = "system.uptime", "Uptime and last reboot"

# One CIM query gives Windows OS version and boot time. Dates are formatted
# in PowerShell so Python receives a predictable string.
WINDOWS_OS_SCRIPT = (
    "$os = Get-CimInstance -ClassName Win32_OperatingSystem; "
    "[pscustomobject]@{ "
    "Caption = $os.Caption; Version = $os.Version; BuildNumber = $os.BuildNumber; "
    "LastBootUpTime = $os.LastBootUpTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') "
    "} | ConvertTo-Json -Compress"
)


def collect(os_name: str, run: RunFunc = run_command, read_file=read_text_file,
            now: datetime | None = None) -> list[CheckResult]:
    now = now or datetime.now(timezone.utc)
    base = {
        "hostname": socket.gethostname(),
        "os_family": os_name,
        "architecture": platform.machine(),
        "kernel": platform.release(),
    }
    if os_name == "Darwin":
        return [_macos_os(base, run), _macos_uptime(run, now)]
    if os_name == "Linux":
        return [_linux_os(base, read_file), _linux_uptime(read_file, now)]
    if os_name == "Windows":
        return _windows(base, run, now)
    return [
        CheckResult.ok(OS_ID, OS_TITLE, {**base, "name": os_name, "version": None, "build": None}),
        unsupported_os(UPTIME_ID, UPTIME_TITLE, os_name),
    ]


# ---------------------------------------------------------------- parsers

def parse_sw_vers(text: str) -> dict[str, str]:
    """Parse `sw_vers` ("ProductName:\\tmacOS" lines) into a dict."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    if "ProductVersion" not in values:
        raise ValueError("ProductVersion missing from sw_vers output")
    return values


def parse_os_release(text: str) -> dict[str, str]:
    """Parse /etc/os-release KEY="value" lines."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key] = value.strip().strip('"').strip("'")
    return values


def parse_macos_boottime(text: str) -> datetime:
    """Parse `sysctl -n kern.boottime`, e.g. "{ sec = 1790034971, usec = 313222 } Mon Sep ..."."""
    match = re.search(r"sec\s*=\s*(\d+)", text)
    if not match:
        raise ValueError(f"unexpected kern.boottime output: {text.strip()[:80]!r}")
    return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)


def parse_proc_uptime(text: str) -> float:
    """Parse /proc/uptime: "<seconds since boot> <idle seconds>"."""
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        raise ValueError(f"unexpected /proc/uptime content: {text.strip()[:80]!r}") from None


def parse_windows_os(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or "Caption" not in data:
        raise ValueError("unexpected Win32_OperatingSystem output")
    return data


# --------------------------------------------------------------- per-OS

def _uptime_check(boot: datetime, now: datetime) -> CheckResult:
    uptime = max((now - boot).total_seconds(), 0)
    return CheckResult.ok(UPTIME_ID, UPTIME_TITLE, {
        "uptime_seconds": int(uptime),
        "last_boot": boot.isoformat(timespec="seconds"),
    })


def _macos_os(base: dict, run: RunFunc) -> CheckResult:
    result = run(["sw_vers"])
    if not result.ok:
        return command_failure(OS_ID, OS_TITLE, result)
    try:
        info = parse_sw_vers(result.stdout)
    except ValueError as exc:
        return CheckResult.failed(OS_ID, OS_TITLE, f"could not parse sw_vers output: {exc}",
                                  debug=result.stdout)
    return CheckResult.ok(OS_ID, OS_TITLE, {
        **base,
        "name": f"{info.get('ProductName', 'macOS')} {info['ProductVersion']}",
        "version": info["ProductVersion"],
        "build": info.get("BuildVersion"),
    })


def _macos_uptime(run: RunFunc, now: datetime) -> CheckResult:
    result = run(["sysctl", "-n", "kern.boottime"])
    if not result.ok:
        return command_failure(UPTIME_ID, UPTIME_TITLE, result)
    try:
        return _uptime_check(parse_macos_boottime(result.stdout), now)
    except ValueError as exc:
        return CheckResult.failed(UPTIME_ID, UPTIME_TITLE, str(exc), debug=result.stdout)


def _linux_os(base: dict, read_file) -> CheckResult:
    try:
        info = parse_os_release(read_file("/etc/os-release"))
    except OSError as exc:
        # The kernel version is still useful even without distro details.
        return CheckResult.ok(OS_ID, OS_TITLE, {
            **base, "name": "Linux", "version": None, "build": base["kernel"],
            "note": f"/etc/os-release not readable: {exc}",
        })
    return CheckResult.ok(OS_ID, OS_TITLE, {
        **base,
        "name": info.get("PRETTY_NAME") or info.get("NAME", "Linux"),
        "version": info.get("VERSION_ID"),
        "build": base["kernel"],
    })


def _linux_uptime(read_file, now: datetime) -> CheckResult:
    try:
        seconds = parse_proc_uptime(read_file("/proc/uptime"))
    except OSError as exc:
        return CheckResult.unavailable(UPTIME_ID, UPTIME_TITLE, f"/proc/uptime not readable: {exc}")
    except ValueError as exc:
        return CheckResult.failed(UPTIME_ID, UPTIME_TITLE, str(exc))
    return _uptime_check(now - timedelta(seconds=seconds), now)


def _windows(base: dict, run: RunFunc, now: datetime) -> list[CheckResult]:
    result = run_powershell(WINDOWS_OS_SCRIPT, run=run)
    if not result.ok:
        return [command_failure(OS_ID, OS_TITLE, result),
                command_failure(UPTIME_ID, UPTIME_TITLE, result)]
    try:
        info = parse_windows_os(result.stdout)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        error = f"could not parse Win32_OperatingSystem output: {exc}"
        return [CheckResult.failed(OS_ID, OS_TITLE, error, debug=result.stdout),
                CheckResult.failed(UPTIME_ID, UPTIME_TITLE, error, debug=result.stdout)]

    os_check = CheckResult.ok(OS_ID, OS_TITLE, {
        **base,
        "name": info["Caption"],
        "version": info.get("Version"),
        "build": info.get("BuildNumber"),
    })
    try:
        boot = datetime.strptime(info["LastBootUpTime"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        uptime_check = _uptime_check(boot, now)
    except (KeyError, TypeError, ValueError):
        uptime_check = CheckResult.failed(UPTIME_ID, UPTIME_TITLE,
                                          f"unexpected LastBootUpTime value: {info.get('LastBootUpTime')!r}")
    return [os_check, uptime_check]
