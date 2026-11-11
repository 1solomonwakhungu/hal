"""Qualify the prepared external setup route; consume only a local release sink."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main(work):
    work = work.resolve()
    source = Path(__file__).resolve().parents[2]
    env = dict(PATH=str(work/'bin')+':'+os.environ['PATH'], GHCRTS='-N2',
               LANG='C.UTF-8', HOME=str(work/'home'), TMPDIR=str(work/'tmp'))
    (work/'tmp').mkdir(exist_ok=True)
    commands = [
        ('tests', ['stack', 'test', '--jobs=2', '--cabal-verbose', '--test-arguments=+RTS -N2 -RTS']),
        ('examples', ['stack', '--stack-yaml', 'examples/stack.yaml', 'build', '--jobs=2', '--cabal-verbose']),
        ('dependencies', ['stack', 'ls', 'dependencies', 'json', '--test']),
        ('release', [sys.executable, 'scripts/release/prepare.py', 'prepare', '--tag', 'v1.1', '--output', str(work/'release')]),
        ('verify', [sys.executable, 'scripts/release/prepare.py', 'verify', '--tag', 'v1.1', '--output', str(work/'release')]),
        ('sink', [sys.executable, 'scripts/release/prepare.py', 'exercise-upload', '--tag', 'v1.1', '--output', str(work/'release'), '--sink', str(work/'sink.tar.gz')]),
    ]
    logs = work/'qualification'
    logs.mkdir()
    for name, argv in commands:
        (logs/(name+'.argv.json')).write_text(json.dumps(argv, indent=2)+'\n')
        with (logs/(name+'.stdout')).open('w') as out, (logs/(name+'.stderr')).open('w') as err:
            result = subprocess.run(argv, cwd=source, env=env, stdout=out, stderr=err)
        (logs/(name+'.exit')).write_text(str(result.returncode)+'\n')
        print(name, result.returncode, flush=True)
        result.check_returncode()
    observed = []
    for path in (work/'invocations').glob('*/routes.json'):
        observed.extend(json.loads(path.read_text()))
    if not observed or not any('network-3.2.12.0' in row['cwd'] for row in observed):
        raise ValueError('missing real Network Configure execution evidence')
    archives = list((work/'release').glob('*.tar.gz'))
    if len(archives) != 1 or archives[0].read_bytes() != (work/'sink.tar.gz').read_bytes():
        raise ValueError('release sink differs from verified sdist')
    (work/'qualification.json').write_text(json.dumps({
        'external_setup_invocations': len(observed),
        'sdist_sha256': hashlib.sha256(archives[0].read_bytes()).hexdigest(),
        'scope': 'external Stack setups only; embedded tools and raw HSEC matches unchanged',
    }, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    main(parser.parse_args().work)
