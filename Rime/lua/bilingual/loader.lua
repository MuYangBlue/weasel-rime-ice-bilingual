-- 候选窗双语数据的加载器
--[[
用途
----
读取同目录下的 <语言>.tsv 数据文件，建立「词 -> 释义」查询表，供
bilingual_filter.lua 使用。

数据文件格式（TSV，UTF-8）
--------------------------
以 # 开头的行为注释；数据行形如：
    苹果<Tab>apple
    电脑<Tab>computer

数据由 tools/build_bilingual_dict.py 生成，不要手工编辑。

多语言扩展方式
--------------
本加载器按「语言代码 = 文件名」的方式扫描数据目录，因此新增一种语言
只需把数据文件放进同一个目录，并在方案的 bilingual/languages 里加上
对应代码即可，无需改动任何 Lua 代码。例如以后加入日语：
    1) 放入 lua/bilingual/ja.tsv
    2) 配置改为 languages: [ en, ja ]

目录定位
--------
优先用本文件自身的路径推导（最可靠）；失败时退回
rime_api:get_user_data_dir()。
]]

local M = {}

-- 语言代码 -> { 词 = 释义 }
M.dicts = {}

-- 实际使用的数据目录（诊断用）
M.data_dir = nil

-- 加载过程中的错误信息（诊断用）
M.errors = {}


-- ---------------------------------------------------------------- 工具

-- 调用时再取全局 log，避免捕获到加载瞬间的值。
-- 注意：若写成 `local function log(...)` 并在函数体内直接用 log.info，
-- 函数体里的 log 会指向它自己，导致日志永远不输出。
local function log(msg)
    local L = _G.log
    if L and L.info then
        L.info("[bilingual] " .. tostring(msg))
    end
end


--- 由本文件的调试信息推导所在目录。
local function script_dir()
    local info = debug and debug.getinfo(1, "S")
    local src = info and info.source
    if type(src) ~= "string" then
        return nil
    end
    if src:sub(1, 1) == "@" then
        src = src:sub(2)
    end
    -- 取出最后一段路径分隔符之前的部分
    local dir = src:match("^(.*)[/\\][^/\\]+$")
    return dir
end


--- 返回可能的数据目录候选列表（按优先级）。
local function candidate_dirs()
    local dirs = {}
    local seen = {}

    local function push(d)
        if type(d) == "string" and #d > 0 and not seen[d] then
            seen[d] = true
            dirs[#dirs + 1] = d
        end
    end

    push(script_dir())

    local ok, user_dir = pcall(function()
        return rime_api:get_user_data_dir()
    end)
    if ok and user_dir then
        push(user_dir .. "/lua/bilingual")
        push(user_dir .. "\\lua\\bilingual")
    end

    return dirs
end


--- 按候选目录依次尝试打开文件，返回已打开的文件句柄与实际路径。
local function open_in_any_dir(dirs, filename)
    local last_path = nil
    for _, dir in ipairs(dirs) do
        local sep = dir:find("\\") and "\\" or "/"
        local path = dir .. sep .. filename
        last_path = path
        local f = io.open(path, "r")
        if f then
            return f, path
        end
    end
    return nil, last_path
end


--- 解析 TSV，返回 表 与 条数。
local function parse_tsv(handle)
    local tbl = {}
    local count = 0
    for line in handle:lines() do
        if line:sub(1, 1) ~= "#" then
            -- 去掉可能的行尾 \r
            line = line:gsub("\r$", "")
            local word, gloss = line:match("^([^\t]+)\t(.+)$")
            if word and gloss and #gloss > 0 then
                tbl[word] = gloss
                count = count + 1
            end
        end
    end
    return tbl, count
end


-- ---------------------------------------------------------------- 对外接口

--- 加载指定语言的词表。
-- @param lang  语言代码，对应 <lang>.tsv
-- @param dirs  可选，数据目录候选列表
-- @return 词条数（0 表示失败）
function M.load_language(lang, dirs)
    dirs = dirs or candidate_dirs()
    local handle, path = open_in_any_dir(dirs, lang .. ".tsv")
    if not handle then
        local msg = string.format("找不到数据文件 %s.tsv（已尝试：%s）", lang, path or "无可用目录")
        M.errors[#M.errors + 1] = msg
        log(msg)
        return 0
    end

    local tbl, count = parse_tsv(handle)
    handle:close()

    M.dicts[lang] = tbl
    M.data_dir = path:match("^(.*)[/\\][^/\\]+$") or M.data_dir
    log(string.format("已加载 %s：%d 条（%s）", lang, count, path))
    return count
end


--- 批量加载多种语言。
-- @param langs  语言代码列表，如 { "en" } 或 { "en", "ja" }
-- @return 语言代码 -> 条数
function M.load(langs, dirs)
    dirs = dirs or candidate_dirs()
    local result = {}
    if type(langs) ~= "table" then
        return result
    end
    for _, lang in ipairs(langs) do
        if type(lang) == "string" and not M.dicts[lang] then
            result[lang] = M.load_language(lang, dirs)
        elseif M.dicts[lang] then
            -- 已加载过，直接复用
            local n = 0
            for _ in pairs(M.dicts[lang]) do n = n + 1 end
            result[lang] = n
        end
    end
    return result
end


--- 查询单个词的释义。
-- @param word  中文词
-- @param lang  语言代码
-- @return 释义字符串，未命中返回 nil
function M.lookup(word, lang)
    local dict = lang and M.dicts[lang]
    if not dict then
        return nil
    end
    return dict[word]
end


--- 按顺序查询多种语言，返回第一个命中的释义。
-- @return 释义, 命中的语言代码
function M.lookup_first(word, langs)
    if type(langs) ~= "table" then
        return nil, nil
    end
    for _, lang in ipairs(langs) do
        local gloss = M.lookup(word, lang)
        if gloss then
            return gloss, lang
        end
    end
    return nil, nil
end


--- 返回已加载语言的条数统计（诊断用）。
function M.stats()
    local out = {}
    for lang, dict in pairs(M.dicts) do
        local n = 0
        for _ in pairs(dict) do n = n + 1 end
        out[lang] = n
    end
    return out
end


--- 卸载已加载的数据（重新部署时释放内存）。
function M.clear()
    M.dicts = {}
    M.errors = {}
    M.data_dir = nil
end


return M
