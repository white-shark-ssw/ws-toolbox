"""Verify a local copy of the handoff's fixed framework snapshot, without executing it."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wspack.protection.processor import protect, ProtectionOptions


def verify(snapshot):
    manifest = json.loads((ROOT / 'docs/framework-audit.json').read_text('utf-8'))
    compilers = []
    for backend in ('lua51', 'luajit21'):
        api = importlib.import_module('lupa.' + backend)
        lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
        compilers.append((backend, lua, lua.eval(b'function(s,n) local f,e=loadstring(s,n);return f~=nil,e end')))
    records = []
    start = time.perf_counter()
    for item in manifest['files']:
        raw = (Path(snapshot) / item['path']).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if blob != item['sha']:
            raise ValueError('Snapshot Git blob SHA mismatch: ' + item['path'])
        for encrypt, obfuscate in ((False, False), (True, False), (False, True), (True, True)):
            name = '@' + item['path']
            try:
                raw.decode('utf-8', errors='strict')
                encoding = 'utf-8'
            except UnicodeError:
                raw.decode('gbk', errors='strict')
                encoding = 'gbk'
            result = protect(raw, ProtectionOptions(encrypt, obfuscate), encoding, name)
            for backend, lua, compile_source in compilers:
                ok, error = compile_source(result.output, name.encode('utf-8'))
                if not ok:
                    raise ValueError(f'{backend} compile failed for {item["path"]}: {error}')
            records.append({'path': item['path'], 'encrypt': encrypt, 'obfuscate': obfuscate,
                            'source_sha': blob, 'output_bytes': len(result.output),
                            'renamed_bindings': result.report.get('renamed_bindings', 0)})
    return {'repository': manifest['repository'], 'commit': manifest['commit'],
            'original_files': len(manifest['files']), 'mode_runs': len(records),
            'backend_compile_checks': len(records) * len(compilers),
            'seconds': round(time.perf_counter() - start, 3),
            'scope': 'exact snapshot bytes, transformation invariants and compilation; no framework execution',
            'results': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', help='Directory containing source/MirServer and source/Client')
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.snapshot), ensure_ascii=False, indent=2))
