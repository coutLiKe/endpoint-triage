"""Specification for the DNS resolution check.

Written before the implementation (test-driven development). Requirements:

1. Resolve a well-known hostname (example.com) through the operating
   system's resolver, so the result matches what browsers and apps see.
2. Success -> status OK with the hostname, the unique resolved addresses
   (IPv4 and IPv6, sorted), and how long the lookup took.
3. A resolver error (socket.gaierror, e.g. NXDOMAIN or no DNS server)
   -> status FAILED with an error that names the hostname and the reason.
4. A lookup that hangs must not hang the tool: after `timeout` seconds the
   check returns FAILED with an error mentioning the timeout.
5. A lookup that returns no addresses is FAILED.
6. Any other OSError is reported as FAILED, never raised.
7. The resolver is injectable so tests never touch the real network.
"""

import socket
import threading
import time
import unittest

from endpoint_triage.collectors import connectivity
from endpoint_triage.models import Status
from tests.helpers import FakeRunner


def addrinfo(*addresses):
    """Build getaddrinfo()-shaped results: (family, type, proto, canonname, sockaddr)."""
    results = []
    for address in addresses:
        if ":" in address:
            results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0)))
        else:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)))
    return results


class DnsResolutionSpec(unittest.TestCase):
    def test_success_returns_unique_sorted_addresses(self):
        calls = []

        def resolver(host, port):
            calls.append(host)
            # getaddrinfo returns one entry per socket type, so duplicates are normal.
            return addrinfo("96.7.128.198", "2600:1406:3a00:21::173e:2e65", "23.192.228.80", "96.7.128.198")

        check = connectivity.check_dns_resolution("example.com", resolver=resolver)
        self.assertEqual(check.id, "connectivity.dns_resolution")
        self.assertEqual(check.status, Status.OK)
        self.assertEqual(calls, ["example.com"])
        self.assertEqual(check.data["hostname"], "example.com")
        self.assertEqual(check.data["addresses"],
                         ["23.192.228.80", "2600:1406:3a00:21::173e:2e65", "96.7.128.198"])
        self.assertIsInstance(check.data["duration_ms"], float)
        self.assertIsNone(check.error)

    def test_resolver_error_is_failed_with_reason(self):
        def resolver(host, port):
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")

        check = connectivity.check_dns_resolution("example.com", resolver=resolver)
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("example.com", check.error)
        self.assertIn("not known", check.error)
        self.assertEqual(check.data["hostname"], "example.com")
        self.assertEqual(check.data["addresses"], [])

    def test_hanging_lookup_times_out(self):
        release = threading.Event()

        def resolver(host, port):
            release.wait(5)
            return addrinfo("1.2.3.4")

        started = time.monotonic()
        check = connectivity.check_dns_resolution("example.com", resolver=resolver, timeout=0.2)
        elapsed = time.monotonic() - started
        release.set()

        self.assertLess(elapsed, 2, "the check must give up after the timeout")
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("timed out", check.error)

    def test_empty_result_is_failed(self):
        check = connectivity.check_dns_resolution("example.com", resolver=lambda host, port: [])
        self.assertEqual(check.status, Status.FAILED)

    def test_other_os_error_is_failed_not_raised(self):
        def resolver(host, port):
            raise OSError("network is unreachable")

        check = connectivity.check_dns_resolution("example.com", resolver=resolver)
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("network is unreachable", check.error)

    def test_collect_includes_dns_check_for_default_hostname(self):
        seen = []

        def resolver(host, port):
            seen.append(host)
            return addrinfo("93.184.215.14")

        checks = connectivity.collect("Linux", None, run=FakeRunner(), resolver=resolver)
        dns = [c for c in checks if c.id == "connectivity.dns_resolution"]
        self.assertEqual(len(dns), 1)
        self.assertEqual(dns[0].status, Status.OK)
        self.assertEqual(seen, [connectivity.DNS_TEST_HOSTNAME])
        self.assertEqual(connectivity.DNS_TEST_HOSTNAME, "example.com")


if __name__ == "__main__":
    unittest.main()
