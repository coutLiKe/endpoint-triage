# Changelog

All notable changes are listed here. Versions follow
[semantic versioning](https://semver.org/); the JSON report has its own
`schema_version` (minor = additive, major = breaking).

## [1.3.0] - 2026-10-06

Correct conclusions on managed networks, from a second design audit.

### Added
- **TCP reachability test** to the `--dns-name` host on port 443, and to the
  configured proxy's host and port. Both only open and close a connection.
  A completed connection is now the deciding evidence for connectivity.
- **VPN tunnel detection** (macOS, Linux, Windows) and whether the default
  route uses a tunnel; new `network.vpn_default_route` INFO finding.
- **Run-context detection:** whether the scan ran as a standard user, root or
  SYSTEM (never the user name), shown in the report, with a
  `system.elevated_context` INFO finding for RMM/Intune runs.
- New connectivity findings: `connectivity.https_blocked`,
  `connectivity.direct_https_blocked_proxy`, `connectivity.proxy_unreachable`,
  `connectivity.dns_failed_proxy_network`.
- Text report: finding IDs with a link to docs/findings.md, local time next to
  UTC, a "Run as" line, unused interfaces on one line, VPN tunnels labeled.
- JSON schema 1.2 (additive): `generated_at_local`, `summary.run_as`,
  `summary.exit_code`, `summary.incomplete_core_checks`, and per-interface
  `tunnel` / gateway `via_tunnel`.
- Release hardening: the release runs the full CI suite first, actions are
  pinned to commit SHAs, the `.pyz` gets a signed build provenance
  attestation, and the build is byte-for-byte reproducible.
- Documented stability policy for exit codes, finding IDs and JSON fields.

### Fixed
- A healthy laptop on a proxy-only network with internal DNS was reported as
  `connectivity.internet_unreachable` (WARNING); connectivity findings now
  take the proxy into account.
- Removed the incorrect claim that a working DNS lookup proves traffic is
  leaving the local network (false with an internal DNS server).
- macOS disk table: the sealed system volume is no longer listed and Total is
  used + free, so the APFS row adds up.

### Removed
- `network.interfaces_disconnected`: it fired on every laptop because of
  unused Thunderbolt, Bluetooth and spare ports. Disconnected interfaces are
  still listed in the report.

### Changed
- README shortened; details moved to docs/reference.md and
  docs/architecture.md.

## [1.2.1] - 2026-10-06

Security fix from a second design audit.

### Security
- Reports were written with default permissions (world-readable on
  macOS/Linux), could overwrite an existing file, and followed symlinks. The
  temp-folder fallback used a fixed shared path (`/tmp/triage-reports`) that
  another local user could create first. Reports are now created `0600` with
  `O_EXCL | O_NOFOLLOW` in a `0700` directory owned by the user, a directory
  owned by someone else is refused, and the fallback is a new private
  `mkdtemp` folder.

### Fixed
- Two runs in the same second no longer overwrite each other (`-1` suffix).
- README claims about overwriting and exit codes corrected; SECURITY.md
  documents report permissions and handling.

## [1.2.0] - 2026-10-06

### Added
- Read-only **proxy detection** (`network.proxy` check): proxy environment
  variables on every OS, the macOS system proxy, and on Windows both the
  per-user (WinINET) and machine-wide (WinHTTP) proxy, including PAC scripts
  and auto-detect. Credentials and query strings in proxy URLs are removed.
- `network.proxy_configured` INFO finding, documented in docs/findings.md.
- The Windows CI lockdown job configures all three proxy sources on a real
  runner and checks they are detected, that no credentials leak, and that
  detection works under Constrained Language Mode.

## [1.1.1] - 2026-10-06

### Fixed
- Under PowerShell Constrained Language Mode (enforced by AppLocker/WDAC on
  managed Windows fleets) the OS and network queries failed because they
  used `[pscustomobject]`, so the scan reported UNKNOWN. They now use plain
  hashtables and run normally; only the Windows Update search is reported as
  "blocked by policy".

### Added
- A Windows CI job that runs the real scan as a standard (non-admin) user and
  under Constrained Language Mode, after verifying the mode is active. This
  job found the bug above.

## [1.1.0] - 2026-10-06

Hardening after a full design audit, focused on correctness and on what an
enterprise IT team needs before trusting the tool.

### Fixed
- A scan that could not collect core diagnostics (OS, memory, disks or
  network interfaces) reported **OK** and exited 0. It now reports
  **UNKNOWN** and exits 3, following the Nagios convention.
- A healthy laptop behind a corporate firewall that blocks outbound ping was
  reported as WARNING. A failed ping with working DNS is now INFO.
- Allow-listed commands were found through PATH (and, on Windows, the
  application folder), so a planted binary could run instead of the real one.
  Commands now run only from fixed system paths.
- PowerShell queries no longer fail under Constrained Language Mode because
  of the output-encoding line.

### Added
- `--ping-target` and `--dns-name` to test internal targets, with strict
  validation that also blocks option injection.
- `--skip-updates` to skip the slow update check and its traffic to update
  servers.
- A stable `id` for every finding, and [docs/findings.md](docs/findings.md)
  with the meaning and a tier-1 first step for each ID.
- Plain-English hints for common Windows Update error codes, and
  "blocked by policy" detection for Constrained Language Mode.
- Single-file `endpoint-triage.pyz` build with a SHA-256 checksum, and
  tag-triggered GitHub Releases.
- Reports fall back to the system temp folder when the current folder is not
  writable.
- The console summary ends with the exit code and its meaning.
- A clear error on Python versions older than 3.11.
- [SECURITY.md](SECURITY.md): every command and argument, network
  destinations, data inventory, threat model and worst-case runtime.
- Golden-file tests that pin the published sample reports; CI runs tests
  with `-W error`, checks `pip install`, and smoke-tests the `.pyz` build.

### Changed
- JSON `schema_version` is now `1.1`: adds `scan_options` and finding `id`;
  `overall_status` can be `UNKNOWN`.
- A check that could not run is listed once, under Errors / Unavailable
  Checks, instead of also appearing as an INFO finding.
- The README states exactly which network requests the tool makes and which
  personal data the reports contain.

## [1.0.0] - 2026-10-05

First release: cross-platform, read-only endpoint triage with system,
resource, network, connectivity and update checks, a findings engine, text
and JSON reports, Nagios-style exit codes, and CI on macOS, Windows and Linux.
