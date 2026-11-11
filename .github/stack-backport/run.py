#!/usr/bin/env python3
"""Run Stack with a prepared prefix and checked, exclusively owned setup caches.

Only the separately qualified frozen HAL graph is in scope. Do not use this as
an approval of arbitrary Custom setup dependencies or restored build caches.
"""
import argparse
import fcntl
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from cache_guard import digest, snapshot
from evidence import routes


def execute(work, stack, arguments, cwd=None):
    work = work.resolve()
    cwd = Path(cwd or Path.cwd()).resolve()
    receipt_path = work/'preparation.json'
    receipt = json.loads(receipt_path.read_text())
    for name, expected in receipt['artifacts'].items():
        if digest(work/name) != expected:
            raise ValueError('prepared artifact changed: '+name)
    symlinks = {str(p.relative_to(work)): os.readlink(p)
                for p in (work/'prefix').rglob('*') if p.is_symlink()}
    if symlinks != receipt['symlinks']:
        raise ValueError('prepared compiler symlinks changed')
    stack = stack.resolve()
    pins = {'aarch64': '14e7040353c7ae3471d6420cfc0005e1cd83973c25f4edab42b029c93ae4fdb3', 'x86_64': 'c9ba670e7efd3f3807528e60ad09667dd37eba3d941c7edf6ab09d938abc70a8'}
    if platform.system() != 'Linux' or digest(stack) != pins.get(platform.machine()):
        raise ValueError('unverified Stack executable')
    identity = {'preparation': digest(receipt_path), 'stack': digest(stack)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = work/('stack-'+key)
    state_file = work/('stack-'+key+'.json')
    with (work/('stack-'+key+'.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = snapshot(root/'setup-exe-cache')
        local = {str(p.relative_to(cwd)): digest(p) for p in cwd.glob('**/.stack-work/**/setup/setup')}
        if state_file.exists():
            state = json.loads(state_file.read_text())
            if state['identity'] != identity or state['cache'] != current:
                raise ValueError('setup cache changed; rejected before Stack')
            if state['projects'].get(str(cwd), {}) != local:
                raise ValueError('external setup changed; rejected before Stack')
        else:
            if root.exists() or local:
                raise ValueError('unreceipted build cache; use a fresh owned root and source export')
            root.mkdir()
            state = {'identity': identity, 'cache': {}, 'projects': {}}
        # Override caller settings, and reject arguments that could select another
        # compiler or cache. Release helper passes the same supported switches.
        forbidden = ('--stack-root', '--compiler', '--resolver', '--snapshot', '--work-dir',
                     '--no-system-ghc', '--install-ghc', '--ghc-variant', '--ghc-build',
                     '--extra-package-db', '--with-ghc')
        if any(arg == x or arg.startswith(x+'=') for arg in arguments for x in forbidden):
            raise ValueError('compiler/cache override is outside qualification')
        env = dict(PATH=str(work/'prefix/bin')+':'+os.environ['PATH'],
                   STACK_ROOT=str(root), HOME=str(work/'home'), GHC_ENVIRONMENT='-')
        for name in ('TMPDIR', 'LANG', 'LC_ALL', 'GHCRTS'):
            if name in os.environ:
                env[name] = os.environ[name]
        arguments = [a for a in arguments if a not in ('--verbose', '--system-ghc', '--no-install-ghc')]
        command = [str(stack), '--verbose', '--system-ghc', '--no-install-ghc', *arguments]
        directory = work/'invocations'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        (directory/'command.json').write_text(json.dumps({'cwd': str(cwd), 'argv': command}, indent=2)+'\n')
        result = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def forward(source, destination, path):
            with path.open('wb') as log:
                while data := source.read1(65536):
                    log.write(data)
                    destination.buffer.write(data)
                    destination.flush()
        threads = [threading.Thread(target=forward, args=(source, destination, directory/name))
                   for source, destination, name in ((result.stdout, sys.stdout, 'stdout.log'),
                                                     (result.stderr, sys.stderr, 'stderr.log'))]
        for thread in threads:
            thread.start()
        result.wait()
        for thread in threads:
            thread.join()
        (directory/'exit').write_text(str(result.returncode)+'\n')
        # A failed build must not bless newly produced setup executables.
        if result.returncode:
            return result.returncode
        observed = routes(work, (directory/'stderr.log').read_text())
        (directory/'routes.json').write_text(json.dumps(observed, indent=2)+'\n')
        state['cache'] = snapshot(root/'setup-exe-cache')
        state['projects'][str(cwd)] = {str(p.relative_to(cwd)): digest(p) for p in cwd.glob('**/.stack-work/**/setup/setup')}
        state_file.write_text(json.dumps(state, indent=2)+'\n')
        return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--stack', required=True, type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    raise SystemExit(execute(args.work, args.stack, arguments))
