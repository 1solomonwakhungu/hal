"""Bind successful external setup invocations to the prepared Cabal archive."""
import hashlib
import json
from pathlib import Path
import re
import shlex


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_link(work, link_map):
    work = Path(work).resolve()
    receipt = json.loads((work/'preparation.json').read_text())
    libraries = [work/name for name in receipt['artifacts']
                 if Path(name).name == 'libHSCabal-3.12.1.0-inplace.a']
    if len(libraries) != 1:
        raise ValueError('ambiguous prepared Cabal archive')
    library = libraries[0]
    if digest(library) != receipt['artifacts'][str(library.relative_to(work))]:
        raise ValueError('prepared Cabal archive changed')
    # Exclude Cabal-syntax: only the executable setup library is replaced.
    selected = set(re.findall(r'[^\s()]+/libHSCabal-[0-9][^\s()]*\.a',
                              Path(link_map).read_text()))
    if selected != {str(library)}:
        raise ValueError('setup did not link exclusively to the prepared Cabal archive')
    return {'library': str(library), 'library_sha256': digest(library),
            'map_sha256': digest(link_map)}


def routes(work, text):
    work = Path(work)
    links = {}
    for path in sorted((work/'setup-links').glob('*/receipt.json')):
        record = json.loads(path.read_text())
        if record['exit'] != 0:
            continue
        if digest(path.parent/'setup') != record['sha256']:
            raise ValueError('retained setup executable changed')
        check_link(work, path.parent/'link.map')
        output = record['output'].replace('/tmp-Cabal-simple_', '/Cabal-simple_')
        links.setdefault(output, []).append((path, record))
    result = []
    for line in text.splitlines():
        match = re.search(r'\[debug\] Run process within (.*?): (.*)', line)
        if not match:
            continue
        argv = shlex.split(match.group(2))
        if not argv:
            continue
        executable = Path(argv[0])
        if executable.name != 'setup' and not executable.name.startswith('Cabal-simple_'):
            continue
        candidates = links.get(str(executable), [])
        # Temporary Configure executables can disappear after a build. Their
        # unique output path and bytes were retained before GHC returned.
        hashes = {record['sha256'] for _, record in candidates}
        if executable.exists():
            hashes &= {digest(executable)}
        if len(hashes) != 1:
            raise ValueError('unbound or ambiguous setup execution: '+str(executable))
        selected = [(path, record) for path, record in candidates if record['sha256'] in hashes]
        result.append({'cwd': match.group(1), 'argv': argv,
                       'sha256': next(iter(hashes)),
                       'link_receipts': [str(path.relative_to(work)) for path, _ in selected]})
    return result
