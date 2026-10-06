import unittest

from endpoint_triage.collectors import connectivity
from endpoint_triage.models import CheckResult, Status
from endpoint_triage.runner import TIMEOUT, CommandResult
from tests.helpers import FakeRunner, fail, not_found, ok

MAC_PING_OK = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=17.032 ms
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=18.170 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 17.032/17.601/18.170/0.569 ms
"""
LINUX_PING_OK = """PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.
64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.412 ms
64 bytes from 10.0.0.1: icmp_seq=2 ttl=64 time=0.398 ms

--- 10.0.0.1 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 0.398/0.405/0.412/0.007 ms
"""
WINDOWS_PING_OK = """Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=14ms TTL=57
Reply from 1.1.1.1: bytes=32 time=16ms TTL=57

Ping statistics for 1.1.1.1:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 14ms, Maximum = 16ms, Average = 15ms
"""
WINDOWS_PING_UNREACHABLE = """Pinging 1.1.1.1 with 32 bytes of data:
Reply from 192.168.1.1: Destination host unreachable.
Reply from 192.168.1.1: Destination host unreachable.

Ping statistics for 1.1.1.1:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
"""
LINUX_PING_LOSS = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 0 received, 100% packet loss, time 1023ms
"""

GATEWAY = CheckResult.ok("network.gateway", "Default gateway", {"gateway": "10.0.0.1", "interface": "eth0"})


def fake_resolver(host, port):
    return [(2, 1, 6, "", ("93.184.215.14", 0))]


def collect(*args, **kwargs):
    """connectivity.collect with a fake DNS resolver so tests never hit the network."""
    kwargs.setdefault("resolver", fake_resolver)
    return connectivity.collect(*args, **kwargs)


def by_id(checks):
    return {c.id: c for c in checks}


class PingCommandTests(unittest.TestCase):
    def test_flags_per_os(self):
        self.assertEqual(connectivity.build_ping_command("Windows", "1.1.1.1"), ["ping", "-n", "2", "-w", "2000", "1.1.1.1"])
        self.assertEqual(connectivity.build_ping_command("Darwin", "1.1.1.1"), ["ping", "-c", "2", "-W", "2000", "1.1.1.1"])
        self.assertEqual(connectivity.build_ping_command("Linux", "1.1.1.1"), ["ping", "-c", "2", "-W", "2", "1.1.1.1"])

    def test_latency_parsing(self):
        self.assertEqual(connectivity.parse_ping_latency(MAC_PING_OK), 17.601)
        self.assertEqual(connectivity.parse_ping_latency(LINUX_PING_OK), 0.405)
        self.assertEqual(connectivity.parse_ping_latency(WINDOWS_PING_OK), 15.0)
        self.assertIsNone(connectivity.parse_ping_latency(LINUX_PING_LOSS))


class PingTests(unittest.TestCase):
    def test_both_pings_succeed(self):
        runner = FakeRunner({"10.0.0.1": ok(LINUX_PING_OK), "1.1.1.1": ok(MAC_PING_OK)})
        checks = by_id(collect("Linux", GATEWAY, run=runner))
        gateway = checks["connectivity.gateway_ping"]
        self.assertEqual(gateway.status, Status.OK)
        self.assertEqual(gateway.data, {"target": "10.0.0.1", "reachable": True, "latency_ms": 0.405})
        self.assertEqual(checks["connectivity.public_ip_ping"].data["target"], "1.1.1.1")

    def test_no_reply_is_failed(self):
        runner = FakeRunner({"10.0.0.1": ok(LINUX_PING_OK), "1.1.1.1": fail(returncode=1, stderr="", stdout=LINUX_PING_LOSS)})
        public = by_id(collect("Linux", GATEWAY, run=runner))["connectivity.public_ip_ping"]
        self.assertEqual(public.status, Status.FAILED)
        self.assertFalse(public.data["reachable"])
        self.assertEqual(public.error, "no reply from 1.1.1.1")

    def test_windows_destination_unreachable_with_exit_zero_is_failure(self):
        runner = FakeRunner({"1.1.1.1": ok(WINDOWS_PING_UNREACHABLE)})
        public = by_id(collect("Windows", None, run=runner))["connectivity.public_ip_ping"]
        self.assertEqual(public.status, Status.FAILED)

    def test_windows_success(self):
        runner = FakeRunner({"1.1.1.1": ok(WINDOWS_PING_OK)})
        public = by_id(collect("Windows", None, run=runner))["connectivity.public_ip_ping"]
        self.assertEqual(public.status, Status.OK)
        self.assertEqual(public.data["latency_ms"], 15.0)

    def test_ping_missing_is_unavailable(self):
        checks = by_id(collect("Linux", GATEWAY, run=FakeRunner({"ping": not_found()})))
        self.assertEqual(checks["connectivity.gateway_ping"].status, Status.UNAVAILABLE)
        self.assertEqual(checks["connectivity.public_ip_ping"].status, Status.UNAVAILABLE)

    def test_ping_timeout_is_failed(self):
        runner = FakeRunner({"ping": CommandResult([], None, error=TIMEOUT)})
        public = by_id(collect("Linux", GATEWAY, run=runner))["connectivity.public_ip_ping"]
        self.assertEqual(public.status, Status.FAILED)
        self.assertFalse(public.data["reachable"])


class GatewayPingSkipTests(unittest.TestCase):
    def test_skipped_without_gateway(self):
        no_gateway = CheckResult.ok("network.gateway", "Default gateway", {"gateway": None, "interface": None})
        runner = FakeRunner({"1.1.1.1": ok(LINUX_PING_OK)})
        checks = by_id(collect("Linux", no_gateway, run=runner))
        self.assertEqual(checks["connectivity.gateway_ping"].status, Status.SKIPPED)
        self.assertIn("no default gateway", checks["connectivity.gateway_ping"].error)
        self.assertEqual(len([c for c in runner.calls if c[0] == "ping"]), 1)

    def test_skipped_when_gateway_unknown(self):
        unknown = CheckResult.failed("network.gateway", "Default gateway", "ip exited 1")
        gateway = by_id(collect("Linux", unknown, run=FakeRunner()))["connectivity.gateway_ping"]
        self.assertEqual(gateway.status, Status.SKIPPED)
        self.assertIn("could not be determined", gateway.error)


if __name__ == "__main__":
    unittest.main()
