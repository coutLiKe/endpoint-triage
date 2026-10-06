"""Resource usage: memory and disk volumes."""

from __future__ import annotations

import json
import re

from endpoint_triage.models import CheckResult
from endpoint_triage.runner import (
    RunFunc,
    command_failure,
    read_text_file,
    run_command,
    run_powershell,
    unsupported_os,
)

MEMORY_ID, MEMORY_TITLE = "resources.memory", "Memory"
DISKS_ID, DISKS_TITLE = "resources.disks", "Disk volumes"

WINDOWS_MEMORY_SCRIPT = (
    "Get-CimInstance -ClassName Win32_OperatingSystem | "
    "Select-Object TotalVisibleMemorySize, FreePhysicalMemory | ConvertTo-Json -Compress"
)

# DriveType 3 = local fixed disk (skips CD drives, USB card readers, network shares).
WINDOWS_DISKS_SCRIPT = (
    "ConvertTo-Json -Compress -InputObject @("
    "Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' | "
    "Select-Object DeviceID, FileSystem, Size, FreeSpace)"
)

# macOS mounts several APFS system volumes that share one container; only
# the root and Data volumes are meaningful to a technician.
MACOS_HIDDEN_PREFIXES = ("/System/Volumes/", "/Library/Developer/CoreSimulator/")
MACOS_KEPT_MOUNTS = ("/", "/System/Volumes/Data")


def collect(os_name: str, run: RunFunc = run_command, read_file=read_text_file) -> list[CheckResult]:
    if os_name == "Darwin":
        return [_macos_memory(run), _posix_disks(os_name, run)]
    if os_name == "Linux":
        return [_linux_memory(read_file), _posix_disks(os_name, run)]
    if os_name == "Windows":
        return [_windows_memory(run), _windows_disks(run)]
    return [unsupported_os(MEMORY_ID, MEMORY_TITLE, os_name),
            unsupported_os(DISKS_ID, DISKS_TITLE, os_name)]


def memory_data(total: int, available: int | None, note: str | None = None) -> dict:
    data: dict = {"total_bytes": total, "available_bytes": available,
                  "used_bytes": None, "used_percent": None}
    if available is not None and total > 0:
        data["used_bytes"] = total - available
        data["used_percent"] = round((total - available) / total * 100, 1)
    if note:
        data["note"] = note
    return data


def volume(mount: str, device: str, used: int, free: int, total: int | None = None) -> dict:
    # Like `df`, utilization is used / (used + free). On Linux this excludes
    # blocks reserved for root, which is what users actually run out of.
    usable = used + free
    return {
        "mount": mount,
        "device": device,
        "total_bytes": total if total is not None else usable,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": round(used / usable * 100, 1) if usable else 0.0,
    }


# ---------------------------------------------------------------- parsers

def parse_meminfo(text: str) -> dict:
    """Parse /proc/meminfo ("MemTotal:  16314044 kB")."""
    values = {}
    for line in text.splitlines():
        match = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    if "MemTotal" not in values:
        raise ValueError("MemTotal missing from /proc/meminfo")
    return memory_data(values["MemTotal"], values.get("MemAvailable"))


def parse_vm_stat(text: str) -> int:
    """Return approximate available bytes from macOS `vm_stat`.

    Available is estimated as free + inactive + speculative pages, which is
    memory macOS can hand to applications without swapping.
    """
    page_size = re.search(r"page size of (\d+) bytes", text)
    if not page_size:
        raise ValueError("page size missing from vm_stat output")
    pages = {}
    for line in text.splitlines():
        match = re.match(r"Pages ([\w ]+):\s+(\d+)\.", line)
        if match:
            pages[match.group(1)] = int(match.group(2))
    if "free" not in pages:
        raise ValueError("'Pages free' missing from vm_stat output")
    available_pages = pages["free"] + pages.get("inactive", 0) + pages.get("speculative", 0)
    return available_pages * int(page_size.group(1))


def parse_df(text: str, os_name: str) -> list[dict]:
    """Parse POSIX `df -P -k` output into volume dicts, keeping real disks only."""
    rows = []
    for line in text.splitlines()[1:]:
        # Mount points may contain spaces, so split at most 5 times.
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        device, blocks, used, available, _capacity, mount = parts
        try:
            rows.append(volume(mount, device, int(used) * 1024, int(available) * 1024,
                               total=int(blocks) * 1024))
        except ValueError:
            continue  # header repeats or pseudo filesystems with odd columns

    kept = [r for r in rows if _is_real_volume(r["device"], r["mount"], os_name)]
    if not kept and rows:
        # Containers often mount root from "overlay"; fall back to "/".
        kept = [r for r in rows if r["mount"] == "/"]
    return kept


def _is_real_volume(device: str, mount: str, os_name: str) -> bool:
    if not device.startswith("/dev/") or device.startswith("/dev/loop"):
        return False  # tmpfs, devfs, autofs maps, snap squashfs loop devices
    if os_name == "Darwin" and mount not in MACOS_KEPT_MOUNTS:
        return not mount.startswith(MACOS_HIDDEN_PREFIXES)
    return True


def parse_windows_disks(text: str) -> list[dict]:
    items = json.loads(text) if text.strip() else []
    if isinstance(items, dict):
        items = [items]
    volumes = []
    for item in items:
        size, free = item.get("Size"), item.get("FreeSpace")
        if size is None or free is None:
            continue
        volumes.append(volume(item["DeviceID"], item.get("FileSystem") or "", int(size) - int(free),
                              int(free), total=int(size)))
    return volumes


# --------------------------------------------------------------- per-OS

def _macos_memory(run: RunFunc) -> CheckResult:
    total = run(["sysctl", "-n", "hw.memsize"])
    if not total.ok:
        return command_failure(MEMORY_ID, MEMORY_TITLE, total)
    try:
        total_bytes = int(total.stdout.strip())
    except ValueError:
        return CheckResult.failed(MEMORY_ID, MEMORY_TITLE, "unexpected hw.memsize output", debug=total.stdout)

    stats = run(["vm_stat"])
    if not stats.ok:
        return CheckResult.ok(MEMORY_ID, MEMORY_TITLE, memory_data(
            total_bytes, None, note=f"available memory unknown: {stats.describe_failure()}"))
    try:
        available = parse_vm_stat(stats.stdout)
    except ValueError as exc:
        return CheckResult.ok(MEMORY_ID, MEMORY_TITLE, memory_data(
            total_bytes, None, note=f"available memory unknown: {exc}"))
    return CheckResult.ok(MEMORY_ID, MEMORY_TITLE, memory_data(
        total_bytes, available, note="available = free + inactive + speculative pages (approximate)"))


def _linux_memory(read_file) -> CheckResult:
    try:
        text = read_file("/proc/meminfo")
    except OSError as exc:
        return CheckResult.unavailable(MEMORY_ID, MEMORY_TITLE, f"/proc/meminfo not readable: {exc}")
    try:
        return CheckResult.ok(MEMORY_ID, MEMORY_TITLE, parse_meminfo(text))
    except ValueError as exc:
        return CheckResult.failed(MEMORY_ID, MEMORY_TITLE, str(exc))


def _windows_memory(run: RunFunc) -> CheckResult:
    result = run_powershell(WINDOWS_MEMORY_SCRIPT, run=run)
    if not result.ok:
        return command_failure(MEMORY_ID, MEMORY_TITLE, result)
    try:
        info = json.loads(result.stdout)
        # Win32_OperatingSystem reports kilobytes.
        data = memory_data(int(info["TotalVisibleMemorySize"]) * 1024, int(info["FreePhysicalMemory"]) * 1024)
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult.failed(MEMORY_ID, MEMORY_TITLE, f"unexpected memory output: {exc}",
                                  debug=result.stdout)
    return CheckResult.ok(MEMORY_ID, MEMORY_TITLE, data)


def _posix_disks(os_name: str, run: RunFunc) -> CheckResult:
    result = run(["df", "-P", "-k"])
    # df exits 1 if *some* filesystem could not be read (e.g. a stale network
    # mount) but still prints the rest, so use the output if there is any.
    if result.error or not result.stdout.strip():
        return command_failure(DISKS_ID, DISKS_TITLE, result)
    volumes = parse_df(result.stdout, os_name)
    if not volumes:
        return CheckResult.failed(DISKS_ID, DISKS_TITLE, "no disk volumes found in df output",
                                  debug=result.stdout)
    data: dict = {"volumes": volumes}
    if result.returncode != 0:
        data["note"] = f"df reported an error for some filesystems: {result.describe_failure()}"
    return CheckResult.ok(DISKS_ID, DISKS_TITLE, data)


def _windows_disks(run: RunFunc) -> CheckResult:
    result = run_powershell(WINDOWS_DISKS_SCRIPT, run=run)
    if not result.ok:
        return command_failure(DISKS_ID, DISKS_TITLE, result)
    try:
        volumes = parse_windows_disks(result.stdout)
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult.failed(DISKS_ID, DISKS_TITLE, f"unexpected disk output: {exc}", debug=result.stdout)
    if not volumes:
        return CheckResult.failed(DISKS_ID, DISKS_TITLE, "no fixed disks reported", debug=result.stdout)
    return CheckResult.ok(DISKS_ID, DISKS_TITLE, {"volumes": volumes})
