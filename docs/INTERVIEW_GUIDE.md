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
> go through an allow-list and never use a shell. It's tested with mocked
> command output for macOS, Windows and Linux, and CI runs on all three."

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
- **Exit codes as an API:** 0/1/2/3 follow the Nagios convention so an
  RMM tool or script can branch on the result. argparse's default "2" for
  usage errors is overridden to 64 so it can't be mistaken for CRITICAL.
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

## General questions to prepare for

- "Walk me through what happens when I run `python -m endpoint_triage`."
  (cli → scanner → each collector → runner → parse → CheckResult →
  findings → reporters → files → exit code)
- "What was the hardest cross-platform difference?" (Good answers: Windows
  ping exit codes, APFS volumes, or locale-dependent output.)
- "How did you make sure the tool is safe to run on a user's machine?"
  (allow-list, no shell, no admin, read-only commands, no network upload,
  a test that asserts only allow-listed commands run)
- "What would you add next?" (Good, scoped answers: proxy detection, Wi-Fi
  signal strength, a `--no-updates` option for speed, a configurable
  thresholds file. Avoid "a dashboard"; explain why it's out of scope.)
- "What would break if you deployed this to 5,000 machines?" (Update
  checks hitting vendor servers at once, PowerShell execution policies or
  AppLocker, fixed connectivity targets blocked by corporate firewalls,
  report storage and PII handling.)
