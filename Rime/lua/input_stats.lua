-- 输入统计：只保存每日汇总数字，不保存实际输入内容。
--
-- 表结构（TSV）：
--   date  first_commit  last_commit  commits  total_chars
--   cjk_chars  latin_chars  digit_chars  punct_chars  hourly  active_seconds
--
-- hourly 是 24 个逗号分隔的整数，第 1 个表示 0 点，第 24 个表示 23 点，
-- 记录每个小时上屏的字符数，供用户中心画码字时段分布。
-- active_seconds 是 3.4.30 新增的第 11 列，3.4.31 起改为按键级口径，对齐阅文
-- 作家助手的「3 秒活跃窗口」：
--
--   3.4.30：按「相邻两次上屏间隔」累加，间隔 ≤120 秒就整段计入。
--           上屏一次后想事情 100 秒，这 100 秒全被算成码字，明显虚高。
--   3.4.31：按「相邻两次按键间隔」累加，每次只计 min(间隔, 3) 秒。
--           超过 3 秒的部分视为空档，不计入。
--
-- 为什么用「按键时刻补记」而不是定时器：Rime 的 Lua 环境没有定时器，
-- processor 只在按键时被调用。因此无法复刻「每秒 tick、3 秒后自动停表」，
-- 只能在下次按键到来时回算上一段。结果等价：最后一次按键之后的静默
-- 不再计入，正是阅文空闲检测的同一效果。
--
-- 语义提醒：输入法看不到文档，这个时长是「全系统键盘活跃时长」，
-- 聊天、写代码、填表同样会被计入，不是纯粹的写作时间。
--
-- 为了不破坏历史数据，读取时兼容三种旧格式：
--   9 列（3.4.28 之前）：无 hourly、无 active_seconds，均按 0 处理；
--   10 列（3.4.28 起）：有 hourly、无 active_seconds；
--   11 列（3.4.30 起）：完整。
-- 缺失字段一律按 0 处理，不伪造历史数据。

local M = {}
local HEADER = "date\tfirst_commit\tlast_commit\tcommits\ttotal_chars\tcjk_chars\tlatin_chars\tdigit_chars\tpunct_chars\thourly\tactive_seconds\n"
local HOURS = 24
-- 每多少次上屏落盘一次。
-- 取 1：每次上屏都写，数据窗口最小（最多丢一个上屏周期）。上屏约每十几秒
-- 一次，对响应速度无影响，磁盘开销也可忽略（见下方说明）。
--
-- 为什么按键侧不参与写盘：按键高达每次击键一次（日码 1 万字约 2.8 万次），
-- 若按按键频率重写文件，会在打字过程中反复插入磁盘 I/O，造成可感知的卡顿。
-- 因此按键只累加内存，由下一次上屏顺带落盘。
local SAVE_EVERY = 1
-- 活跃窗口：两次按键间隔中只把前 ACTIVE_GAP 秒计为码字时长，其余算空档。
-- 3 秒与阅文作家助手 writerStatus 的 idleDuration < 3 对齐。
local ACTIVE_GAP = 3
-- processor 返回值：不拦截按键，原样交给后续组件。
local kNoop = 2

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
    -- 上屏计数达到阈值，或按键侧有未落盘的时长（env.dirty），都要写一次。
    -- 两者共用同一份文件，一次写入即可同时覆盖。
    if (env.pending and env.pending > 0) or env.dirty then
        save(env.stats_path, env.rows)
        env.pending = 0
        env.dirty = false
    end
end

function M.init(env)
    env.stats_path, env.legacy_stats_path = stats_paths()
    local load_path = file_exists(env.stats_path) and env.stats_path or env.legacy_stats_path
    env.rows = load(load_path)
    env.pending = 0
    -- 是否有按键侧的时长尚未落盘。上屏时会一并写出并清除。
    env.dirty = false
    -- 上次按键的时间戳；nil 表示首个间隔不计入活跃时长。
    -- 与 last_commit_at 分开：字数看上屏，时长看按键，互不干扰。
    env.last_key_at = nil
    -- 上次上屏的时间戳，仅用于记录 first/last_commit 展示字段。
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

        -- 活跃时长已由 M.func（按键级）结算，这里只更新上屏时间戳，不再计时。
        -- 分开的原因：上屏是一整串字，间隔不代表打字强度；按键才是真实输入节奏。
        env.last_commit_at = os.time()

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

--- 按键级计时：每次按键记录间隔并结算活跃时长。
--
-- 这是与传统「按上屏结算」的关键差别。上屏是一整串字，两次上屏间隔 100 秒
-- 可能只是中间在想事情，把它整段当码字会明显虚高；按键间隔才反映真实节奏。
--
-- 结算方式：只把「距上次按键的前 ACTIVE_GAP 秒」计入，超出部分视为空档。
-- 相当于阅文 idleDuration < 3 的效果——最后一次按键后的静默不计时。
--
-- 必须返回 kNoop，否则会吞掉正常输入。
function M.func(key, env)
    -- 只统计按下，不统计释放，否则一次击键会算两次间隔。
    if key:release() then
        return kNoop
    end

    local now_ts = os.time()
    if env.last_key_at then
        local delta = now_ts - env.last_key_at
        if delta > 0 then
            -- 关键：截断到 ACTIVE_GAP，超出的空档不计。
            local counted = delta
            if counted > ACTIVE_GAP then
                counted = ACTIVE_GAP
            end
            local date = os.date("%Y-%m-%d")
            local row = env.rows[date]
            if row then
                row.active_seconds = (row.active_seconds or 0) + counted
                -- 只累加内存，不写盘。由下一次上屏（SAVE_EVERY=1）顺带落盘，
                -- 避免每次击键都触发磁盘写入而拖慢输入响应。
                env.dirty = true
            end
            -- row 不存在时（当天还没上屏过）不建行，避免产生 0 字却有秒数的记录。
        end
    end
    env.last_key_at = now_ts
    return kNoop
end

function M.fini(env)
    -- flush 同时处理上屏计数与按键侧的 dirty 标记，退出前兜底写一次。
    flush(env)
    if env.commit_notifier then
        env.commit_notifier:disconnect()
    end
end

return M
