dengjitupo_name = "dengjitupo_Parent"
local dengjitupo_data = {}
local dengjitupo_config = {}

function dengjitupo_main(level, maxLevel)
    local Data = ws.data["dengjitupo"]
    if Data == nil then return end
    dengjitupo_data = Data
    dengjitupo_config = {}
    for _, config in ipairs(dengjitupo_data) do dengjitupo_config[config[1]] = config end

    local ParentHandle = GUI:GetWindow(nil, dengjitupo_name)
    if not GUI:Win_IsNull(ParentHandle) then GUI:Win_Close(ParentHandle) end

    local ui = [[
        <Img|show=4|npcbind=1|name=dengjitupo_Parent|width=610|height=450|img=public/dengjitupo/ui.png|bg=1|esc=1|anim=u1>
        <Button|x=608|width=26|height=42|nimg=public/1900000510.png|size=18|color=#ffffff|link=dengjitupo_close>
        <Node|name=特效节点|x=0|y=0>
        <Text|name=提示|x=305|y=64|a=4|text=提升等级可以压制敌人，获得PK优势!|size=18|color=#ffcc00|outline=#000000,1>
        <Text|name=野蛮冲撞|x=305|y=111|a=4|text=野蛮冲撞|size=34|color=#bfbfbf|opacity=0>
        <Button|name=突破|x=233|y=371|width=144|height=48|nimg=public/custom/buttonmax.png|text=提升等级|size=18|color=#ffffff|link=dengjitupo_start>
        <Text|name=等级|x=385|y=386|text=普通文本|size=18|color=#ffffff|outline=#000000,1>
    ]]

    local Parent = FormCreatStr(ui)
    dengjitupo_update(level, maxLevel)

    local EffectParent = GetWindow(Parent, "特效节点")
    local Label = GetWindow(Parent, "野蛮冲撞")
    if EffectParent and Label then
        local function playEffect()
            DelHandle2(EffectParent, "人物1")
            DelHandle2(EffectParent, "人物2")
            DelHandle2(EffectParent, "特效")
            GUI:setOpacity(Label, 0)

            local sfx1 = GUI:Effect_Create(EffectParent, "人物1", 190, -220, 4, 1, 0, 0, 2, 1)
            local sfx2 = GUI:Effect_Create(EffectParent, "人物2", 250, -220, 4, 1, 0, 0, 6, 1)

            ws.DelayCall(EffectParent, function()
                GUI:Effect_play(sfx1, 23, 2, false, 1)
                GUI:Timeline_FadeTo(Label, 255, 0.8, function()
                    ws.DelayCall(EffectParent, function()
                        GUI:Timeline_FadeTo(Label, 0, 0.8)
                    end, 1)
                end)
            end, 0.4)

            ws.DelayCall(EffectParent, function()
                GUI:Effect_play(sfx2, 7, 6, true, 1)
                GUI:Timeline_MoveTo(sfx1, {x = 330, y = -220}, 0.4, function()
                    GUI:Timeline_FadeTo(sfx1, 0, 0.2)
                end)
                GUI:Timeline_MoveTo(sfx2, {x = 410, y = -220}, 0.4, function()
                    GUI:Effect_play(sfx2, 0, 6, true, 1)
                    local sfx_eff = GUI:Effect_Create(EffectParent, "特效", 410, -220, 0, 10001, 0, 0, 0, 1)
                    GUI:setOpacity(sfx_eff, 0)
                    GUI:Timeline_FadeTo(sfx_eff, 255, 0.2)
                end)
            end, 0.6)
        end

        playEffect()
        SL:schedule(EffectParent, playEffect, 3)
    end
end

function dengjitupo_update(level, maxLevel)
    local Parent = GetWindow(nil, dengjitupo_name.."/ui")
    if not Parent then return end

    DelHandle2(Parent, "consume_Node")
    GUI:Text_setString(GetWindow(Parent, "等级"), "当前等级："..level.."级")
    if level >= maxLevel then return end

    local config = dengjitupo_config[level]
    if not config then return end

    GameBase.ConsumeNode(Parent, ws.table_merge(config[2], config[3]), 72, 305, 330)
end

function dengjitupo_start()
    ws.SubForm("dengjitupo.start")
end

function dengjitupo_close()
    GlobalClose(dengjitupo_name)
end

function dengjitupo_effect(idx)
    local Parent = GetWindow(nil, dengjitupo_name.."/ui")
    if not Parent then return end

    local ext = {count = 13, speed = 60, loop = 1, finishhide = 1}
    local size = GUI:getContentSize(Parent)
    DelHandle2(Parent, "成功特效")
    GUI:Frames_Create(Parent, "成功特效", size.width / 2 - 130, size.height / 2 - 70, "res/public/curr/"..(idx == 1 and "true" or "false").."2_", ".png", 1, 13, ext)
end
