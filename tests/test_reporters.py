import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from endpoint_triage import reporters
from endpoint_triage.models import CheckResult, Report
from tests.sample_report import build_sample_report


class HelperTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(reporters.format_duration(59), "0 minutes")
        self.assertEqual(reporters.format_duration(3660), "1 hour, 1 minute")
        self.assertEqual(reporters.format_duration(2 * 86400 + 7200 + 300), "2 days, 2 hours, 5 minutes")

    def test_format_bytes(self):
        self.assertEqual(reporters.format_bytes(16 * 1024 ** 3), "16.0 GB")
        self.assertEqual(reporters.format_bytes(None), "unknown")


class TextReportTests(unittest.TestCase):
    def setUp(self):
        self.report = build_sample_report()
        self.text = reporters.render_text(self.report)

    def test_sections_in_order(self):
        headings = ["Endpoint Triage Report", "Summary", "Findings", "System Information", "Resource Usage",
                    "Network Configuration", "Connectivity Tests", "Operating System Updates",
                    "Errors / Unavailable Checks"]
        positions = [self.text.index(f"\n{h}\n") if i else self.text.index(h) for i, h in enumerate(headings)]
        self.assertEqual(positions, sorted(positions))

    def test_summary(self):
        self.assertIn("Hostname          : HD-LAPTOP-042.corp.local", self.text)
        self.assertIn("Overall status    : CRITICAL", self.text)
        self.assertIn("1 critical, 1 warning, 2 info", self.text)

    def test_findings_have_severity_title_explanation_evidence(self):
        self.assertIn("[CRITICAL] Critically low disk space on C:", self.text)
        self.assertIn("[WARNING] DNS resolution failed", self.text)
        self.assertIn("public IP connectivity test succeeded", " ".join(self.text.split()))
        self.assertIn("    - Volume C: is 96.4% used, 17.0 GB free of 476.0 GB", self.text)
        # Findings appear before the raw diagnostic sections.
        self.assertLess(self.text.index("[CRITICAL]"), self.text.index("System Information"))

    def test_diagnostic_details(self):
        self.assertIn("Uptime            : 38 days, 5 hours, 12 minutes", self.text)
        self.assertIn("Default gateway   : 192.168.1.1 (via Wi-Fi)", self.text)
        self.assertIn("[OK]          Ping public IP address (1.1.1.1): reply, avg 15.0 ms", self.text)
        self.assertIn("[FAILED]      Resolve public hostname (example.com): DNS lookup", self.text)

    def test_errors_section_and_debug_detail(self):
        self.assertIn("[FAILED] updates.os: powershell exited with code 1", self.text)
        self.assertIn("Run with --debug", self.text)
        self.assertNotIn("returncode: 1", self.text)
        debug_text = reporters.render_text(self.report, debug=True)
        self.assertIn("| returncode: 1", debug_text)

    def test_empty_report_renders(self):
        report = Report("1.0.0", "Linux", datetime(2026, 1, 1, tzinfo=timezone.utc), 0.1, {})
        text = reporters.render_text(report)
        self.assertIn("No issues detected", text)
        self.assertIn("None. All checks completed.", text)


class JsonReportTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(reporters.render_json(build_sample_report()))

    def test_top_level_structure(self):
        self.assertEqual(set(self.data), {"schema_version", "tool", "generated_at", "duration_seconds",
                                          "summary", "scan_options", "findings", "sections"})
        self.assertEqual(self.data["schema_version"], "1.1")
        self.assertEqual(self.data["scan_options"],
                         {"ping_target": "1.1.1.1", "dns_name": "example.com", "skip_updates": False})
        self.assertEqual(self.data["tool"], {"name": "endpoint-triage", "version": "1.0.0"})
        self.assertEqual(self.data["generated_at"], "2026-10-05T14:03:22+00:00")

    def test_summary(self):
        summary = self.data["summary"]
        self.assertEqual(summary["overall_status"], "CRITICAL")
        self.assertEqual(summary["finding_counts"], {"CRITICAL": 1, "WARNING": 1, "INFO": 2})
        self.assertEqual(summary["check_counts"], {"ok": 9, "failed": 2, "unavailable": 0, "skipped": 0})

    def test_findings_shape(self):
        for finding in self.data["findings"]:
            self.assertEqual(set(finding), {"severity", "title", "explanation", "evidence"})
            self.assertIn(finding["severity"], {"INFO", "WARNING", "CRITICAL"})
            self.assertIsInstance(finding["evidence"], list)

    def test_sections_and_checks_shape(self):
        self.assertEqual(list(self.data["sections"]), ["system", "resources", "network", "connectivity", "updates"])
        for checks in self.data["sections"].values():
            for check in checks:
                self.assertEqual(set(check), {"id", "title", "status", "data", "error"})
                self.assertIn(check["status"], {"ok", "failed", "unavailable", "skipped"})

    def test_debug_field_only_in_debug_mode(self):
        debug_data = json.loads(reporters.render_json(build_sample_report(), debug=True))
        update = debug_data["sections"]["updates"][0]
        self.assertIn("returncode", update["debug"])

    def test_report_without_core_checks_is_unknown(self):
        report = Report("1.0.0", "Linux", datetime(2026, 1, 1, tzinfo=timezone.utc), 0.1, {
            "updates": [CheckResult.unavailable("updates.os", "Pending OS updates", "no apt")]})
        from endpoint_triage.findings import analyze
        report.findings = analyze(report)
        self.assertEqual(reporters.report_to_dict(report)["summary"]["overall_status"], "UNKNOWN")


class WriteReportTests(unittest.TestCase):
    def test_writes_both_files_with_dotted_hostname(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "reports"
            text_path, json_path = reporters.write_reports(build_sample_report(), out)
            self.assertEqual(text_path.name, "triage-HD-LAPTOP-042.corp.local-20261005-140322.txt")
            self.assertEqual(json_path.name, "triage-HD-LAPTOP-042.corp.local-20261005-140322.json")
            self.assertTrue(text_path.read_text(encoding="utf-8").startswith("Endpoint Triage Report"))
            json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([text_path.name, json_path.name]))


if __name__ == "__main__":
    unittest.main()
