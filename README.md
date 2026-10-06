# Endpoint Triage

[![Tests](https://github.com/coutLiKe/endpoint-triage/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/coutLiKe/endpoint-triage/actions/workflows/tests.yml)

A cross-platform, **read-only** command-line tool that a help desk technician
runs with one command to collect common endpoint diagnostics and produce a
report that can be attached to a support ticket.

```bash
python endpoint-triage.pyz
```

It answers one question:

> *What basic information and connectivity problems can I identify on this
> computer without making any changes to the system?*

- Python 3.11+, **standard library only**: nothing to `pip install`
- Ships as **one file** (`endpoint-triage.pyz`) with a SHA-256 checksum
- Works on **macOS, Windows and Linux**, without admin/root rights
- Produces a ticket-ready **`.txt` report** and a versioned **`.json` report**
- Highlights **findings** (CRITICAL / WARNING / INFO) with stable IDs at the top of the report
- Never changes the system and never sends collected data anywhere

Security review: see [SECURITY.md](SECURITY.md) for every command it runs,
every network destination, the data it collects, and the threat model.

---

## Contents

1. [Why this tool exists](#why-this-tool-exists)
2. [Installation](#installation)
3. [Usage](#usage)
4. [Supported operating systems](#supported-operating-systems)
5. [Example output](#example-output)
6. [Findings and thresholds](#findings-and-thresholds)
7. [Exit codes](#exit-codes)
8. [Architecture](#architecture)
9. [What is collected and why](#what-is-collected-and-why)
10. [Cross-platform differences](#cross-platform-differences)
11. [Error handling](#error-handling)
12. [Privacy and read-only design](#privacy-and-read-only-design)
13. [Testing](#testing)
14. [Continuous integration and releases](#continuous-integration-and-releases)
15. [Known limitations](#known-limitations)

---

## Why this tool exists

The first few minutes of most endpoint tickets are spent collecting the same
facts: *What OS is this? When was it last rebooted? Is the disk full? Does it
have an IP address? Can it reach the gateway, the internet, DNS? Are updates
pending?* Technicians collect these by hand with a dozen different commands,
and the commands differ on every OS.

Endpoint Triage runs those checks consistently, typically in 10–30 seconds,
and writes the answers into a report that can be attached to the ticket. That
report gives a Tier 2 engineer the same baseline facts every time and avoids
a "can you run this command for me?" round trip.

It is intentionally **not** a monitoring platform: no agent, no server, no
database, no remediation.

## Installation

**Prerequisite: Python 3.11 or newer on the endpoint.** Python is preinstalled
on most Linux distributions, but not on Windows or managed Macs. Deploy it the
way you deploy other software, for example:

| OS | Typical way to provide Python |
|---|---|
| Windows | `winget install Python.Python.3.13`, or push the python.org installer with Intune / SCCM / your RMM |
| macOS | python.org installer or Homebrew, or push a package with Jamf / your MDM |
| Linux | Usually present (`python3 --version`); otherwise install `python3` with the package manager |

On an older Python the tool stops with a clear "requires Python 3.11" message.

### Option 1: single-file release (recommended)

Download `endpoint-triage.pyz` and `endpoint-triage.pyz.sha256` from the
[latest release](https://github.com/coutLiKe/endpoint-triage/releases/latest),
verify the checksum, and run it. There is nothing to install.

```bash
sha256sum -c endpoint-triage.pyz.sha256
```

```bash
python3 endpoint-triage.pyz
```

On Windows (PowerShell), compare `Get-FileHash .\endpoint-triage.pyz` with
the `.sha256` file, then run `py endpoint-triage.pyz`.

### Option 2: from source

```bash
git clone https://github.com/coutLiKe/endpoint-triage.git
cd endpoint-triage
python -m endpoint_triage --help
```

Optionally, install it as a command with `python -m pip install .`, then run
`endpoint-triage`. To build the single file yourself, run
`python tools/build_zipapp.py`; the output is in `dist/`.

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
  - Testing connectivity (gateway, public IP, DNS)
  - Checking for pending OS updates (this can take a minute)

Overall status: WARNING
Findings:
  [WARNING] High disk usage on /System/Volumes/Data
  [INFO] Multiple network interfaces are disconnected
  [INFO] Operating system updates are pending

Text report: triage-reports/triage-MacBook-Pro.local-20261006-004833.txt
JSON report: triage-reports/triage-MacBook-Pro.local-20261006-004833.json
Exit code 1: WARNING (at least one WARNING finding)
```

Report files are named `triage-<hostname>-<UTC timestamp>.txt/.json`, so
repeated runs never overwrite each other.

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
| Windows Server 2025 (GitHub `windows-latest`) | Real scan in CI on every push | All checks complete; ICMP is blocked, reported as INFO |
| Ubuntu 24.04 (GitHub `ubuntu-latest`) | Real scan in CI on every push | All checks complete; ICMP is blocked, reported as INFO |
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

A full sample is in [`sample/sample-report.txt`](sample/sample-report.txt)
and [`sample/sample-report.json`](sample/sample-report.json). It shows a
Windows laptop with a nearly full C: drive, a DNS problem, and a Windows
Update check that failed. The first sections look like this:

```text
Endpoint Triage Report
======================
Generated 2026-10-05 14:03:22 UTC by endpoint-triage 1.2.0
Read-only scan: no system settings were changed.

Summary
-------
  Hostname          : HD-LAPTOP-042.corp.local
  Operating system  : Microsoft Windows 11 Pro
  Overall status    : CRITICAL
  Findings          : 1 critical, 1 warning, 2 info
  Checks            : 10 ok, 2 failed, 0 unavailable, 0 skipped
  Scan duration     : 7.4 s
  Test targets      : ping 1.1.1.1, resolve example.com

Findings
--------
[CRITICAL] Critically low disk space on C:
  C: is at or above 95% used or has less than 5.0 GB free. Very low free
  space can cause failed updates, application crashes and slow performance.
  Evidence:
    - Volume C: is 96.4% used, 17.0 GB free of 476.0 GB

[WARNING] DNS resolution failed
  DNS resolution failed for example.com, but the public IP connectivity test
  succeeded. This may indicate a DNS configuration or DNS server issue.
  Evidence:
    - Ping 192.168.1.1: reply (3.0 ms)
    - Ping 1.1.1.1: reply (15.0 ms)
    - Resolve example.com: failed (DNS lookup for example.com timed out after 5s)

[INFO] System has not been restarted recently
  ...

Connectivity Tests
------------------
  [OK]          Ping default gateway (192.168.1.1): reply, avg 3.0 ms
  [OK]          Ping public IP address (1.1.1.1): reply, avg 15.0 ms
  [FAILED]      Resolve public hostname (example.com): DNS lookup for example.com timed out after 5s

Operating System Updates
------------------------
  Pending OS updates: not completed (failed), see Errors / Unavailable Checks

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
  "schema_version": "1.1",
  "tool": { "name": "endpoint-triage", "version": "1.2.0" },
  "generated_at": "2026-10-05T14:03:22+00:00",
  "duration_seconds": 7.4,
  "summary": {
    "hostname": "HD-LAPTOP-042.corp.local",
    "os": "Microsoft Windows 11 Pro",
    "os_family": "Windows",
    "overall_status": "CRITICAL",
    "finding_counts": { "CRITICAL": 1, "WARNING": 1, "INFO": 2 },
    "check_counts": { "ok": 10, "failed": 2, "unavailable": 0, "skipped": 0 }
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

- `overall_status` is one of `OK`, `WARNING`, `CRITICAL`, `UNKNOWN` and
  always matches the exit code.
- Every finding has a stable `id` (documented in
  [docs/findings.md](docs/findings.md)). Alert on the ID, not the title.
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
prove a root cause. [docs/findings.md](docs/findings.md) lists every finding
ID with its meaning and a tier-1 first step.

All thresholds are constants at the top of
[`endpoint_triage/findings.py`](endpoint_triage/findings.py).

| Condition | Severity | Threshold |
|---|---|---|
| Disk volume usage high | WARNING | ≥ **85 %** used |
| Disk volume critically full | CRITICAL | ≥ **95 %** used, **or** < **5 GB** free on volumes ≥ 20 GB |
| Low available memory | WARNING | < **10 %** of RAM available |
| Long uptime | INFO | ≥ **30 days** since last boot |
| Self-assigned (APIPA) address | WARNING | Any IPv4 address in `169.254.0.0/16` |
| No active network connection | WARNING | No interface is up with a usable IPv4 address |
| Several interfaces disconnected | INFO | ≥ **2** relevant interfaces down |
| No DNS servers configured | WARNING | Empty DNS server list |
| No default gateway | WARNING | No default route |
| Proxy configured | INFO | Any proxy, PAC script or auto-detect setting found |
| Pending OS updates | INFO | ≥ 1 pending update |

Why the 5 GB rule only applies to volumes of 20 GB or more: small partitions
such as a 512 MB EFI partition always have less than 5 GB free, which is
normal.

### How the connectivity tests separate the failure domains

| Gateway ping | Ping target | DNS lookup | Most likely problem area | Severity |
|---|---|---|---|---|
| ✅ | ✅ | ✅ | No connectivity problem detected | none |
| ❌ | ❌ | ❌ | **Local network** (cable, Wi-Fi, VLAN, router) | WARNING |
| ✅ | ❌ | ❌ | **Internet / upstream** (ISP, firewall, required proxy) | WARNING |
| ✅ | ✅ | ❌ | **DNS** (wrong or unreachable DNS server) | WARNING |
| ✅ | ❌ | ✅ | Outbound ping blocked; traffic flows (normal on corporate networks) | INFO |
| ❌ | ✅ | ✅ | Gateway ignores ping; internet works | INFO |
| ❌ | ❌ | ✅ | Ping blocked everywhere; DNS answers prove traffic flows | INFO |

A successful DNS lookup is the deciding evidence: an answer from a DNS server
means packets are leaving the machine, so failed pings alone are not treated
as an outage.

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

## Architecture

```text
endpoint-triage/
├── endpoint_triage/
│   ├── __main__.py          # entry point for `python -m` and the .pyz; Python version check
│   ├── cli.py               # argument parsing and validation, exit codes, console summary
│   ├── scanner.py           # runs the collectors in order, builds the Report
│   ├── runner.py            # the ONLY place commands run (allow-list, trusted paths, timeouts)
│   ├── models.py            # CheckResult, Finding, Report, overall status
│   ├── collectors/
│   │   ├── system.py        # hostname, OS, version, architecture, uptime
│   │   ├── resources.py     # memory, disk volumes
│   │   ├── network.py       # interfaces, default gateway, DNS servers
│   │   ├── connectivity.py  # gateway ping, ping target, DNS resolution
│   │   └── updates.py       # pending OS updates
│   ├── findings.py          # thresholds + rules: CheckResults -> Findings
│   └── reporters.py         # Report -> .txt and .json
├── tests/                   # unittest suite with fixtures of real command output
├── tools/                   # build_zipapp.py, regenerate_samples.py
├── docs/                    # findings.md (finding IDs), INTERVIEW_GUIDE.md
├── sample/                  # example reports (also golden files for the tests)
├── SECURITY.md              # commands, network traffic, data, threat model
├── CHANGELOG.md
└── .github/workflows/       # tests.yml (CI), release.yml (tagged releases)
```

Data flows in one direction:

```text
 runner.py ──► collectors ──► CheckResult[] ──► findings.py ──► Finding[]
 (commands)    (parse output)                                       │
                                     Report (checks + findings) ◄───┘
                                                │
                                         reporters.py ──► .txt / .json
```

Key design decisions:

- **Collectors never format output, and reporters never collect data.**
  Each layer has one job. Adding an HTML report later would not touch a
  single collector.
- **One shared result model.** Every check returns a `CheckResult` with a
  `status` of `ok`, `failed`, `unavailable` or `skipped`. The findings
  engine, both reporters and the JSON schema all depend on this one shape.
- **One place decides the result.** `Report.overall_status()` computes
  OK / WARNING / CRITICAL / UNKNOWN; the text report, the JSON and the exit
  code all use it, so they can never disagree.
- **Parsers are pure functions.** `parse_ifconfig(text)`,
  `parse_df(text, os_name)` and the others take a string and return data,
  so they can be tested directly with saved command output.
- **Dependency injection for testability.** Collectors receive `run`
  (execute a command), `read_file` and `resolver` as parameters. Production
  code uses the real implementations; tests pass fakes. No test depends on
  the machine it runs on.
- **No class hierarchy for collectors.** Each collector is a module with a
  `collect()` function and a few `if os_name == ...` branches. A plugin or
  strategy-pattern design would add indirection without solving a real
  problem at this size.

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
| Connectivity | Ping gateway, ping target, resolve hostname | Narrows a connectivity problem to local network, internet or DNS |
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
- **Interface noise.** Loopback, tunnels, Docker bridges and macOS internal
  interfaces (`awdl`, `utun`, `anpi`, `bridge`, ...) are left out.
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
8. **Reports are always written somewhere.** If the current folder is not
   writable, reports go to the system temp folder and the path is printed.

## Privacy and read-only design

**The tool is read-only.** It:

- ✅ Only *reads* system information
- ✅ Only writes its two report files, and only inside the output directory
- ❌ Never changes settings, network configuration, files or services
- ❌ Never installs, updates or removes software. Update checks only *search*
- ❌ Never sends **collected data** anywhere. There is no upload, telemetry or
  email feature
- ❌ Never needs or requests administrator/root rights

**How this is enforced, not just promised:**

- `runner.py` only runs commands on a short **allow-list**, and only from
  fixed system paths, so a fake `ping.exe` planted in a user-writable folder
  cannot be run in its place. A test verifies that a full scan only runs
  allow-listed commands.
- Commands run **without a shell** (`subprocess.run([...])`, never
  `shell=True`), and user-supplied targets are strictly validated, so no input
  can become an extra command or option.
- `dnf` runs with `-C` (cache only) so it never writes to the package cache;
  `apt list` only reads the local package index.

**Proxy credentials.** Proxy URLs can contain a user name and password
(`http://user:password@proxy:8080`). They are removed, along with any query
string in a PAC URL, before anything is stored; CI checks that a password set
in a proxy variable never appears in the reports.

**Network traffic the tool generates.** The tool *does* make network
requests as part of its tests, but none of them carry collected information:
two ICMP pings (the default gateway and the ping target, default `1.1.1.1`),
one DNS lookup (default `example.com`), and the operating system's own update
search (Apple, Microsoft or your WSUS server, or nothing on Linux, which uses
the local cache). `--skip-updates` removes the update search; `--ping-target`
and `--dns-name` can keep the remaining tests inside your network. The full
list is in [SECURITY.md](SECURITY.md).

**What it deliberately does not collect:** passwords, credentials, tokens,
Wi-Fi keys, browser history, file contents, usernames, installed software
lists, running processes, and serial numbers.

**Personal and sensitive data the reports *do* contain,** because it is
needed for triage: the hostname (also in the report file name), internal IP
addresses, MAC addresses of the listed network interfaces, DNS server
addresses, proxy server names and PAC script URLs, and the names of pending
updates. Treat reports like any other
ticket attachment and follow your organization's data-handling rules before
sharing them outside the company.

## Testing

The test suite uses only `unittest` from the standard library:

```bash
python -W error -m unittest discover -v
```

Run a single test module:

```bash
python -m unittest tests.test_network -v
```

What the tests cover (171 tests, under a second):

- **Parsing for each OS** using fixtures of real command output in
  [`tests/fixtures/`](tests/fixtures) (macOS `ifconfig`/`df`/`vm_stat`/`scutil`,
  Linux `ip -j`/`df`/`meminfo`, Windows PowerShell JSON)
- **Command success, failures (non-zero exit), missing commands, timeouts**
- **Trusted-path resolution**: commands are never looked up through PATH
- **Malformed / unexpected output** (garbage text, broken JSON, old `ip`
  without JSON support, Windows Update error codes, Constrained Language Mode)
- **Findings and every threshold boundary**, including the full
  connectivity classification table
- **Overall status and exit codes**, including a scan where nothing could be
  collected (must be UNKNOWN, never OK)
- **Report generation**: section order, content, de-duplication, debug-only
  details, file names
- **JSON contract**: top-level keys, check and finding shapes, and golden
  files that pin the exact published sample output
- **Docs stay in sync**: every finding ID in the code must be documented in
  `docs/findings.md`
- **CLI**: exit codes, flag validation (including option injection), `--help`,
  `--version`, unwritable output and the temp-folder fallback
- **End-to-end scans** with all commands faked, including a collector crash

How tests stay deterministic:

- `tests/helpers.py` provides `FakeRunner`, which returns canned
  `CommandResult`s and records every command that was "run".
- DNS lookups use an injected fake resolver.
- `subprocess.run` and the trusted-path lookup are patched with
  `unittest.mock` to test the runner's own behavior.

No test touches the real network or depends on the developer's OS.

After an intentional change to the report format, regenerate the golden
samples with `python tools/regenerate_samples.py` and review the diff.

The DNS check was built **test-first**: the specification tests in
[`tests/test_dns.py`](tests/test_dns.py) were committed (failing) before
the implementation. The git history shows that red → green sequence.

## Continuous integration and releases

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every
push and pull request:

- A **matrix** of `ubuntu-latest`, `macos-latest`, `windows-latest` ×
  Python **3.11** and **3.13** (6 jobs). `fail-fast: false` keeps one OS
  failure from hiding results on the others.
- **No dependency install step**, because there are no dependencies.
- Runs the tests with `-W error`, so a deprecation warning fails the build.
- Installs the package with `pip install .` and runs the `endpoint-triage`
  command, proving the packaging works.
- Builds the `.pyz` and runs a **real smoke scan** with it on each OS
  (Python 3.13 jobs). Exit codes 0–2 are accepted, since a CI runner may
  legitimately have findings; UNKNOWN (3) fails the build. The real reports
  are uploaded as build artifacts.
- A **Windows lockdown job** runs the real scan as a newly created
  non-administrator user, and again with PowerShell Constrained Language Mode
  forced machine-wide (the mode AppLocker/WDAC policies enforce). It first
  proves the language mode is active, then requires core diagnostics to
  succeed and the update check to be reported as blocked by policy. It also
  configures user, WinHTTP and environment proxies and requires all three to
  be detected without leaking credentials.

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs when a
version tag such as `v1.1.0` is pushed. It runs the tests, checks that the tag
matches the package version, builds and verifies `endpoint-triage.pyz` and
its SHA-256 checksum, and publishes a GitHub Release with the notes from
[CHANGELOG.md](CHANGELOG.md).

## Known limitations

- **Requires Python 3.11+ on the endpoint.** The single file removes the
  install step, not the interpreter (see [Installation](#installation)).
- **Pending updates on Linux** reflect the local package index / cache. If
  `apt update` or `dnf makecache` has not run recently, the list may be
  stale (the report says so). Only `apt` and `dnf` are supported; zypper and
  pacman report *unavailable*.
- **Update checks can be slow.** `softwareupdate -l` and Windows Update
  searches contact the vendor and can take a minute or more (timeout: 180 s).
  Use `--skip-updates` when time matters.
- **Proxy detection reports settings, not reachability.** It does not
  connect to the proxy or download and evaluate PAC scripts. On Linux only
  environment variables are checked, not GNOME/KDE desktop proxy settings.
- **Ping may be blocked.** Firewalls often drop ICMP. A failed ping is a
  signal, not proof, which is why findings combine ping with the DNS result.
- **`--ping-target` accepts IPv4 only.** macOS needs a separate `ping6`
  command for IPv6, which would add complexity for little triage value.
- **Endpoint security products** may flag or block Python starting
  PowerShell with an inline script; see [SECURITY.md](SECURITY.md).
- **Under AppLocker/WDAC** (Constrained Language Mode) the Windows Update
  search cannot run; the report says "blocked by policy". All other checks work.
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

## License

MIT. See [LICENSE](LICENSE).
