"""Actual handed-off business sources under stubbed ws/GUI APIs, not the engine."""
import importlib
import importlib.util
from pathlib import Path

import pytest

from wspack.protection.processor import protect, ProtectionOptions

FIXTURES = Path(__file__).parent / 'fixtures'
spec = importlib.util.spec_from_file_location('business_stubs', FIXTURES / 'business_stubs.py')
stubs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stubs)


@pytest.mark.parametrize('backend', ['lua51', 'luajit21'])
@pytest.mark.parametrize('encrypt,obfuscate', [(False, False), (True, False), (False, True), (True, True)])
def test_business_traces_all_modes(backend, encrypt, obfuscate):
    api = importlib.import_module('lupa.' + backend)
    lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    lua.execute(stubs.COMMON.encode('ascii'))
    lua.execute(stubs.SERVER.encode('gbk'))
    lua.execute(stubs.CLIENT.encode('utf-8'))
    g = lua.globals()
    envs = {}
    options = ProtectionOptions(encrypt, obfuscate)
    for side, filename, encoding, logical in [
        ('server', '等级突破.lua', 'gbk', '@Market_Def/form/等级突破.lua'),
        ('client', 'dengjitupo.lua', 'utf-8', '@form/dengjitupo.lua'),
    ]:
        source = (FIXTURES / 'business' / side / filename).read_bytes()
        result = protect(source, options, encoding, logical)
        if side == 'server':
            original, original_cap = g.server_env()
            changed, changed_cap = g.server_env()
        else:
            data = envs['server'][0][b'dengjitupo'][b'data']
            original, original_cap = g.client_env(data)
            changed, changed_cap = g.client_env(data)
        name = logical.encode(encoding)
        g.load_in(source, name, original)
        g.load_in(result.output, name, changed)
        assert g.exported(original) == g.exported(changed)
        if encrypt:
            assert changed_cap[b'source'] == result.processed
            assert changed_cap[b'count'] == 1
        if side == 'server':
            assert original_cap[b'registration'] == changed_cap[b'registration']
            assert original_cap[b'register_caller'] == changed_cap[b'register_caller'] == name
            for fn in (b'main', b'start', b'update'):
                assert g.function_info(original[b'dengjitupo'][fn]) == g.function_info(changed[b'dengjitupo'][fn])
        else:
            for fn in (b'dengjitupo_main', b'dengjitupo_update', b'dengjitupo_start', b'dengjitupo_close', b'dengjitupo_effect'):
                assert g.function_info(original[fn]) == g.function_info(changed[fn])
        envs[side] = original, changed, original_cap, changed_cap

    a, b, ac, bc = envs['server']
    levels = (119, 120, 149, 150, 179, 180, 189, 190, 199, 200, 209, 210, 219, 220, 221)
    cases = [(method, level, continent, result, 0)
             for continent in range(2, 10) for level in levels
             for method, result in ((b'main', b'true'), (b'start', b'true'), (b'start', b'not enough'), (b'update', b'true'))]
    for level in range(120, 220):
        continent = 3 if level < 150 else 4 if level < 180 else 5 if level < 190 else 6 if level < 200 else 7 if level < 210 else 8
        cases.append((b'start', level, continent, b'true', 0))
    cases += [(b'main', 115, 3, b'true', 5), (b'start', 115, 3, b'true', 5)]
    assert len(cases) == 582
    for args in cases:
        assert g.server_case(a, *args) == g.server_case(b, *args), args

    a, b, ac, bc = envs['client']
    count = 0
    for level in (119, 120, 149, 150, 180, 200, 219, 220):
        for has_data in (False, True):
            for existing in (False, True):
                for effects in (False, True):
                    for has_parent in (False, True):
                        args = level, 220, has_data, existing, effects, has_parent
                        assert g.client_case(a, ac, *args) == g.client_case(b, bc, *args), args
                        count += 1
    assert count == 128 and ac[b'ui'] == bc[b'ui']
    if encrypt:
        assert all(cap[b'count'] == 1 for _, _, _, cap in envs.values())


@pytest.mark.parametrize('backend', ['lua51', 'luajit21'])
@pytest.mark.parametrize('encrypt,obfuscate', [(True, False), (False, True), (True, True)])
def test_real_entry_error_lines(backend, encrypt, obfuscate):
    api = importlib.import_module('lupa.' + backend)
    lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    lua.execute(stubs.COMMON.encode('ascii'))
    lua.execute(stubs.SERVER.encode('gbk'))
    g = lua.globals()
    for side, filename, encoding, declaration, expected_line in [
        ('server', '等级突破.lua', 'gbk', b'function dengjitupo.main(player, npcId, npcName)', 144),
        ('client', 'dengjitupo.lua', 'utf-8', b'function dengjitupo_main(level, maxLevel)', 6),
    ]:
        lines = (FIXTURES / 'business' / side / filename).read_bytes().splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == declaration) + 1
        assert index + 1 == expected_line
        lines[index] = b'    error("EXPECTED_ENTRY",1); ' + lines[index][4:]
        raw = b''.join(lines)
        logical = '@form/' + filename
        result = protect(raw, ProtectionOptions(encrypt, obfuscate), encoding, logical)
        environment, _ = g.server_env()
        g.load_in(result.output, logical.encode(encoding), environment)
        fn = environment[b'dengjitupo'][b'main'] if side == 'server' else environment[b'dengjitupo_main']
        with pytest.raises(Exception) as caught:
            fn()
        assert ':' + str(expected_line) + ': EXPECTED_ENTRY' in str(caught.value)
