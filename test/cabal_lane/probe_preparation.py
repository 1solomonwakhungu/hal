#!/usr/bin/env python3
"""Exercise stale-plan rejection with a real disposable Cabal 3.12 Custom setup."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.github/cabal-lane'))
import inputs

SPEC = importlib.util.spec_from_file_location('lane_probe', ROOT / '.github/cabal-lane/run.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class StopAfterBuild(Exception):
    pass


def qualify(work):
    work.mkdir()
    results = []
    for mode in ('unchanged', 'project-added-custom', 'override-added-custom', 'fresh-resolution'):
        root = work / mode
        root.mkdir()
        for name in ('logs', 'home', 'cabal', 'source'):
            (root / name).mkdir()
        source = root / 'source'
        (root / 'cabal/config').write_text(
            'active-repositories: :none\nstore-dir: ' + str(root / 'cabal/store') + '\n')
        (source / 'cabal.project').write_text('packages: .\nactive-repositories: :none\n')
        (source / 'tiny.cabal').write_text(
            'cabal-version: 2.4\nname: tiny\nversion: 0.1\nbuild-type: Simple\n'
            'library\n exposed-modules: Tiny\n build-depends: base\n'
            ' default-language: Haskell2010\n')
        (source / 'Tiny.hs').write_text('module Tiny where\nvalue :: Int\nvalue = 1\n')
        lane = M.Lane(root)
        lane.projects = {'hal': source}
        lane.args = {'hal': ['--builddir=dist-hal']}
        lane.identity()
        lane.command('original-resolve', ['cabal', 'build', 'all', '--dry-run', '-v2',
                                         '--builddir=dist-hal'], source)
        plan = inputs.read_json(lane.plan('hal'))
        lane.pins = {'sources': {}, 'plans': {'hal': inputs.plan_digest(plan)}}
        receipt = {'package_cache': str(root / 'cache'),
                   'inputs_sha256': inputs.sha256((M.HERE / 'inputs.json').read_bytes()),
                   'local_inputs': lane.input_snapshot()}
        marker = source / 'extra/executed-custom.txt'
        if mode != 'unchanged':
            extra = source / 'extra'
            extra.mkdir()
            (extra / 'extra.cabal').write_text(
                'cabal-version: 2.4\nname: extra\nversion: 0.1\nbuild-type: Custom\n'
                'custom-setup\n setup-depends: base, Cabal ==3.12.1.0\n'
                'library\n exposed-modules: Extra\n build-depends: base\n'
                ' default-language: Haskell2010\n')
            (extra / 'Extra.hs').write_text('module Extra where\n')
            (extra / 'Setup.hs').write_text(
                'import Distribution.Simple\nmain = writeFile "executed-custom.txt" '
                '"custom setup ran" >> defaultMain\n')
            filename = 'cabal.project.local' if mode == 'override-added-custom' else 'cabal.project'
            (source / filename).write_text('packages: . extra\nactive-repositories: :none\n')
            if mode == 'fresh-resolution':
                # Deliberately bypass the receipt check to test the second boundary:
                # a fresh dry-run must replace the old plan and reject it before build.
                receipt['local_inputs'] = lane.input_snapshot()
        M.write_json(root / 'prepared.json', receipt)
        def stop(*args):
            raise StopAfterBuild()
        lane.test_hal = stop
        rejected = None
        try:
            lane.build()
        except StopAfterBuild:
            pass
        except ValueError as error:
            rejected = str(error)
        row = {'case': mode, 'rejection': rejected, 'custom_marker': marker.exists(),
               'build_started': (lane.logs / 'hal-build.log').exists(),
               'revalidated': (lane.logs / 'hal-revalidate.log').exists()}
        results.append(row)
        assert not marker.exists(), row
        if mode == 'unchanged':
            assert rejected is None and row['build_started'] and row['revalidated'], row
        elif mode == 'fresh-resolution':
            assert rejected and 'resolved graph differs' in rejected, row
            assert row['revalidated'] and not row['build_started'], row
        else:
            assert rejected and 'inputs changed' in rejected, row
            assert not row['revalidated'] and not row['build_started'], row
    M.write_json(work / 'results.json', results)
    print('Four preparation/execution cases passed; no Custom setup executed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    qualify(parser.parse_args().work.resolve())
