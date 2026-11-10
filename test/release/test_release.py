"""Hermetic guard tests. Fake Stack gates are not Haskell build evidence."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("release", "scripts/release/prepare.py")
installer = load("installer", ".github/actions/install_stack/install.py")


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as package:
        for name, value in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            package.addfile(member, io.BytesIO(value))
    return output.getvalue()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ["hal.cabal", "package.yaml"]:
            (self.source / name).write_text("name: hal\nversion: 1.1\n")
        (self.source / "stack.yaml").write_text("resolver: lts-24.66\npackages:\n- .\n")
        self.output = self.root / "output"
        self.sink = self.root / "uploaded.tar.gz"
        self.data = archive({"hal-1.1/hal.cabal": b"name: hal\nversion: 1.1\n",
                             "hal-1.1/test/Spec.hs": b"synthetic fixture\n"})
        self.calls = []
        self.failure = None
        self.mutate = False
        self.extra = False
        patcher = mock.patch.object(release, "stack", side_effect=self.fake_stack)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_stack(self, arguments, cwd, capture=False):
        gate = arguments[0]
        self.calls.append(gate)
        if gate == self.failure:
            raise subprocess.CalledProcessError(1, ["stack", *arguments])
        if gate == "query":
            self.assertTrue(capture)
            return str(cwd) + "\n"
        if gate == "sdist":
            self.assertNotIn("--ignore-check", arguments)
            self.assertIn("--pvp-bounds=none", arguments)
            self.archive_path = Path(arguments[arguments.index("--tar-dir") + 1]) / "hal-1.1.tar.gz"
            self.archive_path.write_bytes(self.data)
            if self.extra:
                self.archive_path.with_name("revision.cabal").write_text("unexpected revision")
        else:
            self.assertNotEqual(cwd, self.source)
            self.assertEqual((cwd / "test/Spec.hs").read_bytes(), b"synthetic fixture\n")
            self.assertEqual((cwd / "stack.yaml").read_bytes(), (self.source / "stack.yaml").read_bytes())
            if gate == "test" and self.mutate:
                self.archive_path.write_bytes(b"replacement")

    def prepare(self):
        return release.prepare(self.source, self.output, "v1.1")

    def no_upload(self, tag="v1.1"):
        with self.assertRaises((ValueError, OSError, KeyError)):
            release.exercise_upload(self.output, tag, self.sink)
        self.assertFalse(self.sink.exists())

    def test_one_sdist_exact_bytes_reach_synthetic_uploader(self):
        receipt = self.prepare()
        self.assertEqual(self.calls, ["sdist", "query", "build", "test"])
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.data).hexdigest())
        release.exercise_upload(self.output, "v1.1", self.sink)
        self.assertEqual(self.sink.read_bytes(), self.data)

    def test_wrong_local_package_fails_before_build(self):
        original = self.fake_stack
        def wrong_package(arguments, cwd, capture=False):
            if arguments[0] == "query":
                return str(self.source)
            return original(arguments, cwd, capture)
        with mock.patch.object(release, "stack", side_effect=wrong_package):
            with self.assertRaisesRegex(ValueError, "unpacked"):
                self.prepare()
        self.assertEqual(self.calls, ["sdist"])
        self.no_upload()

    def test_bad_tags_fail_before_gates(self):
        for tag in ["1.1", "v1.2", "v01.1", "v1.1.0", "v1.1-rc1", "refs/tags/v1.1", "v1.1\n", "v1.1; echo bad"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.prepare(self.source, self.output, tag)
        self.assertEqual(self.calls, [])
        self.no_upload()

    def test_manifest_version_mismatch(self):
        (self.source / "package.yaml").write_text("version: 1.2\n")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.no_upload()

    def test_malformed_versions(self):
        for value in ["", "version: 1.1\nversion: 1.1\n", "version: 01.1\n", "version: 1.1.2.3.4\n"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.version(value)

    def test_failed_package_check_blocks_upload(self):
        self.failure = "sdist"
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual(self.calls, ["sdist"])
        self.no_upload()

    def test_failed_archive_build_blocks_test_and_upload(self):
        self.failure = "build"
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual(self.calls, ["sdist", "query", "build"])
        self.no_upload()

    def test_failed_archive_test_blocks_upload(self):
        self.failure = "test"
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.no_upload()

    def test_replacement_during_validation(self):
        self.mutate = True
        with self.assertRaisesRegex(ValueError, "replaced"):
            self.prepare()
        self.no_upload()

    def test_replacement_after_validation(self):
        self.prepare()
        path = self.output / "hal-1.1.tar.gz"
        path.chmod(0o644)
        path.write_bytes(self.data + b"tamper")
        self.no_upload()

    def test_rebuild_after_validation(self):
        self.prepare()
        path = self.output / "hal-1.1.tar.gz"
        path.chmod(0o644)
        path.write_bytes(archive({"hal-1.1/hal.cabal": b"version: 1.1\n"}))
        self.no_upload()

    def test_sparse_member_is_rejected_before_extraction(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as package:
            manifest = b"name: hal\nversion: 1.1\n"
            member = tarfile.TarInfo("hal-1.1/hal.cabal")
            member.size = len(manifest)
            package.addfile(member, io.BytesIO(manifest))
            sparse = tarfile.TarInfo("hal-1.1/sparse")
            sparse.size = 1
            sparse.pax_headers = {"GNU.sparse.map": "262143,1", "GNU.sparse.size": "262144"}
            package.addfile(sparse, io.BytesIO(b"x"))
        destination = self.root / "sparse-extract"
        # A small tar must not expand a 256 KiB logical member past this bound.
        with mock.patch.object(release, "MAX_TAR", 16384):
            with self.assertRaisesRegex(ValueError, "sparse"):
                release.unpack(output.getvalue(), destination, "1.1")
        self.assertFalse(destination.exists())

    def test_changed_retained_plan_blocks_upload(self):
        self.prepare()
        (self.output / "stack.yaml").write_text("changed")
        self.no_upload()

    def test_missing_retained_plan_blocks_upload(self):
        self.prepare()
        (self.output / "stack.yaml").unlink()
        self.no_upload()

    def test_symlink_retained_plan_blocks_upload(self):
        self.prepare()
        path = self.output / "stack.yaml"
        path.unlink()
        path.symlink_to(self.source / "stack.yaml")
        self.no_upload()

    def test_changed_retained_lock_blocks_upload(self):
        (self.source / "stack.yaml.lock").write_text("original lock")
        self.prepare()
        (self.output / "stack.yaml.lock").write_text("changed lock")
        self.no_upload()

    def test_missing_retained_lock_blocks_upload(self):
        (self.source / "stack.yaml.lock").write_text("original lock")
        self.prepare()
        (self.output / "stack.yaml.lock").unlink()
        self.no_upload()

    def test_unexpected_dangling_lock_blocks_upload(self):
        self.prepare()
        (self.output / "stack.yaml.lock").symlink_to("missing-lock")
        self.no_upload()

    def test_verified_retained_lock_allows_upload(self):
        (self.source / "stack.yaml.lock").write_text("original lock")
        self.prepare()
        release.exercise_upload(self.output, "v1.1", self.sink)
        self.assertEqual(self.sink.read_bytes(), self.data)

    def test_wrong_upload_tag(self):
        self.prepare()
        self.no_upload("v1.2")

    def test_missing_gate_receipt(self):
        self.prepare()
        path = self.output / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt["gates"].remove("archive-test")
        path.write_text(json.dumps(receipt))
        self.no_upload()

    def test_path_in_receipt(self):
        self.prepare()
        path = self.output / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt["artifact"] = "../hal-1.1.tar.gz"
        path.write_text(json.dumps(receipt))
        self.no_upload()

    def test_symlink_artifact(self):
        self.prepare()
        path = self.output / "hal-1.1.tar.gz"
        moved = self.output / "moved.tar.gz"
        path.rename(moved)
        path.symlink_to(moved)
        self.no_upload()

    def test_existing_output_is_never_reused(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.no_upload()

    def test_extra_sdist_or_revision(self):
        self.extra = True
        with self.assertRaises(ValueError):
            self.prepare()
        self.no_upload()

    def test_wrong_archive_version(self):
        self.data = archive({"hal-1.1/hal.cabal": b"version: 1.2\n"})
        with self.assertRaises(ValueError):
            self.prepare()
        self.no_upload()

    def test_unsafe_member(self):
        self.data = archive({"hal-1.1/../outside": b"payload"})
        with self.assertRaises(ValueError):
            self.prepare()
        self.no_upload()

    def test_truncated_archive(self):
        self.data = self.data[:-8]
        with self.assertRaises((ValueError, EOFError)):
            self.prepare()
        self.no_upload()

    def test_sink_failure_propagates(self):
        self.prepare()
        with mock.patch.object(release.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "sink")):
            with self.assertRaises(subprocess.CalledProcessError):
                release.exercise_upload(self.output, "v1.1", self.sink)

    def test_no_publication_command(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/release/prepare.py"), "publish"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_snapshot_consumed_even_if_path_changes_after_read(self):
        self.prepare()
        def sink(command, *, input, check):
            path = self.output / "hal-1.1.tar.gz"
            path.chmod(0o644)
            path.write_bytes(b"replacement after verified read")
            self.assertEqual(input, self.data)
            self.sink.write_bytes(input)
        with mock.patch.object(release.subprocess, "run", side_effect=sink):
            release.exercise_upload(self.output, "v1.1", self.sink)
        self.assertEqual(self.sink.read_bytes(), self.data)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / "bin"
        self.binary = b"synthetic executable; never executed"
        self.data = archive({f"stack-{installer.VERSION}-linux-x86_64/stack": self.binary})
        self.sha = hashlib.sha256(self.data).hexdigest()
        patcher = mock.patch.object(installer, "SHA256", self.sha)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checksum = f"{self.sha}  {installer.ARCHIVE}\n".encode()

    def fetch(self, url, limit):
        self.assertTrue(url.startswith("https://github.com/commercialhaskell/stack/releases/download/v3.7.1/"))
        return self.checksum if url.endswith(".sha256") else self.data

    def rejects(self):
        with self.assertRaises((ValueError, UnicodeError, EOFError, tarfile.TarError, OSError)):
            installer.install(self.destination, fetch=self.fetch)
        self.assertFalse(self.destination.exists())

    def test_install_only_after_verification(self):
        installer.install(self.destination, fetch=self.fetch)
        self.assertEqual((self.destination / "stack").read_bytes(), self.binary)
        self.assertEqual((self.destination / "stack").stat().st_mode & 0o777, 0o755)

    def test_missing_checksum(self):
        self.checksum = b""
        self.rejects()

    def test_truncated_checksum(self):
        self.checksum = self.checksum[:40]
        self.rejects()

    def test_wrong_checksum_filename(self):
        self.checksum = f"{self.sha}  different.tar.gz\n".encode()
        self.rejects()

    def test_changed_upstream_digest(self):
        self.checksum = f"{'0' * 64}  {installer.ARCHIVE}\n".encode()
        self.rejects()

    def test_tampered_archive(self):
        self.data += b"tamper"
        self.rejects()

    def test_truncated_archive(self):
        self.data = self.data[:-10]
        self.rejects()

    def test_missing_archive(self):
        self.data = b""
        self.rejects()

    def test_download_failure(self):
        with self.assertRaises(OSError):
            installer.install(self.destination, fetch=mock.Mock(side_effect=OSError("download failed")))
        self.assertFalse(self.destination.exists())

    def test_valid_digest_but_missing_binary(self):
        self.data = archive({"unrelated": b"contents"})
        sha = hashlib.sha256(self.data).hexdigest()
        self.checksum = f"{sha}  {installer.ARCHIVE}\n".encode()
        with mock.patch.object(installer, "SHA256", sha):
            self.rejects()


class WorkflowTests(unittest.TestCase):
    def test_workflow_cannot_publish_on_tag(self):
        text = (ROOT / ".github/workflows/publish.yaml").read_text()
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        for forbidden in ["secrets.", "HACKAGE_", "stack upload", "exercise-upload"]:
            self.assertNotIn(forbidden, text)
        for line in text.splitlines():
            if "uses:" in line and "./" not in line:
                self.assertRegex(line, r"@[0-9a-f]{40}(?: |$)")


if __name__ == "__main__":
    unittest.main()
