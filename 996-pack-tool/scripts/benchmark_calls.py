"""Synthetic function-call benchmark; not a substitute for real engine measurements."""
import importlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wspack.protection.processor import protect, ProtectionOptions


def measure(iterations=200000):
    source = b'local factor=7\nfunction compute(a,b) return a*factor+b end\n'
    records = []
    for backend in ('lua51', 'luajit21'):
        api = importlib.import_module('lupa.' + backend)
        for encrypt, obfuscate in ((False, False), (True, False), (False, True), (True, True)):
            lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
            lua.execute(b'counter=0;original_loadstring=loadstring;loadstring=function(s,n) counter=counter+1;return original_loadstring(s,n) end')
            result = protect(source, ProtectionOptions(encrypt, obfuscate), 'utf-8', '@calls.lua')
            lua.execute(result.output, name=b'@calls.lua')
            before = lua.globals()[b'counter']
            loop = lua.eval(b'function(f,n) local total=0;for i=1,n do total=total+f(i,3) end;return total end')
            times = []
            for _ in range(3):
                start = time.perf_counter()
                value = loop(lua.globals()[b'compute'], iterations)
                times.append((time.perf_counter() - start) * 1000)
                if value != 7 * iterations * (iterations + 1) // 2 + 3 * iterations:
                    raise ValueError('Function result changed')
            if lua.globals()[b'counter'] != before:
                raise ValueError('Business calls unexpectedly decrypted/reloaded again')
            records.append({'backend': backend, 'encrypt': encrypt, 'obfuscate': obfuscate,
                            'iterations': iterations, 'median_ms': round(statistics.median(times), 3),
                            'extra_decryptions_during_calls': 0})
    return records


if __name__ == '__main__':
    print(json.dumps(measure(), indent=2))
