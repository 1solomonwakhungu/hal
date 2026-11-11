"""Record setup link inputs and finished executables without rewriting output."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from evidence import check_link


def install(work):
    work = Path(work)
    ghc = (work/'prefix/bin/ghc').resolve()
    # The copied distribution's launcher selects the private -B directory.
    if not ghc.is_relative_to(work/'prefix') or not ghc.read_bytes().startswith(b'#!'):
        raise ValueError('unsupported compiler launcher layout')
    original = ghc.with_name(ghc.name+'.original')
    ghc.rename(original)
    helper = work/'trace_compiler.py'
    shutil.copyfile(__file__, helper)
    shutil.copyfile(Path(__file__).with_name('evidence.py'), work/'evidence.py')
    ghc.write_text('#!'+sys.executable+'\nimport os, sys\nos.execv('+repr(sys.executable)+', '+repr([sys.executable,str(helper),str(work),str(original)])+' + sys.argv[1:])\n')
    ghc.chmod(0o755)


def invoke(work, original, argv):
    out = argv[argv.index('-o')+1] if '-o' in argv else None
    is_setup = out and (Path(out).name == 'setup' or 'Cabal-simple_' in Path(out).name)
    record = None
    if is_setup:
        directory = Path(work)/'setup-links'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        argv = [*argv, '-optl-Wl,-Map,'+str(directory/'link.map')]
        record = {'cwd': os.getcwd(), 'argv': [original, *argv], 'output': out}
    result = subprocess.run([original, *argv])
    if record is not None:
        record['exit'] = result.returncode
        if result.returncode == 0:
            # Fail before Stack can run a newly linked affected setup.
            record['link'] = check_link(work, directory/'link.map')
            data = Path(out).read_bytes()
            record['sha256'] = hashlib.sha256(data).hexdigest()
            (directory/'setup').write_bytes(data)
            # The link map records the selected Cabal archive, not merely its version.
        (directory/'receipt.json').write_text(json.dumps(record, indent=2)+'\n')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(invoke(sys.argv[1], sys.argv[2], sys.argv[3:]))
