"""Measure isolated loader decrypt+compile time, without business initialization.

The intercepted loadstring compiles the payload but returns an empty function.
This is NOT a real-engine startup or gameplay benchmark.
"""
import argparse
import importlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wspack.protection.processor import protect, ProtectionOptions


def measure(path, encoding, repeats=3):
    raw = Path(path).read_bytes()
    started = time.perf_counter()
    result = protect(raw, ProtectionOptions(True, True), encoding, '@benchmark.lua')
    build_ms = (time.perf_counter() - started) * 1000
    records = []
    for backend in ('lua51', 'luajit21'):
        api = importlib.import_module('lupa.' + backend)
        lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
        loader = lua.eval(b'''function(s)
            local e=setmetatable({}, {__index=_G});e._G=e
            e.loadstring=function(source,name)
                local fn,err=loadstring(source,name);if not fn then error(err) end
                return function() end
            end
            local fn=assert(loadstring(s,"@benchmark.lua"));setfenv(fn,e);return fn()
        end''')
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            loader(result.output)
            times.append((time.perf_counter() - start) * 1000)
        records.append({'backend': backend, 'median_ms': round(statistics.median(times), 3),
                        'samples_ms': [round(x, 3) for x in times]})
    return {'file': str(path), 'source_bytes': len(raw), 'processed_bytes': len(result.processed),
            'release_bytes': len(result.output), 'build_ms': round(build_ms, 3), 'loader': records}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('path')
    p.add_argument('--encoding', default='utf-8')
    p.add_argument('--repeats', type=int, default=3)
    a = p.parse_args()
    print(json.dumps(measure(a.path, a.encoding, a.repeats), ensure_ascii=False, indent=2))
