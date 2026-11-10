"""Offline regression tests; HSEC_DATABASE must name the verified pinned database."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('scan', ROOT / 'scripts/security/scan.py')
scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scan)
FIXTURES = Path(__file__).parent / 'fixtures'
FILES = {'cabal': 'cabal-plan.json', 'stack-test': 'stack-dependencies.json',
         'stack-examples': 'stack-examples-dependencies.json'}
DB = Path(os.environ['HSEC_DATABASE'])


class ScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan.database(DB)

    def test_all_pinned_records_and_boundary_ranges(self):
        self.assertEqual(len(self.records), 39)
        self.assertEqual(len({r['advisory']['id'] for r in self.records}), 39)
        for record in self.records:
            for affected in record['affected']:
                for interval in affected['versions']:
                    with self.subTest(id=record['advisory']['id'], affected=affected, interval=interval):
                        one = copy.deepcopy(record)
                        # Isolate each affected row/range so another interval cannot mask a boundary bug.
                        row = copy.deepcopy(affected); row['versions'] = [interval]
                        one['affected'] = [row]
                        name = row.get('package', 'unused')
                        introduced = interval['introduced']
                        self.assertEqual(len(scan.matches([one], {(name, introduced)}, introduced, 'linux', 'aarch64')), 1)
                        if 'fixed' in interval:
                            fixed = interval['fixed']
                            self.assertEqual(scan.matches([one], {(name, fixed)}, fixed, 'linux', 'aarch64'), [])
                        if introduced != '0':
                            self.assertEqual(scan.matches([one], {(name, '0')}, '0', 'linux', 'aarch64'), [])

    def ids(self, package, version):
        return {r['advisory'] for r in scan.matches(self.records, {(package, version)}, '9.10.3', 'linux', 'aarch64')}

    def test_numeric_affected_and_fixed_versions(self):
        for v in ['0.12.0.0', '2.1.2.1', '2.2.5.0']:
            self.assertIn('HSEC-2026-0007', self.ids('aeson', v))
        self.assertNotIn('HSEC-2026-0007', self.ids('aeson', '2.2.5.1'))
        self.assertIn('HSEC-2023-0001', self.ids('aeson', '1.5.6.0'))
        self.assertNotIn('HSEC-2023-0001', self.ids('aeson', '2.0.1.0'))
        self.assertIn('HSEC-2026-0011', self.ids('scientific', '0.3.8.0'))
        self.assertIn('HSEC-2026-0012', self.ids('scientific', '0.3.9.0'))
        self.assertEqual(self.ids('scientific', '0.3.10.0'), set())

    def test_compiler_multiple_ranges_and_gaps(self):
        def ids(v):
            return {x['advisory'] for x in scan.matches(self.records, set(), v, 'linux', 'x86_64')}
        for v in ['9.2.1', '9.6.5', '9.8.1', '9.10.1']:
            self.assertIn('HSEC-2024-0008', ids(v))
        for v in ['9.6.6', '9.8.0', '9.8.3', '9.10.0', '9.10.2', '9.10.3']:
            self.assertNotIn('HSEC-2024-0008', ids(v))
        self.assertIn('HSEC-2025-0001', ids('9.12.3'))
        self.assertNotIn('HSEC-2025-0001', ids('9.12.2'))

    def test_platform_constraints_report_without_false_applicability(self):
        for os_name in ['linux', 'mingw32']:
            found = scan.matches(self.records, {('process', '1.6.18.0')}, '9.10.3', os_name, 'x86_64')
            row = next(x for x in found if x['advisory'] == 'HSEC-2024-0003')
            self.assertEqual(row['platform_applicable'], os_name == 'mingw32')
        doc = copy.deepcopy(self.records[0]); doc['affected'][0]['arch'] = ['x86_64']
        a = doc['affected'][0]; v = a['versions'][0]['introduced']
        for arch in ['aarch64', 'x86_64']:
            self.assertEqual(scan.matches([doc], {(a['package'], v)}, '9.10.3', 'linux', arch)[0]['platform_applicable'], arch == 'x86_64')

    def test_unknown_advisory_platform_tokens_fail_before_matching(self):
        source = (DB / 'HSEC-2026-0007.md').read_text()
        for field, values in [('os', ['linuz']), ('arch', ['future-unrecognized-arch']),
                              ('os', ['Linux']), ('arch', ['AArch64']),
                              ('os', ['linux', 'linuz']), ('arch', ['aarch64', 'unknown'])]:
            changed = source.replace('package = "aeson"',
                                     'package = "aeson"\n' + field + ' = ' + json.dumps(values), 1)
            with self.subTest(field=field, values=values), self.assertRaises(scan.Invalid):
                scan.parse_advisory(changed, 'HSEC-2026-0007')

    def test_upstream_platform_vocabulary_and_applicability(self):
        # Independent fixture of the pinned upstream parser's accepted vocabulary.
        vocabulary = {
            'os': 'darwin freebsd linux linux-android mingw32 netbsd openbsd'.split(),
            'arch': ('aarch64 alpha arm hppa hppa1_1 i386 ia64 m68k mips mipseb '
                     'mipsel nios2 powerpc powerpc64 powerpc64le riscv32 riscv64 '
                     'rs6000 s390 s390x sh4 sparc sparc64 vax x86_64').split(),
        }
        source = (DB / 'HSEC-2026-0007.md').read_text()
        for field, values in vocabulary.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    changed = source.replace('package = "aeson"',
                                             'package = "aeson"\n' + field + ' = ' + json.dumps([value]), 1)
                    doc = scan.parse_advisory(changed, 'HSEC-2026-0007')
                    found = scan.matches([doc], {('aeson', '2.1.2.1')}, '9.10.3', 'linux', 'aarch64')
                    self.assertEqual(len(found), 1)
                    self.assertEqual(found[0]['platform_applicable'],
                                     value == ('linux' if field == 'os' else 'aarch64'))

    def test_conditional_api_annotations_never_suppress(self):
        found = scan.matches(self.records, {('jwt', '0.11.0')}, '9.10.3', 'linux', 'aarch64')
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]['platform_applicable'])
        self.assertIn('api', found[0]['constraints'])
        self.assertIn('declarations', found[0]['constraints'])

    def test_all_three_saved_plans_and_remaining_matches(self):
        for kind, filename in FILES.items():
            with self.subTest(kind=kind):
                packages, count = scan.inventory(FIXTURES / filename, kind, '9.10.3', 'linux', 'aarch64')
                self.assertEqual(count, {'cabal': 136, 'stack-test': 126, 'stack-examples': 101}[kind])
                packages.add(('Cabal', '3.12.1.0'))
                found = scan.matches(self.records, packages, '9.10.3', 'linux', 'aarch64')
                expected = {('HSEC-2023-0007', 'base', '4.20.2.0'), ('HSEC-2026-0006', 'Cabal', '3.12.1.0')}
                if kind == 'cabal':
                    expected.add(('HSEC-2026-0006', 'Cabal', '3.14.1.1'))
                self.assertEqual({(x['advisory'], x['name'], x['version']) for x in found}, expected)

    def check_inventory(self, doc, kind):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plan.json'; path.write_text(json.dumps(doc))
            return scan.inventory(path, kind, '9.10.3', 'linux', 'aarch64')

    def test_negative_real_plans_keep_affected_versions_visible(self):
        for kind, filename in FILES.items():
            doc = scan.read_json(FIXTURES / filename)
            for unit in doc['install-plan'] if kind == 'cabal' else doc:
                name_key, ver_key = ('pkg-name', 'pkg-version') if kind == 'cabal' else ('name', 'version')
                if unit[name_key] in {'aeson', 'scientific'}:
                    unit[ver_key] = '2.1.2.1' if unit[name_key] == 'aeson' else '0.3.9.0'
                    if kind != 'cabal':
                        unit['location']['url'] = f'https://hackage.haskell.org/package/{unit[name_key]}-{unit[ver_key]}'
            packages, _ = self.check_inventory(doc, kind)
            ids = {x['advisory'] for x in scan.matches(self.records, packages, '9.10.3', 'linux', 'aarch64')}
            self.assertTrue({'HSEC-2026-0007', 'HSEC-2026-0012'} <= ids)

    def test_every_unit_removal_is_rejected(self):
        for kind, filename in FILES.items():
            original = scan.read_json(FIXTURES / filename)
            units = original['install-plan'] if kind == 'cabal' else original
            for i in range(len(units)):
                doc = copy.deepcopy(original)
                del (doc['install-plan'] if kind == 'cabal' else doc)[i]
                with self.subTest(kind=kind, removed=i), self.assertRaises(scan.Invalid):
                    self.check_inventory(doc, kind)

    def test_every_dependency_edge_removal_is_rejected(self):
        # Contract comparison catches even deletions that leave a closed graph.
        for kind, filename in FILES.items():
            original = scan.read_json(FIXTURES / filename)
            units = original['install-plan'] if kind == 'cabal' else original
            for i, unit in enumerate(units):
                components = unit.get('components', {'unit': unit})
                for component, details in components.items():
                    for key in ('depends', 'exe-depends') if kind == 'cabal' else ('dependencies',):
                        for edge in range(len(details.get(key, []))):
                            doc = copy.deepcopy(original)
                            target = (doc['install-plan'] if kind == 'cabal' else doc)[i]
                            target = target['components'][component] if 'components' in target else target
                            del target[key][edge]
                            with self.subTest(kind=kind, unit=i, key=key, edge=edge), self.assertRaises(scan.Invalid):
                                self.check_inventory(doc, kind)

    def test_malformed_and_unknown_inventory_fields(self):
        for kind, filename in FILES.items():
            original = scan.read_json(FIXTURES / filename)
            units = original['install-plan'] if kind == 'cabal' else original
            for mutate in [lambda u: u.append(copy.deepcopy(u[0])), lambda u: u[0].update(unknown=1),
                           lambda u: u[0].pop('pkg-version' if kind == 'cabal' else 'version'),
                           lambda u: u[0].update({'pkg-version' if kind == 'cabal' else 'version': '1.bad'}),
                           lambda u: u[0].pop('depends' if kind == 'cabal' else 'dependencies'),
                           lambda u: u.clear()]:
                doc = copy.deepcopy(original); mutate(doc['install-plan'] if kind == 'cabal' else doc)
                with self.subTest(kind=kind), self.assertRaises(scan.Invalid):
                    self.check_inventory(doc, kind)

    def test_compiler_platform_and_schema_validation(self):
        doc = scan.read_json(FIXTURES / FILES['cabal'])
        for key, value in [('compiler-id', 'ghc-9.4.7'), ('arch', 'x86_64'), ('os', 'mingw32'), ('cabal-version', '99'), ('unknown', True)]:
            changed = copy.deepcopy(doc); changed[key] = value
            with self.assertRaises(scan.Invalid):
                self.check_inventory(changed, 'cabal')

    def test_json_duplicate_keys_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            for text in ['{"a": 1, "a": 2}', '{', '[NaN]']:
                path.write_text(text)
                with self.assertRaises(ValueError):
                    scan.read_json(path)

    def test_each_db_record_missing_corrupted_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'db'; shutil.copytree(DB, db)
            for path in db.iterdir():
                data = path.read_bytes(); path.unlink()
                with self.assertRaises(scan.Invalid):
                    scan.database(db)
                path.write_bytes(data + b'\n')
                with self.assertRaises(scan.Invalid):
                    scan.database(db)
                path.write_bytes(data)
            (db / 'extra.md').write_text('')
            with self.assertRaises(scan.Invalid):
                scan.database(db)

    def test_parse_all_records_rejects_missing_required_fields(self):
        for path in DB.glob('*.md'):
            text = path.read_text()
            for old, new in [('introduced =', 'unknown ='), ('cvss =', 'cvss-unknown ='), ('[[affected]]', '[[unknown]]'), ('[advisory]', '[unknown]')]:
                if old not in text:
                    continue
                with self.subTest(file=path.name, field=old), self.assertRaises((scan.Invalid, ValueError)):
                    scan.parse_advisory(text.replace(old, new, 1), path.stem)

    def test_malformed_versions_ranges_and_unsupported_semantics(self):
        path = DB / 'HSEC-2026-0007.md'; text = path.read_text()
        for old, new in [('"0.12.0.0"', '"garbage"'), ('"2.2.5.1"', '"0.1"'),
                         ('package = "aeson"', 'repository-url = "https://example.invalid"\npackage = "aeson"'),
                         ('package = "aeson"', 'ghc-component = "unknown"'),
                         ('package = "aeson"', 'package = "aeson"\nos = []'),
                         ('cvss =', 'cvss = "garbage"\n# cvss =')]:
            with self.subTest(old=old), self.assertRaises((scan.Invalid, ValueError)):
                scan.parse_advisory(text.replace(old, new, 1), path.stem)
        for v in ['', '1..0', '1.a', '01.2', '-1', '1.0-beta', None, 2]:
            with self.assertRaises(scan.Invalid):
                scan.version(v)
        self.assertLess(scan.version('1.0'), scan.version('1.0.0'))
        self.assertLess(scan.version('1.9'), scan.version('1.10'))

    def test_cli_fails_on_findings_and_validation_errors(self):
        common = [sys.executable, str(ROOT / 'scripts/security/scan.py'), 'scan', '--database', str(DB),
                  '--kind', 'cabal', '--plan', str(FIXTURES / FILES['cabal']), '--compiler', '9.10.3',
                  '--os', 'linux', '--arch', 'aarch64', '--bundled-cabal', '3.12.1.0']
        r = subprocess.run(common, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)['matches']), 3)
        common[-1] = 'invalid'
        r = subprocess.run(common, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('scan validation error', r.stderr)

    def test_conflicting_cabal_versions_and_nonobjects(self):
        doc = scan.read_json(FIXTURES / FILES['cabal'])
        hal = next(u for u in doc['install-plan'] if u.get('component-name') == 'test:hal-test')
        hal['pkg-version'] = '99'
        with self.assertRaises(scan.Invalid):
            self.check_inventory(doc, 'cabal')
        for kind in FILES:
            with self.assertRaises(scan.Invalid):
                self.check_inventory([None], kind)

    def test_database_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'db'; shutil.copytree(DB, db)
            path = next(db.iterdir()); original = DB / path.name
            path.unlink(); path.symlink_to(original)
            with self.assertRaises(scan.Invalid):
                scan.database(db)

    def test_archive_mutations_fail_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.tar.gz'; archive.write_bytes(b'incomplete')
            dest = Path(tmp) / 'output'
            with self.assertRaises(scan.Invalid):
                scan.fetch(dest, archive)
            self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()
