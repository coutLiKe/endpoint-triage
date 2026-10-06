import unittest

from endpoint_triage.collectors import resources
from endpoint_triage.models import Status
from tests.helpers import FakeRunner, fail, fake_files, fixture, not_found, ok

GIB = 1024 ** 3


def by_id(checks):
    return {c.id: c for c in checks}


class MacOSResourceTests(unittest.TestCase):
    def test_memory_and_disks(self):
        runner = FakeRunner({
            "hw.memsize": ok("17179869184\n"),
            "vm_stat": ok(fixture("macos_vm_stat.txt")),
            "df": ok(fixture("macos_df.txt")),
        })
        checks = by_id(resources.collect("Darwin", run=runner))

        memory = checks["resources.memory"].data
        self.assertEqual(memory["total_bytes"], 16 * GIB)
        self.assertEqual(memory["available_bytes"], (4875 + 335355 + 6862) * 16384)
        self.assertAlmostEqual(memory["used_percent"], 66.9, places=1)

        volumes = checks["resources.disks"].data["volumes"]
        mounts = [v["mount"] for v in volumes]
        # APFS system volumes, read-only asset/simulator images, devfs and
        # autofs maps are filtered out; mount points with spaces survive.
        # The sealed system volume (/) is dropped because it shares space with the Data volume.
        self.assertEqual(mounts, ["/System/Volumes/Data", "/Volumes/Backup Drive"])
        data_volume = volumes[0]
        self.assertEqual(data_volume["used_bytes"], 199815616 * 1024)
        # The APFS container is shared, so Total is used + free and the row adds up.
        self.assertEqual(data_volume["total_bytes"], (199815616 + 14863604) * 1024)
        self.assertIn("APFS", checks["resources.disks"].data["note"])
        self.assertEqual(data_volume["free_bytes"], 14863604 * 1024)
        self.assertAlmostEqual(data_volume["used_percent"], 93.1, places=1)

    def test_vm_stat_failure_keeps_total(self):
        runner = FakeRunner({"hw.memsize": ok("17179869184\n"), "vm_stat": fail(), "df": ok(fixture("macos_df.txt"))})
        memory = by_id(resources.collect("Darwin", run=runner))["resources.memory"]
        self.assertEqual(memory.status, Status.OK)
        self.assertIsNone(memory.data["available_bytes"])
        self.assertIn("unknown", memory.data["note"])

    def test_malformed_memsize(self):
        runner = FakeRunner({"hw.memsize": ok("lots"), "df": ok(fixture("macos_df.txt"))})
        self.assertEqual(by_id(resources.collect("Darwin", run=runner))["resources.memory"].status, Status.FAILED)


class LinuxResourceTests(unittest.TestCase):
    def test_memory_and_disks(self):
        runner = FakeRunner({"df": ok(fixture("linux_df.txt"))})
        files = fake_files({"/proc/meminfo": fixture("linux_meminfo.txt")})
        checks = by_id(resources.collect("Linux", run=runner, read_file=files))

        memory = checks["resources.memory"].data
        self.assertEqual(memory["total_bytes"], 16314044 * 1024)
        self.assertEqual(memory["available_bytes"], 8157022 * 1024)
        self.assertEqual(memory["used_percent"], 50.0)

        mounts = [v["mount"] for v in checks["resources.disks"].data["volumes"]]
        self.assertEqual(mounts, ["/", "/boot/efi", "/mnt/data"])  # no tmpfs, no snap loops

    def test_container_overlay_falls_back_to_root(self):
        df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
              "overlay 61202432 20000000 41202432 33% /\n"
              "tmpfs 65536 0 65536 0% /dev\n")
        runner = FakeRunner({"df": ok(df)})
        disks = by_id(resources.collect("Linux", run=runner, read_file=fake_files({})))["resources.disks"]
        self.assertEqual([v["mount"] for v in disks.data["volumes"]], ["/"])

    def test_df_partial_failure_still_reports(self):
        runner = FakeRunner({"df": ok(fixture("linux_df.txt"), stderr="df: /mnt/nfs: Stale file handle", returncode=1)})
        disks = by_id(resources.collect("Linux", run=runner, read_file=fake_files({})))["resources.disks"]
        self.assertEqual(disks.status, Status.OK)
        self.assertIn("Stale file handle", disks.data["note"])

    def test_missing_meminfo_and_df(self):
        checks = by_id(resources.collect("Linux", run=FakeRunner(), read_file=fake_files({})))
        self.assertEqual(checks["resources.memory"].status, Status.UNAVAILABLE)
        self.assertEqual(checks["resources.disks"].status, Status.UNAVAILABLE)

    def test_malformed_df(self):
        runner = FakeRunner({"df": ok("Filesystem\nthis is not df output\n")})
        disks = by_id(resources.collect("Linux", run=runner, read_file=fake_files({})))["resources.disks"]
        self.assertEqual(disks.status, Status.FAILED)

    def test_meminfo_without_available(self):
        files = fake_files({"/proc/meminfo": "MemTotal: 1024 kB\n"})
        memory = by_id(resources.collect("Linux", run=FakeRunner(), read_file=files))["resources.memory"]
        self.assertEqual(memory.status, Status.OK)
        self.assertIsNone(memory.data["used_percent"])


class WindowsResourceTests(unittest.TestCase):
    def test_memory_and_disks(self):
        runner = FakeRunner({
            "TotalVisibleMemorySize": ok('{"TotalVisibleMemorySize":16658476,"FreePhysicalMemory":4164619}'),
            "Win32_LogicalDisk": ok('[{"DeviceID":"C:","FileSystem":"NTFS","Size":511101108224,"FreeSpace":20444044329},'
                                    '{"DeviceID":"D:","FileSystem":"NTFS","Size":1000202039296,"FreeSpace":800000000000}]'),
        })
        checks = by_id(resources.collect("Windows", run=runner))
        memory = checks["resources.memory"].data
        self.assertEqual(memory["total_bytes"], 16658476 * 1024)
        self.assertAlmostEqual(memory["used_percent"], 75.0, places=1)

        c_drive = checks["resources.disks"].data["volumes"][0]
        self.assertEqual(c_drive["mount"], "C:")
        self.assertEqual(c_drive["total_bytes"], 511101108224)
        self.assertAlmostEqual(c_drive["used_percent"], 96.0, places=1)

    def test_single_disk_object_and_null_size(self):
        runner = FakeRunner({
            "Win32_LogicalDisk": ok('{"DeviceID":"C:","FileSystem":"NTFS","Size":100,"FreeSpace":50}'),
        })
        disks = by_id(resources.collect("Windows", run=runner))["resources.disks"]
        self.assertEqual(len(disks.data["volumes"]), 1)

    def test_powershell_errors(self):
        runner = FakeRunner({"TotalVisibleMemorySize": fail(stderr="Access denied"), "Win32_LogicalDisk": ok("{oops")})
        checks = by_id(resources.collect("Windows", run=runner))
        self.assertEqual(checks["resources.memory"].status, Status.FAILED)
        self.assertEqual(checks["resources.disks"].status, Status.FAILED)

    def test_powershell_missing(self):
        checks = by_id(resources.collect("Windows", run=FakeRunner({"powershell": not_found()})))
        self.assertEqual(checks["resources.memory"].status, Status.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
