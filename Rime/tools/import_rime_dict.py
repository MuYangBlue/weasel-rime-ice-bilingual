#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把外部词表转换为 Rime 可导入的词典。

支持的输入格式（UTF-8）：
    词语<TAB>全拼<TAB>权重
    词语<TAB>全拼
    词语 全拼

第一列是词语，第二列建议使用全拼。雾凇全拼和已有双拼方案都会把
全拼词典作为共同来源，因此同一份导入词典可以同时服务全拼和双拼。

脚本只生成词典文件，不自动修改其他方案配置，避免误覆盖用户文件。
项目已经在 rime_ice.dict.yaml 预留 imported_terms 条目（默认注释），
生成到 Rime/imported_terms.dict.yaml 后取消该行注释并重新部署即可。
该词典属于本地个人内容，不纳入本仓库。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADER = """# Rime dictionary\n# encoding: utf-8\n\n---\nname: {name}\nversion: \"{version}\"\ncolumns:\n  - text\n  - code\n  - weight\n\n"""

PINYIN_RE = re.compile(r"^[a-zA-ZvüÜ' ]+$")


def parse_line(line: str, line_no: int, default_weight: int) -> tuple[str, str, int] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
        return None
    if stripped.startswith(("name:", "version:", "columns:", "  -")):
        return None

    parts = re.split(r"\t+", stripped)
    if len(parts) == 1:
        parts = re.split(r"\s+", stripped, maxsplit=1)
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(f"第 {line_no} 行格式不正确，应为：词语<TAB>全拼[<TAB>权重]")

    word, code = parts[0].strip(), parts[1].strip().lower()
    if not word:
        raise ValueError(f"第 {line_no} 行词语为空")
    if not code or not PINYIN_RE.fullmatch(code):
        raise ValueError(f"第 {line_no} 行编码不是全拼：{code!r}")

    weight = default_weight
    if len(parts) == 3 and parts[2].strip():
        try:
            weight = int(parts[2].strip())
        except ValueError as exc:
            raise ValueError(f"第 {line_no} 行权重不是整数：{parts[2]!r}") from exc
    return word, code, weight


def convert(source: Path, output: Path, name: str, default_weight: int) -> tuple[int, int]:
    entries: dict[tuple[str, str], int] = {}
    with source.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            parsed = parse_line(line, line_no, default_weight)
            if parsed is None:
                continue
            word, code, weight = parsed
            key = (word, code)
            entries[key] = max(weight, entries.get(key, weight))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER.format(name=name, version="2026-10-30"))
        for (word, code), weight in sorted(entries.items(), key=lambda item: (-item[1], item[0])):
            handle.write(f"{word}\t{code}\t{weight}\n")
    return len(entries), 0


def main() -> int:
    parser = argparse.ArgumentParser(description="将外部词表转换为 Rime .dict.yaml")
    parser.add_argument("input", type=Path, help="输入词表，UTF-8")
    parser.add_argument("output", type=Path, help="输出 .dict.yaml 路径")
    parser.add_argument("--name", default="imported_terms", help="词典名称，默认 imported_terms")
    parser.add_argument("--weight", type=int, default=100, help="未指定权重时使用的权重")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"输入文件不存在：{args.input}", file=sys.stderr)
        return 2
    if args.weight < 0:
        print("权重不能为负数", file=sys.stderr)
        return 2

    try:
        count, _ = convert(args.input, args.output, args.name, args.weight)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1

    print(f"已生成 {count} 条词条：{args.output}")
    print(f"请在 rime_ice.dict.yaml 的 import_tables 中加入：{args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
