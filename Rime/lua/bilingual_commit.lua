-- Ctrl+Shift+Return：把当前候选上屏为「对应的英文单词」
--[[
为什么需要这个 processor
------------------------
方案原本把 Control+Shift+Return 绑到 commit_comment，即「把注释原样上屏」。
双语释义放进注释后，这条通道就出了问题：

  1. lua 滤镜把注释渲染成多行（\n 分隔），而 librime 的
     Editor::CommitComment 是 engine_->sink()(cand->comment())，
     对换行不做任何处理 —— 会往文档里插入多行文本。
  2. 注释里还带着词性后缀（如 "to speak→v."）。
  3. 注释可能已被 corrector 追加了错音提示，混在一起会一并上屏。

因此这里接管这个按键：不看注释，直接按候选文本查双语词表，
取出第一个义项的纯英文再上屏。

键位判断
--------
用 ctrl()/shift() 方法而不是比较 repr() 字符串：
KeyEvent::repr() 按修饰键的位掩码从低位到高位拼接，顺序不确定，
拿字符串比较可能因 "Shift+Control+Return" / "Control+Shift+Return" 的
差异而匹配失败。同时接受主键盘回车的 "Return" 与小键盘的 "KP_Enter"。
]]

local loader = require("bilingual.loader")
local format = require("bilingual.format")

local M = {}

-- 未命中时返回 kNoop，把按键交还给后续组件（含原来的 commit_comment）
local kNoop = 2
-- 已处理时返回 kAccepted
local kAccepted = 1

-- 这两种键名都代表回车
local RETURN_CODES = {
    Return = true,
    KP_Enter = true,
}


local function log(msg)
    local L = _G.log
    if L and L.info then
        L.info("[bilingual_commit] " .. tostring(msg))
    end
end


--- 从配置读取语言列表（沿用 bilingual_filter 的配置节）。
-- languages 是纯标量列表（[ en ]），用 get_string() 显式取值；
-- 不要用 .value —— 那是 ConfigValue::GetString() 的隐式包装，
-- 遇到 map 型元素会静默返回 nil，容易埋下难查的问题。
local function read_languages(env)
    local langs = { "en" }
    local ok = pcall(function()
        local config = env.engine.schema.config
        local list = config:get_list("bilingual_filter/languages")
        if not list then
            return
        end
        local out = {}
        for i = 0, (list.size or 0) - 1 do
            local v = list:get_value_at(i)
            local name = v and v:get_string()
            if type(name) == "string" and #name > 0 then
                out[#out + 1] = name
            end
        end
        if #out > 0 then
            langs = out
        end
    end)
    if not ok then
        -- 配置读取失败时退回默认值，不影响上屏
        return { "en" }
    end
    return langs
end


function M.init(env)
    env.languages = read_languages(env)

    -- 复用 loader 的缓存：滤镜初始化时通常已加载过，这里不会重复读盘
    local stats = loader.load(env.languages)
    local total = 0
    for _, n in pairs(stats) do
        total = total + n
    end
    env.loaded = total

    if total == 0 then
        log("词表未加载，Ctrl+Shift+Return 将退回原有行为")
    else
        log(string.format("就绪：%d 条（%s）", total, table.concat(env.languages, ",")))
    end
end


function M.func(key, env)
    -- 只处理 Ctrl+Shift+回车
    if not key:ctrl() or not key:shift() or key:release() then
        return kNoop
    end
    if not RETURN_CODES[key:repr():match("([%w_]+)$") or ""] then
        return kNoop
    end

    local context = env.engine.context
    if not (context:is_composing() or context:has_menu()) then
        return kNoop
    end

    if not env.loaded or env.loaded == 0 then
        return kNoop
    end

    local cand = context:get_selected_candidate()
    if not cand or not cand.text or cand.text == "" then
        return kNoop
    end

    local gloss = loader.lookup_first(cand.text, env.languages)
    if not gloss then
        -- 这个词没有英文释义：不拦截，交回 commit_comment 等原有行为
        return kNoop
    end

    local text = format.plain(gloss)
    if not text or text == "" then
        return kNoop
    end

    -- 上屏失败也不吞掉按键
    local ok = pcall(function()
        env.engine:commit_text(text)
        context:clear()
    end)
    if not ok then
        return kNoop
    end

    return kAccepted
end


return M
