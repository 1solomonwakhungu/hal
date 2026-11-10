#!/usr/bin/env python3
"""Bounded HSEC scanner for the fork's generated Cabal/Stack plans (Python >=3.11)."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import urllib.request

HERE = Path(__file__).resolve().parent
REVISION = '9fa5787e33f5bb3d73f2a0db22b59fd10921d25f'
SOURCE = 'https://github.com/haskell/security-advisories'
COMPONENTS = {'ghc', 'ghci', 'rts', 'ghc-pkg', 'runghc', 'ghc-iserv', 'hp2ps', 'hpc', 'hsc2hs', 'haddock'}


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def fields(obj, required, optional=()):
    require(isinstance(obj, dict), 'expected object')
    require(set(required) <= obj.keys() <= set(required) | set(optional),
            f'missing/unknown fields: required={sorted(required)}, actual={sorted(obj)}')


def string(value):
    require(isinstance(value, str) and bool(value.strip()), 'expected nonempty string')
    return value


def strings(value):
    require(isinstance(value, list), 'expected string list')
    for item in value:
        string(item)
    require(len(value) == len(set(value)), 'duplicate list entries')
    return value


def version(value):
    require(isinstance(value, str) and re.fullmatch(r'(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*', value),
            f'invalid numeric version: {value!r}')
    # Cabal Version uses lexicographic component ordering, without zero padding.
    return tuple(map(int, value.split('.')))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_object,
                      parse_constant=lambda v: (_ for _ in ()).throw(Invalid(f'invalid JSON constant {v}')))


def pin():
    value = read_json(HERE / 'hsec-pin.json')
    require(value['revision'] == REVISION and value['source'] == SOURCE, 'wrong database pin')
    return value


def fetch(destination, archive=None):
    """Never extract archive paths or links; materialize verified published records only."""
    metadata = pin()
    destination = Path(destination)
    require(not destination.exists(), 'database destination must not exist')
    if archive:
        data = Path(archive).read_bytes()
    else:
        with urllib.request.urlopen(metadata['archive_url'], timeout=60) as response:
            data = response.read(8_000_001)
    require(len(data) <= 8_000_000, 'oversized database archive')
    require(hashlib.sha256(data).hexdigest() == metadata['archive_sha256'], 'database archive hash mismatch')
    observed, published = {}, {}
    prefix = 'security-advisories-' + REVISION + '/'
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
        seen = set()
        for member in tar:
            require(member.name not in seen, 'duplicate archive path')
            seen.add(member.name)
            if not member.name.startswith(prefix + 'advisories/') or member.isdir():
                continue
            path = member.name[len(prefix):]
            require(path in metadata['advisory_tree'], f'unexpected advisory path: {path}')
            if member.issym():
                content, mode = member.linkname.encode(), '120000'
            else:
                require(member.isfile() and member.size < 1_000_000, 'invalid advisory archive member')
                content, mode = tar.extractfile(member).read(), '100644'
            blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            observed[path] = {'mode': mode, 'sha': blob}
            if path in metadata['published_sha256']:
                require(mode == '100644', 'published advisory must be a file')
                require(hashlib.sha256(content).hexdigest() == metadata['published_sha256'][path], 'advisory hash mismatch')
                published[path] = content
    require(observed == metadata['advisory_tree'], 'incomplete advisory tree')
    require(set(published) == set(metadata['published_sha256']), 'incomplete published database')
    # Validate every record before writing anything.
    for path, data in published.items():
        parse_advisory(data.decode(), Path(path).stem)
    destination.mkdir(parents=True)
    for path, data in published.items():
        (destination / Path(path).name).write_bytes(data)


def cvss(value):
    value = string(value)
    if value.startswith(('CVSS:3.0/', 'CVSS:3.1/')):
        metrics = value.split('/')[1:]
        required = {'AV', 'AC', 'PR', 'UI', 'S', 'C', 'I', 'A'}
        allowed = {'AV': 'NALP', 'AC': 'LH', 'PR': 'NLH', 'UI': 'NR', 'S': 'UC',
                   'C': 'NLH', 'I': 'NLH', 'A': 'NLH', 'E': 'XUPFH', 'RL': 'XOTWU',
                   'RC': 'XURC', 'CR': 'XLMH', 'IR': 'XLMH', 'AR': 'XLMH',
                   'MAV': 'XNALP', 'MAC': 'XLH', 'MPR': 'XNLH', 'MUI': 'XNR',
                   'MS': 'XUC', 'MC': 'XNLH', 'MI': 'XNLH', 'MA': 'XNLH'}
    else:
        metrics = value.split('/')
        required = {'AV', 'AC', 'Au', 'C', 'I', 'A'}
        allowed = {'AV': 'LAN', 'AC': 'HML', 'Au': 'MSN', 'C': 'NPC', 'I': 'NPC', 'A': 'NPC'}
    seen = set()
    for metric in metrics:
        pair = metric.split(':')
        require(len(pair) == 2, 'invalid CVSS metric')
        key, val = pair
        require(key in allowed and key not in seen and len(val) == 1 and val in allowed[key], 'unsupported CVSS metric')
        seen.add(key)
    require(required <= seen, 'missing CVSS metrics')


def parse_advisory(text, identifier):
    require(text.startswith('```toml\n'), 'missing TOML front matter')
    header, sep, body = text[len('```toml\n'):].partition('\n```\n')
    require(sep and body.strip(), 'missing advisory body or closing fence')
    doc = tomllib.loads(header)
    fields(doc, {'advisory', 'affected', 'references'})
    a = doc['advisory']
    fields(a, {'id', 'keywords'}, {'cwe', 'capec', 'aliases', 'related'})
    require(a['id'] == identifier and re.fullmatch(r'HSEC-\d{4}-\d{4}', identifier), 'advisory ID mismatch')
    for key in ('keywords', 'aliases', 'related'):
        if key in a:
            strings(a[key])
    for key in ('cwe', 'capec'):
        if key in a:
            require(isinstance(a[key], list) and all(type(n) is int and n > 0 for n in a[key]), 'invalid classification')
    require(isinstance(doc['references'], list) and doc['references'], 'missing references')
    for ref in doc['references']:
        fields(ref, {'type', 'url'})
        require(ref['type'] in {'ADVISORY', 'ARTICLE', 'DETECTION', 'DISCUSSION', 'REPORT', 'FIX', 'INTRODUCED', 'PACKAGE', 'EVIDENCE', 'WEB'}, 'unknown reference type')
        require(re.match(r'https?://\S+$', string(ref['url'])), 'invalid reference URL')
    require(isinstance(doc['affected'], list) and doc['affected'], 'missing affected entries')
    for affected in doc['affected']:
        fields(affected, {'cvss', 'versions'}, {'package', 'ghc-component', 'arch', 'os', 'api', 'declarations'})
        require(('package' in affected) != ('ghc-component' in affected), 'ambiguous affected identity')
        if 'package' in affected:
            require(re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', string(affected['package'])), 'invalid package name')
        else:
            require(affected['ghc-component'] in COMPONENTS, 'unknown compiler component')
        # Severity never filters findings; preserve the upstream vector verbatim.
        cvss(affected['cvss'])
        for key in ('os', 'arch'):
            if key in affected:
                require(strings(affected[key]), 'empty platform constraint')
        if 'api' in affected:
            require(isinstance(affected['api'], list) and affected['api'], 'empty API constraints')
            for api in affected['api']:
                fields(api, {'module', 'name'})
                string(api['module']); string(api['name'])
        if 'declarations' in affected:
            require(isinstance(affected['declarations'], dict) and affected['declarations'], 'invalid declarations')
            for name, constraint in affected['declarations'].items():
                string(name)
                # Only syntax present at this pin. These annotations never suppress a match.
                require(isinstance(constraint, str) and re.fullmatch(r'(?:>=|>|<=|<|==) [0-9.]+(?: && (?:>=|>|<=|<|==) [0-9.]+)*', constraint), 'unsupported declaration range')
                for term in constraint.split(' && '):
                    version(term.split(' ')[1])
        require(isinstance(affected['versions'], list) and affected['versions'], 'missing version ranges')
        for interval in affected['versions']:
            fields(interval, {'introduced'}, {'fixed'})
            low = version(interval['introduced'])
            if 'fixed' in interval:
                require(low < version(interval['fixed']), 'reversed/empty version range')
    return doc


def database(directory):
    directory = Path(directory)
    metadata = pin()
    expected = {Path(k).name: v for k, v in metadata['published_sha256'].items()}
    require(directory.is_dir(), 'missing database directory')
    require({p.name for p in directory.iterdir()} == set(expected), 'missing/extra database records')
    result = []
    for name, digest in sorted(expected.items()):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'nonregular database record')
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == digest, f'database content mismatch: {name}')
        result.append(parse_advisory(data.decode(), path.stem))
    require(len(result) == metadata['published_count'], 'wrong database count')
    return result


def edges(component):
    fields(component, {'depends', 'exe-depends'})
    return sorted(strings(component['depends']) + strings(component['exe-depends']))


def cabal_plan(doc):
    fields(doc, {'cabal-version', 'cabal-lib-version', 'compiler-id', 'os', 'arch', 'install-plan'})
    require(doc['cabal-version'] == '3.14.1.1' and doc['cabal-lib-version'] == '3.14.1.1', 'unsupported Cabal schema/version')
    require(doc['compiler-id'] == 'ghc-9.10.3', 'unsupported compiler')
    platform(doc['os'], doc['arch'])
    require(isinstance(doc['install-plan'], list) and doc['install-plan'], 'empty Cabal plan')
    ids, graph, packages, roots = {}, {}, set(), set()
    for unit in doc['install-plan']:
        require(isinstance(unit, dict), 'invalid Cabal unit')
        common = {'type', 'id', 'pkg-name', 'pkg-version'}
        if unit.get('type') == 'pre-existing':
            fields(unit, common | {'depends'})
            component, deps = 'pre-existing', strings(unit['depends'])
        else:
            require(unit.get('type') == 'configured', 'unknown unit type')
            required = common | {'flags', 'style', 'pkg-src'}
            optional = {'pkg-cabal-sha256', 'pkg-src-sha256', 'bin-file', 'dist-dir', 'build-info'}
            if 'components' in unit:
                fields(unit, required | {'components'}, optional)
                require(isinstance(unit['components'], dict) and unit['components'], 'empty components')
                component = 'components:' + ','.join(sorted(unit['components']))
                deps = []
                for name, value in unit['components'].items():
                    string(name); deps.extend(edges(value))
            else:
                fields(unit, required | {'component-name', 'depends', 'exe-depends'}, optional)
                component = string(unit['component-name'])
                deps = edges({k: unit[k] for k in ('depends', 'exe-depends')})
            for key in ('bin-file', 'dist-dir', 'build-info'):
                if key in unit:
                    string(unit[key])
            require(isinstance(unit['flags'], dict) and all(type(x) is bool for x in unit['flags'].values()), 'invalid flags')
            require(unit['style'] in {'local', 'global'}, 'unknown unit style')
            if unit['style'] == 'local':
                fields(unit['pkg-src'], {'type', 'path'})
                require(unit['pkg-src']['type'] == 'local', 'invalid local source')
                string(unit['pkg-src']['path'])
                roots.add((unit['pkg-name'], component))
            else:
                fields(unit['pkg-src'], {'type', 'repo'})
                require(unit['pkg-src']['type'] == 'repo-tar', 'unsupported package source')
                fields(unit['pkg-src']['repo'], {'type', 'uri'})
                require(unit['pkg-src']['repo']['type'] == 'secure-repo', 'unsupported repository')
                require(unit['pkg-src']['repo']['uri'] in {'http://hackage.haskell.org/', 'https://hackage.haskell.org/'}, 'unsupported repository URI')
                for key in ('pkg-cabal-sha256', 'pkg-src-sha256'):
                    require(re.fullmatch(r'[0-9a-f]{64}', unit.get(key, '')), 'missing source hash')
        uid = string(unit['id']); name = string(unit['pkg-name']); version(unit['pkg-version'])
        require(uid not in ids, 'duplicate unit ID')
        label = name + ':' + component
        require(label not in graph, 'duplicate normalized unit')
        ids[uid] = label
        graph[label] = deps
        packages.add((name, unit['pkg-version']))
    require(roots == {('hal', 'lib'), ('hal', 'test:hal-test')}, 'missing/unexpected local components')
    require(all(dep in ids for ds in graph.values() for dep in ds), 'unresolved Cabal dependency edge')
    require(len({name for name, _ in packages}) == len(packages), 'conflicting package versions across units')
    normalized = {key: sorted(ids[dep] for dep in ds) for key, ds in graph.items()}
    return packages, normalized


def stack_plan(doc, kind):
    require(isinstance(doc, list) and doc, 'empty Stack inventory')
    graph, packages, locals_ = {}, set(), set()
    for unit in doc:
        fields(unit, {'name', 'version', 'dependencies', 'license'}, {'location'})
        name = string(unit['name']); version(unit['version']); string(unit['license'])
        require(name not in graph, 'duplicate Stack package')
        graph[name] = sorted(strings(unit['dependencies']))
        packages.add((name, unit['version']))
        if 'location' in unit:
            loc = unit['location']; fields(loc, {'type', 'url'})
            require(loc['type'] in {'hackage', 'project package'}, 'unsupported Stack source')
            if loc['type'] == 'project package':
                require(string(loc['url']).startswith('file:///'), 'invalid project location')
                locals_.add(name)
            else:
                require(loc['url'] == f'https://hackage.haskell.org/package/{name}-{unit["version"]}', 'Stack source/version mismatch')
    require(locals_ == ({'hal'} if kind == 'stack-test' else {'hal', 'examples'}), 'missing/unexpected Stack roots')
    require(all(dep in graph for ds in graph.values() for dep in ds), 'unresolved Stack dependency edge')
    return packages, graph


def platform(os_name, arch):
    require(os_name == 'linux' and arch in {'aarch64', 'x86_64'}, 'unsupported platform')


def inventory(path, kind, compiler, os_name, arch):
    platform(os_name, arch)
    require(compiler == '9.10.3', 'unsupported compiler metadata')
    doc = read_json(path)
    if kind == 'cabal':
        require(isinstance(doc, dict), 'expected Cabal plan object')
        require((doc.get('compiler-id'), doc.get('os'), doc.get('arch')) == ('ghc-' + compiler, os_name, arch), 'compiler/platform metadata mismatch')
        packages, graph = cabal_plan(doc)
        packages |= {('Cabal', doc['cabal-lib-version']), ('cabal-install', doc['cabal-version'])}
    else:
        packages, graph = stack_plan(doc, kind)
    expected = read_json(HERE / 'plan-shapes.json')[kind]
    require(graph == expected, 'resolved graph differs from reviewed support contract; review/regenerate plan-shapes.json')
    return packages, len(graph)


def matches(records, packages, compiler, os_name, arch):
    result = []
    for record in records:
        for affected in record['affected']:
            component = affected.get('ghc-component')
            name = component or affected['package']
            versions = [compiler] if component else sorted(v for n, v in packages if n == name)
            for v in versions:
                if not any(version(v) >= version(r['introduced']) and
                           ('fixed' not in r or version(v) < version(r['fixed'])) for r in affected['versions']):
                    continue
                applicable = ('os' not in affected or os_name in affected['os']) and ('arch' not in affected or arch in affected['arch'])
                result.append({'advisory': record['advisory']['id'], 'name': name, 'version': v,
                               'kind': 'compiler-component' if component else 'package',
                               'platform_applicable': applicable,
                               'constraints': {k: affected[k] for k in ('os', 'arch', 'api', 'declarations') if k in affected},
                               'url': SOURCE + '/blob/' + REVISION + '/advisories/published/' + record['advisory']['id'][5:9] + '/' + record['advisory']['id'] + '.md'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch'); f.add_argument('directory'); f.add_argument('--archive')
    s = sub.add_parser('scan')
    s.add_argument('--database', required=True)
    s.add_argument('--kind', choices=['cabal', 'stack-test', 'stack-examples'], required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--compiler', required=True)
    s.add_argument('--os', required=True); s.add_argument('--arch', required=True)
    s.add_argument('--bundled-cabal', required=True, help='ghc-pkg field Cabal version --simple-output')
    args = parser.parse_args()
    try:
        if args.command == 'fetch':
            fetch(args.directory, args.archive)
            print(f'verified {pin()["published_count"]} published records at {REVISION}')
            return 0
        records = database(args.database)
        packages, count = inventory(args.plan, args.kind, args.compiler, args.os, args.arch)
        version(args.bundled_cabal)
        require(args.bundled_cabal == '3.12.1.0', 'unsupported bundled Cabal library')
        packages.add(('Cabal', args.bundled_cabal))
        found = matches(records, packages, args.compiler, args.os, args.arch)
        print(json.dumps({'database_revision': REVISION, 'published_records': len(records), 'plan': args.kind,
                          'units': count, 'scanned_package_versions_including_tools': len(packages),
                          'compiler': args.compiler, 'os': args.os, 'arch': args.arch,
                          'matches': found, 'reachability': 'Not assessed; applicable version matches fail conservatively.'}, indent=2))
        return 1 if any(x['platform_applicable'] for x in found) else 0
    except (Invalid, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(f'scan validation error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
