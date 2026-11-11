#!/usr/bin/env python3
"""Exercise warning semantics and reject a same-version affected setup cache."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT/'.github/stack-backport/run.py'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment(work):
    result = {'PATH': os.environ['PATH'], 'HOME': str(work/'home'), 'GHC_ENVIRONMENT': '-'}
    for name in ('TMPDIR', 'LANG', 'LC_ALL'):
        if name in os.environ:
            result[name] = os.environ[name]
    return result


def probe(work, stack, original, output):
    work, stack, original, output = [p.resolve() for p in (work, stack, original, output)]
    output.mkdir(parents=True, exist_ok=False)
    receipts = []
    env = environment(work)

    def fixture(name, stacked):
        p = output/name
        p.mkdir()
        (p/'header-probe.cabal').write_text('cabal-version: 2.2\nname: header-probe\nversion: 0.1\nbuild-type: Simple\nlibrary\n  exposed-modules: Probe\n  build-depends: base >=4.20 && <4.21\n  default-language: Haskell2010\n  include-dirs: include\n  includes: probe.h\n')
        (p/'Probe.hs').write_text('module Probe where\nvalue :: Int\nvalue = 1\n')
        (p/'Setup.hs').write_text('import Distribution.Simple\nmain = defaultMain\n')
        (p/'stack.yaml').write_text('resolver: ghc-9.10.3\npackages: [.]\nsystem-ghc: true\ninstall-ghc: false\n')
        a = p/'include/probe.h'
        dist = '.stack-work/dist/'+platform.machine()+'-linux/ghc-9.10.3' if stacked else 'dist'
        b = p/dist/'build/include/probe.h'
        for path, text in [(a, '#define PROBE 1\n'), (b, '#define PROBE 2\n')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return p, a, b

    def run(name, argv, cwd, success=True):
        (output/(name+'.argv.json')).write_text(json.dumps(list(map(str, argv)), indent=2))
        r = subprocess.run(list(map(str, argv)), cwd=cwd, env=(dict(env, GHCRTS='-N2') if str(argv[0]) == sys.executable else env), text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (output/(name+'.log')).write_text(r.stdout)
        if success:
            r.check_returncode()
        return r

    old, a, b = fixture('old-control', False)
    old_setup = output/'affected-setup'
    run('old-link', [original/'bin/ghc', '-threaded', '-rtsopts', '-package', 'Cabal', old/'Setup.hs', '-o', old_setup], old)
    r = run('old-configure', [old_setup, 'configure', '--builddir=dist', '-v2'], old)
    if a.exists() or b.read_text() != '#define PROBE 2\n' or 'Duplicate header' not in r.stdout:
        raise ValueError('affected deletion control did not execute')
    identity = {'preparation': sha(work/'preparation.json'), 'stack': sha(stack)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache_root = work/('stack-'+key)
    state = work/('stack-'+key+'.json')
    if cache_root.exists() or state.exists():
        raise ValueError('cold qualification requires an unused prepared cache root')
    command = [sys.executable, RUNNER, '--work', work, '--stack', stack, '--', 'build', '--cabal-verbose']
    for name in ['cold', 'warm', 'injected', 'rebuilt']:
        p, a, b = fixture(name, True)
        before = [sha(a), sha(b)]
        if name == 'injected':
            setup = next((cache_root/'setup-exe-cache').glob('*/Cabal-simple*'))
            shutil.copyfile(old_setup, setup)
        r = run(name, command, p, success=name != 'injected')
        if name == 'injected':
            if r.returncode == 0 or 'rejected before Stack' not in r.stdout:
                raise ValueError('affected cached setup was accepted')
            # Keep rejected data for inspection. Nothing outside this exclusively
            # owned prepared root is removed, reset or renamed.
            cache_root.rename(output/'rejected-cache')
            state.rename(output/'rejected-state.json')
            setup_hash = None
        else:
            if 'Duplicate header' not in r.stdout or 'unspecified.' not in r.stdout:
                raise ValueError('patched warning branch was not observed')
            setup = next((cache_root/'setup-exe-cache').glob('*/Cabal-simple*'))
            setup_hash = sha(setup)
        if not a.exists() or [sha(a), sha(b)] != before:
            raise ValueError('synthetic header changed')
        receipts.append({'case': name, 'exit': r.returncode, 'headers': before, 'setup_sha256': setup_hash})
    if receipts[0]['setup_sha256'] != receipts[1]['setup_sha256']:
        raise ValueError('warm setup identity changed')
    (output/'results.json').write_text(json.dumps(receipts, indent=2)+'\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['work', 'stack', 'original', 'output']:
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    probe(a.work, a.stack, a.original, a.output)
