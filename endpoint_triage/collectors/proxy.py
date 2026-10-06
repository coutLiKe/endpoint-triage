"""Proxy configuration: environment variables and the OS proxy settings.

On many corporate networks, web traffic must go through a proxy. Ping and
DNS do not use it, so they can pass while websites fail (or the reverse).
Knowing whether a proxy is configured explains those cases.

Read-only: settings are only read. Credentials embedded in proxy URLs
(http://user:password@proxy:8080) are removed before anything is stored.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from endpoint_triage.models import CheckResult
from endpoint_triage.runner import RunFunc, command_failure, run_command, run_powershell

PROXY_ID, PROXY_TITLE = "network.proxy", "Proxy configuration"

ENV_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")

# Reads the per-user proxy (Internet Options / WinINET, used by browsers and
# most apps) and the machine-wide WinHTTP proxy (used by Windows services
# such as Windows Update). Registry reads only; CLM-compatible.
WINDOWS_PROXY_SCRIPT = (
    "$ie = Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
    "-ErrorAction SilentlyContinue; "
    "$wh = Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\\Connections' "
    "-ErrorAction SilentlyContinue; "
    "@{ ProxyEnable = $ie.ProxyEnable; ProxyServer = $ie.ProxyServer; ProxyOverride = $ie.ProxyOverride; "
    "AutoConfigURL = $ie.AutoConfigURL; WinHttpSettings = $wh.WinHttpSettings } | ConvertTo-Json -Compress"
)

# WinHTTP proxy type flags stored in the WinHttpSettings registry value.
WINHTTP_FLAG_PROXY = 0x2


def proxy_entry(source: str, proxy: str | None = None, pac_url: str | None = None,
                auto_detect: bool | None = None, bypass: str | None = None) -> dict:
    return {"source": source, "proxy": proxy, "pac_url": pac_url, "auto_detect": auto_detect, "bypass": bypass}


def redact(value: str) -> str:
    """Remove credentials and query strings from a proxy or PAC URL."""
    value = value.strip()
    if "://" not in value:
        return value.rsplit("@", 1)[-1]  # "user:pass@proxy:8080" -> "proxy:8080"
    parts = urlsplit(value)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


DEFAULT_PROXY_PORTS = {"http": 80, "https": 443, "socks": 1080, "socks5": 1080, "socks4": 1080}


def parse_proxy_endpoint(value: str) -> tuple[str, int] | None:
    """Turn a proxy setting into (host, port) for the TCP reachability probe.

    Handles "proxy:8080", "http://proxy:8080" and Windows per-protocol lists
    such as "http=proxy:80;https=proxy:443" (the HTTPS entry is preferred).
    """
    value = value.strip()
    if "=" in value:
        parts = dict(item.split("=", 1) for item in value.split(";") if "=" in item)
        value = parts.get("https") or parts.get("http") or next(iter(parts.values()), "")
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, port or DEFAULT_PROXY_PORTS.get(parts.scheme, 80)


def collect(os_name: str, run: RunFunc = run_command, environ: Mapping[str, str] | None = None) -> list[CheckResult]:
    environ = os.environ if environ is None else environ
    proxies, sources = [], ["environment variables"]
    env = environment_proxy(environ)
    if env:
        proxies.append(env)

    if os_name == "Darwin":
        sources.append("macOS system proxy")
        result = run(["scutil", "--proxy"])
        if not result.ok:
            return [_partial_failure(result, proxies, sources)]
        system = parse_scutil_proxy(result.stdout)
        if system:
            proxies.append(system)
    elif os_name == "Windows":
        sources += ["Windows user proxy (WinINET)", "Windows system proxy (WinHTTP)"]
        result = run_powershell(WINDOWS_PROXY_SCRIPT, run=run)
        if not result.ok:
            return [_partial_failure(result, proxies, sources)]
        try:
            proxies += parse_windows_proxy(result.stdout)
        except (ValueError, KeyError, TypeError) as exc:
            return [CheckResult.failed(PROXY_ID, PROXY_TITLE, f"unexpected proxy settings output: {exc}",
                                       debug=result.stdout, data=_data(proxies, sources))]

    data = _data(proxies, sources)
    if os_name == "Linux":
        data["note"] = "desktop proxy settings (GNOME/KDE) are not checked; only environment variables"
    return [CheckResult.ok(PROXY_ID, PROXY_TITLE, data)]


def _data(proxies: list[dict], sources: list[str]) -> dict:
    return {"configured": bool(proxies), "proxies": proxies, "sources_checked": sources}


def _partial_failure(result, proxies: list[dict], sources: list[str]) -> CheckResult:
    check = command_failure(PROXY_ID, PROXY_TITLE, result)
    check.data = _data(proxies, sources)  # keep what was found in the environment
    return check


# ---------------------------------------------------------------- parsers

def environment_proxy(environ: Mapping[str, str]) -> dict | None:
    values = {}
    for name in ENV_VARS:
        value = environ.get(name) or environ.get(name.lower())
        if value:
            values[name] = redact(value) if name != "NO_PROXY" else value
    proxy = values.get("HTTPS_PROXY") or values.get("HTTP_PROXY") or values.get("ALL_PROXY")
    if not proxy:
        return None  # NO_PROXY alone does not route traffic anywhere
    return proxy_entry("environment variables", proxy=proxy, bypass=values.get("NO_PROXY"))


def parse_scutil_proxy(text: str) -> dict | None:
    """Parse `scutil --proxy` ("HTTPSProxy : proxy.corp.example" lines)."""
    values: dict[str, str] = {}
    exceptions: list[str] = []
    in_exceptions = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExceptionsList"):
            in_exceptions = True
            continue
        if in_exceptions:
            if stripped == "}":
                in_exceptions = False
            elif ":" in stripped:
                exceptions.append(stripped.split(":", 1)[1].strip())
            continue
        match = re.match(r"^(\w+) : (.+)$", stripped)
        if match:
            values[match.group(1)] = match.group(2).strip()

    def server(prefix: str, scheme: str) -> str | None:
        if values.get(f"{prefix}Enable") == "1" and values.get(f"{prefix}Proxy"):
            port = values.get(f"{prefix}Port")
            return f"{scheme}{values[f'{prefix}Proxy']}" + (f":{port}" if port else "")
        return None

    proxy = server("HTTPS", "") or server("HTTP", "") or server("SOCKS", "socks://")
    pac = values.get("ProxyAutoConfigURLString") if values.get("ProxyAutoConfigEnable") == "1" else None
    auto_detect = values.get("ProxyAutoDiscoveryEnable") == "1"
    if not (proxy or pac or auto_detect):
        return None  # macOS always lists default exceptions; that alone is not a proxy
    return proxy_entry("macOS system proxy", proxy=proxy, pac_url=redact(pac) if pac else None,
                       auto_detect=auto_detect, bypass=", ".join(exceptions) or None)


def parse_winhttp_settings(blob: list[int] | None) -> dict | None:
    """Decode the WinHttpSettings registry value (what `netsh winhttp show proxy` prints).

    Layout (little-endian DWORDs): version, counter, flags, proxy length,
    proxy string, bypass length, bypass string. Decoding the bytes avoids
    parsing netsh output, which is translated into the display language.
    """
    if not blob:
        return None
    data = bytes(blob)
    if len(data) < 16:
        raise ValueError("WinHttpSettings value is too short")
    flags = int.from_bytes(data[8:12], "little")
    if not flags & WINHTTP_FLAG_PROXY:
        return None  # direct access
    proxy_length = int.from_bytes(data[12:16], "little")
    proxy = data[16:16 + proxy_length].decode("ascii", "replace")
    offset = 16 + proxy_length
    bypass = None
    if len(data) >= offset + 4:
        bypass_length = int.from_bytes(data[offset:offset + 4], "little")
        bypass = data[offset + 4:offset + 4 + bypass_length].decode("ascii", "replace") or None
    return proxy_entry("Windows system proxy (WinHTTP)", proxy=redact(proxy) or None, bypass=bypass)


def parse_windows_proxy(text: str) -> list[dict]:
    data = json.loads(text)
    proxies = []
    proxy = redact(data["ProxyServer"]) if data.get("ProxyEnable") == 1 and data.get("ProxyServer") else None
    pac = redact(data["AutoConfigURL"]) if data.get("AutoConfigURL") else None
    if proxy or pac:
        proxies.append(proxy_entry("Windows user proxy (WinINET)", proxy=proxy, pac_url=pac,
                                   bypass=data.get("ProxyOverride") or None))
    winhttp = parse_winhttp_settings(data.get("WinHttpSettings"))
    if winhttp:
        proxies.append(winhttp)
    return proxies
