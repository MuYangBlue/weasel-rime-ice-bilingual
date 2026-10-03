-- 双语释义的解析与渲染（显示与上屏共用）
--[[
为什么单独一个文件
------------------
释义要同时喂给两个地方：

  1. bilingual_filter.lua —— 把释义渲染成候选窗里的多行文本
  2. bilingual_commit.lua —— 把释义还原成「纯单行英文」用于上屏

如果两边各写一份解析逻辑，迟早会漂移（比如显示时能剥掉词性、上屏时
忘了剥），症状是上屏内容里混进 "to speak→v." 这种字样。
因此把「格式知识」集中在这里，两边都调用它。

数据格式
--------
数据由 tools/build_bilingual_dict.py 生成，形如：

    苹果	apple→n.
    说	to speak→v.; to talk→v.; to say→v.

即：中文<Tab>义项1[→词性]; 义项2[→词性]; ...

- 义项之间用 "; " 分隔（CC-CEDICT 原始写法，已验证括号内不会出现分号）
- "→"（U+2192）是词性分隔符。选用它是因为在全部 40,794 条释义中
  出现次数为 0，而 · | [ % & ° @ 等常见符号都会与释义正文冲突
  （如「石膏」的化学式 CaSO4·2(H2O)）
- 词性可能缺失：只有约三成义项能可靠推断，缺了就当作没有
]]

local M = {}

-- 词性分隔符，必须与 tools/build_bilingual_dict.py 的 POS_SEP 一致
M.POS_SEP = "→"

-- 义项之间的分隔符
M.SENSE_SEP = "; "


--- 解析释义串为义项列表。
-- 返回值形如：
--     { { text = "to speak", pos = "v." },
--       { text = "to talk",  pos = "v." } }
-- 无词性时 pos 为 nil；无法解析时返回空表（绝不抛错）。
--
-- 同时按 ";" 与换行切分：原始数据用 "; " 分隔义项，而候选窗里的注释
-- 已经是 render() 渲染过的多行文本（用 "\n" 分隔），两者都要能解析。
function M.parse(gloss)
    local senses = {}
    if type(gloss) ~= "string" or gloss == "" then
        return senses
    end

    for part in gloss:gmatch("[^;\n]+") do
        -- 去掉首尾空白
        local item = part:match("^%s*(.-)%s*$")
        if item ~= "" then
            -- 形如 "to speak→v."；用非贪婪匹配，避免释义里含多个分隔符时切错
            local text, pos = item:match("^(.-)%s*" .. M.POS_SEP .. "%s*(%S+)%s*$")
            if text and text ~= "" then
                senses[#senses + 1] = { text = text, pos = pos }
            else
                senses[#senses + 1] = { text = item }
            end
        end
    end

    return senses
end


--- 判断这次是否要显示词性标签。
-- 模式：
--   "off"    从不显示
--   "always" 只要有词性就显示
--   "auto"   默认。只在「显示出来不会重复」时显示：
--            - 单义项（如 苹果 -> apple）总是显示，因为不会重复
--            - 多义项若词性全都一样（如 我 -> I/me/my 都是 pron.），
--              则隐藏，避免同一个标签竖着刷三遍
--            - 多义项词性不同（如 传真 -> to fax / a fax）则显示
local function want_pos(senses, mode)
    if mode == "off" or mode == nil then
        return false
    end
    if mode == "always" then
        return true
    end
    -- auto
    if #senses < 2 then
        return true
    end
    local first = senses[1].pos
    if not first then
        return false
    end
    for _, s in ipairs(senses) do
        if s.pos ~= first then
            return true
        end
    end
    return false
end


--- 按上限截断，尽量在词边界断开。
-- 只在 max_length > 0 时生效；逐行截断，避免把多行结构切坏。
local function clip(text, max_length)
    if not max_length or max_length <= 0 or #text <= max_length then
        return text
    end
    local cut = text:sub(1, max_length)
    local sp = cut:match("^(.*)%s")
    cut = sp and sp or cut
    return (cut:gsub("[%s;,%-]+$", ""))
end


--- 渲染候选窗里要显示的释义文本。
-- @param gloss  原始释义串（可能含词性）
-- @param opts   { multiline, pos, pos_pad, max_length }
-- @return 显示文本；无有效内容时返回 nil
function M.render(gloss, opts)
    opts = opts or {}
    local senses = M.parse(gloss)
    if #senses == 0 then
        return nil
    end

    local show_pos = want_pos(senses, opts.pos)
    local pad = opts.pos_pad or "  "
    local max_length = opts.max_length or 0

    local lines = {}
    for _, s in ipairs(senses) do
        local line = s.text
        if show_pos and s.pos then
            line = line .. pad .. s.pos
        end
        lines[#lines + 1] = clip(line, max_length)
    end

    if opts.multiline == false then
        return table.concat(lines, M.SENSE_SEP)
    end
    return table.concat(lines, "\n")
end


--- 取出用于上屏的纯文本：只保留第一个义项，并剥掉词性。
-- commit_comment 会把 comment 原样写进文档，多行/词性都会一起进去，
-- 所以上屏必须走这个函数。
-- @return 单行纯文本；无有效内容时返回 nil
function M.plain(gloss)
    local senses = M.parse(gloss)
    if #senses == 0 then
        return nil
    end
    return senses[1].text
end


return M
