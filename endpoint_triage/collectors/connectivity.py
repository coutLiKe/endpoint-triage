"""Connectivity tests: ping, DNS, and TCP connections.

The tests are chosen so their combination points to *where* a problem is:
the local network (gateway ping), name resolution (DNS), the path to an
internet service (a TCP connection to port 443), and the proxy (a TCP
connection to the configured proxy). Ping alone is weak evidence because
firewalls often drop it; a completed TCP connection is strong evidence.

The TCP tests only open and close a connection: no data is sent.
"""

from __future__ import annotations

import re
import socket
import threading
import time

from endpoint_triage.collectors.proxy import parse_proxy_endpoint
from endpoint_triage.models import CheckResult, Status
from endpoint_triage.runner import NOT_FOUND, RunFunc, run_command

GATEWAY_PING_ID, GATEWAY_PING_TITLE = "connectivity.gateway_ping", "Ping default gateway"
PUBLIC_PING_ID, PUBLIC_PING_TITLE = "connectivity.public_ip_ping", "Ping public IP address"
DNS_ID, DNS_TITLE = "connectivity.dns_resolution", "Resolve public hostname"
TCP_ID, TCP_TITLE = "connectivity.tcp_https", "TCP connection to port 443"
PROXY_TCP_ID, PROXY_TCP_TITLE = "connectivity.proxy_tcp", "TCP connection to proxy"

PUBLIC_IP_TARGET = "1.1.1.1"
DNS_TEST_HOSTNAME = "example.com"
DNS_TIMEOUT = 5.0  # seconds
TCP_PORT = 443
TCP_TIMEOUT = 5.0  # seconds
PING_COUNT = 2
PING_TIMEOUT = 15  # seconds for the whole ping command


def collect(os_name: str, gateway_check: CheckResult | None, run: RunFunc = run_command,
            resolver=socket.getaddrinfo, ping_target: str = PUBLIC_IP_TARGET,
            dns_name: str = DNS_TEST_HOSTNAME, proxy_check: CheckResult | None = None,
            connector=socket.create_connection) -> list[CheckResult]:
    """Targets default to 1.1.1.1 / example.com; corporate networks can point
    them at an internal resolver or intranet host with --ping-target/--dns-name.
    The TCP test connects to the --dns-name host on port 443."""
    dns = check_dns_resolution(dns_name, resolver=resolver)
    if dns.status == Status.OK:
        tcp = check_tcp(TCP_ID, TCP_TITLE, dns_name, TCP_PORT, connector=connector)
    else:
        tcp = CheckResult.skipped(TCP_ID, TCP_TITLE, f"{dns_name} did not resolve")
    return [
        _gateway_ping(os_name, gateway_check, run),
        ping(PUBLIC_PING_ID, PUBLIC_PING_TITLE, os_name, ping_target, run),
        dns,
        tcp,
        _proxy_probe(proxy_check, connector),
    ]


def check_tcp(check_id: str, title: str, host: str, port: int, connector=socket.create_connection,
              timeout: float = TCP_TIMEOUT) -> CheckResult:
    """Open a TCP connection and close it immediately; no data is sent.

    Unlike ping, a completed connection proves that this machine can reach a
    real service through every firewall on the path.
    """
    data = {"target": host, "port": port, "reachable": False, "duration_ms": None}
    started = time.monotonic()
    try:
        connection = connector((host, port), timeout=timeout)
    except TimeoutError:
        return CheckResult.failed(check_id, title, f"connection to {host}:{port} timed out after {timeout:g}s",
                                  data=data)
    except OSError as exc:  # refused, unreachable, name not resolved, ...
        reason = exc.strerror or str(exc)
        return CheckResult.failed(check_id, title, f"connection to {host}:{port} failed: {reason}",
                                  debug=repr(exc), data=data)
    connection.close()
    data.update(reachable=True, duration_ms=round((time.monotonic() - started) * 1000, 1))
    return CheckResult.ok(check_id, title, data)


def _proxy_probe(proxy_check: CheckResult | None, connector) -> CheckResult:
    if proxy_check is None or proxy_check.status != Status.OK or not proxy_check.data.get("configured"):
        return CheckResult.skipped(PROXY_TCP_ID, PROXY_TCP_TITLE, "no proxy configured")
    for entry in proxy_check.data.get("proxies", []):
        endpoint = parse_proxy_endpoint(entry.get("proxy") or "")
        if endpoint:
            host, port = endpoint
            # Only a TCP handshake: no proxy request, no credentials.
            return check_tcp(PROXY_TCP_ID, PROXY_TCP_TITLE, host, port, connector=connector)
    return CheckResult.skipped(PROXY_TCP_ID, PROXY_TCP_TITLE,
                               "only a PAC script or auto-detect is configured; the proxy is chosen per request")


def build_ping_command(os_name: str, target: str) -> list[str]:
    """Ping flags differ per OS: count is -n on Windows, and the per-reply
    timeout is milliseconds on Windows/macOS but seconds on Linux."""
    if os_name == "Windows":
        return ["ping", "-n", str(PING_COUNT), "-w", "2000", target]
    if os_name == "Darwin":
        return ["ping", "-c", str(PING_COUNT), "-W", "2000", target]
    return ["ping", "-c", str(PING_COUNT), "-W", "2", target]


def parse_ping_latency(output: str) -> float | None:
    """Average round-trip time in ms, if the output contains a summary."""
    posix = re.search(r"= [\d.]+/([\d.]+)/", output)       # min/avg/max[/stddev|mdev]
    if posix:
        return float(posix.group(1))
    windows = re.search(r"Average = (\d+)ms", output)     # English Windows only
    if windows:
        return float(windows.group(1))
    return None


def ping_succeeded(os_name: str, returncode: int | None, stdout: str) -> bool:
    if returncode != 0:
        return False
    if os_name == "Windows":
        # Windows ping exits 0 when a router answers "Destination host
        # unreachable", so also require a real echo reply (which has a TTL).
        return "TTL=" in stdout.upper()
    return True


def ping(check_id: str, title: str, os_name: str, target: str, run: RunFunc) -> CheckResult:
    result = run(build_ping_command(os_name, target), timeout=PING_TIMEOUT)
    debug = f"command: {result.args}\nreturncode: {result.returncode}\nstdout: {result.stdout.strip()}"
    if result.error == NOT_FOUND:
        return CheckResult.unavailable(check_id, title, "ping command not found", debug=debug)
    if result.error:
        return CheckResult.failed(check_id, title, result.describe_failure(), debug=debug,
                                  data={"target": target, "reachable": False})

    reachable = ping_succeeded(os_name, result.returncode, result.stdout)
    data = {"target": target, "reachable": reachable, "latency_ms": parse_ping_latency(result.stdout)}
    if reachable:
        return CheckResult.ok(check_id, title, data)
    return CheckResult.failed(check_id, title, f"no reply from {target}", debug=debug, data=data)


def _gateway_ping(os_name: str, gateway_check: CheckResult | None, run: RunFunc) -> CheckResult:
    if gateway_check is None or gateway_check.status != Status.OK:
        return CheckResult.skipped(GATEWAY_PING_ID, GATEWAY_PING_TITLE, "default gateway could not be determined")
    gateway = gateway_check.data.get("gateway")
    if not gateway:
        return CheckResult.skipped(GATEWAY_PING_ID, GATEWAY_PING_TITLE, "no default gateway configured")
    return ping(GATEWAY_PING_ID, GATEWAY_PING_TITLE, os_name, gateway, run)


def check_dns_resolution(hostname: str = DNS_TEST_HOSTNAME, resolver=socket.getaddrinfo,
                         timeout: float = DNS_TIMEOUT) -> CheckResult:
    """Resolve `hostname` with the operating system's resolver.

    getaddrinfo() has no timeout parameter and can block for a long time when
    DNS servers do not answer, so the lookup runs in a daemon thread and we
    stop waiting after `timeout` seconds. A daemon thread will not keep the
    program alive if the lookup is still stuck when the tool exits.
    """
    outcome: dict = {}

    def lookup():
        try:
            outcome["infos"] = resolver(hostname, None)
        except Exception as exc:  # handed back to the main thread below
            outcome["error"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=lookup, name="dns-lookup", daemon=True)
    worker.start()
    worker.join(timeout)
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    data = {"hostname": hostname, "addresses": [], "duration_ms": duration_ms}

    if worker.is_alive():
        return CheckResult.failed(DNS_ID, DNS_TITLE, f"DNS lookup for {hostname} timed out after {timeout:g}s",
                                  data=data)
    error = outcome.get("error")
    if isinstance(error, socket.gaierror):
        reason = error.strerror or str(error)
        return CheckResult.failed(DNS_ID, DNS_TITLE, f"DNS lookup for {hostname} failed: {reason}",
                                  debug=repr(error), data=data)
    if error is not None:
        return CheckResult.failed(DNS_ID, DNS_TITLE, f"DNS lookup for {hostname} failed: {error}",
                                  debug=repr(error), data=data)

    # Each getaddrinfo entry is (family, type, proto, canonname, sockaddr);
    # sockaddr[0] is the IP address. Entries repeat per socket type.
    addresses = sorted({info[4][0] for info in outcome.get("infos") or []})
    if not addresses:
        return CheckResult.failed(DNS_ID, DNS_TITLE, f"DNS lookup for {hostname} returned no addresses", data=data)
    data["addresses"] = addresses
    return CheckResult.ok(DNS_ID, DNS_TITLE, data)
