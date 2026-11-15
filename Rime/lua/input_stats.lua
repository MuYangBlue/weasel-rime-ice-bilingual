-- 输入统计：只保存每日汇总数字，不保存实际输入内容。
--
-- 表结构（TSV）：
--   date  first_commit  last_commit  commits  total_chars
--   cjk_chars  latin_chars  digit_chars  punct_chars  hourly  active_seconds
--
-- hourly 是 24 个逗号分隔的整数，第 1 个表示 0 点，第 24 个表示 23 点，
-- 记录每个小时上屏的字符数，供用户中心画码字时段分布。
-- active_seconds 是 3.4.30 新增的第 11 列，累计当天「相邻两次上屏间隔不超过
-- ACTIVE_GAP 秒」的时长，用来近似真实码字时间。用首末上屏时间相减会把看网页、
-- 想事情的空档都算进去，明显虚高，所以按逐段间隔累加。
--
-- 为了不破坏历史数据，读取时兼容三种旧格式：
--   9 列（3.4.28 之前）：无 hourly、无 active_seconds，均按 0 处理；
--   10 列（3.4.28 起）：有 hourly、无 active_seconds；
--   11 列（3.4.30 起）：完整。
-- 缺失字段一律按 0 处理，不伪造历史数据。

local M = {}
local HEADER = "date\tfirst_commit\tlast_commit\tcommits\ttotal_chars\tcjk_chars\tlatin_chars\tdigit_chars\tpunct_chars\thourly\tactive_seconds\n"
local HOURS = 24
-- 累计多少次上屏后落盘一次，避免每次上屏都重写整个文件。
local SAVE_EVERY = 10
-- 相邻两次上屏间隔不超过该秒数才计入活跃时长；超过视为离开，不计。
local ACTIVE_GAP = 120

local function stats_paths()
    local sep = "/"
    local dir = rime_api:get_user_data_dir()
    local dist = rime_api:get_distribution_code_name()
    if dist and dist:lower():find("weasel", 1, true) then
        sep = "\\"
    end
    local root = dir:gsub("[/\\\\]Rime$", "")
    return root .. sep .. "data" .. sep .. "input_stats.tsv",
        dir .. sep .. "lua" .. sep .. "input_stats.tsv"
end

local function file_exists(path)
    local fd = io.open(path, "r")
    if fd then
        fd:close()
        return true
    end
    return false
end

local function now()
    return os.date("%Y-%m-%d"), os.date("%H:%M:%S"), tonumber(os.date("%H"))
end

local function new_hourly()
    local hourly = {}
    for i = 1, HOURS do
        hourly[i] = 0
    end
    return hourly
end

local function parse_hourly(text)
    local hourly = new_hourly()
    if type(text) ~= "string" then
        return hourly
    end
    local index = 0
    for value in text:gmatch("[^,]+") do
        index = index + 1
        if index > HOURS then
            break
        end
        hourly[index] = tonumber(value) or 0
    end
    return hourly
end

local function join_hourly(hourly)
    local parts = {}
    for i = 1, HOURS do
        parts[i] = tostring(hourly[i] or 0)
    end
    return table.concat(parts, ",")
end

local function classify(text)
    local result = {
        total_chars = 0,
        cjk_chars = 0,
        latin_chars = 0,
        digit_chars = 0,
        punct_chars = 0,
    }
    local len = utf8.len(text)
    if not len then
        return result
    end
    for i = 1, len do
        local code = utf8.codepoint(text, utf8.offset(text, i))
        if code == 9 or code == 10 or code == 13 or code == 32 then
            -- 空白不计入字符数。
        else
            result.total_chars = result.total_chars + 1
            if (code >= 0x3400 and code <= 0x4DBF)
                or (code >= 0x4E00 and code <= 0x9FFF)
                or (code >= 0xF900 and code <= 0xFAFF) then
                result.cjk_chars = result.cjk_chars + 1
            elseif (code >= 0x41 and code <= 0x5A)
                or (code >= 0x61 and code <= 0x7A) then
                result.latin_chars = result.latin_chars + 1
            elseif code >= 0x30 and code <= 0x39 then
                result.digit_chars = result.digit_chars + 1
            else
                result.punct_chars = result.punct_chars + 1
            end
        end
    end
    return result
end

local function load(path)
    local rows = {}
    local fd = io.open(path, "r")
    if not fd then
        return rows
    end
    fd:read("*l")
    for line in fd:lines() do
        local fields = {}
        for field in line:gmatch("[^\t]+") do
            fields[#fields + 1] = field
        end
        -- 9 / 10 / 11 列都要能读，缺失字段按 0 处理，不伪造历史数据。
        if #fields == 9 or #fields == 10 or #fields == 11 then
            rows[fields[1]] = {
                first_commit = fields[2],
                last_commit = fields[3],
                commits = tonumber(fields[4]) or 0,
                total_chars = tonumber(fields[5]) or 0,
                cjk_chars = tonumber(fields[6]) or 0,
                latin_chars = tonumber(fields[7]) or 0,
                digit_chars = tonumber(fields[8]) or 0,
                punct_chars = tonumber(fields[9]) or 0,
                hourly = parse_hourly(fields[10]),
                active_seconds = tonumber(fields[11]) or 0,
            }
        end
    end
    fd:close()
    return rows
end

local function save(path, rows)
    local fd = io.open(path, "w")
    if not fd then
        return
    end
    fd:write(HEADER)
    local dates = {}
    for date in pairs(rows) do
        dates[#dates + 1] = date
    end
    table.sort(dates)
    for _, date in ipairs(dates) do
        local row = rows[date]
        fd:write(string.format(
            "%s\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\n",
            date,
            row.first_commit,
            row.last_commit,
            row.commits,
            row.total_chars,
            row.cjk_chars,
            row.latin_chars,
            row.digit_chars,
            row.punct_chars,
            join_hourly(row.hourly),
            row.active_seconds or 0
        ))
    end
    fd:close()
end

local function flush(env)
    if env.pending and env.pending > 0 then
        save(env.stats_path, env.rows)
        env.pending = 0
    end
end

function M.init(env)
    env.stats_path, env.legacy_stats_path = stats_paths()
    local load_path = file_exists(env.stats_path) and env.stats_path or env.legacy_stats_path
    env.rows = load(load_path)
    env.pending = 0
    -- 上次上屏的时间戳；nil 表示首个间隔不计入活跃时长。
    env.last_commit_at = nil
    env.commit_notifier = env.engine.context.commit_notifier:connect(function(ctx)
        local text = ctx:get_commit_text()
        if type(text) ~= "string" or text == "" then
            return
        end
        local date, clock, hour = now()
        local counts = classify(text)
        if counts.total_chars == 0 then
            return
        end
        local row = env.rows[date]
        if not row then
            row = {
                first_commit = clock,
                last_commit = clock,
                commits = 0,
                total_chars = 0,
                cjk_chars = 0,
                latin_chars = 0,
                digit_chars = 0,
                punct_chars = 0,
                hourly = new_hourly(),
                active_seconds = 0,
            }
            env.rows[date] = row
        end
        row.last_commit = clock

        -- 活跃时长：只累加「与上次上屏间隔不超过 ACTIVE_GAP 秒」的那一段。
        -- 进程重启后 env.last_commit_at 为空，首个间隔不计，宁可少算也不虚高。
        local now_ts = os.time()
        if env.last_commit_at and (now_ts - env.last_commit_at) <= ACTIVE_GAP then
            local delta = now_ts - env.last_commit_at
            if delta > 0 then
                row.active_seconds = (row.active_seconds or 0) + delta
            end
        end
        env.last_commit_at = now_ts

        row.commits = row.commits + 1
        row.total_chars = row.total_chars + counts.total_chars
        row.cjk_chars = row.cjk_chars + counts.cjk_chars
        row.latin_chars = row.latin_chars + counts.latin_chars
        row.digit_chars = row.digit_chars + counts.digit_chars
        row.punct_chars = row.punct_chars + counts.punct_chars
        if hour and hour >= 0 and hour < HOURS then
            -- hourly 第 1 项对应 0 点。
            local index = hour + 1
            row.hourly[index] = (row.hourly[index] or 0) + counts.total_chars
        end
        env.pending = env.pending + 1
        if env.pending >= SAVE_EVERY then
            flush(env)
        end
    end)
end

function M.func(_, _)
    return 2
end

function M.fini(env)
    flush(env)
    if env.commit_notifier then
        env.commit_notifier:disconnect()
    end
end

return M
