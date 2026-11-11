import importlib.util
import io
import os
import tarfile
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'.github/stack-backport'))
from cache_guard import digest, snapshot, verify, project_setups, require_enrollment_evidence
from prepare import extract
import run as launcher
import probe


class LauncherBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root/'prepared'
        self.work.mkdir()
        (self.work/'prefix').mkdir()
        (self.work/'preparation.json').write_text(json.dumps({'artifacts': {}, 'symlinks': {}}))
        self.stack = self.root/'stack'
        self.stack.write_bytes(b'synthetic tool')
        self.project = self.root/'project'
        self.project.mkdir()

    def test_nested_example_setup_is_rejected_before_execution(self):
        setup = self.project/'examples/.stack-work/dist/setup/setup'
        setup.parent.mkdir(parents=True)
        setup.write_bytes(b'affected cached setup')
        def identity(path):
            if Path(path) == self.stack:
                return '14e7040353c7ae3471d6420cfc0005e1cd83973c25f4edab42b029c93ae4fdb3'
            return digest(path)
        with mock.patch.object(launcher, 'digest', side_effect=identity), \
                mock.patch.object(launcher.platform, 'system', return_value='Linux'), \
                mock.patch.object(launcher.platform, 'machine', return_value='aarch64'), \
                mock.patch.object(launcher.subprocess, 'Popen') as execute:
            with self.assertRaisesRegex(ValueError, 'unreceipted build cache'):
                launcher.execute(self.work, self.stack, ['build'], self.project)
            execute.assert_not_called()

    def test_added_compiler_symlink_is_rejected_before_execution(self):
        (self.work/'prefix/ghc').symlink_to(self.stack)
        with mock.patch.object(launcher.subprocess, 'Popen') as execute:
            with self.assertRaisesRegex(ValueError, 'symlinks changed'):
                launcher.execute(self.work, self.stack, ['build'], self.project)
            execute.assert_not_called()

    def test_header_control_uses_owned_home_and_no_inherited_credentials(self):
        with mock.patch.dict(os.environ, {'PATH': '/tools', 'HOME': '/personal',
                'GHCRTS': 'bad', 'GHC_PACKAGE_PATH': '/personal/packages',
                'AWS_SECRET_ACCESS_KEY': 'synthetic', 'GITHUB_TOKEN': 'synthetic'}, clear=True):
            self.assertEqual(probe.environment(self.work), {
                'PATH': '/tools', 'HOME': str(self.work/'home'), 'GHC_ENVIRONMENT': '-'})

    def test_logging_overrides_rejected_before_execution(self):
        for flag in ['--verbosity=error', '--verbosity', '--silent', '--color=always',
                     '--colour', '--terminal', '--no-time-in-log', '--rsl-in-log',
                     '--stack-colors', '--terminal-width', '--no-dump-logs']:
            with self.subTest(flag=flag), mock.patch.object(launcher.subprocess, 'Popen') as execute:
                with self.assertRaisesRegex(ValueError, 'logging override'):
                    launcher.execute(self.work, self.stack, ['build', flag], self.project)
                execute.assert_not_called()

    def test_symlinked_dist_directory_is_rejected(self):
        target = self.root/'retained-dist/setup'
        target.mkdir(parents=True)
        (target/'setup').write_bytes(b'affected setup')
        cache = self.project/'examples/.stack-work'
        cache.mkdir(parents=True)
        (cache/'dist').symlink_to(target.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinked project/cache directory'):
            project_setups(self.project)

    def test_symlinked_cache_root_is_rejected(self):
        (self.project/'.stack-work').symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinked project/cache directory'):
            project_setups(self.project)

    def test_symlinked_nested_project_is_rejected(self):
        (self.project/'examples').symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinked project/cache directory'):
            project_setups(self.project)

    def test_dangling_cache_alias_is_rejected(self):
        (self.project/'.stack-work').symlink_to(self.root/'absent', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinked project/cache directory'):
            project_setups(self.project)

    def test_new_enrollment_requires_observed_hash(self):
        with self.assertRaisesRegex(ValueError, 'lacks invocation evidence'):
            require_enrollment_evidence({}, {'setup': 'new'}, [])
        require_enrollment_evidence({}, {'setup': 'new'}, [{'sha256': 'new'}])

    def test_changed_enrollment_requires_observed_hash(self):
        with self.assertRaisesRegex(ValueError, 'lacks invocation evidence'):
            require_enrollment_evidence({'setup': 'old'}, {'setup': 'new'}, [{'sha256': 'old'}])

    def test_no_new_setup_needs_no_execution_evidence(self):
        require_enrollment_evidence({}, {}, [])
        require_enrollment_evidence({'setup': 'same'}, {'setup': 'same'}, [])


class SetupCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'cache'
        self.root.mkdir()
        self.exe = self.root/'Cabal-simple_3.12.1.0'
        self.exe.write_bytes(b'qualified setup')
        self.receipt = self.root.parent/'receipt.json'
        self.identities = {'source': 'pinned', 'library': 'patched'}
        self.receipt.write_text(json.dumps({'identities': self.identities, 'setup_cache': snapshot(self.root)}))

    def test_warm_identity(self):
        verify(self.root, self.receipt, self.identities)

    def test_same_version_affected_executable_rejected(self):
        self.exe.write_bytes(b'affected setup, same version')
        with self.assertRaisesRegex(ValueError, 'before invoking Stack'):
            verify(self.root, self.receipt, self.identities)

    def test_library_change_rejected(self):
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            verify(self.root, self.receipt, dict(self.identities, library='different'))

    def test_unexpected_setup_rejected(self):
        (self.root/'another-setup').write_bytes(b'unknown')
        with self.assertRaises(ValueError):
            verify(self.root, self.receipt, self.identities)

    def test_missing_setup_rejected(self):
        self.exe.unlink()
        with self.assertRaises(ValueError):
            verify(self.root, self.receipt, self.identities)

    def test_symlink_rejected(self):
        self.exe.unlink()
        self.exe.symlink_to(self.receipt)
        with self.assertRaises(ValueError):
            verify(self.root, self.receipt, self.identities)

    def test_unreceipted_empty_cache_rejected(self):
        self.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            verify(self.root, self.receipt, self.identities)


class SourceExtractionTests(unittest.TestCase):
    def unpack(self, members):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root/'source.tar'
            with tarfile.open(archive, 'w') as tar:
                for name, kind in members:
                    item = tarfile.TarInfo('revision/'+name)
                    if kind == 'link':
                        item.type = tarfile.SYMTYPE
                        item.linkname = '/outside'
                        tar.addfile(item)
                    else:
                        item.size = 7
                        tar.addfile(item, io.BytesIO(b'notice!'))
            extract(archive, root/'out')
            return {str(p.relative_to(root/'out')): p.read_bytes()
                    for p in (root/'out').rglob('*') if p.is_file()}

    def test_root_and_library_notices_retained(self):
        result = self.unpack([('AUTHORS', 'file'), ('LICENSE', 'file'), ('Cabal/LICENSE', 'file')])
        self.assertEqual(set(result), {'AUTHORS', 'LICENSE', 'Cabal/LICENSE'})

    def test_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsafe archive path'):
            self.unpack([('Cabal/../../outside', 'file')])

    def test_source_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            self.unpack([('Cabal/link', 'link')])

    def test_duplicate_source_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.unpack([('Cabal/LICENSE', 'file'), ('Cabal/LICENSE', 'file')])


if __name__ == '__main__':
    unittest.main()
