"""Standalone ws/GUI test stubs from the handoff; not the 996 engine."""
COMMON = r'''
function stable(value)
    local t = type(value)
    if t == 'string' then return string.format('%q', value) end
    if t == 'number' or t == 'boolean' or t == 'nil' then return tostring(value) end
    if t == 'function' then
        local i = debug.getinfo(value, 'S')
        return 'function:'..i.source..':'..i.linedefined..':'..i.lastlinedefined
    end
    assert(t == 'table', 'unexpected stub value: '..t)
    local keys, out = {}, {}
    for k in pairs(value) do keys[#keys + 1] = k end
    table.sort(keys, function(a,b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(keys) do out[#out + 1] = '['..stable(k)..']='..stable(value[k]) end
    return '{'..table.concat(out, ',')..'}'
end

function base_env()
    local e, capture = {}, {}
    setmetatable(e, {__index = _G})
    e._G = e
    e.loadstring = function(source, name)
        capture.source, capture.name = source, name
        capture.count = (capture.count or 0) + 1
        return loadstring(source, name)
    end
    return e, capture
end

function load_in(source, name, e)
    local chunk = assert(loadstring(source, name))
    setfenv(chunk, e)
    return chunk()
end

function exported(e)
    local result = {}
    for k in pairs(e) do result[#result + 1] = k end
    table.sort(result)
    return table.concat(result, ',')
end

function function_info(fn)
    local i = debug.getinfo(fn, 'Su')
    return stable({source=i.source, linedefined=i.linedefined,
                   lastlinedefined=i.lastlinedefined, nups=i.nups})
end
'''

SERVER = r'''
function server_env()
    local e, capture = base_env()
    local s = {trace={}}
    local function log(...) s.trace[#s.trace + 1] = stable({...}) end
    e.state = s
    e.ws = {mode={}}
    e.ws.RegisterModule = function(name, module, config)
        log('RegisterModule', name, config)
        e[name], e.ws.mode[name] = module, module
        module.name, module._version = name, config._version
        capture.registration = stable(config)
        capture.register_caller = debug.getinfo(2, 'S').source
    end
    e.getbaseinfo = function(player, index) log('getbaseinfo',player,index); return s.level end
    e.ws.GetAtt = function(player, index) log('GetAtt',player,index); return s.att end
    e.ws.GetInt = function(player, key)
        log('GetInt',player,key)
        if key == '大陆等级' then return s.continent end
        if key == '等级突破等级' then return s.count end
        error('unexpected variable')
    end
    e.ws.SetInt = function(player, key, value) log('SetInt',player,key,value); s.count=value end
    e.ws.msg = function(...) log('msg',...) end
    e.ws.ShowForm = function(...) log('ShowForm',...) end
    e.ws.ShowFormFunc = function(...) log('ShowFormFunc',...) end
    e.ws.SubUseMode = function(...) log('SubUseMode',...); return s.result end
    e.changelevel = function(player, op, level) log('changelevel',player,op,level); s.level=level end
    return e, capture
end

function server_case(e, method, level, continent, result, att)
    local s=e.state
    s.trace,s.level,s.continent,s.result,s.att,s.count = {},level,continent,result,att or 0,7
    e.dengjitupo[method]('player-1',99,'npc')
    return stable({trace=s.trace,level=s.level,count=s.count})
end
'''

CLIENT = r'''
function client_env(data)
    local e, capture = base_env()
    local s={trace={}, hasParent=true, existing=true, effects=true}
    local function log(...) s.trace[#s.trace + 1] = stable({...}) end
    e.state=s
    e.ws = {data={dengjitupo=data}}
    e.GUI, e.GameBase, e.SL = {}, {}, {}
    e.GUI.GetWindow = function(self, parent, name)
        log('GUI.GetWindow',parent,name)
        return s.existing and ('window:'..name) or nil
    end
    e.GUI.Win_IsNull = function(self, handle) return handle==nil end
    e.GUI.Win_Close = function(self, handle) log('Win_Close',handle) end
    e.FormCreatStr = function(ui) s.hasParent=true;log('FormCreatStr',ui);capture.ui=ui;return 'parent' end
    e.GetWindow = function(parent, name)
        log('GetWindow',parent,name)
        if not s.hasParent then return nil end
        if not s.effects and (name=='特效节点' or name=='野蛮冲撞') then return nil end
        return (parent or 'root')..'/'..name
    end
    e.DelHandle2 = function(...) log('DelHandle2',...) end
    e.GUI.setOpacity = function(self,...) log('setOpacity',...) end
    e.GUI.Effect_Create = function(self,parent,name,...) log('Effect_Create',parent,name,...);return 'effect:'..name end
    e.GUI.Effect_play = function(self,...) log('Effect_play',...) end
    e.GUI.Timeline_FadeTo = function(self,node,opacity,duration,callback)
        log('Timeline_FadeTo',node,opacity,duration,callback)
        if callback then callback() end
    end
    e.GUI.Timeline_MoveTo = function(self,node,position,duration,callback)
        log('Timeline_MoveTo',node,position,duration,callback)
        if callback then callback() end
    end
    e.GUI.Text_setString = function(self,...) log('Text_setString',...) end
    e.GUI.getContentSize = function(self,node) log('getContentSize',node);return {width=610,height=450} end
    e.GUI.Frames_Create = function(self,...) log('Frames_Create',...) end
    e.ws.DelayCall = function(parent,callback,delay) log('DelayCall',parent,callback,delay);callback() end
    e.SL.schedule = function(self,parent,callback,delay)
        log('schedule',parent,callback,delay)
        capture.scheduled=callback
    end
    e.ws.table_merge = function(a,b)
        local r={}
        for _,v in ipairs(a) do r[#r+1]=v end
        for _,v in ipairs(b) do r[#r+1]=v end
        return r
    end
    e.GameBase.ConsumeNode = function(...) log('ConsumeNode',...) end
    e.ws.SubForm = function(...) log('SubForm',...) end
    e.GlobalClose = function(...) log('GlobalClose',...) end
    return e, capture
end

function client_case(e,cap,level,maxLevel,hasData,existing,effects,hasParent)
    local s=e.state
    local data=e.ws.data.dengjitupo
    s.trace,s.existing,s.effects,s.hasParent={},existing,effects,hasParent
    if not hasData then e.ws.data.dengjitupo=nil end
    cap.scheduled=nil
    e.dengjitupo_main(level,maxLevel)
    if cap.scheduled then cap.scheduled() end
    e.dengjitupo_update(level,maxLevel)
    e.dengjitupo_start()
    e.dengjitupo_effect(1)
    e.dengjitupo_effect(0)
    e.dengjitupo_close()
    e.ws.data.dengjitupo=data
    return stable(s.trace)
end
'''
