# 小狼毫输入法本地定制

基于 **小狼毫（Weasel）0.17.4 + 雾凇拼音（rime-ice）** 的个人配置与定制，
主要特色是**候选窗双语显示**：输入中文时，在候选右侧显示对应英文释义，顺便记单词。

```
1. 说    to speak  v.
         to talk   v.
         to say    v.
2. 苹果  apple
```

## 功能

- **候选窗显示英文释义**：中文候选右侧附英文，单字与词组都支持（40,794 条词条）
- **竖排多行**：多个义项逐行显示在候选右侧
- **可上屏英文**：`Ctrl+Shift+Return` 把当前中文换成对应英文单词
- **可开关**：`Ctrl+Shift+T` 临时关闭双语显示（方案选单中显示为「中 / 中英」）
- **词性标注**：释义后附 `v.` / `n.` / `pron.` 等标签（规则推断，见下）

## 目录结构

| 路径 | 说明 |
|---|---|
| `Rime/rime_ice.custom.yaml` | 本项目的核心定制（双语滤镜挂载、开关、快捷键、配置） |
| `Rime/tools/build_bilingual_dict.py` | 双语数据生成器，可改参数重跑 |
| `Rime/lua/bilingual/en.tsv` | 英文数据（40,794 条） |
| `Rime/lua/bilingual/loader.lua` | 多语言词表加载器 |
| `Rime/lua/bilingual/format.lua` | 释义的解析/渲染（显示与上屏共用） |
| `Rime/lua/bilingual_filter.lua` | 候选注释滤镜 |
| `Rime/lua/bilingual_commit.lua` | 接管 `Ctrl+Shift+Return`，上屏单行英文 |
| `Rime/` 其余文件 | 雾凇拼音上游配置（见「授权」） |

## 安装

1. 安装小狼毫 0.17.4 或更高版本
2. 获取雾凇拼音的完整配置（本仓库不含约 45 MB 的 `cn_dicts/` 词库）：

   ```bash
   git clone https://github.com/iDvel/rime-ice
   cp -r rime-ice/cn_dicts Rime/
   ```

3. 把 `Rime/` 放到小狼毫的用户目录（托盘图标 → 用户文件夹）
4. 重新部署（托盘图标 → 重新部署）

> 注意：修改 `.lua` 文件后**必须重启 WeaselServer 才会生效**。
> 引擎重载（打字、切换方案）不会重新读取 Lua 脚本——模块被 Lua 的
> `package.loaded` 缓存。这是排查问题时容易踩的坑。

## 配置

在 `Rime/rime_ice.custom.yaml` 的 `bilingual_filter` 节调整：

```yaml
bilingual_filter:
  languages: [ en ]   # 语言列表，取第一个命中的
  min_length: 1       # 1 = 单字与词组都显示；2 = 只显示词组
  max_length: 0       # 单行释义长度上限，0 = 不截断
  separator: "  "     # 与已有注释拼接时的分隔符
  format: "{gloss}"   # 显示格式，可用 {gloss} {lang} {word}
  multiline: true     # true = 竖排多行；false = 挤在一行
  pos: auto           # 词性：off / auto / always
```

改完需重新部署，改 `.lua` 还需重启服务。

### 关于词性

CC-CEDICT 本身**不含词性字段**，标签是由英文释义的书写习惯**规则推断**的
（如 `to xxx` → 动词）。实测 30.4% 的词条能判定，其余不标注。

这是启发式结果，不是权威标注；不需要可设 `pos: off`。

`pos: auto` 会避免重复：单义项直接显示；多义项若词性相同
（如「我 → I / me / my」都是 `pron.`）则隐藏标签，仅在词性不同时显示。

## 重新生成数据

数据由 `Rime/tools/build_bilingual_dict.py` 生成，重新运行是安全的：

```bash
# 默认：词组 + 单字
python Rime/tools/build_bilingual_dict.py

# 只要词组
python Rime/tools/build_bilingual_dict.py --no-single-char

# 不附加词性标签
python Rime/tools/build_bilingual_dict.py --no-pos
```

## 授权

本仓库是**配置整合项目**，内容来自多个来源：

| 内容 | 来源 | 授权 |
|---|---|---|
| `Rime/` 下大部分配置与方案 | [雾凇拼音 rime-ice](https://github.com/iDvel/rime-ice) | **GPL-3.0** |
| `Rime/lua/bilingual/en.tsv` 双语数据 | [CC-CEDICT](https://cc-cedict.org/)（MDBG 发布） | **CC-BY-SA 4.0** |
| `Rime/lua/*` 部分滤镜脚本 | 雾凇拼音及其上游作者 | 见各文件头部注释 |
| 双语显示相关脚本与生成器 | 本项目 | GPL-3.0 |

由于包含 GPL-3.0 内容，**本仓库整体以 GPL-3.0 分发**（见 [LICENSE](LICENSE)）。

CC-CEDICT 数据按 CC-BY-SA 4.0 使用，署名信息同时保存在
`Rime/lua/bilingual/en.tsv` 文件头部。

### 词库说明

`cn_dicts/`（雾凇拼音词库，约 45 MB）与 `weasel-0.17.4/`（输入法程序本体）
未纳入本仓库，请从上游获取。另外 `Rime/installation.yaml` 与 `Rime/user.yaml`
含设备标识与使用记录，不纳入版本控制。

## 已知问题

无。此前 `Rime/rime_ice.dict.yaml` 中指向本地私有词库的两条悬空引用已移除，
部署时不再报 `dictionary 'rime_ice' failed to compile`，克隆后可直接部署。
