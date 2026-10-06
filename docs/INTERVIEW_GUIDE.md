# Interview Guide

A study guide for explaining this project in help desk, IT support and
systems engineering interviews. Each milestone section covers what was built,
why it was built that way, the concepts involved, what could go wrong in
production, and questions you should be able to answer.

Tip: open the referenced file next to each section and trace the code
yourself. Being able to say "here's the function that does that" is what
convinces interviewers.

---

## The 30-second pitch

> "I built a cross-platform endpoint triage tool in Python, standard library
> only. A technician runs one command and gets a text report for the ticket
> and a JSON report for automation. It collects OS info, uptime, memory,
> disk, network config, runs gateway/internet/DNS connectivity tests, and
> checks for pending updates. A findings engine flags things like a nearly
> full disk or 'IP works but DNS fails'. It's strictly read-only: commands
> go through an allow-list, run from fixed system paths and never use a
> shell. It ships as a single file with a checksum, it's tested with mocked
> command output for macOS, Windows and Linux, and CI runs real scans on all
> three. After an audit I fixed exit-code bugs where a broken scan could
> report 'OK', and documented a full threat model."

---

## M1: Foundation (`models.py`, `runner.py`)

**What:** shared dataclasses (`CheckResult`, `Finding`, `Report`) and one
function, `run_command`, which is the only place the tool executes commands.

**Why:** if every collector returns the same `CheckResult` shape, the
findings engine and both reporters can treat all checks the same way.
Centralizing `subprocess` gives one place to enforce safety (the allow-list,
timeouts, no shell) and one place to fake in tests.

**Concepts**
- **`subprocess.run`** starts a child process. `capture_output=True`
  collects its **stdout** (normal output) and **stderr** (error/diagnostic
  output) separately. `timeout=` kills it if it hangs.
- **Exit codes:** `0` means success, non-zero means failure *by
  convention* (dnf uses 100 to mean "updates available").
- **Why no `shell=True`:** with a shell, a string like `"ping 8.8.8.8; rm -rf ~"`
  runs two commands (command injection). Passing a list runs exactly one
  program with literal arguments.
- **`LC_ALL=C`** forces English, predictable output, so parsers don't
  break on a French or German system.
- **Enums** (`Status`, `Severity`) prevent typos like `"fialed"` and
  document the allowed values.

**What could go wrong:** a command hangs (handled by the timeout); a command
is missing (`FileNotFoundError` → `unavailable`); output encoding issues
(`errors="replace"`).

**Questions**
1. What's the difference between stdout and stderr? Why capture them separately?
2. Why is `shell=True` dangerous?
3. What's the difference between "the command failed" and "the command couldn't be run"? How does the code tell them apart?
4. What does the allow-list protect against?

---

## M2: System collector (`collectors/system.py`)

**What:** hostname, OS name/version/build, architecture, uptime, last boot.

**Why this design:** each OS exposes the data differently (`sw_vers`,
`/etc/os-release`, CIM). Parsers are pure functions (text in, data out) so
they're trivial to test with saved output.

**Concepts:** platform detection (`platform.system()` returns `Darwin`,
`Linux` or `Windows`); `/proc` is a virtual filesystem where the Linux kernel
exposes live state as text files; Unix epoch timestamps; storing all times
in UTC.

**Production risks:** clock skew makes the uptime calculation slightly
wrong; containers report the host's uptime.

**Questions**
1. Why would a technician care about uptime?
2. Why read `/proc/uptime` instead of running `uptime` and parsing it?
3. Why store times in UTC in the JSON?

---

## M3: Resources (`collectors/resources.py`)

**What:** memory (total/available) and disk volumes.

**Concepts**
- **Available vs free memory:** OSes use "free" RAM as disk cache, so
  "available" (memory that can be given to apps) is the number that matters.
  Linux exposes `MemAvailable`; macOS needs an approximation from `vm_stat`.
- **`df -P`:** the POSIX flag gives a stable, one-line-per-filesystem format
  on both macOS and Linux, so one parser serves both.
- **Pseudo filesystems** (`tmpfs`, `devfs`, snap loop devices) aren't real
  disks and are filtered out.
- **Reserved blocks:** ext4 reserves ~5% for root, so used% is
  `used / (used + available)` (what users can actually use), same as `df`.
- **APFS containers:** macOS volumes share one pool of free space.

**Questions**
1. A user says their computer is slow. What in this report would you look at first?
2. Why might used + free not equal total on Linux?
3. Why does the tool hide `/System/Volumes/VM` on macOS?

---

## M4: Network configuration (`collectors/network.py`)

**What:** interfaces (status, IPv4/IPv6, MAC), default gateway, DNS servers.

**Why:** Windows uses PowerShell `Get-Net*` cmdlets with `ConvertTo-Json`,
and Linux uses `ip -j`. Both produce **structured data**, which is more
reliable than scraping `ipconfig`/`ifconfig` text. macOS has no JSON option,
so `ifconfig` is parsed line by line.

**Concepts**
- **CIDR notation** (`192.168.1.42/24`): the address plus the prefix length.
  `/24` = netmask `255.255.255.0`. macOS shows the netmask in hex
  (`0xffffff00`); counting the 1-bits gives the prefix length.
- **APIPA (169.254.x.x):** the address an OS gives itself when DHCP fails. Seeing it is a strong sign DHCP is broken.
- **Default gateway:** the router for any destination not on the local subnet.
- **Link-local IPv6 (`fe80::`)** always exists on an IPv6-enabled interface;
  the `%en0` suffix is a zone ID, which is stripped.
- **systemd-resolved stub (127.0.0.53):** a local DNS cache that forwards to the real servers.

**Production risks:** interface names vary (`eth0`, `enp3s0`, `en0`,
`Wi-Fi`), so the code never hard-codes them. VPN clients add virtual
adapters. Old Linux `ip` versions have no JSON output.

**Questions**
1. A machine has the address 169.254.10.20. What does that tell you and what would you check next?
2. What is a default gateway? What happens without one?
3. Why parse JSON on Windows instead of `ipconfig /all`?
4. Why is the MAC address useful to a help desk? Why is it also sensitive?

---

## M5 + M6: Connectivity and DNS (`collectors/connectivity.py`)

**What:** ping the gateway, ping `1.1.1.1`, resolve `example.com`.

**Why three tests:** together they locate the failure:
gateway ❌ → local network; gateway ✅ + internet ❌ → upstream/ISP;
internet ✅ + DNS ❌ → DNS. This is the same bottom-up troubleshooting you'd
do by hand (OSI layers: link → network → application).

**Concepts**
- **ICMP echo (ping)**; firewalls often block it, so a failed ping is a *signal*, not proof.
- **Windows ping gotcha:** exit code 0 even for "Destination host
  unreachable", so the code requires `TTL=` in the reply.
- **DNS resolution through the OS resolver** (`socket.getaddrinfo`)
  respects the hosts file, VPN DNS and corporate resolvers, so it matches
  what the user's browser sees.
- **Timeouts with threads:** `getaddrinfo` can't be given a timeout, so it
  runs in a **daemon thread**; the main thread waits up to 5 s with
  `join(timeout)`. A daemon thread doesn't block program exit.
- **Test-driven development:** `tests/test_dns.py` was committed failing
  first (red), then the implementation made it pass (green).

**Questions**
1. A user can ping 1.1.1.1 but can't open any website. What's your hypothesis and how would you confirm it? (`nslookup`, check DNS servers, try another DNS server)
2. Why ping the gateway *and* a public IP?
3. The gateway doesn't answer ping but the internet works. Is that a problem?
4. Why is `example.com` a good test hostname?
5. How did you keep a hung DNS lookup from freezing the tool?
6. What's the difference between a DNS timeout and NXDOMAIN?

---

## M7: OS updates (`collectors/updates.py`)

**What:** pending update detection without installing anything.

**Concepts:** `softwareupdate -l` (list only), the Windows Update Agent COM
API (`Microsoft.Update.Session`; searching does not need admin), `apt list
--upgradable` (reads the local index), `dnf -C check-update` (cache only;
exit code **100** = updates available).

**Why "undetermined" is not an error:** update checks fail for normal
reasons (offline, proxy, permissions, unsupported distro). The tool reports
*why* it couldn't tell and keeps going.

**Questions**
1. Why doesn't the tool run `apt update` first?
2. dnf returned exit code 100. Is that a failure?
3. Why might the Windows update search fail on a corporate laptop? (WSUS/proxy, error codes like `0x8024402C`)

---

## M8: Findings engine (`findings.py`)

**What:** rules that turn raw checks into CRITICAL / WARNING / INFO findings
with an explanation and evidence.

**Why:** a raw dump forces the technician to interpret everything. Findings
put the likely problems at the top, with the numbers that justify them.
Thresholds are named constants so they're easy to find, document and change.

**Design points:** findings never claim a root cause ("this *may* indicate");
the 5 GB rule skips small volumes to avoid false CRITICALs on EFI partitions;
INFO findings never change the exit code.

**Questions**
1. How did you choose the thresholds? How would you change them for a fleet of servers vs laptops?
2. What's a false positive here and how did you avoid one?
3. Why separate "collecting data" from "deciding what it means"?

---

## M9: Reporters (`reporters.py`)

**What:** a ticket-friendly text report and a versioned JSON report.

**Concepts:** **JSON serialization** (dataclasses → dicts via `to_dict()`;
enums → their string values); **schema versioning** so consumers can detect
changes; machine-friendly units (bytes, ISO 8601) in JSON vs human units
(GB, "3 days") in text.

**Questions**
1. Why produce both text and JSON?
2. Why are sizes in bytes in JSON but GB in the text report?
3. What would you need to change to add an HTML report? (Only the reporter.)

---

## M10: CLI and scanner (`cli.py`, `scanner.py`)

**Concepts**
- **`argparse`**: `--help` is generated automatically.
- **Exit codes as an API:** 0/1/2/3 (OK/WARNING/CRITICAL/UNKNOWN) follow
  the Nagios convention so an RMM tool or script can branch on the result.
  argparse's default "2" for usage errors is overridden to 64 so it can't be
  mistaken for CRITICAL.
- **stderr for progress, stdout for results** keeps output pipeable.
- **Fault isolation:** `_guarded()` turns an unexpected exception in one
  collector into a failed check; the rest of the scan continues.
- **`python -m package`** runs `package/__main__.py`.

**Questions**
1. How would a script use this tool's exit code?
2. What happens if the disk collector has a bug and throws an exception?
3. Why not show stack traces to normal users? How do you still debug problems?

---

## M11: Testing and CI (`tests/`, `.github/workflows/tests.yml`)

**Concepts**
- **Unit tests** check one piece in isolation; **fixtures** are saved real command output.
- **Mocking / fakes:** `FakeRunner` replaces command execution;
  `unittest.mock.patch` replaces `subprocess.run` to simulate
  `FileNotFoundError` and timeouts.
- **Dependency injection:** collectors take `run`, `read_file` and
  `resolver` as parameters, so tests pass fakes without patching internals.
- **CI matrix:** the same tests on 3 OSes × 2 Python versions. The smoke
  test runs the real tool on each runner, which proves the Windows
  PowerShell and Linux commands work on real systems.
- **Git workflow:** small, focused commits per feature with descriptive messages.

**Questions**
1. How do you test Windows parsing on a Mac?
2. What's the difference between your unit tests and the CI smoke test?
3. What is dependency injection and where did you use it?
4. Why `fail-fast: false` in the matrix?

---

## M12: Hardening after a design audit (v1.1.0)

An audit asked "what would stop an enterprise IT team from trusting this?"
and found real bugs, not just missing features. This is the best interview
story in the project: *how you found and fixed problems in your own work.*

**What changed and why**
- **UNKNOWN exit code** (`Report.overall_status()` in `models.py`). Before,
  a scan where every command failed turned each failure into an INFO
  finding, and INFO didn't affect the exit code, so the tool said "OK" and
  exited 0. An RMM would have marked a broken machine healthy. Now, if core
  checks (OS, memory, disks, interfaces) are missing, the result is UNKNOWN
  (exit 3). Precedence: CRITICAL > UNKNOWN > WARNING > OK.
- **False WARNING on corporate laptops** (`findings.py`). Gateway OK, ping to
  1.1.1.1 blocked, DNS works: that's a *healthy* laptop behind a firewall that
  blocks outbound ICMP. It's now INFO. The deciding evidence is DNS: a DNS
  answer proves packets are leaving the machine.
- **PATH hijacking** (`runner.py`, `resolve_executable`). The allow-list
  checked names like `ping`, but the OS decided *which* `ping` by searching
  PATH (and, on Windows, the folder the program was started from). A fake
  `ping.exe` in Downloads could have run. Commands now run only from fixed
  system paths.
- **Configurable targets with validation** (`cli.py`). `--ping-target` must be
  an IPv4 address and `--dns-name` an RFC 1123 hostname. That also prevents
  *argument injection*: `--ping-target=-f` would otherwise reach `ping` as an
  option even without a shell.
- **Stable finding IDs** (`models.Finding.id`, `docs/findings.md`). Titles are
  for humans and may change; IDs are the contract for scripts and KB articles.
  A test fails if the docs and code disagree.
- **One place per problem in the report.** A check that couldn't run used to
  appear three times. Now it appears once, and "couldn't check" is no longer
  counted as a finding.
- **Distribution** (`tools/build_zipapp.py`, `release.yml`). Python's
  `zipapp` packs the tool into one `.pyz` file with a SHA-256 checksum;
  pushing a version tag builds it and publishes a GitHub Release.
- **Golden-file tests.** The sample reports are compared byte-for-byte with
  freshly rendered output, so a format change can't break consumers silently.
- **SECURITY.md** lists every command and argument, every network destination,
  the personal data in reports, and a threat model.

- **Lockdown testing found a bug the unit tests couldn't** (v1.1.1). A CI
  job runs the real scan as a non-admin user and under PowerShell
  Constrained Language Mode. Two details matter:
  - The first attempt to force CLM silently didn't work, but the job checks
    the language mode *before* scanning and caught it. A test that can't tell
    whether its setup worked proves nothing.
  - Once CLM was really active, the OS and network queries failed: they used
    `[pscustomobject]`, which CLM forbids. On an AppLocker-managed laptop the
    tool would have reported UNKNOWN. Unit tests mock PowerShell, so they
    could never find this; only a real run could.

- **Proxy detection** (v1.2.0, `collectors/proxy.py`). Corporate web traffic
  often goes through a proxy that ping and DNS never touch, which explains
  "every test passes but websites don't load". Windows has *two* proxies:
  the per-user WinINET proxy (browsers, most apps) and the machine-wide
  WinHTTP proxy (services like Windows Update). The WinHTTP value is read as
  bytes from the registry and decoded, because `netsh` output is translated
  into the display language. Proxy URLs can contain passwords, so `redact()`
  strips credentials and query strings before anything is stored, and CI
  proves a password set in `HTTPS_PROXY` never reaches the report.

**What was deliberately rejected** (and why that's a good answer)
- `--redact`: hashing a hostname with a short unsalted hash is reversible by
  guessing names, so it would give false confidence. Honest disclosure instead.
- Proxy detection was first rejected (more commands), then added in 1.2.0
  once it could be done with commands already on the allow-list. Priorities
  change; the reasoning should be explicit both times.
- `--config` file, report diffing, JSON on stdout, a JSON Schema file: useful,
  but each adds surface area the project's "keep the CLI small" goal argues
  against. Saying no to features is part of design.

**Concepts**
- **Nagios plugin conventions** and why UNKNOWN exists.
- **Executable search order** (PATH, and Windows' application-directory-first
  search) and why security-sensitive tools use absolute paths.
- **Argument injection vs command injection**: `shell=False` stops the second,
  not the first. Input validation stops both.
- **Constrained Language Mode** (AppLocker/WDAC) and why COM objects fail there.
- **Semantic versioning** for the tool and for the JSON schema.

**Questions**
1. Your tool exited 0 on a machine where nothing could be read. Why was that a bug, and how did you fix it?
2. You run commands without a shell. Why isn't that enough to be safe?
3. Why does a successful DNS lookup change how you interpret failed pings?
3b. Ping and DNS both pass but websites fail. What would you check? (Proxy: is one configured, is it reachable, does the PAC file download? This tool reports which proxy is configured.)
4. Why didn't you add a redaction option?
5. How does someone deploy this to 500 Windows laptops? (Python via Intune/winget, then push one `.pyz`, verify the hash, read the exit code.)
6. What's still not validated? (A real domain-joined laptop with a production AppLocker/WDAC policy, and explicit-proxy networks. CI simulates CLM and a standard user; the README says exactly that.)
7. Your unit tests passed but the tool was broken under CLM. What does that teach you about mocking? (Mocks test your logic against your assumptions about the outside world; only real runs test the assumptions.)

---

## M13: Second audit (v1.2.1 and v1.3.0)

A second council audit asked "what still stops an IT team trusting this?"
Every reviewer agreed the most important problems were things the tool
*already did wrong*, not missing features.

**v1.2.1: a local security hole in report writing** (`reporters.write_reports`, `cli._write`)
- Reports contain hostnames, IPs and MACs, but were created world-readable.
- They overwrote existing files and followed symlinks. On a shared Linux
  machine another user could create the fixed `/tmp/triage-reports` folder
  first, read the reports, or plant a symlink so the tool overwrote a file.
- Fix: `os.open(path, O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)` (create new,
  private, never follow a link), a `0700` directory owned by the user, and
  `tempfile.mkdtemp()` for the fallback (a fresh, unguessable, private folder).
- Concepts: file permissions and umask, symlink attacks, `O_EXCL`, TOCTOU.

**v1.3.0: correct conclusions on real corporate networks**
- **TCP reachability** (`connectivity.check_tcp`): `socket.create_connection`
  to the DNS test host on port 443, then close, with no data sent. Ping is weak
  evidence because firewalls drop it; a completed TCP handshake proves the
  path to a real service works. CI showed the payoff: every cloud runner
  blocks ping but connects on 443, so the report now *proves* ICMP filtering
  instead of guessing.
- **Proxy-aware findings**: a healthy laptop on a proxy-only network with
  internal DNS used to get "internet unreachable" (WARNING). Now the proxy is
  part of the decision, and the tool checks the proxy accepts a connection.
- **Removing a false claim**: findings said "DNS worked, so traffic is leaving
  the network". With a DNS server on the LAN, that's simply not true. Being
  willing to delete your own wrong reasoning is a good interview story.
- **VPN detection**: a full-tunnel VPN changes where "the internet" is; the
  report now says when the default route goes through a tunnel.
- **SYSTEM context**: when Intune/RMM runs the tool as SYSTEM, the "user"
  proxy is SYSTEM's. The tool detects this and says so.
- **Alert fatigue**: the "multiple interfaces disconnected" finding fired on
  every MacBook (Thunderbolt ports), which teaches people to ignore findings.
  It was removed. Fewer, more accurate findings beat more findings.
- **Release trust**: release now waits for the full CI suite; actions are
  pinned to commit SHAs (a tag can be moved, a SHA can't); a signed build
  provenance attestation proves where the file came from; and the build is
  reproducible (sorted entries, fixed timestamps), so anyone can rebuild a
  tag and get the same SHA-256.
- **Stability policy**: exit codes and finding IDs never change meaning; JSON
  minor versions only add fields.

**Rejected this round, and why:** TLS-interception detection (hard to
explain, security teams may object), a clock-skew check (three new commands),
printing first steps in the report (the previously rejected `next_steps`; the
printed ID links to them instead), Nagios performance data and an Intune
recipe (scope), code signing (needs paid certificates; attestation instead).

**Questions**
1. Why is a TCP connection better evidence than ping? What does it still not prove? (It doesn't prove HTTP or TLS work, or that the proxy lets the user through.)
2. What is `O_EXCL` and what attack does it prevent?
3. Why use `mkdtemp()` instead of a fixed folder in `/tmp`?
4. Why pin GitHub Actions to commit SHAs instead of tags?
5. What does a build provenance attestation prove that a SHA-256 checksum doesn't?
6. Why did you remove a finding? (Alert fatigue: a finding that's always true gets ignored, along with the real ones.)
7. The tool runs as SYSTEM from Intune. What changes in the results?

---

## General questions to prepare for

- "Walk me through what happens when I run `python -m endpoint_triage`."
  (cli → scanner → each collector → runner → parse → CheckResult →
  findings → reporters → files → exit code)
- "What was the hardest cross-platform difference?" (Good answers: Windows
  ping exit codes, APFS volumes, or locale-dependent output.)
- "How did you make sure the tool is safe to run on a user's machine?"
  (allow-list, no shell, no admin, read-only commands, no network upload,
  a test that asserts only allow-listed commands run)
- "What would you add next?" (Good, scoped answers: read-only proxy
  detection, Wi-Fi signal strength, validating on a locked-down
  domain-joined laptop. Avoid "a dashboard"; explain why it's out of scope.)
- "What would break if you deployed this to 5,000 machines?" (Update
  checks hitting vendor servers at once, PowerShell execution policies or
  AppLocker, fixed connectivity targets blocked by corporate firewalls,
  report storage and PII handling.)
