import importlib
import json
import re
from pathlib import Path

import pytest

from wspack.lua_syntax import Parser, transform, LuaSyntaxError, NEWLINES
from wspack.protection.processor import protect, ProtectionOptions, ProtectionError

CASES = json.loads((Path(__file__).parent / 'fixtures/scope_cases.json').read_text('utf-8'))['cases']
OPTIONS = [ProtectionOptions(False, False), ProtectionOptions(True, False),
           ProtectionOptions(False, True), ProtectionOptions(True, True)]


@pytest.fixture(params=['lua51', 'luajit21'])
def runtime(request):
    module = importlib.import_module('lupa.' + request.param)
    lua = module.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    load = lua.eval(b'''function(s,n)
        local environment=setmetatable({}, {__index=_G}); environment._G=environment
        local capture={}
        environment.loadstring=function(text,name)
            if capture.count==nil then capture.firstsource=text end
            capture.source=text;capture.name=name;capture.count=(capture.count or 0)+1
            return loadstring(text,name)
        end
        local fn=assert(loadstring(s,n));setfenv(fn,environment)
        return fn,environment,capture
    end''')
    return lua, load


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
@pytest.mark.parametrize('crlf', [False, True], ids=['LF', 'CRLF'])
@pytest.mark.parametrize('options', OPTIONS, ids=['off', 'encrypt', 'obfuscate', 'both'])
def test_real_processor_scope_cases(runtime, case, crlf, options):
    text = case['source'].replace('\n', '\r\n') if crlf else case['source']
    raw = text.encode(case['encoding'])
    name = '@case_' + case['id'] + '.lua'
    if case['policy'] == 'reject_or_preserve_affected_scope' and options.obfuscate:
        with pytest.raises(ProtectionError, match='debug|reflection'):
            protect(raw, options, case['encoding'], name)
        return
    result = protect(raw, options, case['encoding'], name)
    lua, load = runtime
    original, _, _ = load(raw, name.encode())
    transformed, environment, capture = load(result.output, name.encode())
    expected = case['expected'].encode(case['encoding']) if isinstance(case['expected'], str) else case['expected']
    assert original() == transformed() == expected
    if options.enabled:
        assert NEWLINES.findall(result.processed.decode(case['encoding'])) == NEWLINES.findall(text)
        parsed = Parser(result.processed.decode(case['encoding'])).parse()
        assert not parsed.comments
        if options.obfuscate:
            assert result.report['renamed_bindings'] > 0
            assert result.processed != raw
        if options.encrypt:
            assert result.output.isascii()
            assert capture[b'firstsource'] == result.processed
            assert capture[b'count'] >= 1
            assert b'MIT License' in result.output
    else:
        assert result.output == result.processed == raw


@pytest.mark.parametrize('source,expected', [
    ('local a=7; do local a=a; local b=a; a=b+1 end;return a', 7),
    ('local f=function() return 4 end; local f=function() return f()+1 end;return f()', 5),
    ('local t={}; local function f(n) if n==0 then return 0 end return 1+f(n-1) end; t.f=f;return t.f(4)', 4),
    ('local a,b=3,5; a,b=b,a;return a*10+b', 53),
    ('local i=4; for i=i,5 do end;return i', 4),
    ('local sum=0;for k,v in ipairs{2,3} do sum=sum+k*v end;return sum', 8),
    ('local t={v=7}; function t:f(self) return self end;return t:f(9)', 9),
    ('local f=function(...) local arg=4; return arg end;return f(1,2)', 4),
    ('local x=1;while x<3 do local x2=x+1;x=x2 end;return x', 3),
    ('local x=2;return -x^2+2^3^2', 508),
])
def test_extra_binding_and_precedence(runtime, source, expected):
    result = protect(source.encode(), ProtectionOptions(False, True), 'utf-8', '@extra.lua')
    _, load = runtime
    fn, _, _ = load(result.output, b'@extra.lua')
    assert fn() == expected


def test_comments_and_literal_spans():
    text = 'local a=2--[==[comment\r\nmore]==]and 3\r\nlocal b="--literal" -- tail\r\nreturn a,b'
    changed, _ = transform(text, True, True)
    assert NEWLINES.findall(changed) == ['\r\n', '\r\n', '\r\n']
    assert '"--literal"' in changed
    assert 'comment' not in changed and 'more' not in changed and 'tail' not in changed
    assert [x.text for x in Parser(changed).parse().tokens if x.kind == 'string'] == ['"--literal"']


def test_reflection_aliases_fail_explicitly():
    for source in ['local d=debug;return d.getlocal(1,1)',
                   'local f=debug.getupvalue;return f(print,1)',
                   'local t=debug["get".."local"];return t(1,1)',
                   'local d=_G["de".."bug"];return d["get".."local"](1,1)']:
        with pytest.raises(ProtectionError, match='debug|reflection'):
            protect(source.encode(), ProtectionOptions(False, True), 'utf-8', '@reflection.lua')


@pytest.mark.parametrize('fallback', [False, True])
def test_lua_crypto_vector_native_and_fallback(runtime, fallback):
    from wspack.protection.crypto import CRYPTO_PATH, chacha20
    lua, load = runtime
    fn, environment, _ = load(CRYPTO_PATH.read_bytes(), b'@crypto.lua')
    if fallback:
        environment[b'bit'] = False
        environment[b'bit32'] = False
    crypto = fn()
    key = bytes(range(32))
    nonce = bytes.fromhex('000000090000004a00000000')
    expected = bytes.fromhex('10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e'
                             'd2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e')
    apply = lua.eval(b'function(C,k,n,s) return C.new(k,n,1):apply_keystream(s) end')
    assert chacha20(bytes(64), key, nonce) == apply(crypto, key, nonce, bytes(64)) == expected


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbfreturn 1', b'#!lua\nreturn 1', b'\x1bLua', b'\x1bLJ',
                                 b'-- WS-LUA-PROTECTED source-chacha20-v1\nreturn 1'])
def test_special_inputs_fail_only_when_enabled(raw):
    assert protect(raw, ProtectionOptions(), 'utf-8', '@x.lua').output == raw
    with pytest.raises(ProtectionError):
        protect(raw, ProtectionOptions(True, False), 'utf-8', '@x.lua')


def test_strict_encoding_and_invalid_syntax():
    with pytest.raises(ProtectionError):
        protect(b'local x="\xff"', ProtectionOptions(True, False), 'utf-8', '@bad.lua')
    with pytest.raises(ProtectionError, match='语法'):
        protect(b'local x=', ProtectionOptions(True, False), 'utf-8', '@bad.lua')


def test_loader_lines_environment_returns_and_randomization(runtime):
    raw = b'local first,second,third=... -- remove\r\nexported=function() return first,second,third end\r\nreturn exported,nil,select("#",...)\r\n'
    options = ProtectionOptions(True, True)
    one = protect(raw, options, 'utf-8', '@line.lua')
    two = protect(raw, options, 'utf-8', '@line.lua')
    assert one.output != two.output
    lua, load = runtime
    fn, environment, capture = load(one.output, b'@line.lua')
    exported, nil, count = fn(4, None, 8)
    assert nil is None and count == 3 and exported() == (4, None, 8)
    assert capture[b'count'] == 1
    info = lua.eval(b'function(f) local i=debug.getinfo(f,"S");return i.source,i.linedefined end')
    assert info(exported) == (b'@line.lua', 2)
    assert set(environment.keys()) == {b'_G', b'loadstring', b'exported'}
    error_result = protect(b'local x=1 -- comment\nerror("EXPECTED")\n', options, 'utf-8', '@error.lua')
    fn, _, _ = load(error_result.output, b'@error.lua')
    with pytest.raises(Exception, match='error.lua:2: EXPECTED'):
        fn()


@pytest.mark.parametrize('obfuscate', [False, True])
def test_new_loader_rejects_corrupted_payload_before_business(runtime, obfuscate):
    result = protect(b'published=true;return 7', ProtectionOptions(True, obfuscate), 'utf-8', '@corrupt.lua')
    text = result.output.decode('ascii')
    match = re.search(r'\[=\[\n([0-9a-f])', text)
    assert match
    position = match.start(1)
    corrupt = text[:position] + ('0' if text[position] != '0' else '1') + text[position + 1:]
    _, load = runtime
    fn, environment, capture = load(corrupt.encode(), b'@corrupt.lua')
    with pytest.raises(Exception, match='payload checksum mismatch'):
        fn()
    assert capture[b'count'] is None and environment[b'published'] is None


def test_new_loader_source_fallback_without_debug(runtime):
    result = protect(b'local x=7;return x', ProtectionOptions(True, True), 'utf-8', '@form/fallback.lua')
    _, load = runtime
    fn, environment, capture = load(result.output, b'=(no file source)')
    environment[b'debug'] = False
    assert fn() == 7
    assert capture[b'name'] == b'@form/fallback.lua'
