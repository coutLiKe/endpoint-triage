# Architecture, testing and releases

How endpoint-triage is built, tested and released. Start with the
[README](../README.md) for the overview.

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
│   │   ├── network.py       # interfaces, default gateway, DNS servers, VPN tunnels
│   │   ├── proxy.py         # proxy settings (env, macOS, Windows), credential redaction
│   │   ├── connectivity.py  # pings, DNS resolution, TCP to port 443 and to the proxy
│   │   └── updates.py       # pending OS updates
│   ├── findings.py          # thresholds + rules: CheckResults -> Findings
│   └── reporters.py         # Report -> .txt and .json
├── tests/                   # unittest suite with fixtures of real command output
├── tools/                   # build_zipapp.py (reproducible .pyz), regenerate_samples.py
├── docs/                    # reference.md, findings.md, architecture.md, INTERVIEW_GUIDE.md
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

## Testing

The test suite uses only `unittest` from the standard library:

```bash
python -W error -m unittest discover -v
```

Run a single test module:

```bash
python -m unittest tests.test_network -v
```

What the tests cover (202 tests, under a second):

- **Parsing for each OS** using fixtures of real command output in
  [`tests/fixtures/`](../tests/fixtures) (macOS `ifconfig`/`df`/`vm_stat`/`scutil`,
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
- **Safe report writing**: private permissions, no overwrite, planted
  symlinks are not followed, directories owned by others are refused
- **Connectivity logic with TCP and proxy evidence**, including the
  proxy-only, split-DNS network that must not raise a WARNING
- **Reproducible build**: the `.pyz` is built twice and compared byte for
  byte, then run
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
[`tests/test_dns.py`](../tests/test_dns.py) were committed (failing) before
the implementation. The git history shows that red → green sequence.

## Continuous integration and releases

[`.github/workflows/tests.yml`](../.github/workflows/tests.yml) runs on every
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

[`.github/workflows/release.yml`](../.github/workflows/release.yml) runs when a
version tag such as `v1.3.0` is pushed:

1. It runs the **entire test workflow** (all OSes, both Python versions and
   the Windows lockdown job) and stops if anything fails.
2. It checks that the tag matches the package version.
3. It builds `endpoint-triage.pyz` **reproducibly** (sorted entries, fixed
   timestamps and permissions), so anyone can rebuild a tag and get the same
   SHA-256.
4. It creates a signed **build provenance attestation** with
   `actions/attest-build-provenance`, recording that this exact file was
   built by this workflow from this commit.
5. It publishes a GitHub Release with the file, its checksum and the notes
   from [CHANGELOG.md](../CHANGELOG.md).

Every third-party action in both workflows is pinned to a full commit SHA
(with the version in a comment), so a moved or compromised tag cannot change
the code that runs with release permissions.

### Verifying a release

```bash
sha256sum -c endpoint-triage.pyz.sha256
```

```bash
gh attestation verify endpoint-triage.pyz --repo coutLiKe/endpoint-triage
```

To check reproducibility, check out the release tag, run
`python tools/build_zipapp.py`, and compare `dist/endpoint-triage.pyz.sha256`
with the published checksum.

