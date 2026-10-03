# 变更记录

本项目为「小狼毫 0.17.4 + 雾凇拼音」的本地配置与定制。

版本号记录本项目配置与定制的整体状态；上游雾凇拼音、小狼毫自身的版本见各自文件中的
`schema/version` 与 `installation.yaml`。

---

## 3.4.1 — 2026-10-03

### 新增：候选窗双语显示（打字顺便记单词）

中文候选的注释区会显示对应英文释义，实现「一边打字一边记单词」。

- **注释区常显英文**：如 `苹果   apple`、`电脑   computer`
- **可直接上屏英文**：沿用方案原有的 `Control+Shift+Return`（`commit_comment`），
  注释里是英文时，这个键就变成「把当前中文换成对应英文单词」
- **可开关**：`Control+Shift+T` 切换，方案选单中也显示为「中 / 中英」开关
- **默认只在 2 字及以上显示**，避免单字候选过于嘈杂

相关文件：

| 文件 | 作用 |
|---|---|
| `Rime/tools/build_bilingual_dict.py` | 数据生成器，可改参数重跑 |
| `Rime/lua/bilingual/en.tsv` | 英文数据（33,156 条，0.88 MB） |
| `Rime/lua/bilingual/loader.lua` | 多语言加载器 |
| `Rime/lua/bilingual_filter.lua` | 候选注释滤镜 |
| `Rime/rime_ice.custom.yaml` | 补丁文件（开关、滤镜挂载、快捷键、配置） |

### 数据来源与授权

双语数据来自 **CC-CEDICT**（<https://cc-cedict.org/>），由 MDBG 发布，
采用 **CC-BY-SA 4.0** 授权（<https://creativecommons.org/licenses/by-sa/4.0/>）。

生成参数：`min_weight=10000`、`max_length=30`、`max_senses=2`。
按需调整规模可重跑生成器，例如：

```
python Rime/tools/build_bilingual_dict.py --min-weight 50000   # 约 1.2 万条，更精简
python Rime/tools/build_bilingual_dict.py --min-weight 100     # 约 7.7 万条，更全
```

### 设计说明

- 滤镜插在 `engine/filters` 的下标 7 处（`reduce_english_filter` 之后、`simplifier@emoji` 之前）。
  必须排在 `corrector.lua` 之后——后者用注释区做完错音判断后会清空它，注释区正好让给英文；
  必须排在 emoji 与简繁转换之前——此时候选文本仍是简体汉字，才查得到词表。
- 全部改动写在新增的 `rime_ice.custom.yaml` 中，未修改任何上游文件，便于随上游更新。
- 若要新增语言，把数据文件放入 `Rime/lua/bilingual/<语言>.tsv`，
  并在 `rime_ice.custom.yaml` 的 `bilingual_filter/languages` 中追加语言代码即可，无需改代码。

### 已知阻塞（非本次引入）

`rime_ice` 词库编译失败：`Rime/rime_ice.dict.yaml` 引用的 `private_dict_common`
词库文件在磁盘上不存在。该问题早于本次改动，但会阻止部署完成，
从而使本次双语功能无法在运行时生效。详见下方「待处理」。

---

## 待处理

| 问题 | 说明 | 状态 |
|---|---|---|
| `private_dict_common` 悬空引用 | `rime_ice.dict.yaml` 第 15 行引用了不存在的词库文件，导致 `dictionary 'rime_ice' failed to compile` | 未修复 |
| `build/` 与源文件不同步 | `rime_ice.table.bin` 停留在 2026-08-25，未随本次部署刷新（因上一条失败） | 未修复 |
| 双拼缺 `custom_phrase_double.txt` | 双拼方案引用了该文件但未创建 | 未修复（当前 schema_list 只用 rime_ice，不影响） |
