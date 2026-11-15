-- 先按覆盖编码范围排序，同范围内再按实际确认次数调整候选顺序。
-- 只统计真正上屏的中文候选，不统计单纯输入但未确认的编码。

local M = {}

local function paths_for_counts()
    local sep = "/"
    local dir = rime_api:get_user_data_dir()
    local dist = rime_api:get_distribution_code_name()
    if dist and dist:lower():find("weasel", 1, true) then
        sep = "\\"
    end
    local root = dir:gsub("[/\\\\]Rime$", "")
    return root .. sep .. "data" .. sep .. "frequency_counts.tsv",
        dir .. sep .. "lua" .. sep .. "frequency_counts.tsv"
end

local function file_exists(path)
    local fd = io.open(path, "r")
    if fd then
        fd:close()
        return true
    end
    return false
end

local function is_cjk(text)
    if type(text) ~= "string" or text == "" then
        return false
    end
    local len = utf8.len(text)
    if not len then
        return false
    end
    for i = 1, len do
        local code = utf8.codepoint(text, utf8.offset(text, i))
        if (code >= 0x3400 and code <= 0x4DBF)
            or (code >= 0x4E00 and code <= 0x9FFF)
            or (code >= 0xF900 and code <= 0xFAFF) then
            return true
        end
    end
    return false
end

local function load_counts(path)
    local counts = {}
    local fd = io.open(path, "r")
    if not fd then
        return counts
    end
    for line in fd:lines() do
        local word, count = line:match("^([^\t]+)\t(%d+)$")
        if word and count then
            counts[word] = tonumber(count) or 0
        end
    end
    fd:close()
    return counts
end

local function save_counts(path, counts)
    local fd = io.open(path, "w")
    if not fd then
        return
    end
    local words = {}
    for word, count in pairs(counts) do
        words[#words + 1] = { word = word, count = count }
    end
    table.sort(words, function(a, b)
        if a.count == b.count then
            return a.word < b.word
        end
        return a.count > b.count
    end)
    for _, item in ipairs(words) do
        fd:write(item.word, "\t", item.count, "\n")
    end
    fd:close()
end

function M.init(env)
    env.count_path, env.legacy_count_path = paths_for_counts()
    local load_path = file_exists(env.count_path) and env.count_path or env.legacy_count_path
    env.counts = load_counts(load_path)
    env.pending = 0
    env.commit_notifier = env.engine.context.commit_notifier:connect(function(ctx)
        local text = ctx:get_commit_text()
        if not is_cjk(text) then
            return
        end
        env.counts[text] = (env.counts[text] or 0) + 1
        env.pending = env.pending + 1
        -- 不在每次上屏时重写整个文件，最多延迟 10 次提交保存。
        if env.pending >= 10 then
            save_counts(env.count_path, env.counts)
            env.pending = 0
        end
    end)
end

-- 只缓存首批候选；不能先耗尽整个候选流，否则首屏要等全部造句完成。
local SORT_LIMIT = 64

function M.func(input, env)
    local candidates = {}
    local flushed = false
    local function flush()
        table.sort(candidates, function(a, b)
            -- 先比较覆盖的编码范围，不能让高频单字压过完整词组。
            if a.span ~= b.span then
                return a.span > b.span
            end
            if a.count == b.count then
                return a.index < b.index
            end
            return a.count > b.count
        end)
        for _, item in ipairs(candidates) do
            yield(item.candidate)
        end
        candidates = {}
        flushed = true
    end

    for cand in input:iter() do
        if flushed then
            -- 后续页保持上游顺序，不再缓存或排序。
            yield(cand)
        else
            candidates[#candidates + 1] = {
                candidate = cand,
                span = cand._end - cand.start,
                index = #candidates + 1,
                count = env.counts[cand.text] or 0,
            }
            if #candidates >= SORT_LIMIT then
                flush()
            end
        end
    end
    if not flushed then
        flush()
    end
end

function M.fini(env)
    if env.pending and env.pending > 0 then
        save_counts(env.count_path, env.counts)
        env.pending = 0
    end
    if env.commit_notifier then
        env.commit_notifier:disconnect()
    end
end

return M