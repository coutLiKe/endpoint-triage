import unittest

from endpoint_triage.collectors import network
from endpoint_triage.models import Status
from tests.helpers import FakeRunner, fail, fake_files, fixture, not_found, ok

ROUTE_GET = """   route to: default
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""


def by_id(checks):
    return {c.id: c for c in checks}


def macos_runner(**overrides):
    responses = {
        "ifconfig": ok(fixture("macos_ifconfig.txt")),
        "route": ok(ROUTE_GET),
        "scutil": ok(fixture("macos_scutil_dns.txt")),
    }
    responses.update(overrides)
    return FakeRunner(responses)


class MacOSNetworkTests(unittest.TestCase):
    def test_interfaces(self):
        checks = by_id(network.collect("Darwin", run=macos_runner()))
        interfaces = {i["name"]: i for i in checks["network.interfaces"].data["interfaces"]}
        # Loopback, tunnels, AWDL, bridge and other virtual interfaces are skipped.
        self.assertEqual(sorted(interfaces), ["en0", "en1", "en2"])
        en0 = interfaces["en0"]
        self.assertEqual(en0["status"], "up")
        self.assertEqual(en0["ipv4"], ["192.168.1.42/24"])
        self.assertEqual(en0["ipv6"], ["fe80::1c2b:3d4e:5f60:7182/64", "2001:db8:1234:5678:10a2:b3c4:d5e6:f708/64"])
        self.assertEqual(en0["mac"], "a4:83:e7:12:34:56")
        self.assertEqual(interfaces["en1"]["status"], "down")
        self.assertEqual(interfaces["en1"]["ipv4"], [])

    def test_gateway_and_dns(self):
        checks = by_id(network.collect("Darwin", run=macos_runner()))
        self.assertEqual(checks["network.gateway"].data, {"gateway": "192.168.1.1", "interface": "en0"})
        # Duplicates from the scoped resolver section are removed.
        self.assertEqual(checks["network.dns_servers"].data["servers"], ["192.168.1.1", "2001:db8:1234::1"])

    def test_no_default_route_is_a_result_not_an_error(self):
        runner = macos_runner(route=fail(stderr="route: writing to routing socket: not in table"))
        gateway = by_id(network.collect("Darwin", run=runner))["network.gateway"]
        self.assertEqual(gateway.status, Status.OK)
        self.assertIsNone(gateway.data["gateway"])

    def test_route_command_failure(self):
        runner = macos_runner(route=fail(stderr="route: permission denied"))
        self.assertEqual(by_id(network.collect("Darwin", run=runner))["network.gateway"].status, Status.FAILED)

    def test_ifconfig_missing_or_empty(self):
        self.assertEqual(by_id(network.collect("Darwin", run=macos_runner(ifconfig=not_found())))
                         ["network.interfaces"].status, Status.UNAVAILABLE)
        self.assertEqual(by_id(network.collect("Darwin", run=macos_runner(ifconfig=ok("nothing useful"))))
                         ["network.interfaces"].status, Status.FAILED)

    def test_interface_without_status_line_uses_flags(self):
        text = "en5: flags=8863<UP,BROADCAST,RUNNING> mtu 1500\n\tinet 10.1.1.1 netmask 0xffff0000\n"
        parsed = network.parse_ifconfig(text)
        self.assertEqual(parsed[0]["status"], "up")
        self.assertEqual(parsed[0]["ipv4"], ["10.1.1.1/16"])
        self.assertIsNone(parsed[0]["mac"])


class LinuxNetworkTests(unittest.TestCase):
    def runner(self, **overrides):
        responses = {
            "ip -j addr": ok(fixture("linux_ip_addr.json")),
            "ip -j route": ok('[{"dst":"default","gateway":"10.0.0.1","dev":"enp3s0","protocol":"dhcp","metric":100},'
                              '{"dst":"default","gateway":"192.168.50.1","dev":"wlp2s0","metric":600}]'),
        }
        responses.update(overrides)
        return FakeRunner(responses)

    def test_interfaces(self):
        files = fake_files({"/etc/resolv.conf": "# generated\nnameserver 10.0.0.1\nnameserver 9.9.9.9\nsearch lan\n"})
        checks = by_id(network.collect("Linux", run=self.runner(), read_file=files))
        interfaces = {i["name"]: i for i in checks["network.interfaces"].data["interfaces"]}
        self.assertEqual(sorted(interfaces), ["enp3s0", "wg0", "wlp2s0"])  # lo and docker0 skipped
        self.assertEqual(interfaces["enp3s0"]["status"], "up")
        self.assertEqual(interfaces["enp3s0"]["ipv4"], ["10.0.0.25/24"])
        self.assertEqual(interfaces["wlp2s0"]["status"], "down")
        self.assertEqual(interfaces["wg0"]["status"], "up")  # UNKNOWN operstate + LOWER_UP
        self.assertIsNone(interfaces["wg0"]["mac"])
        self.assertEqual(checks["network.gateway"].data, {"gateway": "10.0.0.1", "interface": "enp3s0"})
        self.assertEqual(checks["network.dns_servers"].data["servers"], ["10.0.0.1", "9.9.9.9"])

    def test_no_default_route(self):
        checks = by_id(network.collect("Linux", run=self.runner(**{"ip -j route": ok("[]")}),
                                       read_file=fake_files({})))
        self.assertEqual(checks["network.gateway"].status, Status.OK)
        self.assertIsNone(checks["network.gateway"].data["gateway"])

    def test_systemd_resolved_stub_note(self):
        files = fake_files({"/etc/resolv.conf": "nameserver 127.0.0.53\noptions edns0\n"})
        dns = by_id(network.collect("Linux", run=self.runner(), read_file=files))["network.dns_servers"]
        self.assertIn("resolvectl", dns.data["note"])

    def test_missing_ip_command_and_resolv_conf(self):
        checks = by_id(network.collect("Linux", run=FakeRunner(), read_file=fake_files({})))
        self.assertEqual(checks["network.interfaces"].status, Status.UNAVAILABLE)
        self.assertEqual(checks["network.gateway"].status, Status.UNAVAILABLE)
        self.assertEqual(checks["network.dns_servers"].status, Status.UNAVAILABLE)

    def test_old_ip_without_json_support(self):
        # iproute2 older than 4.14 ignores -j and prints text.
        runner = self.runner(**{"ip -j addr": ok("1: lo: <LOOPBACK,UP> mtu 65536"),
                                "ip -j route": ok("default via 10.0.0.1 dev eth0")})
        checks = by_id(network.collect("Linux", run=runner, read_file=fake_files({})))
        self.assertEqual(checks["network.interfaces"].status, Status.FAILED)
        self.assertEqual(checks["network.gateway"].status, Status.FAILED)


class WindowsNetworkTests(unittest.TestCase):
    def test_success(self):
        runner = FakeRunner({"Get-NetAdapter": ok(fixture("windows_network.json"))})
        checks = by_id(network.collect("Windows", run=runner))
        interfaces = {i["name"]: i for i in checks["network.interfaces"].data["interfaces"]}
        wifi = interfaces["Wi-Fi"]
        self.assertEqual(wifi["status"], "up")
        self.assertEqual(wifi["ipv4"], ["192.168.1.57/24"])
        self.assertEqual(wifi["ipv6"], ["fe80::1c2b:3d4e:5f60:7182/64"])
        self.assertEqual(wifi["mac"], "a4:83:e7:12:34:56")
        self.assertEqual(interfaces["Ethernet"]["status"], "down")
        self.assertEqual(checks["network.gateway"].data, {"gateway": "192.168.1.1", "interface": "Wi-Fi"})
        # Only DNS for "up" adapters, without the fec0:: placeholders.
        self.assertEqual(checks["network.dns_servers"].data["servers"], ["192.168.1.1", "1.1.1.1"])
        self.assertEqual(len(runner.calls), 1)

    def test_powershell_failure(self):
        checks = network.collect("Windows", run=FakeRunner({"Get-NetAdapter": fail(stderr="CIM error")}))
        self.assertTrue(all(c.status == Status.FAILED for c in checks))

    def test_malformed_json(self):
        checks = network.collect("Windows", run=FakeRunner({"Get-NetAdapter": ok('{"adapters": [{"Name": "x"}]}')}))
        self.assertTrue(all(c.status == Status.FAILED for c in checks))


if __name__ == "__main__":
    unittest.main()
