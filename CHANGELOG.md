# 变更记录

本项目为「小狼毫 0.17.4 + 雾凇拼音」的本地配置与定制。

版本号记录本项目配置与定制的整体状态；上游雾凇拼音、小狼毫自身的版本见各自文件中的
`schema/version` 与 `installation.yaml`。

---

## 3.4.1 — 2026-10-03

### 新增：候选窗双语显示（打字顺便记单词）

中文候选的注释区会显示对应英文释义，实现「一边打字一边记单词」。

- **注释区常显英文**：如 `苹果   apple`、`电脑   computer`、单字如 `我   I; me; my`
- **可直接上屏英文**：沿用方案原有的 `Control+Shift+Return`（`commit_comment`），
  注释里是英文时，这个键就变成「把当前中文换成对应英文单词」
- **可开关**：`Control+Shift+T` 切换，方案选单中也显示为「中 / 中英」开关。
  默认开启（`reset: 1`），嫌候选密时一键关掉
- **单字与词组都显示**（`min_length: 1`）；若只想看词组，改为 2 即可

相关文件：

| 文件 | 作用 |
|---|---|
| `Rime/tools/build_bilingual_dict.py` | 数据生成器，可改参数重跑 |
| `Rime/lua/bilingual/en.tsv` | 英文数据（40,794 条：词组 33,156 + 单字 7,638，1.05 MB） |
| `Rime/lua/bilingual/loader.lua` | 多语言加载器 |
| `Rime/lua/bilingual_filter.lua` | 候选注释滤镜 |
| `Rime/rime_ice.custom.yaml` | 补丁文件（开关、滤镜挂载、快捷键、配置） |

### 数据来源与授权

双语数据来自 **CC-CEDICT**（<https://cc-cedict.org/>），由 MDBG 发布，
采用 **CC-BY-SA 4.0** 授权（<https://creativecommons.org/licenses/by-sa/4.0/>）。
常用度筛选依据 `cn_dicts/base.dict.yaml`（词组）与 `cn_dicts/8105.dict.yaml`（单字）。

数据是**词表**，不是翻译引擎：只能逐词给出释义，不会重组语序或处理语法。
因此句子的英文需要你自己按词组合，例如「我爱你」会给出 `我 / 爱 / 你`
三个词各自的释义，而不会给出 `I love you`。

生成参数：`min_weight=10000`、`min_weight_single=0`、`max_length=30`、`max_senses=2`。
按需调整规模可重跑生成器：

```
python Rime/tools/build_bilingual_dict.py                    # 默认：词组+单字，40,794 条
python Rime/tools/build_bilingual_dict.py --no-single-char   # 只要词组，33,156 条
python Rime/tools/build_bilingual_dict.py --min-weight 50000 # 更精简
```

### 单字与读音匹配

单字只存在于字表（`8105.dict.yaml`），词库不含单字。若只用词库筛选，
「我、爱、你、是、在」这类最高频的字会全部漏掉。因此单字单独从字表收录。

字表还带有拼音与真实语料频次，据此为单字挑出与常用读音相符的义项，
覆盖 543 个多音字。这修正了若干义项错配，例如：

| 字 | 修正前 | 修正后 |
|---|---|---|
| 说 | to persuade（`shui4` 低频义） | to speak; to talk; to say |
| 重 | — | heavy; serious |
| 行 | — | row; line |
| 都 | — | all; both; entirely |
| 好 | — | good; appropriate; proper |

### 修复记录

| 问题 | 原因 |
|---|---|
| 候选显示 `苹果 {gloss}` 字面量 | Lua 多返回值陷阱：内层 `gsub` 返回 `(字符串, 次数)`，末位实参把「次数」当成外层 `gsub` 的参数 `n`；`n=0` 表示零次替换 |
| `[bilingual]` 日志从不输出 | `local function log(...)` 在函数体内访问 `log` 指向自身，`log.info` 恒为 nil |
| 生成器遇生僻字中断 | Windows 控制台 GBK 无法编码扩展 A 区汉字，日志已降级处理 |

### 设计说明

- 滤镜插在 `engine/filters` 的下标 7 处（`reduce_english_filter` 之后、`simplifier@emoji` 之前）。
  必须排在 `corrector.lua` 之后——后者用注释区做完错音判断后会清空它，注释区正好让给英文；
  必须排在 emoji 与简繁转换之前——此时候选文本仍是简体汉字，才查得到词表。
- 全部改动写在新增的 `rime_ice.custom.yaml` 中，未修改任何上游文件，便于随上游更新。
- 若要新增语言，把数据文件放入 `Rime/lua/bilingual/<语言>.tsv`，
  并在 `rime_ice.custom.yaml` 的 `bilingual_filter/languages` 中追加语言代码即可，无需改代码。

### 已知阻塞（非本次引入，且不影响本功能）

部署时出现两条错误：

```
E dict_compiler.cc:55] source file 'Rime/private_dict_common.dict.yaml' does not exist.
E deployment_tasks.cc:372] dictionary 'rime_ice' failed to compile.
```

`Rime/rime_ice.dict.yaml` 第 15 行引用的 `private_dict_common` 词库文件在磁盘上不存在。

据时间戳判断，该问题自 2026-08-25 13:25 起就存在：

| 文件 | 时间 |
|---|---|
| `Rime/rime_ice.dict.yaml`（加入 private_dict 引用） | 2026-08-25 13:25 |
| `Rime/build/rime_ice.table.bin`（最后一次成功编译） | 2026-08-25 12:33 |

即 `table.bin` 比词库源文件早约 52 分钟，且 `Rime/build/` 中从未出现 private_dict 产物——
**本地私有词库实际上一直没有生效**。

**对本次双语功能的影响：无。** 双语滤镜在 Lua 层直接读取 `Rime/lua/bilingual/en.tsv`，
不经过 `table.bin`；中文候选仍由既有的 `table.bin` 正常产出（用户词库在 2026-10-03 仍有写入，
说明中文输入本身工作正常）。因此本功能可以独立生效于该问题之外。

该问题仅意味着本地私有词库的词条没有被纳入候选。

---

## 待处理

| 问题 | 说明 | 状态 |
|---|---|---|
| `private_dict_common` 悬空引用 | `rime_ice.dict.yaml` 第 15 行引用了不存在的词库文件，导致 `dictionary 'rime_ice' failed to compile`；本地私有词库自 2026-08-25 起一直未生效 | 未修复（用户决定暂不处理） |
| `build/` 与源文件不同步 | `rime_ice.table.bin` 停留在 2026-08-25，未随本次部署刷新（因上一条失败） | 未修复 |
| 双拼缺 `custom_phrase_double.txt` | 双拼方案引用了该文件但未创建 | 未修复（当前 schema_list 只用 rime_ice，不影响） |
