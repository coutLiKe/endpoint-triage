import json
import unittest

from endpoint_triage.collectors import proxy
from endpoint_triage.models import Status
from tests.helpers import FakeRunner, fail, ok

SCUTIL_NO_PROXY = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
  }
  FTPPassive : 1
}
"""
SCUTIL_PROXY = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
    2 : *.corp.example
  }
  FTPPassive : 1
  HTTPEnable : 1
  HTTPPort : 8080
  HTTPProxy : proxy.corp.example
  HTTPSEnable : 1
  HTTPSPort : 8443
  HTTPSProxy : secure-proxy.corp.example
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : http://wpad.corp.example/wpad.dat?token=abc
  ProxyAutoDiscoveryEnable : 0
}
"""


def winhttp_blob(proxy_server: str | None, bypass: str = "") -> list[int]:
    """Build a WinHttpSettings value: version, counter, flags, then length-prefixed strings."""
    def dword(n):
        return n.to_bytes(4, "little")
    if proxy_server is None:
        data = dword(0x28) + dword(0) + dword(0x1) + dword(0) + dword(0) + bytes(8)
    else:
        data = (dword(0x28) + dword(1) + dword(0x3) + dword(len(proxy_server)) + proxy_server.encode()
                + dword(len(bypass)) + bypass.encode() + bytes(8))
    return list(data)


def windows_output(**values):
    base = {"ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None, "AutoConfigURL": None,
            "WinHttpSettings": winhttp_blob(None)}
    base.update(values)
    return ok(json.dumps(base))


class RedactTests(unittest.TestCase):
    def test_removes_credentials_and_query_strings(self):
        self.assertEqual(proxy.redact("http://alice:s3cret@proxy.corp.example:3128"), "http://proxy.corp.example:3128")
        self.assertEqual(proxy.redact("alice:s3cret@proxy:8080"), "proxy:8080")
        self.assertEqual(proxy.redact("http://wpad/wpad.dat?token=abc#x"), "http://wpad/wpad.dat")
        self.assertEqual(proxy.redact("proxy.corp.example:8080"), "proxy.corp.example:8080")


class EnvironmentTests(unittest.TestCase):
    def test_env_proxy_with_credentials_is_redacted(self):
        env = {"HTTPS_PROXY": "http://alice:s3cret@proxy.corp.example:3128", "no_proxy": "localhost,.corp.example"}
        check = proxy.collect("Linux", run=FakeRunner(), environ=env)[0]
        self.assertEqual(check.status, Status.OK)
        self.assertTrue(check.data["configured"])
        entry = check.data["proxies"][0]
        self.assertEqual(entry["proxy"], "http://proxy.corp.example:3128")
        self.assertEqual(entry["bypass"], "localhost,.corp.example")
        self.assertNotIn("s3cret", json.dumps(check.to_dict(include_debug=True)))
        self.assertIn("GNOME", check.data["note"])

    def test_lowercase_and_no_proxy_only(self):
        self.assertEqual(proxy.environment_proxy({"http_proxy": "proxy:80"})["proxy"], "proxy:80")
        self.assertIsNone(proxy.environment_proxy({"NO_PROXY": "localhost"}))

    def test_no_proxy_anywhere(self):
        check = proxy.collect("Linux", run=FakeRunner(), environ={})[0]
        self.assertFalse(check.data["configured"])
        self.assertEqual(check.data["sources_checked"], ["environment variables"])


class MacOSTests(unittest.TestCase):
    def test_default_exceptions_alone_are_not_a_proxy(self):
        check = proxy.collect("Darwin", run=FakeRunner({"scutil --proxy": ok(SCUTIL_NO_PROXY)}), environ={})[0]
        self.assertFalse(check.data["configured"])

    def test_system_proxy_and_pac(self):
        check = proxy.collect("Darwin", run=FakeRunner({"scutil --proxy": ok(SCUTIL_PROXY)}), environ={})[0]
        entry = check.data["proxies"][0]
        self.assertEqual(entry["source"], "macOS system proxy")
        self.assertEqual(entry["proxy"], "secure-proxy.corp.example:8443")  # HTTPS preferred
        self.assertEqual(entry["pac_url"], "http://wpad.corp.example/wpad.dat")  # query string dropped
        self.assertFalse(entry["auto_detect"])
        self.assertIn("*.corp.example", entry["bypass"])

    def test_scutil_failure_keeps_environment_result(self):
        runner = FakeRunner({"scutil --proxy": fail(stderr="scutil: error")})
        check = proxy.collect("Darwin", run=runner, environ={"HTTP_PROXY": "proxy:80"})[0]
        self.assertEqual(check.status, Status.FAILED)
        self.assertEqual(check.data["proxies"][0]["proxy"], "proxy:80")


class WindowsTests(unittest.TestCase):
    def test_no_proxy(self):
        check = proxy.collect("Windows", run=FakeRunner({"Internet Settings": windows_output()}), environ={})[0]
        self.assertEqual(check.status, Status.OK)
        self.assertFalse(check.data["configured"])

    def test_user_and_winhttp_proxies(self):
        runner = FakeRunner({"Internet Settings": windows_output(
            ProxyEnable=1, ProxyServer="proxy.corp.example:8080", ProxyOverride="<local>",
            AutoConfigURL="http://wpad.corp.example/proxy.pac",
            WinHttpSettings=winhttp_blob("winhttp-proxy.corp.example:3128", "<local>;*.corp.example"))})
        check = proxy.collect("Windows", run=runner, environ={})[0]
        user, system = check.data["proxies"]
        self.assertEqual(user["source"], "Windows user proxy (WinINET)")
        self.assertEqual(user["proxy"], "proxy.corp.example:8080")
        self.assertEqual(user["pac_url"], "http://wpad.corp.example/proxy.pac")
        self.assertEqual(system["source"], "Windows system proxy (WinHTTP)")
        self.assertEqual(system["proxy"], "winhttp-proxy.corp.example:3128")
        self.assertEqual(system["bypass"], "<local>;*.corp.example")

    def test_disabled_user_proxy_is_ignored(self):
        runner = FakeRunner({"Internet Settings": windows_output(ProxyEnable=0, ProxyServer="old-proxy:8080")})
        self.assertFalse(proxy.collect("Windows", run=runner, environ={})[0].data["configured"])

    def test_missing_winhttp_value(self):
        self.assertIsNone(proxy.parse_winhttp_settings(None))

    def test_malformed_winhttp_value(self):
        runner = FakeRunner({"Internet Settings": windows_output(WinHttpSettings=[1, 2, 3])})
        self.assertEqual(proxy.collect("Windows", run=runner, environ={})[0].status, Status.FAILED)

    def test_powershell_failure(self):
        runner = FakeRunner({"Internet Settings": fail(stderr="denied")})
        self.assertEqual(proxy.collect("Windows", run=runner, environ={})[0].status, Status.FAILED)

    def test_script_is_read_only_and_clm_compatible(self):
        script = proxy.WINDOWS_PROXY_SCRIPT
        self.assertIn("Get-ItemProperty", script)
        for forbidden in ("Set-", "New-Item", "Remove-", "[pscustomobject]"):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
