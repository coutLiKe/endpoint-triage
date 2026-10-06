# Findings Reference

Every finding has a stable `id` in the JSON report. Titles may be reworded
between versions; IDs do not change. Use the ID to alert on a finding, link
it to a knowledge-base article, or group tickets by cause.

The "First step" column is a suggested tier-1 action. Findings are
troubleshooting signals, not diagnoses: confirm before acting.

Thresholds are constants at the top of
[`endpoint_triage/findings.py`](../endpoint_triage/findings.py).
A test fails if a finding ID in the code is missing from this page, or if
this page lists an ID the code no longer produces.

## Disk and memory

| ID | Severity | Meaning | First step |
|---|---|---|---|
| `disk.critical_low_space` | CRITICAL | A volume is ≥ 95 % used, or a volume of 20 GB or more has < 5 GB free. | Free space now: empty the Recycle Bin/Trash, clear temp files and the Downloads folder, and check for large logs or old update caches. |
| `disk.high_usage` | WARNING | A volume is ≥ 85 % used. | Plan a clean-up before it becomes critical; check which folders grew recently. |
| `memory.low_available` | WARNING | Less than 10 % of RAM is available. | Check Task Manager / Activity Monitor for the processes using the most memory; close or restart them. |

## System

| ID | Severity | Meaning | First step |
|---|---|---|---|
| `system.long_uptime` | INFO | The system has run for 30 days or more without a restart. | Ask the user to restart (after saving work) before deeper troubleshooting; pending updates often need it. |

## Network configuration

| ID | Severity | Meaning | First step |
|---|---|---|---|
| `network.apipa_address` | WARNING | An interface has a self-assigned `169.254.x.x` address, so DHCP likely failed. | Renew the lease (`ipconfig /renew`, or turn Wi-Fi off and on); if it persists, check the DHCP scope and the switch port or Wi-Fi network. |
| `network.no_active_connection` | WARNING | No interface is up with a usable IPv4 address. | Check the cable or Wi-Fi connection and that the adapter is enabled (and airplane mode is off). |
| `network.proxy_configured` | INFO | Web traffic goes through a proxy or a PAC script (from environment variables or the OS proxy settings). | If websites fail while ping and DNS pass, check that the proxy is reachable and the PAC file downloads; confirm the user is allowed through the proxy. |
| `network.no_dns_servers` | WARNING | No DNS servers are configured. | Check the adapter's DNS settings and DHCP options; renew the DHCP lease. |

## Connectivity

The connectivity findings combine four tests: ping to the gateway, ping to the
ping target, DNS resolution of the DNS test name, and a TCP connection to that
name on port 443 (plus a TCP connection to the configured proxy, if any). A
completed TCP connection is the deciding evidence; ping only explains the rest.

| ID | Severity | Meaning | First step |
|---|---|---|---|
| `connectivity.no_default_gateway` | WARNING | No default route is configured. | Renew DHCP; if the address is static, check the gateway setting. |
| `connectivity.proxy_unreachable` | WARNING | A proxy is configured but did not accept a TCP connection. | Check the proxy server is up and reachable from this network (VPN required?). |
| `connectivity.ping_blocked` | INFO | Neither ping answered, but a TCP connection worked (or DNS worked and TCP did not run). | No action; ICMP is filtered. |
| `connectivity.gateway_no_ping_reply` | INFO | The gateway ignored ping, but the internet tests worked. | No action; many routers drop ping. |
| `connectivity.public_ip_no_ping_reply` | INFO | The ping target ignored ping, but a TCP connection worked (or DNS worked and TCP did not run). | No action on networks that block outbound ICMP. |
| `connectivity.direct_https_blocked_proxy` | INFO | A direct connection to port 443 failed and a proxy is configured. | Expected where all web traffic must use the proxy; if websites fail, check the proxy. |
| `connectivity.https_blocked` | WARNING | The DNS test name resolved, but a TCP connection to port 443 failed and no proxy is configured. | Check outbound firewall rules for HTTPS, or whether this network requires a proxy. |
| `connectivity.dns_failed` | WARNING | DNS resolution failed while IP connectivity worked (or was not tested). | Check the configured DNS servers; try another server (`nslookup example.com 1.1.1.1`); flush the DNS cache. |
| `connectivity.dns_failed_proxy_network` | INFO | The public test name did not resolve, direct pings failed, and a proxy is configured. | Often normal with internal DNS; re-run with `--dns-name` set to an internal hostname. |
| `connectivity.local_network_unreachable` | WARNING | Neither the gateway nor the ping target answered, and DNS failed. | Check the physical link, Wi-Fi association, VLAN or switch port, and the router. |
| `connectivity.internet_unreachable` | WARNING | The ping target did not answer and DNS failed (gateway answered or was not tested). | Check for an ISP outage, an upstream firewall, or a VPN that must be connected. |
| `connectivity.dns_failed_no_internet` | INFO | DNS failed, which is expected while there is no internet connectivity. | Fix connectivity first, then re-test DNS. |

## Updates

| ID | Severity | Meaning | First step |
|---|---|---|---|
| `updates.pending` | INFO | Operating system updates are pending. | Install updates through your organization's patching process; restart if required. |

## Checks that could not run

These are not findings. They are listed once under
**Errors / Unavailable Checks** in the text report, and with
`status: "failed"`, `"unavailable"` or `"skipped"` in the JSON. If a core
check (OS, memory, disks or network interfaces) could not run, the overall
status is `UNKNOWN` (exit code 3).
