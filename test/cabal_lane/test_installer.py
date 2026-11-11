"""Hermetic installer tests. Synthetic executables are never run."""
import hashlib
import importlib.util
import io
import lzma
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/actions/install_cabal/install.py"
spec = importlib.util.spec_from_file_location("cabal_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def member(name, data=b"x", kind=tarfile.REGTYPE, linkname="", pax=None):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.type = kind
    info.linkname = linkname
    info.pax_headers = pax or {}
    return info, data


def tar_bytes(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, data in members:
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.destination = self.root / "bin"
        self.binary = b"synthetic cabal executable; never executed"
        self.members = [member("cabal", self.binary), member("plan.json", b"{}")]
        self.raw = tar_bytes(self.members)
        self.data = lzma.compress(self.raw)
        self.original_releases = installer.RELEASES
        for name, value in [("system", "Linux"), ("machine", "x86_64")]:
            patcher = mock.patch.object(installer.platform, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pins(self, data=None, binary=None):
        return {
            machine: release._replace(
                archive_sha256=hashlib.sha256(self.data if data is None else data).hexdigest(),
                binary_sha256=hashlib.sha256(self.binary if binary is None else binary).hexdigest(),
            )
            for machine, release in self.original_releases.items()
        }

    def install(self, pins=None, fetch=None):
        if pins is None:
            pins = self.pins()
        if fetch is None:
            fetch = mock.Mock(return_value=self.data)
        with mock.patch.object(installer, "RELEASES", pins):
            result = installer.install(self.destination, fetch=fetch)
        return result, fetch

    def rejects(self, pins=None, pattern=None):
        errors = (ValueError, OSError, EOFError, tarfile.TarError, lzma.LZMAError)
        with self.assertRaisesRegex(errors, pattern or ".*"):
            self.install(pins=pins)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def replace_tar(self, members):
        self.data = lzma.compress(tar_bytes(members))

    def test_both_linux_architectures_select_their_pinned_asset(self):
        expected = {
            "aarch64": (
                "e4d940131ab0b78943a4b5ce64a9ef5acc452ea849df197787eaeecb1c3bf276",
                "5b2eebf0cf0c3f0b5f38cd3b1b6dedf81fa064671d09002b319cec0983392e74",
            ),
            "x86_64": (
                "b4f4c83135da77cfc18ccd6056398ca714a2dad86d5f635621ffdda8f9afe587",
                "9686ccc04aab211fcd02e09470cdf1806e1e1f31918ecfb3aca96698964c80a0",
            ),
        }
        self.assertEqual(set(installer.RELEASES), set(expected))
        for machine, hashes in expected.items():
            with self.subTest(machine=machine), mock.patch.object(installer.platform, "machine", return_value=machine):
                release = installer.release_for_host()
                self.assertEqual((release.archive_sha256, release.binary_sha256), hashes)
                self.destination = self.root / machine
                installed, fetch = self.install()
                fetch.assert_called_once_with(
                    f"https://github.com/haskell/cabal/releases/download/cabal-install-v3.18.2.0/"
                    f"cabal-install-3.18.2.0-{machine}-linux-unknown.tar.xz", installer.MAX_ARCHIVE)
                self.assertEqual(installed.read_bytes(), self.binary)
                self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
                self.assertEqual(list(self.destination.iterdir()), [installed])

    def test_unsupported_hosts_fail_before_fetch_or_filesystem_changes(self):
        for system, machine in [("Darwin", "aarch64"), ("Windows", "x86_64"),
                                ("Linux", "arm64"), ("Linux", "i386"), ("Linux", "AMD64")]:
            with self.subTest(system=system, machine=machine):
                fetch = mock.Mock()
                with mock.patch.object(installer.platform, "system", return_value=system), \
                        mock.patch.object(installer.platform, "machine", return_value=machine):
                    with self.assertRaisesRegex(ValueError, "supports Linux"):
                        installer.install(self.destination, fetch=fetch)
                fetch.assert_not_called()
                self.assertFalse(self.destination.exists())

    def test_changed_archive_fails_before_decompression(self):
        pins = self.pins()
        self.data += b"tampering"
        with mock.patch.object(installer.lzma, "LZMADecompressor") as parser:
            self.rejects(pins=pins, pattern="archive checksum")
        parser.assert_not_called()

    def test_wrong_architecture_archive_is_rejected_before_decompression(self):
        with mock.patch.object(installer.lzma, "LZMADecompressor") as parser:
            self.rejects(pins=self.original_releases, pattern="archive checksum")
        parser.assert_not_called()

    def test_changed_executable_fails_after_valid_archive_hash(self):
        self.replace_tar([member("cabal", b"different executable"), self.members[1]])
        self.rejects(pattern="executable checksum")

    def test_missing_or_oversized_archive(self):
        self.data = b""
        self.rejects(pattern="missing or oversized")
        self.data = b"oversized"
        with mock.patch.object(installer, "MAX_ARCHIVE", 4):
            self.rejects(pattern="missing or oversized")

    def test_download_failure_creates_no_destination(self):
        with self.assertRaisesRegex(OSError, "unavailable"):
            self.install(fetch=mock.Mock(side_effect=OSError("unavailable")))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_truncated_invalid_and_concatenated_xz(self):
        valid = self.data
        for bad in [valid[:-1], b"not XZ", valid + valid, valid + b"trailing"]:
            with self.subTest(length=len(bad)):
                self.data = bad
                self.rejects()

    def test_decompressed_size_and_decoder_memory_are_bounded(self):
        with mock.patch.object(installer, "MAX_TAR", 4096):
            self.rejects(pattern="oversized")
        with mock.patch.object(installer, "MAX_LZMA_MEMORY", 1024):
            self.rejects(pattern="Memory usage limit")

    def test_unsafe_or_unexpected_names(self):
        for name in ["../cabal", "/cabal", "./cabal", "dir/cabal", "cabal/../cabal", "other"]:
            with self.subTest(name=name):
                self.replace_tar([member(name, self.binary), self.members[1]])
                self.rejects(pattern="unexpected")

    def test_duplicates_missing_members_and_extra_members(self):
        for members in [[], self.members[:1], self.members[1:], self.members + [self.members[0]],
                        self.members + [member("unexpected")]]:
            with self.subTest(count=len(members)):
                self.replace_tar(members)
                self.rejects()

    def test_link_device_directory_and_extension_records_are_rejected(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE,
                     tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME]:
            with self.subTest(kind=kind):
                self.replace_tar([member("cabal", self.binary, kind, "outside"), self.members[1]])
                self.rejects()

    def test_regular_member_with_link_target_is_rejected(self):
        self.replace_tar([member("cabal", self.binary, linkname="outside"), self.members[1]])
        self.rejects(pattern="plain regular")

    def test_sparse_pax_metadata_is_rejected_before_payload_access(self):
        sparse = member("cabal", b"x", pax={"GNU.sparse.map": "999999999,1",
                                            "GNU.sparse.size": "1000000000"})
        self.replace_tar([sparse, self.members[1]])
        self.rejects(pattern="plain regular")

    def test_plan_member_must_also_be_plain_and_bounded(self):
        self.replace_tar([self.members[0], member("plan.json", b"{}", tarfile.SYMTYPE, "outside")])
        self.rejects(pattern="plain regular")
        self.replace_tar(self.members)
        with mock.patch.object(installer, "MAX_PLAN", 1):
            self.rejects(pattern="oversized")

    def test_empty_oversized_and_negative_executable_size(self):
        self.replace_tar([member("cabal", b""), self.members[1]])
        self.rejects(pattern="empty or oversized")
        self.replace_tar(self.members)
        with mock.patch.object(installer, "MAX_BINARY", 1):
            self.rejects(pattern="oversized")
        info = tarfile.TarInfo("cabal")
        info.size = -1
        self.data = lzma.compress(info.tobuf(format=tarfile.GNU_FORMAT) + bytes(1024))
        self.rejects(pattern="empty or oversized")

    def test_tar_checksum_truncation_and_terminator_failures(self):
        bad_header = b"X" + self.raw[1:]
        for raw in [bad_header, self.raw[:511], self.raw[:512], self.raw[:2048],
                    self.raw[:2560], self.raw + b"hidden payload", self.raw + bytes(511),
                    self.raw + b"X" * 512]:
            with self.subTest(length=len(raw)):
                self.data = lzma.compress(raw)
                self.rejects()

    def test_explicit_new_destination_is_required(self):
        for destination in [Path("relative"), self.root / "line\nbreak", self.root / "line\rbreak",
                            self.root / "missing" / "bin"]:
            with self.subTest(destination=destination):
                self.destination = destination
                fetch = mock.Mock()
                with self.assertRaises((ValueError, OSError)):
                    self.install(fetch=fetch)
                fetch.assert_not_called()

    def test_existing_destinations_and_links_are_never_replaced(self):
        sentinel = self.root / "keep"
        sentinel.write_bytes(b"keep")
        for kind in ["directory", "file", "symlink", "dangling"]:
            with self.subTest(kind=kind):
                self.destination = self.root / kind
                if kind == "directory":
                    self.destination.mkdir()
                elif kind == "file":
                    self.destination.write_bytes(b"original")
                else:
                    self.destination.symlink_to(sentinel if kind == "symlink" else self.root / "absent")
                fetch = mock.Mock()
                with self.assertRaises(FileExistsError):
                    self.install(fetch=fetch)
                fetch.assert_not_called()
                self.assertEqual(sentinel.read_bytes(), b"keep")
                if kind == "file":
                    self.assertEqual(self.destination.read_bytes(), b"original")
                elif kind in {"symlink", "dangling"}:
                    self.assertTrue(self.destination.is_symlink())

    def test_parent_must_be_owned_and_not_shared_writable(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "task-owned"):
            self.install()
        self.root.chmod(0o700)
        with mock.patch.object(installer.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, "task-owned"):
                self.install()
        self.assertFalse(self.destination.exists())

    def test_destination_created_during_fetch_is_preserved(self):
        def fetch(url, limit):
            self.destination.mkdir()
            (self.destination / "keep").write_bytes(b"keep")
            return self.data
        with self.assertRaises(FileExistsError):
            self.install(fetch=fetch)
        self.assertEqual((self.destination / "keep").read_bytes(), b"keep")

    def test_atomic_install_publishes_verified_executable_only(self):
        replace = os.replace
        def inspect(staged, target):
            self.assertEqual(Path(staged).read_bytes(), self.binary)
            self.assertEqual(Path(staged).stat().st_mode & 0o777, 0o755)
            self.assertEqual(Path(staged).parent, self.destination)
            self.assertFalse(Path(target).exists())
            replace(staged, target)
        with mock.patch.object(installer.os, "replace", side_effect=inspect) as publish:
            installed, _ = self.install()
        publish.assert_called_once()
        self.assertEqual(installed.read_bytes(), self.binary)

    def test_staging_failures_remove_only_our_new_destination(self):
        for function in ["fchmod", "fsync", "replace"]:
            with self.subTest(function=function):
                with mock.patch.object(installer.os, function, side_effect=OSError("staging failed")):
                    self.rejects(pattern="staging failed")

    def test_cli_has_no_implicit_destination(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--destination", result.stderr)


class DownloadTests(unittest.TestCase):
    def test_download_is_bounded_and_unauthenticated(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"data"
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(installer.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(installer.download("https://example.invalid/release", 4), b"data")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/release")
        self.assertEqual(request.header_items(), [("User-agent", "hal-cabal-installer")])
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 60})
        response.read.assert_called_once_with(5)

    def test_empty_and_oversized_downloads_are_rejected(self):
        for data in [b"", b"12345"]:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = data
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch.object(installer.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, "missing or oversized"):
                    installer.download("https://example.invalid/release", 4)

    def test_non_https_or_authenticated_redirects_are_rejected_before_request(self):
        request = urllib.request.Request("https://example.invalid/release")
        redirect = installer.HTTPSRedirect()
        for url in ["http://example.invalid/file", "file:///tmp/file", "ftp://example.invalid/file",
                    "https://user:secret@example.invalid/file"]:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "unauthenticated HTTPS"):
                redirect.redirect_request(request, None, 302, "Found", {}, url)
        next_request = redirect.redirect_request(
            request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/file")
        self.assertEqual(next_request.full_url, "https://release-assets.githubusercontent.com/file")


class ActionTests(unittest.TestCase):
    def exercise_action(self, tamper=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner_temp = root / "runner temp"
            runner_temp.mkdir()
            action_path = root / "action fixture"
            action_path.mkdir()
            github_path = root / "github_path"
            github_path.write_text("previous\n")
            binary = b"synthetic action executable; never executed"
            archive = lzma.compress(tar_bytes([member("cabal", binary), member("plan.json", b"{}")]))
            (root / "archive.xz").write_bytes(archive + (b"tamper" if tamper else b""))
            # Use the actual CLI/installer with a synthetic pinned download; this
            # fixture neither fetches a release nor executes the installed file.
            wrapper = f"""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("fixture_installer", {str(SCRIPT)!r})
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
installer.platform.system = lambda: "Linux"
installer.platform.machine = lambda: "x86_64"
installer.RELEASES = {{"x86_64": installer.RELEASES["x86_64"]._replace(
    archive_sha256={hashlib.sha256(archive).hexdigest()!r},
    binary_sha256={hashlib.sha256(binary).hexdigest()!r})}}
install = installer.install
installer.install = lambda destination: install(destination, fetch=lambda url, limit: Path({str(root / 'archive.xz')!r}).read_bytes())
installer.main()
"""
            (action_path / "install.py").write_text(wrapper)
            action = (ROOT / ".github/actions/install_cabal/action.yaml").read_text()
            shell = textwrap.dedent(action.split("      run: |\n", 1)[1])
            result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, timeout=30,
                                    env={"PATH": os.environ["PATH"], "RUNNER_TEMP": str(runner_temp),
                                         "GITHUB_PATH": str(github_path), "CABAL_ACTION_PATH": str(action_path),
                                         "PYTHONDONTWRITEBYTECODE": "1"})
            if tamper:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("archive checksum mismatch", result.stderr)
                self.assertEqual(github_path.read_text(), "previous\n")
                self.assertTrue(all(not list(path.iterdir()) for path in runner_temp.iterdir()))
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                entries = github_path.read_text().splitlines()
                self.assertEqual(len(entries), 2)
                self.assertEqual(entries[0], "previous")
                destination = Path(entries[1])
                self.assertEqual(destination.parent.parent, runner_temp)
                self.assertEqual((destination / "cabal").read_bytes(), binary)

    def test_success_adds_only_verified_runner_temp_install_to_path(self):
        self.exercise_action()

    def test_failure_does_not_publish_destination_to_path(self):
        self.exercise_action(tamper=True)


if __name__ == "__main__":
    unittest.main()
