# Endpoint Triage

[![Tests](https://github.com/coutLiKe/endpoint-triage/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/coutLiKe/endpoint-triage/actions/workflows/tests.yml)

A cross-platform, **read-only** command-line tool that a help desk technician
runs with one command to collect common endpoint diagnostics and produce a
report to attach to a support ticket.

> *What basic information and connectivity problems can I identify on this
> computer without making any changes to the system?*

**Who it's for:** IT teams supporting macOS, Linux and Windows machines.
**Requirement: Python 3.11+ on the endpoint.** Python ships with most Linux
distributions; on Windows and managed Macs it must be deployed first (for
example with winget, Intune, Jamf or your RMM). Where that isn't possible,
this tool is not the right fit.

- Standard library only: nothing to `pip install`; ships as **one file**
- Runs without admin/root rights and never changes the system
- Never sends collected data anywhere
- Writes a ticket-ready **`.txt` report** and a versioned **`.json` report**
- Puts **findings** (CRITICAL / WARNING / INFO) with stable IDs at the top
- Returns **Nagios-style exit codes** for scripts and RMM monitors

## Quick start

Download `endpoint-triage.pyz` and `endpoint-triage.pyz.sha256` from the
[latest release](https://github.com/coutLiKe/endpoint-triage/releases/latest),
verify them, and run:

```bash
sha256sum -c endpoint-triage.pyz.sha256
```

```bash
gh attestation verify endpoint-triage.pyz --repo coutLiKe/endpoint-triage
```

```bash
python3 endpoint-triage.pyz
```

On Windows, use `py endpoint-triage.pyz` and compare
`Get-FileHash .\endpoint-triage.pyz` with the `.sha256` file. The attestation
check proves the file was built by this repository's release workflow.

From source: `git clone` the repository and run `python -m endpoint_triage`.

```text
$ python3 endpoint-triage.pyz
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

Text report: triage-reports/triage-MacBook-Pro.local-20261006-031253.txt
JSON report: triage-reports/triage-MacBook-Pro.local-20261006-031253.json
Exit code 1: WARNING (at least one WARNING finding)
```

See a full example in [sample/sample-report.txt](sample/sample-report.txt)
and [sample/sample-report.json](sample/sample-report.json).

## Options

| Option | Purpose |
|---|---|
| `-o, --output DIR` | Where to write reports. Default `./triage-reports`, or a new private temp folder if that isn't writable |
| `--ping-target IP` | IPv4 address for the internet ping test (default `1.1.1.1`) |
| `--dns-name HOST` | Hostname for the DNS and TCP port 443 tests (default `example.com`); use an internal name on corporate networks |
| `--skip-updates` | Skip the pending-update check (can take minutes and contacts update servers) |
| `--debug` | Log every command to stderr and add raw error detail to the reports |
| `--version`, `-h` | Version and help |

## What it checks

| Area | Checks |
|---|---|
| System | Hostname, OS and build, architecture, uptime, last reboot, whether it ran as user/root/SYSTEM |
| Resources | Memory, every disk volume (size, used, free, % used) |
| Network | Interfaces (status, IPv4/IPv6, MAC), default gateway, DNS servers, proxy settings, VPN tunnels |
| Connectivity | Ping the gateway and a public IP, resolve a hostname, open a TCP connection to it on port 443 and to the proxy |
| Updates | Pending OS updates (`softwareupdate`, Windows Update, `apt`/`dnf`) |

The connectivity tests are combined to point at the likely problem area:
local network, DNS, an outbound firewall, the proxy, or the internet path. A
completed TCP connection is the deciding evidence, so a network that only
blocks ping is reported as healthy. Every finding and its first
troubleshooting step is listed in [docs/findings.md](docs/findings.md).

## Exit codes

| Code | Status | Meaning |
|---|---|---|
| `0` | OK | No WARNING or CRITICAL findings |
| `1` | WARNING | At least one WARNING finding |
| `2` | CRITICAL | At least one CRITICAL finding |
| `3` | UNKNOWN | Core diagnostics (OS, memory, disks, interfaces) could not be collected, or the tool failed |
| `64` | — | Invalid command-line arguments |

Precedence is CRITICAL > UNKNOWN > WARNING > OK: a scan that could not read
the disks never claims to be healthy.

## Safety and privacy

- **Read-only:** only reads system information; update checks only search.
- **Fixed commands from fixed paths:** a short allow-list, run without a
  shell, from trusted system locations only; PATH is never searched.
- **Validated input:** `--ping-target` and `--dns-name` are strictly
  validated, so no value can become an extra command or option.
- **No data leaves the machine.** The network tests (pings, one DNS lookup,
  TCP handshakes with no data, the OS update search) carry no collected data.
- **Private reports:** created readable only by the user, never overwriting
  an existing file or following a symlink. Proxy credentials are stripped.
- **What reports contain:** hostname, internal IP and MAC addresses, DNS and
  proxy servers. Handle them like any ticket attachment.

[SECURITY.md](SECURITY.md) lists every command and argument, every network
destination, the data collected, report handling, endpoint-security notes
and the threat model.

## Tested on

Every push runs real scans in CI on macOS, Windows Server 2025 and Ubuntu
24.04, plus a Windows job that scans as a **standard (non-admin) user**,
under **PowerShell Constrained Language Mode** (what AppLocker/WDAC enforce),
and with **user, WinHTTP and environment proxies** configured. Not yet
validated: a real domain-joined laptop with a production AppLocker/WDAC
policy, and traffic through a real corporate proxy. Details in
[docs/reference.md](docs/reference.md#tested-on).

## Documentation

| Document | Contents |
|---|---|
| [docs/reference.md](docs/reference.md) | Usage, report format and JSON schema, findings logic and thresholds, platform differences, error handling, stability policy, limitations |
| [docs/findings.md](docs/findings.md) | Every finding ID with its meaning and a first troubleshooting step |
| [docs/architecture.md](docs/architecture.md) | Code structure, design decisions, testing, CI and the release process |
| [SECURITY.md](SECURITY.md) | Commands, network traffic, data, report handling, threat model |
| [CHANGELOG.md](CHANGELOG.md) | Changes in each release |
| [docs/INTERVIEW_GUIDE.md](docs/INTERVIEW_GUIDE.md), [PLAN.md](PLAN.md) | How the project was built and the reasoning behind it |

## Development

```bash
python -W error -m unittest discover -v
```

```bash
python tools/build_zipapp.py
```

The tests use only the standard library, mock every OS command, and never
touch the network. The build is reproducible: the same source always gives
the same SHA-256.

## License

MIT. See [LICENSE](LICENSE).
