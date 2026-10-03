-- 候选窗双语显示（打字顺便记单词）
--[[
功能
----
给中文候选项的注释区（comment）加上外文释义，例如：

    1. 苹果   apple  n.
    2. 说     to speak  v.
              to talk   v.
              to say    v.

默认竖向多行显示（每条义项一行），显示在中文候选右侧。

配合方案里已有的编辑器绑定：

    Control+Shift+Return: commit_comment

把注释直接上屏，于是这个键就变成「把当前中文换成对应英文单词」。
注意：因为注释现在是多行的，这个键由 lua_processor@*bilingual_commit
接管（见 lua/bilingual_commit.lua），它会按词表查出纯单行英文再上屏，
不再依赖 commit_comment 把多行注释原样写进文档。

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
      max_length: 0         # 单行释义显示长度上限，0 表示不额外截断
      separator: "  "       # 与已有注释拼接时的分隔符
      format: "{gloss}"     # 显示格式，可用 {gloss} {lang} {word}
      multiline: true       # 释义竖排成多行；false 则挤在一行
      pos: auto             # 词性：off / auto / always（详见 format.lua）

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
local format = require("bilingual.format")

local M = {}

-- 默认配置
local DEFAULTS = {
    languages = { "en" },
    min_length = 1,
    max_length = 0,
    separator = "  ",
    format = "{gloss}",
    multiline = true,
    pos = "auto",
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


--- 取出列表中的元素，返回 ConfigItem（同时具备 map 与标量接口）。
--
-- 这是一处踩过坑的地方：librime-lua 里
--     get_at(i)                  -> ConfigItem  有 get_map() / get_value()
--     get_value_at(i)            -> ConfigValue 只有 get_string() 等标量接口
-- 而 LuaImpl::index 不做父类回退（只查当前类型的 methods/vars_get），
-- 所以对 get_value_at 的返回值调 get_map() 会失败。该失败若被 pcall
-- 吞掉就会「静默失效」——switches 读不出 name、has_switch 恒为 false，
-- 表现就是快捷键 Ctrl+Shift+T 切了也没反应。
--
-- 不同版本的 librime-lua 暴露的接口不完全一致，因此这里依次尝试两条路：
--   get_at(i)                 （较新版本）
--   get_value_at(i).element   （ConfigValue 的 element 属性，返回 ConfigItem）
-- 两条都不通就返回 nil，由调用方按「取不到」处理，绝不抛错。
local function element_at(list, i)
    local item = nil
    pcall(function() item = list:get_at(i) end)
    if item then
        return item
    end
    pcall(function() item = list:get_value_at(i).element end)
    return item
end


--- 读取列表型配置，返回 Lua 数组（元素为字符串）。
--
-- 列表元素有两种形态，都要能处理：
--   1. map，如 switches: [ { name: xx, ... } ]  -> 取 name 字段
--   2. 纯标量，如 languages: [ en ]
--
-- 取值顺序是「先试 map、再试标量」：对不需要的形态调用取值接口会抛错，
-- 由 pcall 接住即可，比先判断 type 少依赖一个接口。
local function config_list(config, path)
    local out = {}
    local ok, list = pcall(function() return config:get_list(path) end)
    if not ok or not list then
        return out
    end
    local size = list.size or 0
    for i = 0, size - 1 do
        local item = element_at(list, i)
        if item then
            -- map 形态（switches 的元素）
            local name = nil
            pcall(function()
                name = item:get_map():get_value("name"):get_string()
            end)
            if type(name) == "string" and #name > 0 then
                out[#out + 1] = name
            else
                -- 标量形态（languages 的元素）
                local scalar = nil
                pcall(function() scalar = item:get_value():get_string() end)
                if type(scalar) == "string" and #scalar > 0 then
                    out[#out + 1] = scalar
                end
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
    env.pos = get_str("pos", DEFAULTS.pos)

    -- 布尔配置：get_bool 在没有该键时会失败，用 pcall 兜底
    local function get_bool(key, default)
        local ok, v = pcall(function() return config:get_bool(ns .. "/" .. key) end)
        if ok and type(v) == "boolean" then
            return v
        end
        return default
    end
    env.multiline = get_bool("multiline", DEFAULTS.multiline)

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
            string.format("（min_length=%d, max_length=%d, multiline=%s, pos=%s, switch=%s, switches=[%s]）",
                env.min_length, env.max_length, tostring(env.multiline),
                tostring(env.pos), tostring(env.has_switch),
                table.concat(switches, ",")))
    end
end


--- 把释义按 display 格式渲染。
-- 全部取值都有兜底：即使 init 部分失败导致 env 字段缺失，
-- 这里也只退回默认格式，不会抛错影响候选生成。
local function render(env, word, gloss, lang)
    local opts = {
        multiline = env.multiline,
        pos = env.pos,
        max_length = env.max_length or 0,
    }

    -- 释义本身的多行/词性排版交给 format 模块，这里只负责套用户配置的
    -- 显示格式（format: "{gloss}" 等）。注意：内层 gsub 必须用括号包住，
    -- 只取第一个返回值。否则 gsub 会返回 (字符串, 替换次数) 两个值，
    -- 作为最后一个参数时会把「替换次数」当作外层 gsub 的第 4 个参数 n
    -- 传入；当 n == 0 时，外层 gsub 执行零次替换，结果原样返回 "{gloss}"。
    local body = format.render(gloss, opts)
    if not body or body == "" then
        return nil
    end

    local function esc(s)
        return (tostring(s or ""):gsub("%%", "%%%%"))
    end

    local text = env.format or DEFAULTS.format
    text = text:gsub("{gloss}", esc(body))
    text = text:gsub("{word}", esc(word))
    text = text:gsub("{lang}", esc(lang))
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
        -- 整段标注都包在 pcall 里：任何意外（数据异常、API 变动等）
        -- 最多只是这一个候选不加释义，绝不会影响打字本身。
        pcall(function()
            local text = cand.text
            if not text or not has_cjk(text) then
                return
            end

            local len = utf8.len(text)
            if not len or len < (env.min_length or DEFAULTS.min_length) then
                return
            end

            local gloss, lang = loader.lookup_first(text, env.languages)
            if not gloss then
                return
            end

            local extra = render(env, text, gloss, lang)
            if not extra or extra == "" then
                return
            end

            local genuine = cand:get_genuine()
            local existing = genuine.comment or ""

            if existing == "" then
                genuine.comment = extra
            elseif existing:match("^［.*］$") then
                -- corrector.lua 遗留的 ［拼音］ 标记：注释区已让出，直接替换
                genuine.comment = extra
            else
                -- 已有其它内容（多为错音提示）：拼接而非覆盖，避免丢失纠错信息
                genuine.comment = existing .. (env.separator or DEFAULTS.separator) .. extra
            end
        end)
        yield(cand)
    end
end


return M
