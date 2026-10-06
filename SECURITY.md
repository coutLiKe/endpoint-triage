# Security

This page is for anyone approving endpoint-triage for use on managed
endpoints. It lists everything the tool executes, reads, writes and sends,
the data that ends up in its reports, and the threats it is designed against.

## Summary

- **Read-only.** It never changes settings, files, services or software.
- **No privileges.** It runs as a standard user and never asks for elevation
  (verified in CI with a newly created non-administrator Windows account).
- **No data leaves the machine.** Reports are written locally. The only
  network traffic is the connectivity tests and the OS's own update search,
  and none of it contains collected data.
- **No dependencies.** Python standard library only, so there is no
  third-party supply chain.
- **Fixed commands from fixed paths.** A short allow-list, run without a
  shell, from trusted system locations only.

## Commands executed

Commands are run by `endpoint_triage/runner.py`, the only module that starts
processes. A command that is not on the allow-list raises an error, and each
allowed command is resolved to a trusted absolute path. PATH is never
searched. All arguments are fixed in the code except the two validated test
targets (`<target>`, see below).

### macOS

| Executable | Arguments | Purpose |
|---|---|---|
| `/usr/bin/sw_vers` | none | OS name, version, build |
| `/usr/sbin/sysctl` | `-n kern.boottime` | Last boot time |
| `/usr/sbin/sysctl` | `-n hw.memsize` | Total memory |
| `/usr/bin/vm_stat` | none | Available memory |
| `/bin/df` | `-P -k` | Disk volumes |
| `/sbin/ifconfig` | none | Network interfaces |
| `/sbin/route` | `-n get default` | Default gateway |
| `/usr/sbin/scutil` | `--dns` | DNS servers |
| `/usr/sbin/scutil` | `--proxy` | System proxy settings |
| `/sbin/ping` | `-c 2 -W 2000 <target>` | Gateway and ping-target tests |
| `/usr/sbin/softwareupdate` | `-l` | List pending updates (list only, never installs) |

### Linux

| Executable (first that exists) | Arguments | Purpose |
|---|---|---|
| `/bin/df`, `/usr/bin/df` | `-P -k` | Disk volumes |
| `/usr/sbin/ip`, `/sbin/ip`, `/usr/bin/ip`, `/bin/ip` | `-j addr show` | Network interfaces |
| same as above | `-j route show default` | Default gateway |
| `/sbin/ping`, `/bin/ping`, `/usr/bin/ping`, `/usr/sbin/ping` | `-c 2 -W 2 <target>` | Gateway and ping-target tests |
| `/usr/bin/apt` | `list --upgradable` | Pending updates from the local package index |
| `/usr/bin/dnf` | `-C -q check-update` | Pending updates from cached metadata only (`-C`: no network, no cache writes) |

Files read: `/etc/os-release`, `/proc/uptime`, `/proc/meminfo`, `/etc/resolv.conf`.

### Windows

| Executable | Arguments | Purpose |
|---|---|---|
| `%SystemRoot%\System32\PING.EXE` | `-n 2 -w 2000 <target>` | Gateway and ping-target tests |
| `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` | `-NoProfile -NonInteractive -Command <script>` | Five read-only queries, below |

The PowerShell scripts are fixed strings in the source code; **no user input
is ever inserted into a script.** They use only read-only cmdlets:

| Script | Cmdlets / APIs |
|---|---|
| OS and boot time | `Get-CimInstance Win32_OperatingSystem` |
| Memory | `Get-CimInstance Win32_OperatingSystem` |
| Disks | `Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'` |
| Network | `Get-NetAdapter`, `Get-NetIPAddress`, `Get-NetRoute`, `Get-DnsClientServerAddress` |
| Proxy | `Get-ItemProperty` on `HKCU:\...\Internet Settings` (user proxy) and `HKLM:\...\Internet Settings\Connections` (WinHTTP proxy); registry reads only |
| Updates | `New-Object -ComObject Microsoft.Update.Session`, then `CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0')`: search only, no download or install |

Each script ends with `ConvertTo-Json`, so Python parses structured data.

### In-process (no command)

Hostname (`socket.gethostname`), architecture and kernel version
(`platform`), the DNS test (`socket.getaddrinfo`, the OS resolver), and the
proxy environment variables `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` and
`NO_PROXY` (and their lowercase forms).

## Files written

Only the two report files, `triage-<hostname>-<timestamp>.txt` and `.json`,
in the output directory: `--output DIR`, otherwise `./triage-reports`, or a
new private folder (`tempfile.mkdtemp`, e.g. `/tmp/triage-reports-k3j9x2`) if
the current folder is not writable. The hostname in the file name keeps only
letters, digits, `.` and `-`; any other character becomes `_`, so it cannot
contain path separators.

Because reports contain identifying data, they are written defensively
(`reporters.write_reports`):

- A new output directory is created with mode `0700` (owner only), and a
  directory owned by another user, or a symlinked directory, is refused.
- Files are created with mode `0600` using `O_CREAT | O_EXCL | O_NOFOLLOW`:
  an existing file is never overwritten and a planted symlink is never
  followed, so another local user cannot redirect the write or read the result.
- If a name is taken (two runs in the same second), `-1`, `-2`, ... is added.

On Windows, files inherit the permissions of the output folder (by default
the user's profile or the current folder).

## Handling reports

- Treat reports like any ticket attachment containing internal network
  details. Attach them to the ticket, then delete the local copies; the tool
  never deletes or rotates old reports itself.
- Follow your organization's retention policy for ticket attachments.
- Remove the hostname, IP and MAC addresses by hand before sharing a report
  outside your organization.

## Network traffic

| Destination | Protocol | When | Contains collected data? |
|---|---|---|---|
| Default gateway | ICMP echo ×2 | Every scan with a gateway | No |
| Ping target (default `1.1.1.1`, Cloudflare) | ICMP echo ×2 | Every scan | No |
| Configured DNS servers, asking for the DNS test name (default `example.com`) | DNS | Every scan | No |
| Apple software update service | HTTPS (by `softwareupdate`) | macOS, unless `--skip-updates` | No (OS-managed request) |
| Microsoft Update or your WSUS server | HTTPS (by Windows Update Agent) | Windows, unless `--skip-updates` | No (OS-managed request) |
| None | — | Linux update check (local cache only) | — |

To keep all tests inside your network, run with
`--skip-updates --ping-target <internal IP> --dns-name <internal hostname>`.

## Data in the reports

Collected because it is needed for triage:

- Hostname (also in the report file name)
- OS name, version, build, architecture, kernel version, uptime, last boot time
- Memory and disk sizes and usage, and disk mount points or drive letters
- Network interface names, status, internal IPv4/IPv6 addresses and **MAC addresses**
- Default gateway and DNS server addresses
- Proxy server names, PAC script URLs and bypass lists (**credentials and URL
  query strings are removed** before storage; CI verifies a password in a
  proxy variable never reaches the report)
- Test targets used and their results
- Names of pending updates

Never collected: usernames, passwords, credentials, tokens, Wi-Fi keys,
browser history, file contents, installed software lists, running processes,
serial numbers.

Treat reports like any other ticket attachment. There is deliberately no
built-in redaction option: a short hash of the hostname can be reversed by
guessing likely names, so redaction would give a false sense of anonymity.
Remove identifiers by hand before sharing a report outside your organization.

## Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| A fake `ping.exe` / `powershell.exe` planted in a user-writable folder on PATH, or next to the tool (Windows searches the application folder first), runs instead of the real binary | Commands run only from fixed system paths; PATH is never searched; a missing binary is reported as unavailable, never searched for elsewhere | On Windows the path is built from `%SystemRoot%`. Changing that variable requires control of the user's environment, which already means control of the user's processes |
| Command or option injection through `--ping-target` / `--dns-name` | No shell (`subprocess.run` with an argument list); targets must be a valid IPv4 address or RFC 1123 hostname, so values like `-f` are rejected; user input never reaches PowerShell | None known |
| The tool changes the system | Allow-list of read-only commands with fixed arguments; a test asserts a full scan only runs allow-listed commands; `dnf -C` avoids cache writes | Commands themselves (for example `softwareupdate -l`) may update their own OS-managed caches, as they do whenever a user runs them |
| Collected data leaks off the machine | No upload, email or telemetry code exists; reports are local files | Reports contain hostname, IPs and MACs; handle them as sensitive |
| A scan that silently fails is read as "healthy" | Missing core diagnostics make the result UNKNOWN (exit 3); every incomplete check is listed in the report | None known |
| A hung command stalls the technician | Every command has a timeout; the DNS lookup runs in a daemon thread with a 5 s limit; `--skip-updates` removes the slowest check | See worst-case runtime below |
| A tampered download | Releases are built by CI from a tag and published with a SHA-256 checksum | The checksum is published next to the file, so it detects corruption, not a malicious release. Code signing is out of scope (no paid certificates) |

### Endpoint security and application control

- **EDR view.** Python starting `powershell.exe -NoProfile -NonInteractive
  -Command` with an inline script resembles techniques attackers use, so EDR
  products may alert on it. The scripts are static and read-only (listed
  above). If needed, allow-list the release by its SHA-256 hash or path.
- **AppLocker / WDAC Constrained Language Mode** blocks COM objects, so the
  Windows Update search cannot run. The tool recognizes this and reports
  the check as *unavailable: blocked by policy*. All other queries are
  written for Constrained Language Mode (plain hashtables, no
  `[pscustomobject]`, no method calls on non-core types, and the one
  encoding setting it forbids is wrapped in `try/catch`). **Validated in CI**
  on every push: a Windows job forces Constrained Language Mode machine-wide,
  verifies it is active, and requires every other check to succeed. A real
  domain-joined machine with a production policy has not been tested.
- **Python itself** may be restricted by application control. In that case
  the tool cannot run at all; deploy Python through your normal software
  distribution so it is allowed.

### Worst-case runtime

Typical scans take 10–30 seconds. Every external operation has a timeout,
so the worst case is bounded:

| OS | Worst case | With `--skip-updates` |
|---|---|---|
| macOS (8 commands × 15 s, 2 pings × 15 s, DNS 5 s, updates 180 s) | about 5.5 minutes | about 2.5 minutes |
| Linux (3 commands × 15 s, 2 pings × 15 s, DNS 5 s, updates 180 s) | about 4.5 minutes | about 1.5 minutes |
| Windows (4 PowerShell queries × 30 s, 2 pings × 15 s, DNS 5 s, updates 180 s) | about 5.5 minutes | about 2.5 minutes |

## Reporting a vulnerability

Please open an issue on the
[GitHub repository](https://github.com/coutLiKe/endpoint-triage/issues)
without exploit details, and ask for a private contact channel; details can
then be shared privately.
