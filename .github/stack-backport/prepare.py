#!/usr/bin/env python3
"""Build the pinned Cabal backport in an exclusively owned Linux prefix.

Experimental preparation only. The resulting prefix is not a qualification of
arbitrary Stack projects, external setups, binary boot tools, or publication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
from trace_compiler import install as install_trace

HERE = Path(__file__).resolve().parent
CLIENTS = {
    'aarch64': '5b2eebf0cf0c3f0b5f38cd3b1b6dedf81fa064671d09002b319cec0983392e74',
    'x86_64': '9686ccc04aab211fcd02e09470cdf1806e1e1f31918ecfb3aca96698964c80a0',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def extract(archive, destination):
    """Only Cabal's library subtree is needed; preserve its license and notices."""
    with tarfile.open(archive) as src:
        seen = set()
        for member in src.getmembers():
            parts = PurePosixPath(member.name).parts
            require(not member.name.startswith('/') and '..' not in parts, 'unsafe archive path')
            if len(parts) < 2 or parts[1] not in ('Cabal', 'AUTHORS', 'LICENSE'):
                continue
            rel = Path(*parts[1:])
            require(str(rel) not in seen, 'duplicate archive member')
            seen.add(str(rel))
            path = destination / rel
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile(), 'non-regular library source')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as out:
                    shutil.copyfileobj(src.extractfile(member), out)
                path.chmod(member.mode & 0o777)


def prepare(work, original, archive, client):
    require(platform.system() == 'Linux' and platform.machine() in CLIENTS, 'unsupported platform')
    lock = json.loads((HERE / 'provenance.json').read_text())
    require(digest(archive) == lock['archive']['sha256'], 'archive identity changed')
    require(digest(client) == CLIENTS[platform.machine()], 'bootstrap client identity changed')
    require(digest(HERE / 'Cabal-3.12.1.0.patch') == lock['patch_sha256'], 'patch identity changed')
    work = work.absolute()
    require(not work.exists(), 'work directory must be new')
    require(not work.is_relative_to(original), 'work directory is inside the original compiler')
    work.mkdir(parents=True)
    env = dict(HOME=str(work / 'home'), CABAL_DIR=str(work / 'cabal'),
               GHC_ENVIRONMENT='-', PATH=str(original / 'bin') + ':' + os.environ['PATH'])
    for name in ('TMPDIR', 'LANG', 'LC_ALL'):
        if name in os.environ:
            env[name] = os.environ[name]
    for name in ['home', 'cabal', 'source', 'logs']:
        (work / name).mkdir()

    def run(name, argv, cwd=work):
        (work / 'logs' / (name + '.argv.json')).write_text(json.dumps(list(map(str, argv)), indent=2))
        proc = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (work / 'logs' / (name + '.log')).write_text(proc.stdout)
        (work / 'logs' / (name + '.exit')).write_text(str(proc.returncode))
        proc.check_returncode()
        return proc.stdout

    require(run('compiler-version', [original/'bin/ghc', '--numeric-version']).strip() == '9.10.3', 'compiler version')
    baseline = run('baseline-global', [original/'bin/ghc-pkg', 'dump', '--global'])
    records = baseline.split('---')
    cabal = [r for r in records if re.search(r'^name:\s+Cabal\s*$', r, re.M)]
    require(len(cabal) == 1, 'expected one original Cabal')
    old = re.search(r'^id:\s*(\S+)', cabal[0], re.M).group(1)
    require(re.search(r'^version:\s*3\.12\.1\.0\s*$', cabal[0], re.M), 'original Cabal version')
    for record in records:
        dep = re.search(r'^depends:\s*(.*?)(?=^\S|\Z)', record, re.M | re.S)
        require(not dep or old not in dep.group(1).split(), 'prebuilt consumer of original Cabal; stop')
    extract(archive, work/'source')
    source = work/'source'/lock['path']
    require(digest(source) == lock['preimage_sha256'], 'source preimage')
    run('patch', ['patch', '--batch', '--fuzz=0', '-p1', '-i', HERE/'Cabal-3.12.1.0.patch'], work/'source')
    require(digest(source) == lock['postimage_sha256'], 'source postimage')
    (work/'cabal/config').write_text(f'active-repositories: :none\nstore-dir: {work}/store\nremote-repo-cache: {work}/packages\n')
    project = work/'bootstrap.project'
    # Spaces are rejected to keep Cabal's package path syntax unambiguous.
    require(not any(c.isspace() for c in str(work)), 'work path contains whitespace')
    project.write_text(f'packages: {work}/source/Cabal\nactive-repositories: :none\noptimization: 0\nwrite-ghc-environment-files: never\n')
    build = work/'build'
    log = run('bootstrap', [client, 'v2-build', '--offline', f'--project-file={project}', f'--builddir={build}', '-v3', '-j2', 'Cabal'])
    require('Using in-library setup method with build-type Simple' in log, 'unqualified bootstrap setup route')
    plan = json.loads((build/'cache/plan.json').read_text())
    require(plan['cabal-lib-version'] == '3.18.2.0', 'bootstrap library')
    configured = [u for u in plan['install-plan'] if u['type'] == 'configured']
    require(len(configured) == 1 and configured[0]['pkg-name'] == 'Cabal', 'unexpected bootstrap graph')
    prefix = work/'prefix'
    shutil.copytree(original, prefix, symlinks=True)
    for script in (prefix/'bin').iterdir():
        if script.is_symlink() or not script.is_file():
            continue
        data = script.read_bytes()
        if data.startswith(b'#!'):
            script.write_bytes(data.replace(str(original).encode(), str(prefix).encode()))
    ghc, pkg = prefix/'bin/ghc', prefix/'bin/ghc-pkg'
    info = run('private-info', [ghc, '--info'])
    require(str(prefix) in info and str(original) not in info, 'compiler prefix relocation incomplete')
    run('unregister-private', [pkg, 'unregister', '--global', '--ipid', old])
    confs = list((build/'packagedb/ghc-9.10.3').glob('Cabal-*.conf'))
    require(len(confs) == 1, 'ambiguous Cabal registration')
    run('register-private', [pkg, 'register', '--global', confs[0]])
    run('check-private', [pkg, 'check'])
    current = run('baseline-after', [original/'bin/ghc-pkg', 'dump', '--global'])
    require(current == baseline, 'original database changed')
    install_trace(work)
    artifacts = {}
    for root in [build, prefix]:
        for path in sorted(root.rglob('*')):
            if path.is_file() and not path.is_symlink():
                artifacts[str(path.relative_to(work))] = digest(path)
    artifacts['trace_compiler.py'] = digest(work/'trace_compiler.py')
    artifacts['evidence.py'] = digest(work/'evidence.py')
    symlinks = {str(p.relative_to(work)): os.readlink(p)
                for p in prefix.rglob('*') if p.is_symlink()}
    receipt = {'provenance': lock, 'client_sha256': digest(client), 'old_unit': old,
               'new_unit': configured[0]['id'], 'artifacts': artifacts, 'symlinks': symlinks,
               'status': 'library prepared; Stack route qualification required'}
    (work/'preparation.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(prefix/'bin')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ['work', 'compiler-prefix', 'archive', 'client']:
        parser.add_argument('--'+option, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.work, args.compiler_prefix.resolve(), args.archive.resolve(), args.client.resolve())
