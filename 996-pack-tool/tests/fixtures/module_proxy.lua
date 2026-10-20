local GameData = GameData
local GameData_Object = GameData.Object
local GameData_ModuleMain = GameData.ModuleMain
local ws_mode = ws.mode

function ws_installModuleMainProxy(scriptName, mode)
    if type(mode) ~= "table" then return false end

    local tag = "ws.module.main:" .. scriptName
    local currentMeta = getmetatable(mode)

    -- 同一模块表重复注册，不重复安装元表
    if currentMeta == tag then
        return ws_mode[scriptName] == mode and rawget(mode, "main") == nil
    end

    if currentMeta ~= nil then
        print("自动化注册业务模块：" .. scriptName .. " 模块已有元表，无法接管 main")
        return false
    end

    local realMain = rawget(mode, "main")
    if realMain ~= nil and type(realMain) ~= "function" then
        print("自动化注册业务模块：" .. scriptName .. " main 必须是 function")
        return false
    end

    local proxy = function(player, ...)
        if ws_mode[scriptName] ~= mode or type(GameData_ModuleMain[scriptName]) ~= "function" then return end
        return ws.formInitCheckFun(player, scriptName, table_pack(...))
    end

    -- 清掉原始键，保证读取和后续赋值都经过元表
    rawset(mode, "main", nil)
    GameData_ModuleMain[scriptName] = realMain

    setmetatable(mode, {
        -- 避免后续 setmetatable 覆盖代理
        __metatable = tag,

        __index = function(_, key)
            if key == "main" and type(GameData_ModuleMain[scriptName]) == "function" then return proxy end
        end,

        __newindex = function(t, key, value)
            if key == "main" then
                if value ~= nil and type(value) ~= "function" then error(scriptName .. ".main 必须是 function") end
                GameData_ModuleMain[scriptName] = value
                return
            end
            rawset(t, key, value)
        end,
    })

    return true
end
