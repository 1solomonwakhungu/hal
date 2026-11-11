import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'.github/stack-backport'))
from ci import compiler_prefix


class CompilerPrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.prefix = self.root/'installed'
        self.lib = self.prefix/'lib/ghc-9.10.3/lib'
        self.lib.mkdir(parents=True)
        self.installed = self.prefix/'bin/ghc'
        self.installed.parent.mkdir()
        self.installed.write_text('synthetic compiler')
        self.shim = self.root/'ghcup/bin/ghc'
        self.shim.parent.mkdir(parents=True)
        self.shim.symlink_to(self.installed)

    def test_shim_selects_installation_not_shim_root(self):
        with mock.patch('ci.subprocess.check_output', return_value=str(self.lib)+'\n') as query:
            self.assertEqual(compiler_prefix(self.shim), self.prefix)
            self.assertNotEqual(self.shim.parent.parent, self.prefix)
            self.assertEqual([call.args[0][0] for call in query.call_args_list],
                             [str(self.shim), str(self.installed)])

    def test_direct_installation_is_unchanged(self):
        with mock.patch('ci.subprocess.check_output', return_value=str(self.lib)+'\n'):
            self.assertEqual(compiler_prefix(self.installed), self.prefix)

    def test_unknown_layout_rejected(self):
        with mock.patch('ci.subprocess.check_output', return_value=str(self.prefix/'lib')):
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                compiler_prefix(self.shim)

    def test_relative_or_empty_libdir_rejected(self):
        for value in ('', 'lib/ghc-9.10.3/lib'):
            with self.subTest(value=value), mock.patch('ci.subprocess.check_output', return_value=value):
                with self.assertRaisesRegex(ValueError, 'absolute'):
                    compiler_prefix(self.shim)

    def test_installed_compiler_disagreement_rejected(self):
        with mock.patch('ci.subprocess.check_output', side_effect=[str(self.lib), str(self.root/'another/lib/ghc-9.10.3/lib')]):
            with self.assertRaisesRegex(ValueError, 'disagree'):
                compiler_prefix(self.shim)

    def test_escaping_installed_launcher_rejected(self):
        outside = self.root/'outside'
        outside.write_text('other compiler')
        self.installed.unlink()
        self.installed.symlink_to(outside)
        with mock.patch('ci.subprocess.check_output', return_value=str(self.lib)):
            with self.assertRaisesRegex(ValueError, 'disagree'):
                compiler_prefix(self.shim)

    def test_query_does_not_inherit_credentials_or_package_overrides(self):
        with mock.patch.dict(os.environ, {'PATH': '/tools', 'GITHUB_TOKEN': 'synthetic',
                                        'GHC_PACKAGE_PATH': '/personal'}, clear=True), \
                mock.patch('ci.subprocess.check_output', return_value=str(self.lib)) as query:
            compiler_prefix(self.shim)
            for call in query.call_args_list:
                self.assertEqual(call.kwargs['env'], {'PATH': '/tools', 'GHC_ENVIRONMENT': '-'})


if __name__ == '__main__':
    unittest.main()
