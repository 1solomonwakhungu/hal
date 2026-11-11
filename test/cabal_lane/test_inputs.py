"""Regression checks for build-input and setup-route boundaries."""
import copy
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import io

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('lane_inputs', ROOT / '.github/cabal-lane/inputs.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'local.cabal').write_text('cabal-version: 2.4\nbuild-type: Simple\n')
        self.plan = {'cabal-version': '3.18.2.0', 'cabal-lib-version': '3.18.2.0',
                     'compiler-id': 'ghc-9.10.3', 'os': 'linux', 'arch': 'aarch64',
                     'install-plan': [
                         {'id': 'base-unit', 'pkg-name': 'base', 'pkg-version': '4.20.2.0',
                          'type': 'pre-existing', 'depends': []},
                         {'id': 'local-unit', 'pkg-name': 'local', 'pkg-version': '1',
                          'type': 'configured', 'component-name': 'lib', 'style': 'local',
                          'flags': {}, 'pkg-src': {'type': 'local', 'path': str(self.root)},
                          'depends': ['base-unit'], 'exe-depends': []}]}
        self.pins = {'plans': {'hal': M.plan_digest(self.plan)}, 'sources': {}}

    def tearDown(self):
        self.temp.cleanup()

    def test_original_supported_graph(self):
        M.verify_plan(self.plan, self.pins, 'hal')

    def test_architecture_and_unit_ids_do_not_hide_graph_changes(self):
        self.plan['arch'] = 'x86_64'
        self.plan['install-plan'][0]['id'] = 'different-abi-id'
        self.plan['install-plan'][1]['depends'] = ['different-abi-id']
        M.verify_plan(self.plan, self.pins, 'hal')
        self.plan['install-plan'][0]['pkg-version'] = '4.20.1.0'
        with self.assertRaises(ValueError): M.verify_plan(self.plan, self.pins, 'hal')

    def test_rejects_missing_or_extra_units_and_changed_flags(self):
        for mode in ('missing', 'extra', 'flags', 'tool-edge'):
            plan = copy.deepcopy(self.plan)
            if mode == 'missing': plan['install-plan'].pop()
            if mode == 'extra': plan['install-plan'].append({'id': 'extra', 'pkg-name': 'extra',
                'pkg-version': '1', 'type': 'pre-existing', 'depends': []})
            if mode == 'flags': plan['install-plan'][1]['flags'] = {'new': True}
            if mode == 'tool-edge': plan['install-plan'][1]['exe-depends'] = ['base-unit']
            with self.subTest(mode=mode), self.assertRaises(ValueError): M.verify_plan(plan, self.pins, 'hal')

    def test_rejects_unresolved_and_duplicate_edges(self):
        for deps in (['missing'], ['base-unit', 'base-unit'], 'base-unit'):
            self.plan['install-plan'][1]['depends'] = deps
            with self.assertRaises((ValueError, KeyError)): M.plan_digest(self.plan)

    def test_rejects_unknown_platform_client_and_compiler(self):
        for key, value in [('arch', 'AMD64'), ('os', 'windows'), ('compiler-id', 'ghc-9.14.1'),
                           ('cabal-version', '3.14.1.1'), ('cabal-lib-version', '3.12.1.0')]:
            plan = copy.deepcopy(self.plan); plan[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): M.plan_digest(plan)

    def test_rejects_duplicate_id_and_logical_unit(self):
        for different_id in (False, True):
            plan = copy.deepcopy(self.plan); duplicate = copy.deepcopy(plan['install-plan'][0])
            if different_id: duplicate['id'] = 'another'
            plan['install-plan'].append(duplicate)
            with self.assertRaises(ValueError): M.plan_digest(plan)

    def test_custom_route_rejected_even_with_matching_graph_pin(self):
        (self.root / 'local.cabal').write_text('build-type: Custom\n')
        with self.assertRaises(ValueError): M.verify_plan(self.plan, self.pins, 'hal')

    def test_setup_component_rejected_even_with_matching_graph_pin(self):
        self.plan['install-plan'][1]['components'] = {'setup': {'depends': ['base-unit'], 'exe-depends': []}}
        self.pins['plans']['hal'] = M.plan_digest(self.plan)
        with self.assertRaises(ValueError): M.verify_plan(self.plan, self.pins, 'hal')

    def test_setup_dependencies_are_in_graph_digest(self):
        old = M.plan_digest(self.plan)
        self.plan['install-plan'][1]['components'] = {'setup': {'depends': ['base-unit'], 'exe-depends': []}}
        self.assertNotEqual(old, M.plan_digest(self.plan))

    def test_legacy_whole_package_simple_representation_is_supported(self):
        self.plan['install-plan'][1]['components'] = {'lib': {'depends': ['base-unit'], 'exe-depends': []}}
        self.pins['plans']['hal'] = M.plan_digest(self.plan)
        M.verify_plan(self.plan, self.pins, 'hal')

    def test_duplicate_json_keys_rejected(self):
        p = self.root / 'plan.json'; p.write_text('{"install-plan": [], "install-plan": [1]}')
        with self.assertRaises(ValueError): M.read_json(p)

    def test_manifest_type_must_be_explicit_and_unambiguous(self):
        for value in (b'name: x\n', b'build-type: Simple\nbuild-type: Custom\n'):
            with self.assertRaises(ValueError): M.build_type(value)

    def test_index_revision_hash_and_build_type_required(self):
        data = b'build-type: Simple\n'; pin = {'name': 'pkg', 'version': '1',
                'manifest_sha256': M.sha256(data), 'build_type': 'Simple'}
        pins = {'sources': {'pkg-1': pin}}
        with tarfile.open(self.root / '01-index.tar', 'w') as archive:
            item = tarfile.TarInfo('pkg/1/pkg.cabal'); item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        M.verify_manifests(self.root, pins, self.root / 'manifests')
        pin['build_type'] = 'Custom'
        with self.assertRaises(ValueError): M.verify_manifests(self.root, pins, self.root / 'manifests')
        pin['manifest_sha256'] = '0' * 64
        with self.assertRaises(ValueError): M.verify_manifests(self.root, pins, self.root / 'manifests')

    def test_archive_missing_and_tampered_fail_without_fetch(self):
        data = b'synthetic source'; p = self.root / 'pkg/1/pkg-1.tar.gz'
        pins = {'sources': {'pkg-1': {'name': 'pkg', 'version': '1', 'source_sha256': M.sha256(data)}}}
        with self.assertRaises(ValueError): M.fetch_sources(self.root, pins)
        p.parent.mkdir(parents=True); p.write_bytes(data)
        M.fetch_sources(self.root, pins)
        p.write_bytes(b'tampered')
        with self.assertRaises(ValueError): M.fetch_sources(self.root, pins)


if __name__ == '__main__': unittest.main()
