import unittest
from datetime import datetime, timezone

from endpoint_triage import findings
from endpoint_triage.models import CheckResult, Report, Severity

GB = 1024 ** 3


def disk(mount, used_gb, free_gb):
    used, free = int(used_gb * GB), int(free_gb * GB)
    return {"mount": mount, "device": "/dev/x", "total_bytes": used + free, "used_bytes": used,
            "free_bytes": free, "used_percent": round(used / (used + free) * 100, 1)}


def disks(*volumes):
    return CheckResult.ok("resources.disks", "Disk volumes", {"volumes": list(volumes)})


def iface(name, status, ipv4=()):
    return {"name": name, "status": status, "ipv4": list(ipv4), "ipv6": [], "mac": None}


def interfaces(*items):
    return CheckResult.ok("network.interfaces", "Network interfaces", {"interfaces": list(items)})


GATEWAY = CheckResult.ok("network.gateway", "Default gateway", {"gateway": "10.0.0.1", "interface": "eth0"})


def ping(check_id, target, reachable):
    if reachable:
        return CheckResult.ok(check_id, "Ping", {"target": target, "reachable": True, "latency_ms": 2.0})
    return CheckResult.failed(check_id, "Ping", f"no reply from {target}",
                              data={"target": target, "reachable": False, "latency_ms": None})


def gw_ping(ok):
    return ping("connectivity.gateway_ping", "10.0.0.1", ok)


def ip_ping(ok):
    return ping("connectivity.public_ip_ping", "1.1.1.1", ok)


def dns(ok):
    if ok:
        return CheckResult.ok("connectivity.dns_resolution", "Resolve",
                              {"hostname": "example.com", "addresses": ["93.184.215.14"], "duration_ms": 12.0})
    return CheckResult.failed("connectivity.dns_resolution", "Resolve", "DNS lookup for example.com failed: not known",
                              data={"hostname": "example.com", "addresses": [], "duration_ms": 5.0})


def titles(found):
    return [(f.severity, f.title) for f in found]


class DiskThresholdTests(unittest.TestCase):
    def test_below_warning(self):
        self.assertEqual(findings.disk_findings(disks(disk("/", 84, 16))), [])

    def test_warning_at_85_percent(self):
        found = findings.disk_findings(disks(disk("/", 85, 15)))
        self.assertEqual(titles(found), [(Severity.WARNING, "High disk usage on /")])
        self.assertIn("85.0% used", found[0].evidence[0])

    def test_critical_at_95_percent(self):
        self.assertEqual(findings.disk_findings(disks(disk("C:", 950, 50)))[0].severity, Severity.CRITICAL)

    def test_critical_when_under_5_gb_free_on_large_volume(self):
        # 80% used, but only 4 GB free on a 20 GB volume.
        self.assertEqual(findings.disk_findings(disks(disk("/", 16, 4)))[0].severity, Severity.CRITICAL)

    def test_small_partition_ignores_free_space_rule(self):
        # A 1 GB EFI partition always has < 5 GB free; that is not a problem.
        self.assertEqual(findings.disk_findings(disks(disk("/boot/efi", 0.1, 0.9))), [])

    def test_ignored_when_check_failed(self):
        self.assertEqual(findings.disk_findings(CheckResult.failed("resources.disks", "Disks", "df failed")), [])


class MemoryAndUptimeTests(unittest.TestCase):
    def memory(self, used_percent):
        total = 16 * GB
        available = int(total * (100 - used_percent) / 100)
        return CheckResult.ok("resources.memory", "Memory", {
            "total_bytes": total, "available_bytes": available,
            "used_bytes": total - available, "used_percent": used_percent})

    def test_low_memory(self):
        self.assertEqual(titles(findings.memory_findings(self.memory(95))), [(Severity.WARNING, "Low available memory")])
        self.assertEqual(findings.memory_findings(self.memory(85)), [])

    def test_unknown_available_memory(self):
        check = CheckResult.ok("resources.memory", "Memory", {"total_bytes": GB, "available_bytes": None,
                                                              "used_bytes": None, "used_percent": None})
        self.assertEqual(findings.memory_findings(check), [])

    def test_long_uptime(self):
        long = CheckResult.ok("system.uptime", "Uptime", {"uptime_seconds": 31 * 86400, "last_boot": "x"})
        short = CheckResult.ok("system.uptime", "Uptime", {"uptime_seconds": 2 * 86400, "last_boot": "x"})
        self.assertEqual(findings.uptime_findings(long)[0].severity, Severity.INFO)
        self.assertEqual(findings.uptime_findings(short), [])


class InterfaceFindingTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(findings.interface_findings(interfaces(iface("en0", "up", ["192.168.1.5/24"]))), [])

    def test_apipa(self):
        found = findings.interface_findings(interfaces(iface("Ethernet", "up", ["169.254.10.20/16"])))
        self.assertIn((Severity.WARNING, "Self-assigned (APIPA) IPv4 address"), titles(found))
        self.assertIn((Severity.WARNING, "No active network connection"), titles(found))

    def test_no_connection(self):
        found = findings.interface_findings(interfaces(iface("en0", "down")))
        self.assertEqual(titles(found), [(Severity.WARNING, "No active network connection")])

    def test_multiple_disconnected_is_info(self):
        found = findings.interface_findings(interfaces(
            iface("en0", "up", ["10.0.0.5/24"]), iface("en1", "down"), iface("en2", "down")))
        self.assertEqual(titles(found), [(Severity.INFO, "Multiple network interfaces are disconnected")])
        self.assertIn("en1, en2", found[0].evidence[0])

    def test_no_dns_servers(self):
        empty = CheckResult.ok("network.dns_servers", "DNS servers", {"servers": []})
        self.assertEqual(findings.dns_server_findings(empty)[0].title, "No DNS servers configured")


class ConnectivityClassificationTests(unittest.TestCase):
    def classify(self, gateway_ok, public_ok, dns_ok, gateway=GATEWAY):
        return titles(findings.connectivity_findings(gateway, gw_ping(gateway_ok), ip_ping(public_ok), dns(dns_ok)))

    def test_all_healthy(self):
        self.assertEqual(self.classify(True, True, True), [])

    def test_local_network_failure(self):
        found = self.classify(False, False, False)
        self.assertIn((Severity.WARNING, "Local network or gateway unreachable"), found)
        self.assertIn((Severity.INFO, "DNS resolution also failed"), found)

    def test_both_pings_fail_but_dns_works_suggests_icmp_blocked(self):
        # Seen on GitHub's Azure-hosted runners, which drop ICMP.
        self.assertEqual(self.classify(False, False, True), [(Severity.INFO, "Ping appears to be blocked")])

    def test_internet_failure(self):
        self.assertEqual(self.classify(True, False, False),
                         [(Severity.WARNING, "Public IP address unreachable"),
                          (Severity.INFO, "DNS resolution also failed")])

    def test_dns_failure_with_working_ip_connectivity(self):
        found = findings.connectivity_findings(GATEWAY, gw_ping(True), ip_ping(True), dns(False))
        self.assertEqual(titles(found), [(Severity.WARNING, "DNS resolution failed")])
        self.assertIn("public IP connectivity test succeeded", found[0].explanation)

    def test_gateway_ignores_ping_but_internet_works(self):
        self.assertEqual(self.classify(False, True, True), [(Severity.INFO, "Default gateway did not respond to ping")])

    def test_public_ping_blocked_but_dns_works_is_info(self):
        # The normal state of a healthy laptop behind a proxy-only corporate firewall.
        self.assertEqual(self.classify(True, False, True),
                         [(Severity.INFO, "Public IP address did not respond to ping")])

    def test_no_default_gateway(self):
        no_gateway = CheckResult.ok("network.gateway", "Default gateway", {"gateway": None, "interface": None})
        skipped = CheckResult.skipped("connectivity.gateway_ping", "Ping", "no default gateway configured")
        found = titles(findings.connectivity_findings(no_gateway, skipped, ip_ping(False), dns(False)))
        self.assertIn((Severity.WARNING, "No default gateway"), found)
        self.assertIn((Severity.WARNING, "Public IP address unreachable"), found)

    def test_ping_unavailable_is_not_a_finding(self):
        # A test that could not run is listed under Errors / Unavailable Checks, not as a finding.
        missing = CheckResult.unavailable("connectivity.public_ip_ping", "Ping public IP address", "ping command not found")
        self.assertEqual(findings.connectivity_findings(GATEWAY, gw_ping(True), missing, dns(True)), [])


class OverallStatusTests(unittest.TestCase):
    CORE = [CheckResult.ok("system.os", "OS", {}), CheckResult.ok("resources.memory", "Memory", {}),
            CheckResult.ok("network.interfaces", "Interfaces", {"interfaces": []})]

    def report(self, disks_check, found):
        report = Report("1.0.0", "Linux", datetime(2026, 10, 5, tzinfo=timezone.utc), 1.0,
                        {"system": self.CORE[:1], "resources": [self.CORE[1], disks_check],
                         "network": self.CORE[2:]})
        report.findings = found
        return report

    def test_info_only_is_ok(self):
        info = findings.Finding("test.info", Severity.INFO, "x", "y")
        self.assertEqual(self.report(disks(disk("/", 1, 99)), [info]).overall_status(), "OK")

    def test_missing_core_check_is_unknown_even_with_warning(self):
        warning = findings.Finding("test.warning", Severity.WARNING, "x", "y")
        failed = CheckResult.failed("resources.disks", "Disks", "df timed out")
        self.assertEqual(self.report(failed, [warning]).overall_status(), "UNKNOWN")

    def test_critical_beats_unknown(self):
        critical = findings.Finding("test.critical", Severity.CRITICAL, "x", "y")
        failed = CheckResult.failed("resources.disks", "Disks", "df timed out")
        self.assertEqual(self.report(failed, [critical]).overall_status(), "CRITICAL")


class AnalyzeTests(unittest.TestCase):
    def report(self, **sections):
        return Report("1.0.0", "Linux", datetime(2026, 10, 5, tzinfo=timezone.utc), 1.0, sections)

    def test_sorted_by_severity_and_incomplete_checks_are_not_findings(self):
        uptime = CheckResult.ok("system.uptime", "Uptime", {"uptime_seconds": 40 * 86400, "last_boot": "x"})
        report = self.report(
            system=[uptime],
            resources=[disks(disk("/data", 86, 14), disk("/", 96, 4))],
            updates=[CheckResult.unavailable("updates.os", "Pending OS updates", "no supported package manager")],
        )
        found = findings.analyze(report)
        self.assertEqual([f.severity for f in found], [Severity.CRITICAL, Severity.WARNING, Severity.INFO])
        self.assertEqual([f.id for f in found], ["disk.critical_low_space", "disk.high_usage", "system.long_uptime"])

    def test_pending_updates_info(self):
        check = CheckResult.ok("updates.os", "Pending OS updates", {
            "source": "apt", "pending_count": 7, "updates": [f"pkg{i}" for i in range(7)], "restart_required": None})
        found = findings.update_findings(check)
        self.assertEqual(found[0].severity, Severity.INFO)
        self.assertIn("- ... and 2 more", found[0].evidence)



class FindingsDocTests(unittest.TestCase):
    def test_every_finding_id_is_documented(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        code_ids = set(re.findall(r'"([a-z]+\.[a-z_]+)", Severity\.', (root / "endpoint_triage/findings.py").read_text()))
        doc_ids = set(re.findall(r"^\| `([a-z]+\.[a-z_]+)` \|", (root / "docs/findings.md").read_text(), re.MULTILINE))
        self.assertTrue(code_ids)
        self.assertEqual(code_ids - doc_ids, set(), "finding IDs missing from docs/findings.md")
        self.assertEqual(doc_ids - code_ids, set(), "docs/findings.md lists IDs the code no longer produces")

if __name__ == "__main__":
    unittest.main()
