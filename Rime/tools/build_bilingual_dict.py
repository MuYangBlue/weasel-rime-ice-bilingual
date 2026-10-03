#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成候选窗双语词典数据（中文词 -> 外语释义）。

数据源：CC-CEDICT（https://cc-cedict.org/），由 MDBG 发布
授权：  CC-BY-SA 4.0（署名见生成文件头部）

用法示例：
    python build_bilingual_dict.py                        # 默认英文，权重阈值 10000
    python build_bilingual_dict.py --min-weight 50000     # 更精简（约 1.2 万条）
    python build_bilingual_dict.py --min-weight 100       # 更全（约 7.7 万条）
    python build_bilingual_dict.py --max-length 20        # 释义更短，候选窗更窄
    python build_bilingual_dict.py --no-single-char       # 不收录单字（只保留词组）

输出：Rime/lua/bilingual/<lang>.tsv

重新运行是安全的：会覆盖输出文件，不影响其他配置。

关于常用词筛选
--------------
以两份数据作为「常用度」依据：
  - cn_dicts/base.dict.yaml  词库（词组）
  - cn_dicts/8105.dict.yaml  《通用规范汉字表》字表（单字）
单字只存在于字表中，词库不含单字，故两者都需要，否则「我、爱、你」
这类最高频的字会全部漏掉。
"""

import argparse
import gzip
import io
import os
import re
import sys
import time
import urllib.request

# ---------------------------------------------------------------- 常量

CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"

# 项目根目录（本脚本位于 Rime/tools/ 下）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RIME_DIR = os.path.dirname(SCRIPT_DIR)

# 词组来源（不含单字）
DEFAULT_BASE_DICT = os.path.join(RIME_DIR, "cn_dicts", "base.dict.yaml")
# 单字来源（《通用规范汉字表》）
DEFAULT_CHAR_DICT = os.path.join(RIME_DIR, "cn_dicts", "8105.dict.yaml")
DEFAULT_OUTPUT_DIR = os.path.join(RIME_DIR, "lua", "bilingual")

# 汉字范围（含扩展 A 区、兼容区）
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# CC-CEDICT 词条： 繁體 简体 [pin yin] /gloss/
ENTRY_RE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")

# 释义里的噪音
LEADING_TAG_RE = re.compile(r"^\([^)]*\)\s*")
CL_RE = re.compile(r"\bCL:[^;]*")
CROSSREF_RE = re.compile(
    r"^(variant of|old variant of|erhua variant of|see |also written|"
    r"abbr\. for|abbreviation for|used in)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- 工具函数

def log(msg):
    """输出日志。

    Windows 控制台默认 GBK，遇到生僻字（如扩展 A 区的 㑇）会抛
    UnicodeEncodeError，因此这里降级处理，只影响日志显示，不影响生成结果。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe, flush=True)


def fetch_cedict(cache_dir):
    """下载 CC-CEDICT，若已缓存则直接复用。返回解压后的文本行迭代所需路径。"""
    cache_path = os.path.join(cache_dir, "cedict_full.gz")
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 1_000_000:
        log("使用已缓存的 CC-CEDICT: %s" % cache_path)
        return cache_path

    log("下载 CC-CEDICT ...")
    log("  %s" % CEDICT_URL)
    os.makedirs(cache_dir, exist_ok=True)
    req = urllib.request.Request(CEDICT_URL, headers={"User-Agent": "rime-bilingual-build/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    with open(cache_path, "wb") as f:
        f.write(data)
    log("  已下载 %d 字节 -> %s" % (len(data), cache_path))
    return cache_path


def load_weights(path):
    """读取词库或字表，返回 {词: 权重}。权重用作「常用度」代理。"""
    weights = {}
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                try:
                    weights[parts[0]] = int(parts[2])
                except ValueError:
                    pass
    return weights


def load_all_weights(paths):
    """合并多份来源的权重（词库 + 字表）。

    同一词条在多处出现时取较大权重，避免被低权重的重复条目压低。
    """
    merged = {}
    for p in paths:
        if not p or not os.path.isfile(p):
            log("  跳过不存在的来源：%s" % p)
            continue
        one = load_weights(p)
        log("  %s：%d 条" % (os.path.basename(p), len(one)))
        for word, weight in one.items():
            if word not in merged or weight > merged[word]:
                merged[word] = weight
    return merged


def load_pronunciations(path):
    """从字表读取 {字: [(拼音, 频次), ...]}。

    字表（8105.dict.yaml）形如：
        说	shui	1
        说	shuo	12000460
    频次是真实语料统计，可用来判断哪个读音才是常用音，
    进而选出与之匹配的义项（否则「说」会取到 to persuade 这种低频义）。
    """
    readings = {}
    if not path or not os.path.isfile(path):
        return readings
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                try:
                    freq = int(parts[2])
                except ValueError:
                    continue
                readings.setdefault(parts[0], []).append((parts[1], freq))
    # 每个字按频次降序，常用音在前
    for ch in readings:
        readings[ch].sort(key=lambda x: -x[1])
    return readings


def cedict_pinyin_to_plain(pin):
    """把 CC-CEDICT 的拼音转成字表用的无调、小写、无空格形式。

    CC-CEDICT: "shuo1" / "zhong1 guo2"  ->  "shuo" / "zhongguo"
    字表:      "shuo" / "zhong"
    """
    return re.sub(r"[^a-z]", "", pin.lower())


def pick_variants(word, variants, readings):
    """选出该词条的义项。

    单字且字表中有读音记录时，优先采用读音与输入音一致的义项——
    这既修正常用度判断，也保证释义和你实际打出的音对应。
    找不到匹配时退回原来的排序结果。
    """
    ordered = order_variants(variants)

    if len(word) != 1 or not readings:
        return ordered

    recs = readings.get(word)
    if not recs:
        return ordered

    # 按字表频次从高到低，逐个读音查找匹配的 CEDICT 义项
    for plain, _freq in recs:
        if not plain:
            continue
        matched = [
            v for v in ordered
            if cedict_pinyin_to_plain(v[1]) == plain
        ]
        if matched:
            # 命中的读音义项排最前，其余作为补充留在后面
            rest = [v for v in ordered if v not in matched]
            return matched + rest

    return ordered


def clean_sense(sense):
    """清洗单条释义。

    CC-CEDICT 原文示例：
        frog (CL:隻|只[zhi1])                  -> frog
        telescope (CL:部[bu4]); binoculars (CL:副[fu4])  -> telescope; binoculars
        (slang) ugly guy                       -> ugly guy
        to have (a (motivating etc) effect)     -> to have (a (motivating etc) effect)
    """
    s = sense.strip()

    # 去掉 CL:部[bu4] 这类量词标注，连它外面的括号一起摘掉。
    # 注意要处理 (CL:...|...[...]) 这种内部带 | 和 [] 的形式。
    s = re.sub(r"\(\s*CL:[^)]*\)", "", s)
    s = re.sub(r"\bCL:[^;)]*", "", s)

    # 去掉 (slang) (Tw) (Internet slang) (old) 等前缀标签
    while True:
        new = LEADING_TAG_RE.sub("", s).strip()
        if new == s:
            break
        s = new

    # 去掉残留的孤立空括号，并清理分号/空格周围的冗余空白
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([;,.])", r"\1", s)   # "telescope ; binoculars" -> "telescope; binoculars"
    s = re.sub(r"([;,])\s*", r"\1 ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ;,")


def strip_dangling(text):
    """移除结尾处残缺的成分：未闭合括号、被截断的长词组残片、裸标点。

    注意：ex- / giga- / cyber- / super- 这类英文前缀是合法写法，必须保留。
    判据是「末段只含一个尾随连字符」= 前缀；若末段出现多个连字符
    （如 take-it-or-leave-），说明是被长度截断的复合词残片，应予剔除。
    """
    text = text.strip(" ;,.")

    # 未闭合括号：砍掉残缺的开括号及其后内容
    while text.count("(") > text.count(")"):
        idx = text.rfind("(")
        if idx < 0:
            break
        text = text[:idx].rstrip(" ;,.")

    # 逐段检查：末段若是被截断的复合词残片，则整段丢掉
    parts = [p.strip() for p in text.split(";")]
    while parts and _looks_truncated(parts[-1]):
        parts.pop()
    text = "; ".join(p for p in parts if p)

    return text.strip(" ;,.")


def _looks_truncated(segment):
    """判断一个义项段是否是截断残片。"""
    if not segment:
        return True
    if segment.endswith("-"):
        # 末段只有一个尾随连字符 -> 合法前缀（ex-、giga-）
        # 末段出现多个连字符 -> 复合词被截断（take-it-or-leave-）
        return segment.count("-") > 1
    return False


def truncate_words(text, max_length):
    """在单词边界截断，避免留下「careless; t」这种半截词。"""
    if max_length <= 0 or len(text) <= max_length:
        return text
    cut = text.rfind(" ", 0, max_length + 1)
    if cut < max_length * 0.5:
        return text[:max_length]
    return text[:cut]


def build_gloss(gloss_field, max_senses, max_length):
    """把 CC-CEDICT 的 /a;b;c/ 释义字段整理成一行简短释义。"""
    senses = [clean_sense(x) for x in gloss_field.split("/")]
    senses = [s for s in senses if s]

    # 优先保留非交叉引用型释义（"variant of ..." 这类对学习没帮助）
    useful = [s for s in senses if not CROSSREF_RE.match(s)]
    chosen = (useful if useful else senses)[:max_senses]
    if not chosen:
        return ""

    # 逐条拼入，直到超过长度上限为止
    text = ""
    for sense in chosen:
        candidate = sense if not text else text + "; " + sense
        if max_length > 0 and len(candidate) > max_length:
            if not text:
                text = truncate_words(sense, max_length)
            break
        text = candidate

    if not text:
        text = truncate_words(chosen[0], max_length)

    return strip_dangling(text)


def parse_cedict(gz_path):
    """解析 CC-CEDICT，返回 {简体: [(繁体, 拼音, 释义字段), ...]}，并统计词条数。

    同一个词可能有多个词条（同形异音），例如：
        蘋果 苹果 [Ping2 guo3] /Apple (American tech company)/
        蘋果 苹果 [ping2 guo3] /apple/
    这里全部保留，留待后续按优先级排序，避免专有名词义项挤掉常用义项。
    """
    entries = {}
    total = 0
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            m = ENTRY_RE.match(line.rstrip("\n"))
            if not m:
                continue
            total += 1
            trad, simp, pin, gloss = m.groups()
            entries.setdefault(simp, []).append((trad, pin, gloss))
    return entries, total


def order_variants(variants):
    """把同一个词的多条词条排序：常用义项优先，专有名词义项靠后。

    CC-CEDICT 用拼音首字母大小写区分专有名词（Ping2 guo3 = 苹果公司）。
    普通读音（全小写拼音）通常才是学习者需要的日常义项。
    """
    def priority(variant):
        _trad, pin, _gloss = variant
        # 拼音首字母大写 => 专有名词，排后面
        is_proper = bool(pin[:1].isupper())
        return (1 if is_proper else 0,)

    return sorted(variants, key=priority)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="生成候选窗双语词典 TSV（数据源 CC-CEDICT）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--lang", default="en", help="语言代码，决定输出文件名（默认 en）")
    ap.add_argument("--min-weight", type=int, default=10000,
                    help="词组的最低权重（默认 10000，约 3.3 万条）")
    ap.add_argument("--min-weight-single", type=int, default=0,
                    help="单字的最低权重（默认 0 = 收录字表中全部有释义的单字）")
    ap.add_argument("--no-single-char", action="store_true",
                    help="不收录单字，只保留词组")
    ap.add_argument("--no-reading-match", action="store_true",
                    help="不做单字读音匹配（默认会用字表读音挑出对应的义项）")
    ap.add_argument("--max-length", type=int, default=30,
                    help="释义最大字符数，0 表示不限（默认 30）")
    ap.add_argument("--max-senses", type=int, default=2,
                    help="每个词最多保留几个义项（默认 2）")
    ap.add_argument("--base-dict", default=DEFAULT_BASE_DICT,
                    help="词组来源（用于筛选常用词）")
    ap.add_argument("--char-dict", default=DEFAULT_CHAR_DICT,
                    help="单字来源（《通用规范汉字表》）")
    ap.add_argument("--output", default=None, help="输出文件路径")
    ap.add_argument("--cache-dir", default=None, help="CC-CEDICT 缓存目录（默认系统临时目录）")
    ap.add_argument("--keep-traditional", action="store_true",
                    help="同时为繁体写法建立索引（默认关闭）")
    args = ap.parse_args()

    out_path = args.output or os.path.join(DEFAULT_OUTPUT_DIR, "%s.tsv" % args.lang)
    cache_dir = args.cache_dir or os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp", "rime-bilingual"
    )

    # 1. 数据源
    gz_path = fetch_cedict(cache_dir)

    # 2. 常用度来源：词组来自词库，单字来自字表
    if not os.path.isfile(args.base_dict):
        log("错误：找不到词组来源 %s" % args.base_dict)
        return 1
    sources = [args.base_dict]
    if not args.no_single_char:
        if os.path.isfile(args.char_dict):
            sources.append(args.char_dict)
        else:
            log("警告：找不到单字来源 %s，将只收录词组" % args.char_dict)
    log("常用度来源：")
    weights = load_all_weights(sources)
    log("  合并后共 %d 个候选词条" % len(weights))

    # 单字读音表（用于挑出与实际读音相符的义项）
    readings = {}
    if not args.no_single_char and not args.no_reading_match:
        readings = load_pronunciations(args.char_dict)
        log("  单字读音记录：%d 个字" % len(readings))

    # 3. 解析 CC-CEDICT
    entries, total = parse_cedict(gz_path)
    log("CC-CEDICT 词条数：%d（去重后 %d）" % (total, len(entries)))

    # 4. 取交集 + 清洗
    rows = {}
    skipped_no_cjk = 0
    n_word = 0
    n_single = 0
    n_reading_matched = 0
    for word, weight in weights.items():
        is_single = len(word) == 1
        threshold = args.min_weight_single if is_single else args.min_weight
        if weight < threshold:
            continue
        if word not in entries:
            continue
        # 纯拉丁/数字词条（如 "AB"）跳过，只保留含汉字的
        if not CJK_RE.search(word):
            skipped_no_cjk += 1
            continue
        variants = pick_variants(word, entries[word], readings)
        if is_single and readings.get(word):
            top_plain = readings[word][0][0]
            if any(cedict_pinyin_to_plain(v[1]) == top_plain for v in variants):
                n_reading_matched += 1
        gloss = "/".join(v[2] for v in variants)
        text = build_gloss(gloss, args.max_senses, args.max_length)
        if not text:
            continue
        rows[word] = text
        if is_single:
            n_single += 1
        else:
            n_word += 1

    if args.keep_traditional:
        for word in list(rows.keys()):
            variants = entries.get(word) or []
            if variants:
                trad = variants[0][0]
                if trad and trad != word and CJK_RE.search(trad) and trad not in rows:
                    rows[trad] = rows[word]

    # 5. 输出
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Rime 候选窗双语数据（%s）\n" % args.lang)
        f.write("# 本文件由 tools/build_bilingual_dict.py 自动生成，请勿手工编辑——重跑会覆盖。\n")
        f.write("#\n")
        f.write("# 数据源：CC-CEDICT (https://cc-cedict.org/)\n")
        f.write("# 发布方：MDBG (https://www.mdbg.net/chinese/dictionary?page=cc-cedict)\n")
        f.write("# 授权：  CC-BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)\n")
        f.write("#\n")
        f.write("# 生成参数：min_weight=%d  min_weight_single=%d  max_length=%d  max_senses=%d  keep_traditional=%s  reading_match=%s\n"
                % (args.min_weight, args.min_weight_single, args.max_length,
                   args.max_senses, args.keep_traditional, not args.no_reading_match))
        f.write("# 生成时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("# 词条数：  %d（词组 %d，单字 %d）\n" % (len(rows), n_word, n_single))
        f.write("#\n")
        f.write("# 格式：中文词<Tab>释义\n")
        for word in sorted(rows):
            f.write("%s\t%s\n" % (word, rows[word]))

    size = os.path.getsize(out_path)
    log("")
    log("已生成：%s" % out_path)
    log("  词条数：%d（词组 %d，单字 %d）" % (len(rows), n_word, n_single))
    log("  其中单字按读音匹配到义项的：%d" % n_reading_matched)
    log("  体积：  %.2f MB" % (size / 1048576.0))
    if skipped_no_cjk:
        log("  跳过纯拉丁词条：%d" % skipped_no_cjk)

    log("")
    log("样例（排序后前 8 条）：")
    for word in sorted(rows)[:8]:
        log("  %-10s -> %s" % (word, rows[word]))

    log("")
    log("单字样例（按常用度最高的 12 个）：")
    top_single = sorted(
        (w for w in rows if len(w) == 1),
        key=lambda w: -weights.get(w, 0),
    )[:12]
    for word in top_single:
        log("  %-4s -> %s" % (word, rows[word]))

    log("")
    log("词组样例（随机位置）：")
    keys = [w for w in sorted(rows) if len(w) > 1]
    for i in (len(keys) // 3, len(keys) // 2, len(keys) * 2 // 3):
        if i < len(keys):
            log("  %-10s -> %s" % (keys[i], rows[keys[i]]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
