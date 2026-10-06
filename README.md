# Endpoint Triage

A cross-platform, **read-only** command-line tool that a help desk technician
runs with one command to collect common endpoint diagnostics and produce a
report that can be attached to a support ticket.

```bash
python -m endpoint_triage
```

It answers one question:

> *What basic information and connectivity problems can I identify on this
> computer without making any changes to the system?*

- Python 3.10+, **standard library only**: nothing to `pip install`
- Works on **macOS, Windows and Linux**, without admin/root rights
- Produces a ticket-ready **`.txt` report** and a structured **`.json` report**
- Highlights **findings** (CRITICAL / WARNING / INFO) at the top of the report
- Never changes the system, never sends data anywhere

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
14. [Continuous integration](#continuous-integration)
15. [Known limitations](#known-limitations)

---

## Why this tool exists

The first few minutes of most endpoint tickets are spent collecting the same
facts: *What OS is this? When was it last rebooted? Is the disk full? Does it
have an IP address? Can it reach the gateway, the internet, DNS? Are updates
pending?* Technicians collect these by hand with a dozen different commands,
and the commands differ on every OS.

Endpoint Triage runs those checks consistently, in about 10–30 seconds,
and writes the answers into a report that can be attached to the ticket. That
report gives a Tier 2 engineer the same baseline facts every time and avoids
a "can you run this command for me?" round trip.

It is intentionally **not** a monitoring platform: no agent, no server, no
database, no remediation.

## Installation

Requirements: **Python 3.10 or newer**. No third-party packages.

```bash
git clone https://github.com/coutLiKe/endpoint-triage.git
cd endpoint-triage
python -m endpoint_triage --help
```

Optionally, install it as a command:

```bash
python -m pip install .
endpoint-triage --help
```

On Windows, use `py` instead of `python` if `python` is not on your PATH.

## Usage

```bash
python -m endpoint_triage                    # scan and write reports to ./triage-reports/
python -m endpoint_triage --output C:\Temp   # choose the output directory
python -m endpoint_triage --debug            # log every command to stderr and add detail to the reports
python -m endpoint_triage --version
python -m endpoint_triage --help
```

| Option | Purpose |
|---|---|
| `-o, --output DIR` | Directory for the reports (created if missing). Default: `./triage-reports` |
| `--debug` | Logs each command, its exit code and duration to stderr, and adds raw error detail (stderr, tracebacks) to both reports |
| `--version` | Print the version and exit |
| `-h, --help` | Show help and exit |

The CLI is deliberately small. Progress messages go to **stderr** and the
short summary goes to **stdout**, so output can be redirected cleanly:

```text
$ python -m endpoint_triage
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
```

Report files are named `triage-<hostname>-<UTC timestamp>.txt/.json`, so
repeated runs never overwrite each other.

## Supported operating systems

| OS | Tested in CI | Notes |
|---|---|---|
| macOS 13+ (Intel and Apple Silicon) | `macos-latest` | Built-in tools only |
| Windows 10 / 11, Windows Server 2016+ | `windows-latest` | Uses Windows PowerShell 5.1 (built in) |
| Linux with iproute2 4.14+ (Ubuntu 18.04+, Debian 10+, RHEL/Rocky 8+, Fedora) | `ubuntu-latest` | Update detection supports `apt` and `dnf` |

On any other OS (for example FreeBSD) the tool still runs: the unsupported
checks are reported as *unavailable* instead of crashing.

## Example output

A full sample is in [`sample/sample-report.txt`](sample/sample-report.txt)
and [`sample/sample-report.json`](sample/sample-report.json). It shows a
Windows laptop with a nearly full C: drive, a DNS problem, and a Windows
Update check that failed. The first sections look like this:

```text
Endpoint Triage Report
======================
Generated 2026-10-05 14:03:22 UTC by endpoint-triage 1.0.0
Read-only scan: no system settings were changed.

Summary
-------
  Hostname          : HD-LAPTOP-042.corp.local
  Operating system  : Microsoft Windows 11 Pro
  Overall status    : CRITICAL
  Findings          : 1 critical, 1 warning, 2 info
  Checks            : 9 ok, 2 failed, 0 unavailable, 0 skipped
  Scan duration     : 7.4 s

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

Errors / Unavailable Checks
---------------------------
  [FAILED] connectivity.dns_resolution: DNS lookup for example.com timed out after 5s
  [FAILED] updates.os: powershell exited with code 1: Exception from HRESULT: 0x8024402C
```

The report sections are: **Summary, Findings, System Information, Resource
Usage, Network Configuration, Connectivity Tests, Operating System Updates,
Errors / Unavailable Checks**.

### JSON structure

The JSON report contains the same data in a versioned, documented shape:

```json
{
  "schema_version": "1.0",
  "tool": { "name": "endpoint-triage", "version": "1.0.0" },
  "generated_at": "2026-10-05T14:03:22+00:00",
  "duration_seconds": 7.4,
  "summary": {
    "hostname": "HD-LAPTOP-042.corp.local",
    "os": "Microsoft Windows 11 Pro",
    "os_family": "Windows",
    "overall_status": "CRITICAL",
    "finding_counts": { "CRITICAL": 1, "WARNING": 1, "INFO": 2 },
    "check_counts": { "ok": 9, "failed": 2, "unavailable": 0, "skipped": 0 }
  },
  "findings": [
    { "severity": "CRITICAL", "title": "...", "explanation": "...", "evidence": ["..."] }
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

Every check has the same five fields (`id`, `title`, `status`, `data`,
`error`). `status` is always one of `ok`, `failed`, `unavailable`, `skipped`.
Sizes are in bytes and times are ISO 8601 UTC, so other tools never have to
parse human-readable strings like "16.0 GB". `schema_version` lets consumers
detect breaking changes. With `--debug`, each check also has a `debug` field.

## Findings and thresholds

Findings are **troubleshooting signals, not diagnoses**. They point the
technician at likely problems and show the evidence; they never claim to
prove a root cause.

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
| Gateway and public IP both unreachable | WARNING | Likely local network problem |
| Gateway OK, public IP unreachable | WARNING | Likely problem beyond the local network |
| Gateway ignores ping, internet works | INFO | Many routers drop ping, so this is usually harmless |
| Public IP reachable, DNS fails | WARNING | Likely DNS configuration or server problem |
| DNS fails while internet is down | INFO | Expected consequence; retest later |
| Pending OS updates | INFO | ≥ 1 pending update |
| A check could not run | INFO | Any check that is `failed` or `unavailable` |

Why the 5 GB rule only applies to volumes of 20 GB or more: small partitions
such as a 512 MB EFI partition always have less than 5 GB free, which is
normal.

### How the connectivity tests separate the failure domains

| Gateway ping | Public IP ping | DNS lookup | Most likely problem area |
|---|---|---|---|
| ✅ | ✅ | ✅ | No connectivity problem detected |
| ❌ | ❌ | ❌ | **Local network** (cable, Wi-Fi, VLAN, router) |
| ✅ | ❌ | ❌ | **Internet / upstream** (ISP, firewall) |
| ✅ | ✅ | ❌ | **DNS** (wrong or unreachable DNS server) |
| ❌ | ✅ | ✅ | Gateway ignores ping; internet works (INFO only) |

## Exit codes

The exit codes follow the widely used Nagios/monitoring convention, so a
script or RMM tool can act on the result without parsing the report.

| Code | Meaning |
|---|---|
| `0` | Scan completed. No WARNING or CRITICAL findings (INFO findings are allowed) |
| `1` | Scan completed. At least one **WARNING** finding |
| `2` | Scan completed. At least one **CRITICAL** finding |
| `3` | The tool itself failed (e.g. the report could not be written) |
| `64` | Invalid command-line arguments (`EX_USAGE`) |
| `130` | Interrupted with Ctrl+C |

A failed *check* (for example, update status unavailable) does **not** make
the tool exit with 3. It is reported in the report and the scan still
completes. argparse normally exits with 2 for bad arguments, which would
collide with CRITICAL, so the CLI overrides it to use 64.

```bash
python -m endpoint_triage; echo "exit code: $?"          # macOS / Linux
python -m endpoint_triage; echo "exit code: $LASTEXITCODE" # PowerShell
```

## Architecture

```text
endpoint-triage/
├── endpoint_triage/
│   ├── __main__.py          # enables `python -m endpoint_triage`
│   ├── cli.py               # argument parsing, exit codes, console summary
│   ├── scanner.py           # runs the collectors in order, builds the Report
│   ├── runner.py            # the ONLY place commands are executed (allow-list, timeouts)
│   ├── models.py            # CheckResult, Finding, Report, Status, Severity
│   ├── collectors/
│   │   ├── system.py        # hostname, OS, version, architecture, uptime
│   │   ├── resources.py     # memory, disk volumes
│   │   ├── network.py       # interfaces, default gateway, DNS servers
│   │   ├── connectivity.py  # gateway ping, public IP ping, DNS resolution
│   │   └── updates.py       # pending OS updates
│   ├── findings.py          # thresholds + rules: CheckResults -> Findings
│   └── reporters.py         # Report -> .txt and .json
├── tests/                   # unittest suite with fixtures of real command output
├── sample/                  # example reports
└── .github/workflows/       # CI on macOS, Windows, Linux
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
| Connectivity | Ping gateway, ping `1.1.1.1`, resolve `example.com` | Narrows a connectivity problem to local network, internet or DNS |
| Updates | Pending OS updates, restart required | Missing patches are a common cause of bugs and a security risk |

**Gateway / default route:** the router that receives any traffic not
destined for the local subnet. A device with an IP address but no default
gateway can talk to its neighbors but not the internet.

**DNS vs IP connectivity:** pinging `1.1.1.1` tests whether packets can
reach the internet *by IP address*. Resolving `example.com` tests whether
*names* can be turned into addresses. If the IP test passes and the DNS test
fails, the network path works and the problem is name resolution. This is
the case where users say the "internet is down" even though the network is
fine.

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
| Ping | `ping -c 2 -W 2000` (ms) | `ping -c 2 -W 2` (seconds) | `ping -n 2 -w 2000` (ms) |
| Updates | `softwareupdate -l` | `apt list --upgradable` or `dnf -C check-update` | Windows Update Agent COM API (search only) |

Notable differences the code handles:

- **Prefer structured output.** On Windows, PowerShell objects are converted
  with `ConvertTo-Json`, and on Linux `ip -j` emits JSON. Parsing JSON is far
  more reliable than scraping `ipconfig` text, which changes with the
  display language.
- **Locale.** On macOS/Linux commands run with `LC_ALL=C` so output is always
  English with `.` as the decimal separator.
- **Ping flags differ.** `-c` vs `-n` for count; `-W` is milliseconds on macOS
  but seconds on Linux.
- **Windows ping exit codes.** `ping` exits `0` even when a router replies
  "Destination host unreachable", so on Windows a reply must also contain
  `TTL=` to count as success.
- **APFS on macOS.** The root and Data volumes share one container (and free
  space). System-only APFS volumes (`/System/Volumes/VM`, `Preboot`, ...) are
  hidden; `/` and `/System/Volumes/Data` are shown.
- **Linux pseudo filesystems** (`tmpfs`, snap `loop` devices, `overlay`) are
  filtered out of the disk list. Inside a container the tool falls back to `/`.
- **systemd-resolved.** If `/etc/resolv.conf` only lists `127.0.0.53`, the
  report notes that this is a local stub and the real servers are in
  `resolvectl status`.
- **Interface noise.** Loopback, tunnels, Docker bridges and macOS internal
  interfaces (`awdl`, `utun`, `anpi`, `bridge`, ...) are left out.

## Error handling

The guiding rule: **one failed check never stops the scan, and no failure is
silent.**

1. **The runner never raises for ordinary failures.** A missing command
   (`FileNotFoundError`), a timeout (`TimeoutExpired`), a permission error,
   or a non-zero exit code all come back as a `CommandResult` that describes
   what happened.
2. **Collectors translate failures into statuses.**
   - Command not found / unsupported OS → `unavailable`
   - Non-zero exit, timeout, or output that cannot be parsed → `failed`
   - Not applicable (e.g. no gateway to ping) → `skipped`
3. **Partial data is still reported.** If `vm_stat` fails, total memory is
   still reported. If `df` exits 1 because of a stale network mount, the
   other volumes are still shown with a note.
4. **The scanner has a last line of defense.** Each collector runs inside a
   guard; if a bug raises an unexpected exception, it becomes a `failed`
   check (with the traceback saved for `--debug`) and the other sections
   still run.
5. **Users see messages, not stack traces.** Normal mode shows one-line
   errors. `--debug` logs every command to stderr and adds stderr output and
   tracebacks to the reports.
6. **Every non-OK check is listed** in *Errors / Unavailable Checks*, and the
   findings engine adds an INFO finding for each diagnostic it could not
   determine.

## Privacy and read-only design

**The tool is read-only.** It:

- ✅ Only *reads* system information
- ✅ Only writes its two report files, and only inside the output directory
- ❌ Never changes settings, network configuration, files or services
- ❌ Never installs, updates or removes software. Update checks only *search*
- ❌ Never sends collected data anywhere. There is no upload, telemetry or
  email feature
- ❌ Never needs or requests administrator/root rights

**How this is enforced, not just promised:**

- `runner.py` refuses to run any executable that is not on a short
  **allow-list** (`sw_vers`, `sysctl`, `vm_stat`, `ifconfig`, `route`,
  `scutil`, `softwareupdate`, `df`, `ping`, `ip`, `apt`, `dnf`,
  `powershell`). Every listed command is used only in a read-only mode, and
  a test verifies that a full scan only runs allow-listed commands.
- Commands run **without a shell** (`subprocess.run([...])`, never
  `shell=True`), so no input can be interpreted as extra shell commands.
- `dnf` runs with `-C` (cache only) so it never writes to the package cache;
  `apt list` only reads the local package index.

**Network traffic the tool generates:** two ICMP pings (to the gateway and
to `1.1.1.1`), one DNS lookup for `example.com`, and the OS's own update
check (`softwareupdate` / Windows Update / package metadata). None of these
carry any collected information.

**What it deliberately does not collect:** passwords, credentials, tokens,
Wi-Fi keys, browser history, file contents, usernames, installed software
lists, running processes, and serial numbers.

**Personally identifiable / sensitive data that *is* collected,** because
it is needed for triage: the hostname, IP addresses and MAC addresses. Treat
reports like any other ticket attachment.

## Testing

The test suite uses only `unittest` from the standard library:

```bash
python -m unittest discover -v
```

Run a single test module:

```bash
python -m unittest tests.test_network -v
```

What the tests cover (133 tests, under a second):

- **Parsing for each OS** using fixtures of real command output in
  [`tests/fixtures/`](tests/fixtures) (macOS `ifconfig`/`df`/`vm_stat`/`scutil`,
  Linux `ip -j`/`df`/`meminfo`, Windows PowerShell JSON)
- **Command success, failures (non-zero exit), missing commands, timeouts**
- **Malformed / unexpected output** (garbage text, broken JSON, old `ip`
  without JSON support)
- **Findings and every threshold boundary**, including the full
  connectivity classification table
- **Report generation**: section order, content, debug-only details, file names
- **JSON structure**: top-level keys, check and finding shapes, status values
- **CLI**: exit codes, `--help`, `--version`, bad arguments, unwritable output
- **End-to-end scans** with all commands faked, including a collector crash

How tests stay deterministic:

- `tests/helpers.py` provides `FakeRunner`, which returns canned
  `CommandResult`s and records every command that was "run".
- DNS lookups use an injected fake resolver.
- `subprocess.run` is patched with `unittest.mock` to test the runner's own
  error handling.

No test touches the real network or depends on the developer's OS.

The DNS check was built **test-first**: the specification tests in
[`tests/test_dns.py`](tests/test_dns.py) were committed (failing) before
the implementation. The git history shows that red → green sequence.

## Continuous integration

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every
push and pull request:

- A **matrix** of `ubuntu-latest`, `macos-latest`, `windows-latest` ×
  Python **3.10** and **3.13** (6 jobs). `fail-fast: false` keeps one OS
  failure from hiding results on the others.
- **No install step** beyond setting up Python, because there are no
  dependencies.
- Runs `python -m unittest discover -v`; any failing test fails the job.
- Runs a **real smoke scan** on each OS (Python 3.13 jobs). Exit codes 0–2
  are accepted, since a CI runner may legitimately have findings; 3 or
  higher fails the build. The real reports are uploaded as build artifacts,
  which is a convenient way to see actual Windows and Linux output.

## Known limitations

- **Pending updates on Linux** reflect the local package index / cache. If
  `apt update` or `dnf makecache` has not run recently, the list may be
  stale (the report says so). Only `apt` and `dnf` are supported; zypper and
  pacman report *unavailable*.
- **Update checks can be slow.** `softwareupdate -l` and Windows Update
  searches contact the vendor and can take up to a minute or more (timeout:
  180 s). They may fail offline or behind restrictive proxies, which is
  reported as *undetermined*.
- **Ping may be blocked.** Firewalls often drop ICMP. A failed ping is a
  signal, not proof, which is why findings combine ping with the DNS result.
- **The connectivity tests use fixed targets** (`1.1.1.1`, `example.com`).
  Networks that block these specific destinations will show failures.
- **macOS available memory** is an approximation (free + inactive +
  speculative pages); Activity Monitor uses a more complex formula.
- **Mounted disk images** (DMG/ISO) on macOS/Linux may appear as nearly full
  volumes.
- **Windows latency** is parsed only from English `ping` output; reachability
  works in every language because it relies on `TTL=`.
- **Old Linux distributions** whose `ip` lacks JSON output (iproute2 < 4.14)
  report network interfaces and gateway as *failed*.
- **Locked-down Windows** environments that block PowerShell will report the
  Windows checks as failed or unavailable.
- The tool runs once and reports a snapshot; intermittent problems may not
  appear in a single run.

## License

MIT. See [LICENSE](LICENSE).
