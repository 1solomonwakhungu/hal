#!/usr/bin/env python3
"""Prepare a fresh Stack prefix after installing verified Stack and Cabal tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from prepare import prepare


def main(work, cached_archive=None):
    client = Path(shutil.which('cabal')).resolve()
    stack = Path(shutil.which('stack')).resolve()
    # Use the installed wrapper's directory, not the resolved compiler ELF path.
    compiler = Path(shutil.which('ghc')).absolute().parent.parent
    lock = json.loads((Path(__file__).parent/'provenance.json').read_text())
    work = work.absolute()
    if work.exists():
        raise ValueError('CI preparation requires a new work directory')
    with tempfile.TemporaryDirectory(prefix='hal-cabal-source-', dir=work.parent) as directory:
        archive = Path(directory)/'cabal.tar.gz'
        if cached_archive is not None:
            with cached_archive.open('rb') as source:
                data = source.read(8*1024*1024+1)
        else:
            request = urllib.request.Request(lock['archive']['url'], headers={'User-Agent': 'hal-stack-backport'})
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('source download redirected away from HTTPS')
                data = response.read(8*1024*1024+1)
        if len(data) > 8*1024*1024 or hashlib.sha256(data).hexdigest() != lock['archive']['sha256']:
            raise ValueError('source archive identity changed')
        archive.write_bytes(data)
        prepare(work, compiler, archive, client)
    probe = Path(__file__).resolve().parents[2]/'test/stack_backport/probe.py'
    subprocess.run([sys.executable, str(probe), '--work', str(work), '--stack', str(stack),
                    '--original', str(compiler), '--output', str(work/'header-probe')], check=True)
    bindir = work/'bin'
    bindir.mkdir()
    runner = Path(__file__).parent/'run.py'
    wrapper = bindir/'stack'
    wrapper.write_text('#!'+sys.executable+'\nimport os, sys\nos.execv('+repr(sys.executable)+', '+repr([sys.executable,str(runner.resolve()),'--work',str(work),'--stack',str(stack),'--'])+' + sys.argv[1:])\n')
    wrapper.chmod(0o755)
    with open(os.environ['GITHUB_PATH'], 'a') as output:
        output.write(str(bindir)+'\n')
    print('Prepared Stack launcher:', wrapper)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--archive', type=Path, help='Previously downloaded archive; the same digest is required')
    args = parser.parse_args()
    main(args.work, args.archive)
