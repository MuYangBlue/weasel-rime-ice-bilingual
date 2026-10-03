-- 候选窗双语显示（打字顺便记单词）
--[[
功能
----
给中文候选项的注释区（comment）加上外文释义，例如：

    1. 苹果   apple
    2. 电脑   computer

配合方案里已有的编辑器绑定：

    Control+Shift+Return: commit_comment

把注释直接上屏，于是这个键就变成「把当前中文换成对应英文单词」。

挂载方式（写在 rime_ice.custom.yaml 的 patch 里）
------------------------------------------------
    engine/filters:
      "engine/filters/@before 7": lua_filter@*bilingual_filter

位置很关键：必须排在 corrector.lua 之后（它会清空 comment，
把注释区让出来），并且排在 simplifier@emoji / 简繁转换之前
（那时文本还是简体汉字，才查得到表）。

配置项（命名空间 bilingual_filter，即滤镜自身名称）
----------------------------------------------------
    bilingual_filter:
      languages: [ en ]     # 要显示的语言，按顺序取第一个命中的
      min_length: 1         # 只给长度 >= 此值的词加释义（2 = 只给词组加）
      max_length: 0         # 释义显示长度上限，0 表示不额外截断
      separator: " "        # 与已有注释拼接时的分隔符
      format: "{gloss}"     # 显示格式，可用 {gloss} {lang} {word}

配置节名之所以叫 bilingual_filter，是因为 librime-lua 把
env.name_space 设为「* + 滤镜名」，现有 pin_cand_filter、long_word_filter
等均遵循此约定。若需要换名字，把滤镜写成 lua_filter@*bilingual_filter@你的名字，
则配置节名随之变为「你的名字」。

开关
----
    switches 里加一项 name: bilingual，即可用快捷键开关；
    本滤镜通过 context:get_option('bilingual') 读取，默认视为开启。
]]

local loader = require("bilingual.loader")

local M = {}

-- 默认配置
local DEFAULTS = {
    languages = { "en" },
    min_length = 1,
    max_length = 0,
    separator = " ",
    format = "{gloss}",
}

-- 汉字判定（含扩展 A 区与兼容区）。
-- 只用 utf8.codepoint，不用 utf8.codes —— 后者需 Lua 5.4，
-- 而本项目其余脚本仅使用 utf8.len / utf8.offset 这类通用接口。
local function has_cjk(s)
    if not s or #s == 0 then
        return false
    end
    local ok, hit = pcall(function()
        local len = utf8.len(s)
        if not len then
            return false
        end
        for i = 1, len do
            local code = utf8.codepoint(s, utf8.offset(s, i))
            if (code >= 0x3400 and code <= 0x4DBF)
                or (code >= 0x4E00 and code <= 0x9FFF)
                or (code >= 0xF900 and code <= 0xFAFF) then
                return true
            end
        end
        return false
    end)
    return ok and hit or false
end


-- 调用时再取全局 log，避免捕获到加载瞬间的值。
-- 注意：若写成 `local function log(...)` 并在函数体内用 log.info，
-- 函数体里的 log 会指向它自己，导致日志永远不输出。
local function log(msg)
    local L = _G.log
    if L and L.info then
        L.info("[bilingual] " .. tostring(msg))
    end
end


--- 读取列表型配置，返回 Lua 数组（元素为字符串）。
local function config_list(config, path)
    local out = {}
    local ok, list = pcall(function() return config:get_list(path) end)
    if not ok or not list then
        return out
    end
    local size = list.size or 0
    for i = 0, size - 1 do
        local v = list:get_value_at(i)
        if v then
            -- 元素可能是纯字符串（如 languages），也可能是 map（如 switches）
            if type(v.value) == "string" then
                out[#out + 1] = v.value
            elseif type(v.value) == "table" and v.value.name then
                out[#out + 1] = v.value.name
            end
        end
    end
    return out
end


function M.init(env)
    local config = env.engine.schema.config
    local ns = (env.name_space or ""):gsub("^*", "")
    env.ns = ns

    -- 语言列表
    local langs = config_list(config, ns .. "/languages")
    if #langs == 0 then
        langs = DEFAULTS.languages
    end
    env.languages = langs

    -- 数值与字符串配置
    local function get_int(key, default)
        local ok, v = pcall(function() return config:get_int(ns .. "/" .. key) end)
        if ok and type(v) == "number" then
            return v
        end
        return default
    end

    local function get_str(key, default)
        local ok, v = pcall(function() return config:get_string(ns .. "/" .. key) end)
        if ok and type(v) == "string" and #v > 0 then
            return v
        end
        return default
    end

    env.min_length = get_int("min_length", DEFAULTS.min_length)
    env.max_length = get_int("max_length", DEFAULTS.max_length)
    env.separator = get_str("separator", DEFAULTS.separator)
    env.format = get_str("format", DEFAULTS.format)

    -- 判断 switches 里是否声明了 bilingual 开关。
    -- 若未声明，get_option 会返回 false，导致功能被误判为「已关闭」，
    -- 因此这里以「开关是否声明」为准来决定默认值。
    env.has_switch = false
    local switches = config_list(config, "switches")
    for _, item in ipairs(switches) do
        if item == "bilingual" then
            env.has_switch = true
            break
        end
    end

    -- 加载词表（loader 内部会跳过已加载的语言，供多方案复用）
    local stats = loader.load(env.languages)
    local loaded = 0
    for _, n in pairs(stats) do
        loaded = loaded + n
    end
    env.loaded = loaded

    if loaded == 0 then
        log("未能加载任何词表，双语显示将不生效")
        for _, e in ipairs(loader.errors) do
            log("  " .. tostring(e))
        end
    else
        local parts = {}
        for lang, n in pairs(stats) do
            parts[#parts + 1] = string.format("%s=%d", lang, n)
        end
        log("初始化完成：" .. table.concat(parts, " ") ..
            string.format("（min_length=%d, max_length=%d, switch=%s）",
                env.min_length, env.max_length, tostring(env.has_switch)))
    end
end


--- 把释义按 display 格式渲染。
local function render(env, word, gloss, lang)
    local text = env.format
    text = text:gsub("{gloss}", gloss or "")
    text = text:gsub("{word}", word or "")
    text = text:gsub("{lang}", lang or "")

    if env.max_length and env.max_length > 0 and #text > env.max_length then
        local cut = text:sub(1, env.max_length)
        local sp = cut:match("^(.*)%s")
        text = sp and sp or cut
        text = text:gsub("[%s;,%-]+$", "")
    end
    return text
end


function M.func(input, env)
    -- 开关处理：只有「声明了开关 且 开关被明确关掉」时才跳过。
    -- 未声明开关时 get_option 返回 false，不能据此判定为关闭。
    if env.has_switch then
        local off = false
        pcall(function()
            off = env.engine.context:get_option("bilingual") == false
        end)
        if off then
            for cand in input:iter() do
                yield(cand)
            end
            return
        end
    end

    -- 没有数据时不做任何改动
    if not env.loaded or env.loaded == 0 then
        for cand in input:iter() do
            yield(cand)
        end
        return
    end

    for cand in input:iter() do
        local text = cand.text

        -- 只处理含汉字、且长度达标的候选
        if text and has_cjk(text) and utf8.len(text) and utf8.len(text) >= env.min_length then
            local gloss, lang = loader.lookup_first(text, env.languages)
            if gloss then
                local extra = render(env, text, gloss, lang)
                local genuine = cand:get_genuine()
                local existing = genuine.comment or ""

                if existing == "" then
                    genuine.comment = extra
                else
                    -- 注释区已有内容（多为 corrector.lua 的错音提示）。
                    -- 若只是遗留的 ［拼音］ 标记则直接替换，否则拼接，避免丢失纠错信息。
                    if existing:match("^［.*］$") then
                        genuine.comment = extra
                    else
                        genuine.comment = existing .. env.separator .. extra
                    end
                end
            end
        end
        yield(cand)
    end
end


return M
