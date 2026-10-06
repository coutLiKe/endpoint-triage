"""A fully populated Report built from fake data, shared by reporter and CLI tests."""

from datetime import datetime, timezone

from endpoint_triage import __version__, findings
from endpoint_triage.models import CheckResult, Report

GB = 1024 ** 3


def build_sample_report(debug_detail: bool = True) -> Report:
    checks = {
        "system": [
            CheckResult.ok("system.os", "Operating system", {
                "hostname": "HD-LAPTOP-042.corp.local", "os_family": "Windows", "architecture": "AMD64",
                "kernel": "10.0.26100", "name": "Microsoft Windows 11 Pro", "version": "10.0.26100", "build": "26100"}),
            CheckResult.ok("system.uptime", "Uptime and last reboot", {
                "uptime_seconds": 38 * 86400 + 5 * 3600 + 12 * 60, "last_boot": "2026-08-28T07:48:00+00:00"}),
        ],
        "resources": [
            CheckResult.ok("resources.memory", "Memory", {
                "total_bytes": 16 * GB, "available_bytes": 5 * GB, "used_bytes": 11 * GB, "used_percent": 68.8}),
            CheckResult.ok("resources.disks", "Disk volumes", {"volumes": [
                {"mount": "C:", "device": "NTFS", "total_bytes": 476 * GB, "used_bytes": 459 * GB,
                 "free_bytes": 17 * GB, "used_percent": 96.4},
                {"mount": "D:", "device": "NTFS", "total_bytes": 931 * GB, "used_bytes": 410 * GB,
                 "free_bytes": 521 * GB, "used_percent": 44.0},
            ]}),
        ],
        "network": [
            CheckResult.ok("network.interfaces", "Network interfaces", {"interfaces": [
                {"name": "Wi-Fi", "status": "up", "ipv4": ["192.168.1.57/24"],
                 "ipv6": ["fe80::1c2b:3d4e:5f60:7182/64"], "mac": "a4:83:e7:12:34:56"},
                {"name": "Ethernet", "status": "down", "ipv4": [], "ipv6": [], "mac": "00:1a:2b:3c:4d:5e"},
            ]}),
            CheckResult.ok("network.gateway", "Default gateway", {"gateway": "192.168.1.1", "interface": "Wi-Fi"}),
            CheckResult.ok("network.dns_servers", "DNS servers", {"servers": ["192.168.1.1"]}),
            CheckResult.ok("network.proxy", "Proxy configuration", {
                "configured": True,
                "sources_checked": ["environment variables", "Windows user proxy (WinINET)",
                                    "Windows system proxy (WinHTTP)"],
                "proxies": [{"source": "Windows user proxy (WinINET)", "proxy": None,
                             "pac_url": "http://wpad.corp.example/proxy.pac", "auto_detect": None,
                             "bypass": None}]}),
        ],
        "connectivity": [
            CheckResult.ok("connectivity.gateway_ping", "Ping default gateway",
                           {"target": "192.168.1.1", "reachable": True, "latency_ms": 3.0}),
            CheckResult.ok("connectivity.public_ip_ping", "Ping public IP address",
                           {"target": "1.1.1.1", "reachable": True, "latency_ms": 15.0}),
            CheckResult.failed("connectivity.dns_resolution", "Resolve public hostname",
                               "DNS lookup for example.com timed out after 5s",
                               data={"hostname": "example.com", "addresses": [], "duration_ms": 5001.2}),
        ],
        "updates": [
            CheckResult.failed("updates.os", "Pending OS updates",
                               ("powershell exited with code 1: Exception from HRESULT: 0x8024402C (hint: 0x8024402C: "
                                "the update server name could not be resolved; check proxy settings and the "
                                "WSUS server URL)"),
                               debug="command: ['powershell', ...]\nreturncode: 1" if debug_detail else None),
        ],
    }
    report = Report(__version__, "Windows", datetime(2026, 10, 5, 14, 3, 22, tzinfo=timezone.utc), 7.4, checks,
                    options={"ping_target": "1.1.1.1", "dns_name": "example.com", "skip_updates": False})
    report.findings = findings.analyze(report)
    return report
