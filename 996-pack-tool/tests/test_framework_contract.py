"""Execute real registration/proxy functions with stubbed engine prerequisites.

Fixtures are function excerpts of the fixed framework commit, not replacement
implementations. This does not execute the complete framework or the 996 engine.
"""
import importlib
from pathlib import Path

import pytest

from wspack.protection.processor import protect, ProtectionOptions

FIXTURES = Path(__file__).parent / 'fixtures'
BOOT = b'''
GameData={Object={},ModuleMain={},ModuleRoute={}}
ws={mode={},allowed={},checks=0}
ws.mode.setnpcRelations=function() end
ws.SetFormFunc=function(list) for _,name in ipairs(list) do ws.allowed[name]=true end end
ws.SetFormFuncKf=ws.SetFormFunc
ws.formInitCheckFun=function(player,key,args)
    ws.checks=ws.checks+1
    return GameData.ModuleMain[key](player,unpack(args,1,args.n or #args))
end
'''
FEATURE = b'''
local entity={data={buff=7}}
function entity.main(player,a,b,c) return player,select("#",a,b,c),a,b,c end
ws.RegisterModule("feature",entity,{_version=3,formFuncs={"feature.main"}})
local routed={data={buff=11}}
function routed.main(player) return player,routed.data.buff end
ws.RegisterModule("feature",routed,{},"1")
function feature_callback() return feature.data.buff end
event_callbacks={kill="feature_callback"}
'''


@pytest.mark.parametrize('backend', ['lua51', 'luajit21'])
@pytest.mark.parametrize('encrypt,obfuscate', [(False, False), (True, False), (False, True), (True, True)])
def test_actual_proxy_registration_and_route_contracts(backend, encrypt, obfuscate):
    api = importlib.import_module('lupa.' + backend)
    lua = api.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    lua.execute(BOOT)
    options = ProtectionOptions(encrypt, obfuscate)
    protected_files = []
    for filename, raw in [('module_proxy.lua', (FIXTURES / 'module_proxy.lua').read_bytes()),
                          ('module_registration.lua', (FIXTURES / 'module_registration.lua').read_bytes()),
                          ('feature.lua', FEATURE)]:
        name = '@' + filename
        output = protect(raw, options, 'utf-8', name).output
        protected_files.append((name.encode(), output))
        lua.execute(output, name=name.encode())
    g = lua.globals()
    feature = g[b'feature']
    assert feature[b'main'](b'player', 4, None, 8) == (b'player', 3, 4, None, 8)
    assert g[b'ws'][b'checks'] == 1
    assert lua.eval(b'rawget(feature,"main")==nil')
    assert g[b'ws'][b'allowed'][b'feature.main']
    assert g[b'GameData'][b'ModuleRoute'][b'feature'][b'1'] == b'feature__route__1'
    assert g[b'ws'][b'GetRouteMode'](b'feature', b'1')[b'main'](b'player') == (b'player', 11)
    assert g[b'ws'][b'checks'] == 1  # route copy is not forced through the main proxy
    combat = b'local level=1; return feature.data.buff + ws.GetRouteMode("feature","1").data.buff'
    assert lua.execute(protect(combat, options, 'utf-8', '@combat.lua').output) == 18
    assert lua.eval(b'_G[event_callbacks.kill]()') == 7
    lua.execute(b'feature.main=function(player) return player,"replaced" end')
    assert feature[b'main'](b'p') == (b'p', b'replaced')
    assert g[b'ws'][b'checks'] == 2
    assert lua.eval(b'rawget(feature,"main")==nil')
    assert lua.eval(b'ws_installModuleMainProxy("feature",feature)')
    # QF-like rebuild: initialize cached aliases against fresh global registries.
    lua.execute(BOOT)
    for name, output in protected_files:
        lua.execute(output, name=name)
    assert lua.globals()[b'feature'][b'main'](b'fresh', 1, None, 3) == (b'fresh', 3, 1, None, 3)
    assert lua.globals()[b'ws'][b'checks'] == 1
