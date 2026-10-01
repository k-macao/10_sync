# 极趣墨水屏 章鱼 AI·全景分析 看板

**⏱️ 5分钟复刻，专属桌面财经资讯看板。**

本项目为极趣墨水屏 (Zectrix) 打造，五页顶栏统一显示 **章鱼 AI·全景分析**，
正文 = **调用「仓库 02 · 章鱼 AI · 打氧日报」的当日推送页**，自动抓取、自动排版、自动推屏。

<img src="./images/preview.jpg" width="60%">

五页实拍（离线样例日报渲染，`python3 main.py --from-file tests/fixtures/latest_sample.html --dry-run`）：

<img src="./images/octopus_preview.png" width="70%">

---

## 📌 看板显示内容

适配 400×300 分辨率，共 5 页（**顶栏五页统一**，正文互不重复）：

| 页 | 顶栏 | 来自仓库 02 的栏目 | 内容策略 |
|---|---|---|---|
| 1 | 章鱼 AI·全景分析 | `00 · SHORT CARD`【闪电飞鱼】短线速查卡 | 30 秒读完：定调 / 明日剧本 / 七日风 / 水位 / 新手小抄 |
| 2 | 章鱼 AI·全景分析 | `02 · FORECAST`【回游金枪鱼】今日预判 | 方向与关键数字：量化预测 / 七日预测 / 倾向 / 核心判断 / A股 / 港股 / 美股 |
| 3 | 章鱼 AI·全景分析 | `01 · AI DIGEST`【爪爪八爪鱼】AI 全篇速览（上） | 市场与资金 / 量化与策略 |
| 4 | 章鱼 AI·全景分析 | `01 · AI DIGEST`【爪爪八爪鱼】AI 全篇速览（下） | 政策与日程 / 资讯与情绪 |
| 5 | 章鱼 AI·全景分析 | `03 · ECON CALENDAR`【探照安康鱼】时间节点 | 今明 ★★★ 时间点逐条（北京时间为序）+ 30 天窗口摘要（央行议息 / 最密集日） |

```bash
python3 main.py --dry-run                            # 五页全预览（真实拉仓库 02）
python3 main.py --from-file 样例.html --dry-run       # 离线预览（不联网）
python3 main.py --mode news --dry-run                # 切回原来的财新+东财新闻看板（只用 1-4 页）
python3 main.py --pages 1,2,3,4 --dry-run            # 只要前四页（不推日历页）
```

> ✅ 顶栏只显示 **章鱼 AI·全景分析**（不带 ◆ 前缀、不带来源标签、不带页码），五页完全一致。
> ✅ 每页顶栏下方写明**日报栏目原名**（对着微信日报能核对是哪一栏），页脚固定一行口径
> （`日报更新时间 · 当天源 N/M · 数字与正文同源 · 非投资建议`）。
> ✅ **放不下就如实说**：被版面截断的条目加「…」，整条没上屏的写「▼ 另有 N 项 · 全文见微信打氧日报」——
> 绝不悄悄少给，也不用旧内容顶替。
> 默认：`--mode octopus`（调用仓库 02）。

### 顶栏想改？

```python
# board_core.py 顶部
BOARD_TITLE = "章鱼 AI·全景分析"   # 顶栏文案
HEADER_SHOW_SOURCE = False        # True → 追加 ·财新社 / ·东方财富（仅 news 模式）
HEADER_SHOW_PART   = False        # True → 追加 (一) / (二)
HEADER_PREFIX      = ""           # 填 "◆ " 可加回菱形前缀
ENABLED_PAGES      = "1,2,3,4,5"  # 控制推哪几页（1,2,3,4 = 不推日历页）
```

也可临时用命令行覆盖顶栏文案（五页统一）：`python3 main.py --title "章鱼 AI·全景分析"`

### 数据源：仓库 02 的推送页

- **来源**：`k-macao/02` 的 `output/latest.html`（章鱼 AI · 打氧日报，推微信的那一页）。
- **抓取顺序**：`raw.githubusercontent.com` 优先 → 失败自动切 GitHub Contents API（`GITHUB_TOKEN` 可选）。
  可用 `OCTOPUS_REPO` / `OCTOPUS_REF` / `OCTOPUS_PATH` 换源。
- **解析**：日报用 `<!--SPLIT-->` 把 18 个栏目切开，每栏再按「标签 `<br>` 内容」拆成条目；
  「🦑 鲜鲜解读 / 阅读提醒」这类附注不进墨水屏正文。
- **日历页**：第 5 页读 `03 · ECON CALENDAR` 的表格——表里只有每天第一行写日期，
  其余行按行继承（否则「20:15 ADP」会被算到明天），并根据公共前后缀把同一时刻的
  多条读数压成一条（`ISM:PMI:制造业/就业/新订单/…:季调`），整句不做改写。
- **新鲜度**：日报生成时间按**北京时间**比对，超过 `OCTOPUS_MAX_AGE_HOURS`（默认 36 小时）判定仓库 02 停更。

---

## 🛠️ 部署指南

### 1. Fork
右上角 `Fork` 到自己账号。

### 2. 字体（可选）
上传 `.ttf` 字体重命名为 `font.ttf` 覆盖。

### 3. Secrets
只需配置 2 个（天气 Key 已不再需要）：

| Name | Secret | 获取 |
|---|---|---|
| `ZECTRIX_API_KEY` | 极趣云 API Key | https://cloud.zectrix.com |
| `ZECTRIX_MAC` | 墨水屏 MAC | 如 `AA:BB:CC:DD:EE:FF` |

> 仓库 02 是 **public** 仓库，拉它的日报**不需要任何密钥**。

### 4. 自定义
- 顶栏 / 启用页：编辑 `board_core.py` 顶部（见上）。
- 5 页各放哪些内容：编辑 `octopus_report.py` 的 `P1_KEEP` / `P2_KEEP` / `DIGEST_GROUPS` /
  `CALENDAR_WINDOW_LABELS`。
- 版面参数（字号、行高、留白）：编辑 `octopus_board.py` 顶部的 `MARGIN_X` / `BODY_TOP` / `DENSITY`。

### 5. Actions 全自动推

以根目录 **`w.yml`** 为准（升级版工作流）。`cron` 为 UTC 时间，当前默认：
**每 2 小时自检一次**（`0 */2 * * *`，UTC 偶数整点 = 北京时间 8:00 / 10:00 / … / 次日 6:00），
每次运行都会：拉仓库 02 的当日日报 → 校验新鲜度 → 排 5 页 → 推墨水屏，**全程无需人工干预**。
想改频率就改这一行 cron（`w.yml` 与 `.github/workflows/run.yml` 要同步改）——
`tests/test_octopus_board.py::TestWorkflow` 会校验：模板锁死每 2 小时，两份文件的
**除 cron 外**必须逐字一致。

> **⚠️ 定时频率：`w.yml` 已改成每 2 小时，活动工作流还差一行。**
> `.github/workflows/` 下的文件要令牌具备 `workflows` 权限才能改，Agent 令牌没有这个权限，
> 所以 `.github/workflows/run.yml` 目前仍是 `*/30 * * * *`（**实际还是每 30 分钟一次**）。
> 补上这一行即可生效（二选一）：
> 1. GitHub 网页端打开 `.github/workflows/run.yml`，把第 17 行改成
>    `    - cron: '0 */2 * * *'    # 每 2 小时自检一次`，提交到 `main`；
> 2. 或把根目录 `w.yml` 整份复制覆盖过去（两边只差这一行）。
>
> 没同步之前，测试里会打印
> 「⚠️ .github/workflows/run.yml 的 cron 还是 `*/30 * * * *`，尚未同步成每 2 小时一次」，
> 但**不会变红**——避免因为一行待同步的 cron 把整轮推屏卡死。

> **⚠️ 5 页版要改一行工作流**：工作流里 `pages` 的默认值还是 4 页时代的 `'1,2,3,4'`
> （`workflow_dispatch` 的 `default:` 与 `PAGES="${{ inputs.pages || '1,2,3,4' }}"` 各一处）。
> 不改的话定时任务只推 1-4 页、第 5 页（日历）永远不上屏——所以 `main.py` 每轮都会打印
> 「⚠️ 第 5 页有内容，但不在本次推送列表里…」把这件事喊出来。
> 手动触发时把 `pages` 填 `1,2,3,4,5` 即可；想全自动就把这两处都改成 `'1,2,3,4,5'`
> （改完 `w.yml` 与 `.github/workflows/run.yml` 要逐字一致，
> `tests/test_octopus_board.py::TestWorkflow` 会盯着）。

> **合并后不用改工作流就会自动切过来。** 现有 `.github/workflows/run.yml` 本来就是调
> `python main.py`（传的是 `--source / --pages / --east-column / --title`），
> 而新版 `main.py` 的 `--mode` 默认就是 `octopus`——那几个参数在 octopus 模式下照单全收、
> 不报错。所以合了就是「每 2 小时自动推仓库 02 的打氧日报」，一步不用动。
> `tests/test_octopus_board.py::TestWorkflow` 盯着这件事。

升级版 `w.yml` 多做两件事（想启用需要**手动把 w.yml 复制成 `.github/workflows/run.yml`**
——GitHub 网页端直接改也行；本次提交的令牌没有 `workflows` 权限，Agent 改不了这个文件）：

1. **推屏前先跑离线回归测试**：解析 / 排版退化了当场变红，不会拿着一份坏日报去刷屏。
2. **内容没变就不刷屏**：日报一天一份、工作流每 2 小时一次，不记状态的话一天会往屏上推 12 张
   一模一样的图（还是 30 分钟一次时是 48 张；墨水屏每推一次闪一次）。推送成功的指纹存在 Actions cache 里，
   下一轮内容一致就跳过；要强制重推用 `--force`（或手动触发时勾 `force`）。

不管用哪个版本，下面这条都成立：

**失败即显红、整轮不推** —— 仓库 02 拉不到 / 过期 / HTML 改版 / 墨水屏推送失败，
一律**退出码 1**，Actions 变红告警；墨水屏**保留上一次的内容**，不会被覆盖成空白或残缺内容。
工作流还带 `concurrency` 防重叠：上一次没跑完就排队，不会并发推送。

### 6. 手动运行
Actions → 章鱼 AI·全景分析 看板 → Run workflow
- `mode`：`octopus`（默认，调用仓库 02）/ `news`（财新+东财）/ `both`
- `pages`：工作流文件里的默认值是 `1,2,3,4`；**推满五页请填 `1,2,3,4,5`**（含日历页）
- `custom_title`：覆盖顶栏文案（五页统一），留空即「章鱼 AI·全景分析」
- `max_age_hours`：日报新鲜度上限（小时），默认 `36`
- `force`：内容与上次一致也照推
- `dry_run`：预览不推送

---

## 💻 本地预览

```bash
pip install requests pillow

# 调用仓库 02（需要联网）
python3 main.py --dry-run

# 离线自测（不联网，用仓库里的样例日报）
python3 main.py --from-file tests/fixtures/latest_sample.html --dry-run

# 切回新闻看板：1-2财新 + 3-4东方财富
python3 main.py --mode news --pages 1,2,3,4 --dry-run

# 直接调日报模块，打印解析结果（不渲染）
python3 octopus_report.py tests/fixtures/latest_sample.html
```

推送：

```bash
export ZECTRIX_API_KEY=xxx
export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF
python3 main.py                    # 调用仓库 02，推 5 页
```

---

## 🧪 测试

全部离线，不联网、不推墨水屏：

```bash
python3 -m unittest discover -s tests -v      # 59 项
```

覆盖：日报解析（栏目/标签/附注分流）、财经日历解析（日期按行继承 / 同刻事件合并 / 月份不混）、
5 页排版计划与优先级、退化保护（少栏目也不推空屏）、
新鲜度（北京时间 / 过期 / 时钟异常）、截断与「另有 N 项」如实披露、越界与重叠防护、
抓取失败整轮跳过、标签归一化、工作流与 `main.py` 的衔接（旧工作流的参数在 octopus 模式下必须接得住）、
定时节奏（模板锁死每 2 小时 / 活动工作流待同步时只告警不变红）。

---

## 🗂 代码结构

| 文件 | 作用 |
|---|---|
| `board_core.py` | 两块看板共用的底层：字体、顶栏文案、启用页、Zectrix 推送通道 |
| `octopus_report.py` | 拉取 / 解析仓库 02 的日报 HTML，新鲜度校验，排 5 页的内容计划 |
| `octopus_board.py` | 把内容渲染成 400×300 1-bit 图并推送；推送指纹（内容没变就跳过） |
| `main.py` | 命令行入口：`--mode octopus`（默认）/ `news` / `both` |
| `tests/` | 离线回归测试 + 日报样例 `fixtures/latest_sample.html` |
| `manual_*.py` | 单来源手动推送小工具（独立脚本，不受本次改动影响） |

---

## 致谢
- 财新网 https://www.caixin.com
- 东方财富、知乎等数据源
- 极趣云 Zectrix
- 数据与结论来自 **仓库 02 · 章鱼 AI · 打氧日报**（推送到微信的那一页）
