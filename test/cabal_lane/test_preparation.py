"""Preparation receipts reject changed inputs before executing any subprocess."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.github/cabal-lane'))
SPEC = importlib.util.spec_from_file_location('lane_run', ROOT / '.github/cabal-lane/run.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.lane = M.Lane(self.work)
        for root in set(self.lane.projects.values()):
            root.mkdir()
            (root / 'cabal.project').write_text('packages: .\n')
            (root / 'tiny.cabal').write_text('build-type: Simple\n')
        (self.work / 'home').mkdir()
        (self.work / 'cabal').mkdir()
        (self.work / 'cabal/config').write_text('active-repositories: :none\n')
        self.receipt = {'inputs_sha256': M.sha256((M.HERE / 'inputs.json').read_bytes()),
                        'local_inputs': self.lane.input_snapshot()}
        M.write_json(self.work / 'prepared.json', self.receipt)

    def tearDown(self):
        self.temp.cleanup()

    def assert_rejected_before_execution(self):
        with patch.object(self.lane, 'identity') as identity, \
                patch.object(self.lane, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                self.lane.build()
            identity.assert_not_called()
            command.assert_not_called()
        self.assertFalse((self.work / 'build-start.json').exists())

    def test_added_overrides_in_every_project_are_rejected(self):
        for label, root in self.lane.projects.items():
            filename = 'cabal.examples.project.local' if label == 'examples' else 'cabal.project.local'
            with self.subTest(project=label):
                path = root / filename
                path.write_text('packages: extra\n')
                self.assert_rejected_before_execution()
                path.unlink()

    def test_changed_project_manifest_freeze_and_source_are_rejected(self):
        root = self.lane.source
        for name in ('cabal.project', 'tiny.cabal', 'cabal.project.freeze', 'Setup.hs'):
            with self.subTest(name=name):
                path = root / name
                original = path.read_bytes() if path.exists() else None
                path.write_text('changed input\n')
                self.assert_rejected_before_execution()
                if original is None: path.unlink()
                else: path.write_bytes(original)

    def test_config_and_home_additions_are_rejected(self):
        path = self.work / 'cabal/config'
        original = path.read_bytes()
        path.write_text('allow-newer: all\n')
        self.assert_rejected_before_execution()
        path.write_bytes(original)
        (self.work / 'home/.ghc.environment').write_text('unexpected\n')
        self.assert_rejected_before_execution()

    def test_removal_and_symlinks_are_rejected(self):
        p = self.lane.source / 'tiny.cabal'
        p.unlink()
        self.assert_rejected_before_execution()
        p.symlink_to(self.lane.projects['hpack'] / 'tiny.cabal')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.lane.input_snapshot()

    def test_only_known_build_output_roots_are_excluded(self):
        root = self.lane.source
        (root / 'dist-hal').mkdir()
        (root / 'dist-hal/plan-output').write_text('generated\n')
        self.assertEqual(self.receipt['local_inputs'], self.lane.input_snapshot())
        (root / 'dist-unexpected').mkdir()
        (root / 'dist-unexpected/tiny.cabal').write_text('build-type: Custom\n')
        self.assert_rejected_before_execution()

    def test_each_command_rechecks_inputs_before_execution(self):
        self.lane.accepted_inputs = self.receipt['local_inputs']
        (self.lane.projects['hpack'] / 'cabal.project.local').write_text('packages: extra\n')
        with patch.object(M.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                self.lane.command('must-not-run', ['cabal', 'build'])
            run.assert_not_called()

    def test_bootstrap_flags_cover_tool_dependency_scopes(self):
        text = (M.HERE / 'hpack/cabal.project.freeze').read_text()
        flags = re.findall(r'^\s+(\S+)\s+[+-][^\n]+', text, re.M)
        self.assertTrue(flags)
        self.assertTrue(all(name.startswith('any.') for name in flags), flags)
        self.assertIn('any.zlib -bundled-c-zlib +non-blocking-ffi -pkg-config', text)


if __name__ == '__main__': unittest.main()
