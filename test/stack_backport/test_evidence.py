import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'.github/stack-backport'))
from evidence import check_link, routes


class ExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.library = self.root/'build/libHSCabal-3.12.1.0-inplace.a'
        self.library.parent.mkdir()
        self.library.write_bytes(b'patched library')
        self.receipt = {'artifacts': {'build/'+self.library.name: self.sha(self.library)}}
        (self.root/'preparation.json').write_text(json.dumps(self.receipt))
        self.link = self.root/'setup-links/one'
        self.link.mkdir(parents=True)
        self.mapping = self.link/'link.map'
        self.mapping.write_text(str(self.library)+'(Configure.o)\n')
        self.setup = self.root/'temporary/setup'
        self.setup.parent.mkdir()
        self.setup.write_bytes(b'linked setup')
        (self.link/'setup').write_bytes(self.setup.read_bytes())
        (self.link/'receipt.json').write_text(json.dumps({
            'exit': 0, 'output': str(self.setup), 'sha256': self.sha(self.setup)}))
        self.log = '[debug] Run process within /package: '+str(self.setup)+' configure\n'

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_patched_library_identity(self):
        self.assertEqual(check_link(self.root, self.mapping)['library_sha256'], self.sha(self.library))

    def test_same_version_affected_library_rejected(self):
        self.mapping.write_text('/original/libHSCabal-3.12.1.0-2849.a(Configure.o)\n')
        with self.assertRaisesRegex(ValueError, 'exclusively'):
            check_link(self.root, self.mapping)

    def test_both_libraries_rejected(self):
        self.mapping.write_text(self.mapping.read_text()+'/original/libHSCabal-3.12.1.0-2849.a(Simple.o)\n')
        with self.assertRaises(ValueError):
            check_link(self.root, self.mapping)

    def test_library_bytes_changed(self):
        self.library.write_bytes(b'other library')
        with self.assertRaisesRegex(ValueError, 'archive changed'):
            check_link(self.root, self.mapping)

    def test_missing_link_identity_rejected(self):
        self.mapping.write_text('unrelated libHSbase.a\n')
        with self.assertRaises(ValueError):
            check_link(self.root, self.mapping)

    def test_live_and_removed_temporary_setup_bind(self):
        self.assertEqual(len(routes(self.root, self.log)), 1)
        self.setup.unlink()
        self.assertEqual(len(routes(self.root, self.log)), 1)

    def test_changed_live_setup_rejected(self):
        self.setup.write_bytes(b'affected setup')
        with self.assertRaisesRegex(ValueError, 'unbound'):
            routes(self.root, self.log)

    def test_changed_retained_setup_rejected(self):
        (self.link/'setup').write_bytes(b'other setup')
        with self.assertRaisesRegex(ValueError, 'retained setup'):
            routes(self.root, self.log)

    def test_unknown_execution_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unbound'):
            routes(self.root, self.log.replace('/temporary/setup', '/unknown/setup'))

    def test_nonsetup_commands_are_not_setup_evidence(self):
        self.assertEqual(routes(self.root, '[debug] Run process within /work: ghc --info\n'), [])


if __name__ == '__main__':
    unittest.main()
