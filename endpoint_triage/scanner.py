"""Run every collector in order and assemble the Report."""

from __future__ import annotations

import logging
import os
import platform
import socket
import time
import traceback
from datetime import datetime, timezone
from typing import Callable

from endpoint_triage import __version__, findings
from endpoint_triage.collectors import connectivity, network, proxy, resources, system, updates
from endpoint_triage.models import CheckResult, Report
from endpoint_triage.runner import RunFunc, read_text_file, run_command

log = logging.getLogger(__name__)


def run_scan(os_name: str | None = None, run: RunFunc = run_command, read_file=read_text_file,
             resolver=socket.getaddrinfo, progress: Callable[[str], None] = lambda message: None,
             ping_target: str = connectivity.PUBLIC_IP_TARGET, dns_name: str = connectivity.DNS_TEST_HOSTNAME,
             skip_updates: bool = False, environ=None, connector=socket.create_connection) -> Report:
    os_name = os_name or platform.system()
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    checks: dict[str, list[CheckResult]] = {}

    progress("Collecting system information")
    checks["system"] = _guarded("system", lambda: system.collect(os_name, run, read_file))

    progress("Collecting memory and disk usage")
    checks["resources"] = _guarded("resources", lambda: resources.collect(os_name, run, read_file))

    progress("Collecting network configuration")
    checks["network"] = (_guarded("network", lambda: network.collect(os_name, run, read_file))
                         + _guarded("proxy", lambda: proxy.collect(os_name, run, environ)))

    progress("Testing connectivity (ping, DNS, TCP)")
    gateway = next((c for c in checks["network"] if c.id == network.GATEWAY_ID), None)
    proxy_check = next((c for c in checks["network"] if c.id == proxy.PROXY_ID), None)
    checks["connectivity"] = _guarded("connectivity", lambda: connectivity.collect(
        os_name, gateway, run, resolver, ping_target=ping_target, dns_name=dns_name,
        proxy_check=proxy_check, connector=connector))

    if skip_updates:
        checks["updates"] = [CheckResult.skipped(updates.UPDATES_ID, updates.UPDATES_TITLE,
                                                 "skipped with --skip-updates")]
    else:
        progress("Checking for pending OS updates (this can take a minute)")
        checks["updates"] = _guarded("updates", lambda: updates.collect(os_name, run))

    options = {"ping_target": ping_target, "dns_name": dns_name, "skip_updates": skip_updates}
    report = Report(__version__, os_name, started_at, time.monotonic() - started, checks, options=options,
                    run_as=detect_run_context(os_name, os.environ if environ is None else environ),
                    local_time=started_at.astimezone())
    report.findings = findings.analyze(report)
    return report


def detect_run_context(os_name: str, environ, geteuid=getattr(os, "geteuid", None)) -> str:
    """Return "system", "root" or "user" without recording the user name.

    RMM and Intune scripts usually run as SYSTEM (Windows) or root. Per-user
    settings are then read for that account, not the signed-in user.
    """
    if os_name == "Windows":
        user = environ.get("USERNAME", "")
        profile = environ.get("USERPROFILE", "").lower()
        # The SYSTEM account's USERNAME is "<COMPUTERNAME>$".
        if user.upper() == "SYSTEM" or user.endswith("$") or "systemprofile" in profile:
            return "system"
        return "user"
    if geteuid is not None and geteuid() == 0:
        return "root"
    return "user"


def _guarded(section: str, collect: Callable[[], list[CheckResult]]) -> list[CheckResult]:
    """Last line of defense: a bug in one collector must not stop the scan.

    Expected problems (missing commands, bad output) are handled inside the
    collectors. Anything that still raises is a bug, so it is logged with a
    traceback and reported as a failed check instead of crashing.
    """
    try:
        return collect()
    except Exception as exc:
        log.debug("Collector %s crashed", section, exc_info=True)
        return [CheckResult.failed(f"{section}.collector", f"{section.title()} collector",
                                   f"unexpected error: {type(exc).__name__}: {exc}",
                                   debug=traceback.format_exc())]
