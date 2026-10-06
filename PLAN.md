# Endpoint Triage — Milestone Plan

Goal: a safe, cross-platform, read-only endpoint diagnostic CLI that produces a
help desk report (`.txt` + `.json`) with one command:

```bash
python -m endpoint_triage
```

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Python version | 3.11+ | Oldest version still receiving security fixes (3.10 reaches end of life in October 2026); allows `str \| None` hints. |
| Dependencies | Standard library only | Runs on any machine with Python; nothing to install. |
| Test framework | `unittest` (stdlib) | No `pip install` needed in CI or on a technician's laptop. |
| Windows data source | PowerShell CIM / `Get-Net*` cmdlets piped to `ConvertTo-Json` | Structured output instead of screen-scraping locale-dependent `ipconfig` text. |
| Linux network source | `ip -j` (iproute2 JSON output) + `/etc/resolv.conf` | Structured, present on every modern distro. |
| macOS sources | `sw_vers`, `sysctl`, `vm_stat`, `df`, `ifconfig`, `route`, `scutil`, `softwareupdate` | Built into every Mac. |
| Disk thresholds | WARNING ≥ 85 %, CRITICAL ≥ 95 % or < 5 GB free (volumes ≥ 20 GB) | Common help desk defaults. |
| Connectivity targets | Ping `1.1.1.1`, resolve `example.com` | Neutral, stable, well known. |
| Exit codes | Nagios-style: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN (incomplete scan or tool error), 64 usage error | Widely understood monitoring convention. |
| Safety mechanism | Allow-list of executables, run from fixed system paths, in the command runner | Read-only behavior is enforced in code, not just promised; PATH hijacking is not possible. |
| Distribution | Single-file `zipapp` (`.pyz`) with SHA-256, published by a tag-triggered release | One file to copy or push with an RMM; no install step. |

## Milestones

- [x] **M1 — Foundation:** project layout, `pyproject.toml`, data models
  (`CheckResult`, `Finding`, `Report`), safe command runner with allow-list,
  timeouts and `LC_ALL=C`.
- [x] **M2 — System collector:** hostname, OS/version/build, architecture,
  uptime, last boot (macOS / Linux / Windows parsers + tests).
- [x] **M3 — Resource collector:** memory and disk volumes (+ tests).
- [x] **M4 — Network collector:** interfaces, default gateway, DNS servers (+ tests).
- [x] **M5 — Connectivity:** gateway ping, public IP ping (+ tests).
- [x] **M6 — DNS resolution check:** specification tests first, then
  implementation with a timeout.
- [x] **M7 — OS update collector:** `softwareupdate`, Windows Update Agent,
  `apt` / `dnf` (+ tests).
- [x] **M8 — Findings engine:** thresholds, connectivity classification (+ tests).
- [x] **M9 — Reporters:** text and JSON reports (+ tests).
- [x] **M10 — CLI + scanner:** argument parsing, exit codes, debug mode (+ tests).
- [x] **M11 — CI:** GitHub Actions matrix on macOS, Windows, Linux.
- [x] **M12 — Documentation:** README, sample report.
- [x] **M13 — Hardening after a design audit (v1.1.0):** UNKNOWN exit code
  for incomplete scans, blocked-ping false WARNING, trusted executable
  paths, validated `--ping-target` / `--dns-name`, `--skip-updates`, stable
  finding IDs + `docs/findings.md`, report de-duplication, Windows Update
  error hints, temp-folder fallback, single-file `.pyz` releases, golden-file
  tests, SECURITY.md and CHANGELOG.md.
- [x] **M14 — Lockdown validation (v1.1.1):** CI runs the real scan as a
  standard user and under Constrained Language Mode; fixed the CLM bug it found.
- [x] **M15 — Proxy detection (v1.2.0):** environment, macOS and Windows
  (WinINET + WinHTTP) proxy settings, credential redaction, CI validation.
- [x] **M16 — Secure report writing (v1.2.1):** private files, no overwrite,
  no symlink following, private temp fallback.
- [x] **M17 — Correct conclusions and release trust (v1.3.0):** TCP and proxy
  reachability tests, proxy- and VPN-aware findings, SYSTEM/root detection,
  report readability, JSON 1.2, pinned actions, provenance attestation,
  reproducible build, stability policy, shorter README.

## Out of scope

GUI, web dashboard, cloud backend, database, remote management, automatic
remediation, emailing reports, SIEM integration, authentication,
AI-generated diagnoses, third-party services, package dependencies.
