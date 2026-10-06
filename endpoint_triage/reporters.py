"""Render a Report as a human-readable text file and a structured JSON file.

Reporters only format data; they never collect anything. That separation
means a new output format can be added without touching any collector.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any, Callable

from endpoint_triage.models import SECTIONS, CheckResult, Report, Severity, Status

SCHEMA_VERSION = "1.1"
WIDTH = 76
LABEL_WIDTH = 18


# ---------------------------------------------------------------- helpers

def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / 1024 ** 3:.1f} GB"


def format_duration(seconds: int) -> str:
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)


def overall_status(report: Report) -> str:
    return report.overall_status()


def finding_counts(report: Report) -> dict[str, int]:
    counts = {s.value: 0 for s in (Severity.CRITICAL, Severity.WARNING, Severity.INFO)}
    for finding in report.findings:
        counts[finding.severity.value] += 1
    return counts


def check_counts(report: Report) -> dict[str, int]:
    counts = {s.value: 0 for s in Status}
    for check in report.all_checks():
        counts[check.status.value] += 1
    return counts


def _summary_fields(report: Report) -> tuple[str, str]:
    os_check = report.get("system.os")
    if os_check and os_check.status == Status.OK:
        return os_check.data.get("hostname") or "unknown", os_check.data.get("name") or report.os_name
    return "unknown", report.os_name


def _line(label: str, value: Any, indent: int = 2) -> str:
    return f"{' ' * indent}{label:<{LABEL_WIDTH}}: {value}"


def _heading(title: str) -> list[str]:
    return ["", title, "-" * len(title)]


def _wrap(text: str, indent: int) -> list[str]:
    return textwrap.wrap(text, width=WIDTH, initial_indent=" " * indent, subsequent_indent=" " * indent)


# ------------------------------------------------------ section renderers

def _render_os(check: CheckResult) -> list[str]:
    d = check.data
    version = d.get("version") or "unknown"
    if d.get("build"):
        version += f" (build {d['build']})"
    lines = [
        _line("Hostname", d.get("hostname")),
        _line("Operating system", d.get("name")),
        _line("Version", version),
        _line("Architecture", d.get("architecture") or "unknown"),
        _line("Kernel", d.get("kernel") or "unknown"),
    ]
    if d.get("note"):
        lines.append(_line("Note", d["note"]))
    return lines


def _render_uptime(check: CheckResult) -> list[str]:
    boot = check.data["last_boot"].replace("T", " ").replace("+00:00", " UTC")
    return [_line("Uptime", format_duration(check.data["uptime_seconds"])), _line("Last reboot", boot)]


def _render_memory(check: CheckResult) -> list[str]:
    d = check.data
    if d.get("used_percent") is None:
        value = f"{format_bytes(d['total_bytes'])} total (usage unknown)"
    else:
        value = (f"{format_bytes(d['used_bytes'])} used of {format_bytes(d['total_bytes'])} "
                 f"({d['used_percent']:.1f}%), {format_bytes(d['available_bytes'])} available")
    lines = [_line("Memory", value)]
    if d.get("note"):
        lines.append(_line("Note", d["note"]))
    return lines


def _render_disks(check: CheckResult) -> list[str]:
    volumes = check.data["volumes"]
    width = max([len("Mount")] + [len(v["mount"]) for v in volumes])
    lines = ["  Disk volumes:",
             f"    {'Mount':<{width}}  {'Total':>10}  {'Used':>10}  {'Free':>10}  {'Used %':>6}"]
    for v in volumes:
        lines.append(f"    {v['mount']:<{width}}  {format_bytes(v['total_bytes']):>10}  "
                     f"{format_bytes(v['used_bytes']):>10}  {format_bytes(v['free_bytes']):>10}  "
                     f"{v['used_percent']:>5.1f}%")
    if check.data.get("note"):
        lines.append(_line("Note", check.data["note"]))
    return lines


def _render_interfaces(check: CheckResult) -> list[str]:
    interfaces = check.data["interfaces"]
    if not interfaces:
        return ["  Interfaces: none found"]
    lines = []
    for i in interfaces:
        lines.append(f"  {i['name']} ({i['status']})")
        lines.append(_line("MAC", i["mac"] or "n/a", indent=4))
        lines.append(_line("IPv4", ", ".join(i["ipv4"]) or "none", indent=4))
        lines.append(_line("IPv6", ", ".join(i["ipv6"]) or "none", indent=4))
    return lines


def _render_gateway(check: CheckResult) -> list[str]:
    gateway = check.data.get("gateway")
    if not gateway:
        return [_line("Default gateway", "none")]
    via = f" (via {check.data['interface']})" if check.data.get("interface") else ""
    return [_line("Default gateway", gateway + via)]


def _render_dns_servers(check: CheckResult) -> list[str]:
    lines = [_line("DNS servers", ", ".join(check.data["servers"]) or "none")]
    if check.data.get("note"):
        lines.append(_line("Note", check.data["note"]))
    return lines


def _render_ping(check: CheckResult) -> list[str]:
    latency = check.data.get("latency_ms")
    result = "reply" + (f", avg {latency:.1f} ms" if latency is not None else "")
    return [f"  [OK]          {check.title} ({check.data['target']}): {result}"]


def _render_dns_resolution(check: CheckResult) -> list[str]:
    d = check.data
    return [f"  [OK]          {check.title} ({d['hostname']}): {', '.join(d['addresses'])} "
            f"in {d['duration_ms']:.0f} ms"]


def _render_updates(check: CheckResult) -> list[str]:
    d = check.data
    count = str(d["pending_count"])
    if d.get("restart_required"):
        count += " (restart required)"
    lines = [_line("Source", d["source"]), _line("Pending updates", count)]
    lines += [f"    - {name}" for name in d["updates"][:20]]
    if len(d["updates"]) > 20:
        lines.append(f"    - ... and {len(d['updates']) - 20} more (see JSON report)")
    if d.get("note"):
        lines.append(_line("Note", d["note"]))
    return lines


def _render_proxy(check: CheckResult) -> list[str]:
    d = check.data
    if not d["configured"]:
        lines = [_line("Proxy", f"none detected (checked: {', '.join(d['sources_checked'])})")]
    else:
        lines = []
        for entry in d["proxies"]:
            lines.append(_line("Proxy", entry["source"]))
            if entry.get("proxy"):
                lines.append(_line("Server", entry["proxy"], indent=4))
            if entry.get("pac_url"):
                lines.append(_line("PAC script", entry["pac_url"], indent=4))
            if entry.get("auto_detect"):
                lines.append(_line("Auto-detect", "enabled (WPAD)", indent=4))
            if entry.get("bypass"):
                lines.append(_line("Bypass", entry["bypass"], indent=4))
    if d.get("note"):
        lines.append(_line("Note", d["note"]))
    return lines


RENDERERS: dict[str, Callable[[CheckResult], list[str]]] = {
    "system.os": _render_os,
    "system.uptime": _render_uptime,
    "resources.memory": _render_memory,
    "resources.disks": _render_disks,
    "network.interfaces": _render_interfaces,
    "network.gateway": _render_gateway,
    "network.dns_servers": _render_dns_servers,
    "network.proxy": _render_proxy,
    "connectivity.gateway_ping": _render_ping,
    "connectivity.public_ip_ping": _render_ping,
    "connectivity.dns_resolution": _render_dns_resolution,
    "updates.os": _render_updates,
}


def is_test_result(check: CheckResult) -> bool:
    """A failed ping or DNS lookup is a result ("no reply"), not a check that
    could not run, so it belongs with the other connectivity results."""
    return check.id.startswith("connectivity.") and check.status == Status.FAILED


def checks_not_completed(report: Report) -> list[CheckResult]:
    return [c for c in report.all_checks() if c.status != Status.OK and not is_test_result(c)]


def _render_check(check: CheckResult) -> list[str]:
    if check.id.startswith("connectivity.") and check.status != Status.OK:
        target = check.data.get("target") or check.data.get("hostname")
        label = f"{check.title} ({target})" if target else check.title
        return [f"  [{check.status.value.upper()}]{' ' * (12 - len(check.status.value))}{label}: {check.error}"]
    if check.status != Status.OK:
        # Details appear once, under Errors / Unavailable Checks.
        return [_line(check.title, f"not completed ({check.status.value}), see Errors / Unavailable Checks")]
    renderer = RENDERERS.get(check.id)
    if renderer is None:  # fallback for any future check without a custom layout
        return [_line(key, value) for key, value in check.data.items()]
    return renderer(check)


# ------------------------------------------------------------ text report

def _option_lines(report: Report) -> list[str]:
    options = report.options
    if not options:
        return []
    lines = [_line("Test targets", f"ping {options['ping_target']}, resolve {options['dns_name']}")]
    if options.get("skip_updates"):
        lines.append(_line("Skipped", "OS update check (--skip-updates)"))
    return lines


def render_text(report: Report, debug: bool = False) -> str:
    hostname, os_label = _summary_fields(report)
    findings = finding_counts(report)
    checks = check_counts(report)
    generated = report.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "Endpoint Triage Report",
        "======================",
        f"Generated {generated} by endpoint-triage {report.tool_version}",
        "Read-only scan: no system settings were changed.",
    ]

    lines += _heading("Summary")
    lines += [
        _line("Hostname", hostname),
        _line("Operating system", os_label),
        _line("Overall status", overall_status(report)),
        *([_line("Incomplete", ", ".join(report.incomplete_core_checks()))]
          if report.incomplete_core_checks() else []),
        _line("Findings", f"{findings['CRITICAL']} critical, {findings['WARNING']} warning, {findings['INFO']} info"),
        _line("Checks", ", ".join(f"{count} {status}" for status, count in checks.items())),
        _line("Scan duration", f"{report.duration_seconds:.1f} s"),
        *_option_lines(report),
    ]

    lines += _heading("Findings")
    if not report.findings:
        lines.append("  No issues detected by the automated checks.")
    for finding in report.findings:
        lines.append(f"[{finding.severity.value}] {finding.title}")
        lines += _wrap(finding.explanation, 2)
        if finding.evidence:
            lines.append("  Evidence:")
            lines += [f"    {item}" if item.startswith("- ") else f"    - {item}" for item in finding.evidence]
        lines.append("")
    if report.findings:
        lines.pop()  # no blank line before the next heading's own blank line

    for key, heading in SECTIONS:
        lines += _heading(heading)
        section_checks = report.checks.get(key, [])
        if not section_checks:
            lines.append("  No checks ran in this section.")
        for check in section_checks:
            lines += _render_check(check)

    lines += _heading("Errors / Unavailable Checks")
    problems = checks_not_completed(report)
    if not problems:
        lines.append("  None. All checks completed.")
    for check in problems:
        lines.append(f"  [{check.status.value.upper()}] {check.title} ({check.id}): {check.error}")
        if debug and check.debug:
            lines += [f"      | {line}" for line in check.debug.strip().splitlines()]
    if problems and not debug:
        lines += ["", "  Run with --debug for more troubleshooting detail."]

    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ JSON report

def report_to_dict(report: Report, debug: bool = False) -> dict[str, Any]:
    hostname, os_label = _summary_fields(report)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "endpoint-triage", "version": report.tool_version},
        "generated_at": report.generated_at.isoformat(timespec="seconds"),
        "duration_seconds": round(report.duration_seconds, 2),
        "summary": {
            "hostname": hostname,
            "os": os_label,
            "os_family": report.os_name,
            "overall_status": overall_status(report),
            "finding_counts": finding_counts(report),
            "check_counts": check_counts(report),
        },
        "scan_options": report.options,
        "findings": [f.to_dict() for f in report.findings],
        "sections": {
            key: [check.to_dict(include_debug=debug) for check in report.checks.get(key, [])]
            for key, _ in SECTIONS
        },
    }


def render_json(report: Report, debug: bool = False) -> str:
    return json.dumps(report_to_dict(report, debug), indent=2) + "\n"


# ----------------------------------------------------------------- files

def report_basename(report: Report) -> str:
    hostname, _ = _summary_fields(report)
    safe_host = re.sub(r"[^A-Za-z0-9.-]", "_", hostname)[:64] or "unknown"
    return f"triage-{safe_host}-{report.generated_at.strftime('%Y%m%d-%H%M%S')}"


def write_reports(report: Report, output_dir: Path, debug: bool = False) -> tuple[Path, Path]:
    """Write both reports into output_dir (created if needed). Raises OSError."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Not Path.with_suffix(): hostnames such as "pc.corp.local" contain dots.
    base = report_basename(report)
    text_path, json_path = output_dir / f"{base}.txt", output_dir / f"{base}.json"
    text_path.write_text(render_text(report, debug), encoding="utf-8")
    json_path.write_text(render_json(report, debug), encoding="utf-8")
    return text_path, json_path
