"""Turn raw check results into troubleshooting signals ("findings").

Findings point a technician at likely problems. They describe what the
evidence suggests; they never claim to prove a root cause. Checks that
could not run are not findings: reporters list them once under
"Errors / Unavailable Checks", and missing core checks make the overall
status UNKNOWN (see Report.overall_status).
"""

from __future__ import annotations

from endpoint_triage.models import CheckResult, Finding, Report, Severity, Status

# Thresholds (documented in the README).
DISK_WARNING_PERCENT = 85.0
DISK_CRITICAL_PERCENT = 95.0
DISK_CRITICAL_FREE_BYTES = 5 * 1024 ** 3
# The free-space rule only applies to volumes big enough to hold an OS or
# user data; small partitions such as a 512 MB EFI partition are skipped.
DISK_FREE_RULE_MIN_TOTAL_BYTES = 20 * 1024 ** 3
MEMORY_WARNING_AVAILABLE_PERCENT = 10.0
UPTIME_INFO_DAYS = 30
DISCONNECTED_INTERFACES_INFO_COUNT = 2

APIPA_PREFIX = "169.254."


def analyze(report: Report) -> list[Finding]:
    findings: list[Finding] = []
    findings += disk_findings(report.get("resources.disks"))
    findings += memory_findings(report.get("resources.memory"))
    findings += uptime_findings(report.get("system.uptime"))
    findings += interface_findings(report.get("network.interfaces"))
    findings += dns_server_findings(report.get("network.dns_servers"))
    findings += proxy_findings(report.get("network.proxy"))
    findings += connectivity_findings(
        report.get("network.gateway"),
        report.get("connectivity.gateway_ping"),
        report.get("connectivity.public_ip_ping"),
        report.get("connectivity.dns_resolution"),
    )
    findings += update_findings(report.get("updates.os"))
    # Most severe first; sorted() is stable so equal severities keep their order.
    return sorted(findings, key=lambda f: f.severity.rank, reverse=True)


def _ok(check: CheckResult | None) -> bool:
    return check is not None and check.status == Status.OK


def _failed(check: CheckResult | None) -> bool:
    return check is not None and check.status == Status.FAILED


def gib(value: int) -> str:
    return f"{value / 1024 ** 3:.1f} GB"


# ------------------------------------------------------------ resources

def disk_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check):
        return []
    findings = []
    for vol in check.data.get("volumes", []):
        percent, free, total = vol["used_percent"], vol["free_bytes"], vol["total_bytes"]
        evidence = [f"Volume {vol['mount']} is {percent:.1f}% used, {gib(free)} free of {gib(total)}"]
        low_free = total >= DISK_FREE_RULE_MIN_TOTAL_BYTES and free < DISK_CRITICAL_FREE_BYTES
        if percent >= DISK_CRITICAL_PERCENT or low_free:
            findings.append(Finding(
                "disk.critical_low_space", Severity.CRITICAL, f"Critically low disk space on {vol['mount']}",
                f"{vol['mount']} is at or above {DISK_CRITICAL_PERCENT:.0f}% used or has less than "
                f"{gib(DISK_CRITICAL_FREE_BYTES)} free. Very low free space can cause failed updates, "
                "application crashes and slow performance.", evidence))
        elif percent >= DISK_WARNING_PERCENT:
            findings.append(Finding(
                "disk.high_usage", Severity.WARNING, f"High disk usage on {vol['mount']}",
                f"{vol['mount']} is above the {DISK_WARNING_PERCENT:.0f}% usage warning threshold. "
                "Consider freeing space before it affects updates or performance.", evidence))
    return findings


def memory_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check) or check.data.get("used_percent") is None:
        return []
    available_percent = 100 - check.data["used_percent"]
    if available_percent >= MEMORY_WARNING_AVAILABLE_PERCENT:
        return []
    return [Finding(
        "memory.low_available", Severity.WARNING, "Low available memory",
        f"Less than {MEMORY_WARNING_AVAILABLE_PERCENT:.0f}% of memory is available. This may explain "
        "slowness or applications freezing; check which processes are using the most memory.",
        [f"{gib(check.data['available_bytes'])} available of {gib(check.data['total_bytes'])} "
         f"({available_percent:.1f}%)"])]


def uptime_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check):
        return []
    days = check.data["uptime_seconds"] / 86400
    if days < UPTIME_INFO_DAYS:
        return []
    return [Finding(
        "system.long_uptime", Severity.INFO, "System has not been restarted recently",
        f"The system has been running for more than {UPTIME_INFO_DAYS} days. Pending updates and "
        "long-running resource leaks are often resolved by a restart.",
        [f"Uptime: {days:.0f} days (last boot {check.data['last_boot']})"])]


# -------------------------------------------------------------- network

def interface_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check):
        return []
    interfaces = check.data.get("interfaces", [])
    findings = []

    apipa = [(i["name"], addr) for i in interfaces for addr in i["ipv4"] if addr.startswith(APIPA_PREFIX)]
    if apipa:
        findings.append(Finding(
            "network.apipa_address", Severity.WARNING, "Self-assigned (APIPA) IPv4 address",
            "An interface has a 169.254.x.x address, which the OS assigns itself when it cannot reach "
            "a DHCP server. This usually means DHCP failed on that network.",
            [f"{name}: {addr}" for name, addr in apipa]))

    connected = [i for i in interfaces if i["status"] == "up"
                 and any(not a.startswith(APIPA_PREFIX) for a in i["ipv4"])]
    if not connected:
        findings.append(Finding(
            "network.no_active_connection", Severity.WARNING, "No active network connection",
            "No network interface is up with a usable IPv4 address. Check the cable or Wi-Fi "
            "connection and whether the network adapter is enabled.",
            [f"{i['name']}: {i['status']}, IPv4: {', '.join(i['ipv4']) or 'none'}" for i in interfaces]
            or ["No network interfaces found"]))

    down = [i["name"] for i in interfaces if i["status"] == "down"]
    if len(down) >= DISCONNECTED_INTERFACES_INFO_COUNT:
        findings.append(Finding(
            "network.interfaces_disconnected", Severity.INFO, "Multiple network interfaces are disconnected",
            "Several interfaces are down. This is normal for unused ports or adapters, but confirm "
            "the interface the user expects to be using is connected.",
            [f"Disconnected: {', '.join(down)}"]))
    return findings


def dns_server_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check) or check.data.get("servers"):
        return []
    return [Finding("network.no_dns_servers", Severity.WARNING, "No DNS servers configured",
                    "No DNS servers were found, so hostnames cannot be resolved.",
                    ["DNS server list is empty"])]


def describe_proxy(entry: dict) -> str:
    parts = []
    if entry.get("proxy"):
        parts.append(f"proxy {entry['proxy']}")
    if entry.get("pac_url"):
        parts.append(f"PAC script {entry['pac_url']}")
    if entry.get("auto_detect"):
        parts.append("auto-detect (WPAD)")
    return f"{entry['source']}: {', '.join(parts)}"


def proxy_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check) or not check.data.get("configured"):
        return []
    return [Finding(
        "network.proxy_configured", Severity.INFO, "A proxy is configured",
        "Web traffic from this machine is sent through a proxy or a proxy auto-config (PAC) script. "
        "The ping and DNS tests do not use the proxy, so they can pass while websites fail if the "
        "proxy is unreachable, the PAC script cannot be downloaded, or the proxy rejects the user.",
        [describe_proxy(entry) for entry in check.data["proxies"]])]


def _ping_evidence(check: CheckResult | None) -> str:
    if check is None:
        return "Ping: not run"
    target = check.data.get("target", "?")
    if check.status == Status.OK:
        latency = check.data.get("latency_ms")
        return f"Ping {target}: reply" + (f" ({latency:.1f} ms)" if latency is not None else "")
    if check.status == Status.FAILED:
        return f"Ping {target}: no reply"
    return f"{check.title}: {check.status.value} ({check.error})"


def _dns_evidence(check: CheckResult | None) -> str:
    if check is None:
        return "DNS lookup: not run"
    host = check.data.get("hostname", "?")
    if check.status == Status.OK:
        return f"Resolve {host}: {', '.join(check.data['addresses'][:3])}"
    return f"Resolve {host}: failed ({check.error})"


def connectivity_findings(gateway: CheckResult | None, gateway_ping: CheckResult | None,
                          public_ping: CheckResult | None, dns: CheckResult | None) -> list[Finding]:
    """Compare the three connectivity tests to suggest where a problem lies.

    gateway fails + public IP fails -> local network problem
    gateway OK    + public IP fails -> problem beyond the local network
    public IP OK  + DNS fails       -> DNS problem
    """
    findings = []
    evidence = [_ping_evidence(gateway_ping), _ping_evidence(public_ping), _dns_evidence(dns)]
    gateway_failed, public_ok, public_failed = _failed(gateway_ping), _ok(public_ping), _failed(public_ping)

    if _ok(gateway) and not gateway.data.get("gateway"):
        findings.append(Finding(
            "connectivity.no_default_gateway", Severity.WARNING, "No default gateway",
            "No default route is configured, so traffic cannot leave the local subnet. This often "
            "means the device is not connected or did not receive a full DHCP configuration.",
            ["Default gateway: none"]))

    if gateway_failed and public_failed and _ok(dns):
        findings.append(Finding(
            "connectivity.ping_blocked", Severity.INFO, "Ping appears to be blocked",
            "Neither the gateway nor a public IP address answered ping, but DNS resolution worked. "
            "Because DNS answers had to travel over the network, ping (ICMP) is most likely being "
            "filtered, which is common on corporate and cloud networks. If the user still reports "
            "problems, note that a DNS answer can come from a local cache.", evidence))
    elif gateway_failed and public_failed:
        findings.append(Finding(
            "connectivity.local_network_unreachable", Severity.WARNING, "Local network or gateway unreachable",
            "Neither the default gateway nor a public IP address responded. The problem is likely on "
            "the local network (cable, Wi-Fi, switch port, VLAN, or the router itself).", evidence))
    elif gateway_failed and public_ok:
        findings.append(Finding(
            "connectivity.gateway_no_ping_reply", Severity.INFO, "Default gateway did not respond to ping",
            "The gateway did not answer ping, but internet connectivity works. Many routers and "
            "firewalls ignore ping, so this is usually not a problem by itself.", evidence))
    elif public_failed and _ok(dns):
        # Normal behind corporate firewalls that only allow traffic through a proxy.
        findings.append(Finding(
            "connectivity.public_ip_no_ping_reply", Severity.INFO, "Public IP address did not respond to ping",
            "The public IP address did not answer ping, but DNS resolution worked, so traffic is "
            "leaving the local network. Outbound ping (ICMP) is most likely blocked by a firewall.",
            evidence))
    elif public_failed:
        findings.append(Finding(
            "connectivity.internet_unreachable", Severity.WARNING, "Public IP address unreachable",
            "A public IP address did not respond and DNS resolution also failed. If the gateway "
            "responded, the problem is likely beyond the local network (ISP, upstream firewall, "
            "or a required proxy).", evidence))

    if _failed(dns):
        if public_failed:
            findings.append(Finding(
                "connectivity.dns_failed_no_internet", Severity.INFO, "DNS resolution also failed",
                "DNS resolution failed, which is expected when there is no internet connectivity. "
                "Re-test DNS after connectivity is restored.", evidence))
        else:
            explanation = f"DNS resolution failed for {dns.data.get('hostname', 'the test hostname')}"
            explanation += (", but the public IP connectivity test succeeded. This may indicate a DNS "
                            "configuration or DNS server issue." if public_ok else
                            ". Check the configured DNS servers.")
            findings.append(Finding("connectivity.dns_failed", Severity.WARNING, "DNS resolution failed", explanation, evidence))
    return findings


# -------------------------------------------------------------- updates

def update_findings(check: CheckResult | None) -> list[Finding]:
    if not _ok(check) or not check.data.get("pending_count"):
        return []
    names = check.data["updates"]
    evidence = [f"{check.data['pending_count']} pending update(s) via {check.data['source']}"]
    evidence += [f"- {name}" for name in names[:5]]
    if len(names) > 5:
        evidence.append(f"- ... and {len(names) - 5} more")
    if check.data.get("restart_required"):
        evidence.append("At least one update requires a restart")
    return [Finding(
        "updates.pending", Severity.INFO, "Operating system updates are pending",
        "Pending updates may include security or bug fixes relevant to the reported issue. "
        "Install them according to your organization's patching process.", evidence)]
