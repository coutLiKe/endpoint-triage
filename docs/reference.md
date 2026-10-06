# Reference

Detailed usage, report format, findings logic and platform notes for
endpoint-triage. Start with the [README](../README.md) for the overview.

## Contents

1. [Usage](#usage)
2. [Supported operating systems](#supported-operating-systems)
3. [Example output](#example-output)
4. [Findings and thresholds](#findings-and-thresholds)
5. [Exit codes](#exit-codes)
6. [What is collected and why](#what-is-collected-and-why)
7. [Cross-platform differences](#cross-platform-differences)
8. [Error handling](#error-handling)
9. [Compatibility and stability](#compatibility-and-stability)
10. [Known limitations](#known-limitations)

## Usage

```bash
python endpoint-triage.pyz                                   # scan and write reports to ./triage-reports/
python endpoint-triage.pyz --output C:\Temp                  # choose the output directory
python endpoint-triage.pyz --skip-updates                    # fast scan without the update check
python endpoint-triage.pyz --ping-target 10.0.0.53 --dns-name intranet.corp.example
python endpoint-triage.pyz --debug                           # log every command and add detail to the reports
```

(From source, use `python -m endpoint_triage` instead of `python endpoint-triage.pyz`.)

| Option | Purpose |
|---|---|
| `-o, --output DIR` | Directory for the reports (created if missing). Default: `./triage-reports`, or the system temp folder if the current folder is not writable |
| `--ping-target IP` | IPv4 address for the internet ping test. Default: `1.1.1.1`. Use an internal host on networks that block outbound ping |
| `--dns-name HOST` | Hostname for the DNS test. Default: `example.com`. Use an internal name to test split DNS or VPN DNS |
| `--skip-updates` | Skip the pending-update check, which can take minutes and contacts update servers |
| `--debug` | Logs each command, its exit code and duration to stderr, and adds raw error detail to both reports |
| `--version` | Print the version and exit |
| `-h, --help` | Show help and exit |

`--ping-target` and `--dns-name` are strictly validated (an IPv4 address and an
RFC 1123 hostname), so a value such as `--ping-target=-f` is rejected instead
of being passed to `ping` as an option.

The CLI is deliberately small. Progress messages go to **stderr** and the
short summary goes to **stdout**, so output can be redirected cleanly:

```text
$ python endpoint-triage.pyz
Endpoint triage: running read-only diagnostics...
  - Collecting system information
  - Collecting memory and disk usage
  - Collecting network configuration
  - Testing connectivity (ping, DNS, TCP)
  - Checking for pending OS updates (this can take a minute)

Overall status: WARNING
Findings:
  [WARNING] High disk usage on /System/Volumes/Data
  [INFO] Operating system updates are pending

Text report: triage-reports/triage-MacBook-Pro.local-20261006-004833.txt
JSON report: triage-reports/triage-MacBook-Pro.local-20261006-004833.json
Exit code 1: WARNING (at least one WARNING finding)
```

Report files are named `triage-<hostname>-<UTC timestamp>.txt/.json`. An
existing file is never overwritten: if two runs land in the same second, the
second gets a `-1` suffix. Reports are created readable only by the user who
ran the tool.

## Supported operating systems

| OS | Notes |
|---|---|
| macOS 13+ (Intel and Apple Silicon) | Built-in tools only |
| Windows 10 / 11, Windows Server 2016+ | Uses Windows PowerShell 5.1 (built in) |
| Linux with iproute2 4.14+ (Ubuntu 18.04+, Debian 10+, RHEL/Rocky 8+, Fedora) | Update detection supports `apt` and `dnf` |

On any other OS (for example FreeBSD) the tool still runs: unsupported checks
are reported as *unavailable* and the overall status is `UNKNOWN`.

### Tested on

| Environment | How | Result |
|---|---|---|
| macOS 26 (Apple Silicon), standard user | Manual runs | All checks complete |
| macOS (GitHub `macos-latest`) | Real scan in CI on every push | All checks complete |
| Windows Server 2025 (GitHub `windows-latest`) | Real scan in CI on every push | All checks complete; ping is blocked but TCP 443 connects, so it is reported as ICMP filtering (INFO) |
| Ubuntu 24.04 (GitHub `ubuntu-latest`) | Real scan in CI on every push | All checks complete; ping is blocked but TCP 443 connects, reported as INFO |
| Windows Server 2025, **standard (non-admin) user** | CI creates a local user and runs the scan as that user | All checks complete |
| Windows Server 2025, **PowerShell Constrained Language Mode** (what AppLocker/WDAC enforce) | CI forces CLM machine-wide, verifies it is active, then scans | All checks complete except the update search, which is reported as "blocked by policy" |
| Windows Server 2025, **user, WinHTTP and environment proxies configured** | CI sets all three, scans, then restores them | All three detected; embedded credentials never reach the report |

The lockdown simulation found a real bug that the unit tests could not: two
PowerShell queries used a construct that Constrained Language Mode forbids,
so on an AppLocker-managed laptop the scan reported UNKNOWN (fixed in 1.1.1).

**Not yet validated:** a real domain-joined laptop with a production
AppLocker/WDAC policy (CI simulates the PowerShell restrictions, not a
complete policy), and traffic through a real corporate proxy (CI verifies the
proxy settings are detected, not that a proxy works).

## Example output

A full sample is in [`sample/sample-report.txt`](../sample/sample-report.txt)
and [`sample/sample-report.json`](../sample/sample-report.json). It shows a
Windows laptop with a nearly full C: drive, a DNS problem, and a Windows
Update check that failed. The first sections look like this:

```text
Endpoint Triage Report
======================
Generated 2026-10-05 14:03:22 UTC by endpoint-triage 1.3.0
Read-only scan: no system settings were changed.

Summary
-------
  Hostname          : HD-LAPTOP-042.corp.local
  Operating system  : Microsoft Windows 11 Pro
  Local time        : 2026-10-05 10:03:22 (UTC-04:00)
  Overall status    : CRITICAL
  Findings          : 1 critical, 1 warning, 2 info
  Checks            : 10 ok, 2 failed, 0 unavailable, 2 skipped
  Scan duration     : 7.4 s
  Run as            : standard user
  Test targets      : ping 1.1.1.1, resolve example.com

Findings
--------
  The ID after each title links to a first troubleshooting step:
  https://github.com/coutLiKe/endpoint-triage/blob/main/docs/findings.md

[CRITICAL] Critically low disk space on C: (disk.critical_low_space)
  C: is at or above 95% used or has less than 5.0 GB free. Very low free
  space can cause failed updates, application crashes and slow performance.
  Evidence:
    - Volume C: is 96.4% used, 17.0 GB free of 476.0 GB

[WARNING] DNS resolution failed (connectivity.dns_failed)
  DNS resolution failed for example.com, but the public IP connectivity test
  succeeded. This may indicate a DNS configuration or DNS server issue. A
  proxy is configured; if this network resolves public names only through
  the proxy, this can be expected.
  Evidence:
    - Ping 192.168.1.1: reply (3.0 ms)
    - Ping 1.1.1.1: reply (15.0 ms)
    - Resolve example.com: failed (DNS lookup for example.com timed out after 5s)

[INFO] System has not been restarted recently (system.long_uptime)
  ...

Connectivity Tests
------------------
  [OK]          Ping default gateway (192.168.1.1): reply, avg 3.0 ms
  [OK]          Ping public IP address (1.1.1.1): reply, avg 15.0 ms
  [FAILED]      Resolve public hostname (example.com): DNS lookup for example.com timed out after 5s
  [SKIPPED]     TCP connection to port 443: example.com did not resolve
  [SKIPPED]     TCP connection to proxy: only a PAC script or auto-detect is configured; the proxy is chosen per request

Errors / Unavailable Checks
---------------------------
  [FAILED] Pending OS updates (updates.os): powershell exited with code 1: Exception from HRESULT: 0x8024402C (hint: 0x8024402C: the update server name could not be resolved; check proxy settings and the WSUS server URL)
```

The report sections are: **Summary, Findings, System Information, Resource
Usage, Network Configuration, Connectivity Tests, Operating System Updates,
Errors / Unavailable Checks**. A check that could not run is described once,
in *Errors / Unavailable Checks*; its own section points there. A failed ping
or DNS lookup is a test *result*, so it stays under *Connectivity Tests*.

### JSON structure

The JSON report contains the same data in a versioned, documented shape:

```json
{
  "schema_version": "1.2",
  "tool": { "name": "endpoint-triage", "version": "1.3.0" },
  "generated_at": "2026-10-05T14:03:22+00:00",
  "generated_at_local": "2026-10-05T10:03:22-04:00",
  "duration_seconds": 7.4,
  "summary": {
    "hostname": "HD-LAPTOP-042.corp.local",
    "os": "Microsoft Windows 11 Pro",
    "os_family": "Windows",
    "run_as": "user",
    "overall_status": "CRITICAL",
    "exit_code": 2,
    "incomplete_core_checks": [],
    "finding_counts": { "CRITICAL": 1, "WARNING": 1, "INFO": 2 },
    "check_counts": { "ok": 10, "failed": 2, "unavailable": 0, "skipped": 2 }
  },
  "scan_options": { "ping_target": "1.1.1.1", "dns_name": "example.com", "skip_updates": false },
  "findings": [
    { "id": "disk.critical_low_space", "severity": "CRITICAL", "title": "...", "explanation": "...", "evidence": ["..."] }
  ],
  "sections": {
    "system":       [ { "id": "system.os", "title": "...", "status": "ok", "data": { }, "error": null } ],
    "resources":    [ "..." ],
    "network":      [ "..." ],
    "connectivity": [ "..." ],
    "updates":      [ "..." ]
  }
}
```

- `overall_status` is one of `OK`, `WARNING`, `CRITICAL`, `UNKNOWN`, and
  `exit_code` is the exit code of the run that produced the report. When the
  status is `UNKNOWN`, `incomplete_core_checks` lists what could not be
  collected. (If the report cannot be written at all, the tool exits 3 and
  there is no report.)
- `run_as` is `user`, `root` or `system` (never the user name).
- Every finding has a stable `id` (documented in
  [docs/findings.md](findings.md)). Alert on the ID, not the title.
- Every check has the same five fields (`id`, `title`, `status`, `data`,
  `error`). `status` is always one of `ok`, `failed`, `unavailable`, `skipped`.
- Sizes are in bytes and times are ISO 8601 UTC, so other tools never parse
  strings like "16.0 GB". With `--debug`, each check also has a `debug` field.
- `schema_version` follows "minor = additive, major = breaking". The sample
  JSON is a golden file in the tests, so format changes cannot slip in
  unnoticed.

## Findings and thresholds

Findings are **troubleshooting signals, not diagnoses**. They point the
technician at likely problems and show the evidence; they never claim to
prove a root cause. [docs/findings.md](findings.md) lists every finding
ID with its meaning and a tier-1 first step.

All thresholds are constants at the top of
[`endpoint_triage/findings.py`](../endpoint_triage/findings.py).

| Condition | Severity | Threshold |
|---|---|---|
| Disk volume usage high | WARNING | ≥ **85 %** used |
| Disk volume critically full | CRITICAL | ≥ **95 %** used, **or** < **5 GB** free on volumes ≥ 20 GB |
| Low available memory | WARNING | < **10 %** of RAM available |
| Long uptime | INFO | ≥ **30 days** since last boot |
| Self-assigned (APIPA) address | WARNING | Any IPv4 address in `169.254.0.0/16` |
| No active network connection | WARNING | No interface is up with a usable IPv4 address |
| Default route through a VPN tunnel | INFO | Default route's interface is a VPN tunnel |
| Scan ran as SYSTEM or root | INFO | Per-user settings reflect that account |
| No DNS servers configured | WARNING | Empty DNS server list |
| No default gateway | WARNING | No default route |
| Proxy configured | INFO | Any proxy, PAC script or auto-detect setting found |
| Pending OS updates | INFO | ≥ 1 pending update |

Why the 5 GB rule only applies to volumes of 20 GB or more: small partitions
such as a 512 MB EFI partition always have less than 5 GB free, which is
normal.

### How the connectivity tests separate the failure domains

Ping is weak evidence because firewalls often drop it. The deciding test is a
**TCP connection** to the `--dns-name` host on port 443: if it opens, this
machine can reach a real internet service through every firewall on the path.
When a proxy is configured, the tool also checks that the proxy accepts a TCP
connection. Both tests only open and close a connection; no data is sent.

| DNS lookup | TCP to host:443 | Proxy configured | Pings | Most likely situation | Severity |
|---|---|---|---|---|---|
| ✅ | ✅ | any | ✅ | No connectivity problem detected | none |
| ✅ | ✅ | any | ❌ | Network path works; ICMP is filtered | INFO |
| ✅ | ❌ | yes | any | Direct HTTPS blocked; expected if all web traffic must use the proxy | INFO |
| ✅ | ❌ | no | any | **Outbound HTTPS blocked**, or a required proxy is missing | WARNING |
| ❌ | — | any | target ✅ | **DNS problem** (IP connectivity works) | WARNING |
| ❌ | — | yes | ❌ | Internal DNS cannot resolve public names; common on proxy networks | INFO |
| ❌ | — | no | gateway ❌, target ❌ | **Local network** (cable, Wi-Fi, VLAN, router) | WARNING |
| ❌ | — | no | gateway ✅, target ❌ | **Internet / upstream** (ISP, firewall, VPN) | WARNING |

Independently: a configured proxy that refuses the TCP connection is a
WARNING (`connectivity.proxy_unreachable`), and a default route through a
VPN tunnel adds an INFO finding, because failures may then come from the VPN.

## Exit codes

The exit codes follow the widely used Nagios/monitoring convention, so a
script or RMM tool can act on the result without parsing the report.

| Code | Status | Meaning |
|---|---|---|
| `0` | OK | Scan completed. No WARNING or CRITICAL findings (INFO findings are allowed) |
| `1` | WARNING | At least one **WARNING** finding |
| `2` | CRITICAL | At least one **CRITICAL** finding |
| `3` | UNKNOWN | **Core diagnostics could not be collected** (OS, memory, disks or network interfaces), or the tool itself failed |
| `64` | — | Invalid command-line arguments (`EX_USAGE`) |
| `130` | — | Interrupted with Ctrl+C |

Precedence is **CRITICAL > UNKNOWN > WARNING > OK**. A scan that could not
read the disks cannot honestly report "OK", so it reports UNKNOWN; a CRITICAL
finding is still reported as CRITICAL because it is already actionable.
Non-core checks that cannot run (for example the update check) are listed in
the report but do not change the exit code. argparse normally exits with 2
for bad arguments, which would collide with CRITICAL, so the CLI uses 64.

```bash
python endpoint-triage.pyz; echo "exit code: $?"            # macOS / Linux
```

```powershell
py endpoint-triage.pyz; echo "exit code: $LASTEXITCODE"    # PowerShell
```

## What is collected and why

| Section | Data | Why a technician cares |
|---|---|---|
| System | Hostname, OS name, version/build, architecture, kernel | Identify the machine; check the OS is supported and patched |
| System | Uptime, last reboot | "Have you restarted it?" is answered by fact, not by asking |
| Resources | Total / available memory | Explains slowness and freezing |
| Resources | Each disk volume: total, used, free, % used | Full disks break updates, profiles, logins and apps |
| Network | Interfaces: status, IPv4/IPv6 (CIDR), MAC | Is the adapter up? Did DHCP work? Is it on the right subnet? MAC addresses are needed for DHCP reservations and NAC |
| Network | Default gateway | Without one, traffic cannot leave the local subnet |
| Network | DNS servers | Wrong DNS servers are a classic cause of "internet works but websites don't" |
| Network | Proxy configuration (environment variables, OS proxy, PAC script, auto-detect) | Ping and DNS don't use the proxy, so a broken or unreachable proxy explains "tests pass but websites fail" |
| Connectivity | Ping gateway and target, resolve hostname, TCP connection to port 443 and to the proxy | Narrows a connectivity problem to local network, DNS, firewall, proxy or internet |
| Network | VPN tunnels and whether the default route uses one | A full-tunnel VPN changes where "the internet" is |
| Context | Whether the scan ran as a standard user, root or SYSTEM (not the user name) | RMM/Intune runs read per-user settings for the wrong account |
| Updates | Pending OS updates, restart required | Missing patches are a common cause of bugs and a security risk |

**Gateway / default route:** the router that receives any traffic not
destined for the local subnet. A device with an IP address but no default
gateway can talk to its neighbors but not the internet.

**DNS vs IP connectivity:** pinging `1.1.1.1` tests whether packets can
reach the internet *by IP address*. Resolving `example.com` tests whether
*names* can be turned into addresses. If the IP test passes and the DNS test
fails, the network path works and the problem is name resolution. This is
the case where users say the "internet is down" even though the network is
fine. On corporate networks, point the tests at internal targets with
`--ping-target` and `--dns-name` to answer "can this laptop reach *our*
services?".

The DNS check uses the operating system's resolver (`socket.getaddrinfo`),
so it sees the same result as browsers and apps, including VPN DNS, the hosts
file and corporate resolvers. The lookup runs in a background thread with a
5-second timeout, because `getaddrinfo` has no timeout of its own and can
hang when DNS servers do not answer.

## Cross-platform differences

| Data | macOS | Linux | Windows |
|---|---|---|---|
| OS version | `sw_vers` | `/etc/os-release` | `Win32_OperatingSystem` (CIM) |
| Boot time | `sysctl -n kern.boottime` | `/proc/uptime` | `Win32_OperatingSystem.LastBootUpTime` |
| Memory | `sysctl hw.memsize` + `vm_stat` | `/proc/meminfo` | `Win32_OperatingSystem` |
| Disks | `df -P -k` | `df -P -k` | `Win32_LogicalDisk` (fixed disks) |
| Interfaces | `ifconfig` | `ip -j addr` (JSON) | `Get-NetAdapter`, `Get-NetIPAddress` |
| Gateway | `route -n get default` | `ip -j route show default` (JSON) | `Get-NetRoute 0.0.0.0/0` |
| DNS servers | `scutil --dns` | `/etc/resolv.conf` | `Get-DnsClientServerAddress` |
| Proxy | `scutil --proxy` + environment variables | Environment variables (`HTTPS_PROXY`, ...) | Registry: user proxy (WinINET) and system proxy (WinHTTP) + environment variables |
| Ping | `ping -c 2 -W 2000` (ms) | `ping -c 2 -W 2` (seconds) | `ping -n 2 -w 2000` (ms) |
| Updates | `softwareupdate -l` | `apt list --upgradable` or `dnf -C check-update` | Windows Update Agent COM API (search only) |

Notable differences the code handles:

- **Prefer structured output.** On Windows, PowerShell objects are converted
  with `ConvertTo-Json`, and on Linux `ip -j` emits JSON. Parsing JSON is far
  more reliable than scraping `ipconfig` text, which changes with the
  display language.
- **Locale.** On macOS/Linux commands run with `LC_ALL=C` so output is always
  English with `.` as the decimal separator.
- **Trusted binary locations.** Each command is run from a fixed system path
  (`/usr/bin`, `/sbin`, `%SystemRoot%\System32`, ...), which differ per OS
  and distribution; PATH is never searched.
- **Ping flags differ.** `-c` vs `-n` for count; `-W` is milliseconds on macOS
  but seconds on Linux.
- **Windows ping exit codes.** `ping` exits `0` even when a router replies
  "Destination host unreachable", so on Windows a reply must also contain
  `TTL=` to count as success.
- **APFS on macOS.** The root and Data volumes share one container (and free
  space). Only `/`, `/System/Volumes/Data` and drives under `/Volumes/` are
  shown. System volumes (`VM`, `Preboot`, ...) and read-only images such as
  cryptexes, Siri/asset images and simulator runtimes always look 100% full and
  would cause false CRITICAL findings.
- **Linux pseudo filesystems** (`tmpfs`, snap `loop` devices, `overlay`) are
  filtered out of the disk list. Inside a container the tool falls back to `/`.
- **systemd-resolved.** If `/etc/resolv.conf` only lists `127.0.0.53`, the
  report notes that this is a local stub and the real servers are in
  `resolvectl status`.
- **Interface noise.** Loopback, Docker bridges and macOS internal
  interfaces (`awdl`, `anpi`, `bridge`, ...) are left out, and down
  interfaces with no address are listed on one "Not connected" line.
- **VPN tunnels.** macOS `utun`/`ipsec`/`ppp` interfaces count as tunnels
  only when they have an IPv4 address (macOS always has several idle `utun`
  interfaces); Linux uses names such as `tun`, `wg` and `ppp`; Windows
  recognizes common VPN clients by adapter description.
- **Windows has two proxy settings.** The per-user Internet Options proxy
  (WinINET) is used by browsers and most apps; the machine-wide WinHTTP proxy
  is used by services such as Windows Update. Both are reported. The WinHTTP
  value is decoded from its binary registry format instead of parsing
  `netsh winhttp show proxy`, whose output is translated into the display
  language.

## Error handling

The guiding rule: **one failed check never stops the scan, no failure is
silent, and an incomplete scan never claims to be healthy.**

1. **The runner never raises for ordinary failures.** A missing command
   (or one missing from its trusted location), a timeout, a permission
   error, or a non-zero exit code all come back as a `CommandResult` that
   describes what happened.
2. **Collectors translate failures into statuses.**
   - Command not found / unsupported OS / blocked by policy → `unavailable`
   - Non-zero exit, timeout, or output that cannot be parsed → `failed`
   - Not applicable or turned off (no gateway to ping, `--skip-updates`) → `skipped`
3. **Partial data is still reported.** If `vm_stat` fails, total memory is
   still reported. If `df` exits 1 because of a stale network mount, the
   other volumes are still shown with a note.
4. **Errors are explained.** Common Windows Update error codes get a
   one-line hint (proxy, WSUS, timeout), and a Constrained Language Mode
   block is reported as "blocked by policy".
5. **The scanner has a last line of defense.** Each collector runs inside a
   guard; if a bug raises an unexpected exception, it becomes a `failed`
   check (with the traceback saved for `--debug`) and the other sections
   still run.
6. **Missing core data means UNKNOWN.** If OS, memory, disk or interface
   data is missing, the overall status is UNKNOWN (exit 3) unless something
   is already CRITICAL.
7. **Users see messages, not stack traces.** Normal mode shows one-line
   errors. `--debug` logs every command to stderr and adds stderr output and
   tracebacks to the reports.
8. **Reports are written safely.** If the current folder is not writable,
   reports go to a new private folder in the system temp directory and the
   path is printed. If even that fails, the tool exits 3 with an error.

## Compatibility and stability

What scripts and RMM monitors can rely on, starting with 1.3.0:

- **Exit codes** `0`, `1`, `2`, `3`, `64`, `130` keep their meaning. A change
  would be a new major version.
- **Finding IDs** listed in [findings.md](findings.md) are never renamed or
  reused. A finding may be retired (listed in the CHANGELOG); its ID is then
  never given a different meaning.
- **JSON** follows `schema_version`: a minor bump (`1.1` → `1.2`) only adds
  fields; a major bump may remove or change fields. Parse by key, and ignore
  keys you don't know.
- **Not stable:** finding titles and explanations, the text report layout,
  and the order of checks. Don't parse the `.txt` report; use the JSON.
- **Versions** follow semantic versioning; each release lists its changes in
  the [CHANGELOG](../CHANGELOG.md).

## Known limitations

- **Requires Python 3.11+ on the endpoint.** The single file removes the
  install step, not the interpreter (see [Installation](../README.md#installation)).
- **Pending updates on Linux** reflect the local package index / cache. If
  `apt update` or `dnf makecache` has not run recently, the list may be
  stale (the report says so). Only `apt` and `dnf` are supported; zypper and
  pacman report *unavailable*.
- **Update checks can be slow.** `softwareupdate -l` and Windows Update
  searches contact the vendor and can take a minute or more (timeout: 180 s).
  Use `--skip-updates` when time matters.
- **Proxy checks stop at the TCP handshake.** The tool checks that a
  configured proxy accepts a connection, but does not send a request through
  it, authenticate, or download and evaluate PAC scripts. On Linux only
  environment variables are checked, not GNOME/KDE desktop proxy settings.
- **One TCP target.** The TCP test uses the `--dns-name` host on port 443;
  a network that blocks that specific host will show a failure.
- **`--ping-target` accepts IPv4 only.** macOS needs a separate `ping6`
  command for IPv6, which would add complexity for little triage value.
- **Endpoint security products** may flag or block Python starting
  PowerShell with an inline script; see [SECURITY.md](../SECURITY.md).
- **Under AppLocker/WDAC** (Constrained Language Mode) the Windows Update
  search cannot run; the report says "blocked by policy". All other checks work.
- **IPv6-only networks and captive portals** are not specifically detected.
- **macOS available memory** is an approximation (free + inactive +
  speculative pages); Activity Monitor uses a more complex formula.
- **Mounted disk images** (DMG/ISO) on macOS/Linux may appear as nearly full
  volumes.
- **Windows latency** is parsed only from English `ping` output; reachability
  works in every language because it relies on `TTL=`.
- **Old Linux distributions** whose `ip` lacks JSON output (iproute2 < 4.14)
  report network interfaces and gateway as *failed*.
- The tool runs once and reports a snapshot; intermittent problems may not
  appear in a single run.

