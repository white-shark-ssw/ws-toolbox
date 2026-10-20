local GameData = GameData

function table_pack(...)
    return { n = select("#", ...), ... }
end

function ws.RegisterModule(scriptName, mode, config, routeId)
    config = config or {}

    if type(scriptName) ~= "string" or scriptName == "" then
        print("自动化注册业务模块：唯一包名 传递错误！")
        return
    end

    if type(mode) ~= "table" then
        print("自动化注册业务模块：" .. scriptName .. " 模块局部实体表类型错误！")
        return
    end

    local originScriptName = scriptName
    local realScriptName = scriptName
    local isRouteModule = false

    -- 第四参数 routeId 存在时，表示注册渠道/脚本路由副本模块
    if routeId ~= nil and routeId ~= "" then
        routeId = tostring(routeId)

        if routeId ~= "" then
            isRouteModule = true
            realScriptName = originScriptName .. "__route__" .. routeId

            local routeMap = GameData.ModuleRoute[originScriptName]
            if routeMap == nil then
                routeMap = {}
                GameData.ModuleRoute[originScriptName] = routeMap
            end

            routeMap[routeId] = realScriptName
            scriptName = realScriptName

            -- 渠道副本默认不走 dataInitCheck
            if config._version == nil then
                config._version = 0
            end
        end
    end

    -- if _G[scriptName] and _G[scriptName] ~= mode then
    --     print("警告：自动化注册业务模块 出现重复覆盖全局模块 -> " .. scriptName)
    -- end

    -- 核心路由挂载
    _G[scriptName] = mode
    ws.mode[scriptName] = mode

    mode.name = scriptName

    mode._version = config._version or 0
    mode.dataInitCheck = not isRouteModule and mode._version > 0
    -- mode.dataInitCheck = (mode._version > 0)    -- 由_version确定是否需要开启 dataInitCheck 参数

    if mode.dataInitCheck and not ws_installModuleMainProxy(scriptName, mode) then
        error("自动化注册业务模块：" .. scriptName .. " main 自动代理安装失败")
    end

    -- 渠道副本模块不自动绑定 NPC，避免和原模块抢 NPC 入口
    if not isRouteModule then
        -- 自动绑定 NPC
        if config.npcRelations and type(config.npcRelations) == "table" then
            ws.mode.setnpcRelations(scriptName, config.npcRelations)
        end
    end

    -- 自动注册客户端白名单
    if config.formFuncs and type(config.formFuncs) == "table" then
        ws.SetFormFunc(config.formFuncs)
    end

    if config.formFuncskf and type(config.formFuncskf) == "table" then
        ws.SetFormFuncKf(config.formFuncskf)
    end

    return scriptName
end

function ws.GetRouteMode(moduleName, routeId)
    if moduleName == nil then return nil end
    moduleName = tostring(moduleName)
    if moduleName == "" then return nil end

    local realModule = moduleName
    if routeId ~= nil and routeId ~= false then
        local routeMap = GameData.ModuleRoute[moduleName]
        local routeModule = routeMap and routeMap[tostring(routeId)]
        if routeModule ~= nil and routeModule ~= "" then
            realModule = tostring(routeModule)
        end
    end

    return ws.mode[realModule]
end
