"""Network configuration: interfaces, default gateway, DNS servers."""

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

INTERFACES_ID, INTERFACES_TITLE = "network.interfaces", "Network interfaces"
GATEWAY_ID, GATEWAY_TITLE = "network.gateway", "Default gateway"
DNS_ID, DNS_TITLE = "network.dns_servers", "DNS servers"

# Virtual/system interfaces that are noise for help desk triage.
SKIPPED_PREFIXES = {
    "Darwin": ("lo", "gif", "stf", "utun", "awdl", "llw", "anpi", "ap", "bridge"),
    "Linux": ("lo", "docker", "veth", "br-", "virbr", "vnet"),
    "Windows": ("Loopback",),
}

# Windows placeholder DNS addresses used when no IPv6 DNS is configured.
WINDOWS_PLACEHOLDER_DNS = ("fec0:0:0:ffff::",)

# Constrained Language Mode compatible: plain hashtables instead of
# [pscustomobject], and [string] casts instead of method calls on
# non-core types (CLM only allows methods on core .NET types).
WINDOWS_NETWORK_SCRIPT = (
    "$adapters = @(Get-NetAdapter | Select-Object Name, Status, MacAddress, ifIndex); "
    "$addresses = @(Get-NetIPAddress | Select-Object InterfaceIndex, IPAddress, PrefixLength, "
    "@{n='Family';e={[string]$_.AddressFamily}}); "
    "$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
    "Select-Object NextHop, InterfaceIndex, RouteMetric); "
    "$dns = @(Get-DnsClientServerAddress | Select-Object InterfaceIndex, ServerAddresses); "
    "@{ adapters = $adapters; addresses = $addresses; routes = $routes; dns = $dns } "
    "| ConvertTo-Json -Depth 4 -Compress"
)


def collect(os_name: str, run: RunFunc = run_command, read_file=read_text_file) -> list[CheckResult]:
    if os_name == "Darwin":
        return [_macos_interfaces(run), _macos_gateway(run), _macos_dns(run)]
    if os_name == "Linux":
        return [_linux_interfaces(run), _linux_gateway(run), _linux_dns(read_file)]
    if os_name == "Windows":
        return _windows(run)
    return [unsupported_os(INTERFACES_ID, INTERFACES_TITLE, os_name),
            unsupported_os(GATEWAY_ID, GATEWAY_TITLE, os_name),
            unsupported_os(DNS_ID, DNS_TITLE, os_name)]


def interface(name: str, status: str, ipv4=None, ipv6=None, mac=None) -> dict:
    return {"name": name, "status": status, "ipv4": ipv4 or [], "ipv6": ipv6 or [], "mac": mac}


def is_relevant(name: str, os_name: str) -> bool:
    return not name.startswith(SKIPPED_PREFIXES.get(os_name, ()))


def _dedupe(items) -> list:
    return list(dict.fromkeys(items))


def _strip_scope(address: str) -> str:
    """fe80::1%en0 -> fe80::1 (the %zone suffix is interface-local noise)."""
    return address.split("%", 1)[0]


# ------------------------------------------------------------ macOS parsers

def parse_ifconfig(text: str) -> list[dict]:
    """Parse macOS `ifconfig` output into interface dicts (all interfaces)."""
    interfaces = []
    current = None
    for line in text.splitlines():
        header = re.match(r"^(\S+?): flags=\w+<([^>]*)>", line)
        if header:
            current = {"name": header.group(1), "flags": header.group(2).split(","),
                       "ipv4": [], "ipv6": [], "mac": None, "status_line": None}
            interfaces.append(current)
            continue
        if current is None:
            continue
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "ether" and len(fields) > 1:
            current["mac"] = fields[1].lower()
        elif fields[0] == "inet" and len(fields) > 1:
            prefix = ""
            if "netmask" in fields:
                mask = fields[fields.index("netmask") + 1]
                prefix = f"/{bin(int(mask, 16)).count('1')}" if mask.startswith("0x") else ""
            current["ipv4"].append(fields[1] + prefix)
        elif fields[0] == "inet6" and len(fields) > 1:
            prefix = f"/{fields[fields.index('prefixlen') + 1]}" if "prefixlen" in fields else ""
            current["ipv6"].append(_strip_scope(fields[1]) + prefix)
        elif fields[0] == "status:" and len(fields) > 1:
            current["status_line"] = fields[1]

    result = []
    for item in interfaces:
        if item["status_line"] is not None:
            status = "up" if item["status_line"] == "active" else "down"
        else:
            status = "up" if {"UP", "RUNNING"} <= set(item["flags"]) else "down"
        result.append(interface(item["name"], status, item["ipv4"], item["ipv6"], item["mac"]))
    return result


def parse_route_get(text: str) -> dict:
    """Parse `route -n get default` ("gateway: 192.168.1.1", "interface: en0")."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key in ("gateway", "interface"):
            values[key] = value.strip()
    return {"gateway": values.get("gateway"), "interface": values.get("interface")}


def parse_scutil_dns(text: str) -> list[str]:
    return _dedupe(re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", text))


# ------------------------------------------------------------ Linux parsers

def parse_ip_addr_json(text: str) -> list[dict]:
    """Parse `ip -j addr show` (iproute2 JSON) into interface dicts."""
    result = []
    for item in json.loads(text):
        state = item.get("operstate", "UNKNOWN")
        if state == "UP":
            status = "up"
        elif state in ("DOWN", "LOWERLAYERDOWN", "DORMANT", "NOTPRESENT"):
            status = "down"
        else:  # UNKNOWN is common for virtual/tunnel links; fall back to flags.
            status = "up" if "LOWER_UP" in item.get("flags", []) else "unknown"
        ipv4, ipv6 = [], []
        for addr in item.get("addr_info", []):
            cidr = f"{addr['local']}/{addr['prefixlen']}"
            (ipv4 if addr.get("family") == "inet" else ipv6).append(cidr)
        result.append(interface(item["ifname"], status, ipv4, ipv6, item.get("address")))
    return result


def parse_ip_route_json(text: str) -> dict:
    """Pick the default route with the lowest metric from `ip -j route show default`."""
    routes = [r for r in json.loads(text) if r.get("gateway")]
    if not routes:
        return {"gateway": None, "interface": None}
    best = min(routes, key=lambda r: r.get("metric", 0))
    return {"gateway": best["gateway"], "interface": best.get("dev")}


def parse_resolv_conf(text: str) -> list[str]:
    servers = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            servers.append(fields[1])
    return _dedupe(servers)


# ---------------------------------------------------------- Windows parser

def parse_windows_network(text: str) -> tuple[list[dict], dict, list[str]]:
    data = json.loads(text)
    addresses_by_index: dict = {}
    for addr in data.get("addresses") or []:
        addresses_by_index.setdefault(addr["InterfaceIndex"], []).append(addr)

    interfaces = []
    up_indexes = set()
    for adapter in data.get("adapters") or []:
        index = adapter["ifIndex"]
        status = "up" if adapter.get("Status") == "Up" else "down"
        if status == "up":
            up_indexes.add(index)
        ipv4, ipv6 = [], []
        for addr in addresses_by_index.get(index, []):
            cidr = f"{_strip_scope(addr['IPAddress'])}/{addr['PrefixLength']}"
            (ipv4 if addr.get("Family") == "IPv4" else ipv6).append(cidr)
        mac = (adapter.get("MacAddress") or "").replace("-", ":").lower() or None
        interfaces.append(interface(adapter["Name"], status, ipv4, ipv6, mac))

    gateway = {"gateway": None, "interface": None}
    routes = [r for r in data.get("routes") or [] if r.get("NextHop") not in (None, "0.0.0.0")]
    if routes:
        best = min(routes, key=lambda r: r.get("RouteMetric", 0))
        names = {a["ifIndex"]: a["Name"] for a in data.get("adapters") or []}
        gateway = {"gateway": best["NextHop"], "interface": names.get(best.get("InterfaceIndex"))}

    dns = []
    for entry in data.get("dns") or []:
        if entry.get("InterfaceIndex") in up_indexes:
            dns.extend(s for s in entry.get("ServerAddresses") or []
                       if not s.startswith(WINDOWS_PLACEHOLDER_DNS))
    return interfaces, gateway, _dedupe(dns)


# ----------------------------------------------------------------- per-OS

def _interfaces_check(all_interfaces: list[dict], os_name: str) -> CheckResult:
    relevant = [i for i in all_interfaces if is_relevant(i["name"], os_name)]
    return CheckResult.ok(INTERFACES_ID, INTERFACES_TITLE, {"interfaces": relevant})


def _macos_interfaces(run: RunFunc) -> CheckResult:
    result = run(["ifconfig"])
    if not result.ok:
        return command_failure(INTERFACES_ID, INTERFACES_TITLE, result)
    try:
        parsed = parse_ifconfig(result.stdout)
    except (ValueError, IndexError) as exc:
        return CheckResult.failed(INTERFACES_ID, INTERFACES_TITLE, f"could not parse ifconfig: {exc}",
                                  debug=result.stdout)
    if not parsed:
        return CheckResult.failed(INTERFACES_ID, INTERFACES_TITLE, "no interfaces found in ifconfig output",
                                  debug=result.stdout)
    return _interfaces_check(parsed, "Darwin")


def _macos_gateway(run: RunFunc) -> CheckResult:
    result = run(["route", "-n", "get", "default"])
    output = result.stdout + result.stderr
    if result.error is None and "not in table" in output:
        # A definite answer: this machine has no default route.
        return CheckResult.ok(GATEWAY_ID, GATEWAY_TITLE, {"gateway": None, "interface": None})
    if not result.ok:
        return command_failure(GATEWAY_ID, GATEWAY_TITLE, result)
    return CheckResult.ok(GATEWAY_ID, GATEWAY_TITLE, parse_route_get(result.stdout))


def _macos_dns(run: RunFunc) -> CheckResult:
    result = run(["scutil", "--dns"])
    if not result.ok:
        return command_failure(DNS_ID, DNS_TITLE, result)
    return CheckResult.ok(DNS_ID, DNS_TITLE, {"servers": parse_scutil_dns(result.stdout)})


def _linux_interfaces(run: RunFunc) -> CheckResult:
    result = run(["ip", "-j", "addr", "show"])
    if not result.ok:
        return command_failure(INTERFACES_ID, INTERFACES_TITLE, result)
    try:
        parsed = parse_ip_addr_json(result.stdout)
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult.failed(INTERFACES_ID, INTERFACES_TITLE, f"unexpected `ip -j addr` output: {exc}",
                                  debug=result.stdout)
    return _interfaces_check(parsed, "Linux")


def _linux_gateway(run: RunFunc) -> CheckResult:
    result = run(["ip", "-j", "route", "show", "default"])
    if not result.ok:
        return command_failure(GATEWAY_ID, GATEWAY_TITLE, result)
    try:
        return CheckResult.ok(GATEWAY_ID, GATEWAY_TITLE, parse_ip_route_json(result.stdout or "[]"))
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult.failed(GATEWAY_ID, GATEWAY_TITLE, f"unexpected `ip -j route` output: {exc}",
                                  debug=result.stdout)


def _linux_dns(read_file) -> CheckResult:
    try:
        servers = parse_resolv_conf(read_file("/etc/resolv.conf"))
    except OSError as exc:
        return CheckResult.unavailable(DNS_ID, DNS_TITLE, f"/etc/resolv.conf not readable: {exc}")
    data: dict = {"servers": servers}
    if servers == ["127.0.0.53"]:
        data["note"] = ("127.0.0.53 is the local systemd-resolved stub; "
                        "run `resolvectl status` to see the upstream DNS servers")
    return CheckResult.ok(DNS_ID, DNS_TITLE, data)


def _windows(run: RunFunc) -> list[CheckResult]:
    result = run_powershell(WINDOWS_NETWORK_SCRIPT, run=run)
    if not result.ok:
        return [command_failure(INTERFACES_ID, INTERFACES_TITLE, result),
                command_failure(GATEWAY_ID, GATEWAY_TITLE, result),
                command_failure(DNS_ID, DNS_TITLE, result)]
    try:
        interfaces, gateway, dns = parse_windows_network(result.stdout)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        error = f"unexpected network output: {exc}"
        return [CheckResult.failed(check_id, title, error, debug=result.stdout) for check_id, title in
                ((INTERFACES_ID, INTERFACES_TITLE), (GATEWAY_ID, GATEWAY_TITLE), (DNS_ID, DNS_TITLE))]
    return [_interfaces_check(interfaces, "Windows"),
            CheckResult.ok(GATEWAY_ID, GATEWAY_TITLE, gateway),
            CheckResult.ok(DNS_ID, DNS_TITLE, {"servers": dns})]
