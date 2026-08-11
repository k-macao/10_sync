# 极趣墨水屏 财新社看板

**⏱️ 5分钟复刻，专属桌面财经资讯看板。**

本项目为极趣墨水屏 (Zectrix) 打造，**已去除日历页、天气页，新增财新社两页**，聚焦高质量财经新闻。

<img src="./images/preview.jpg" width="60%">

---

## 📌 看板显示内容（已更新）

适配 400×300 分辨率，共 2-4 页可配：

- **第 1-2 页：热榜看板** – 支持 `caixin`财新社（默认）、`eastmoney`东方财富、`zhihu`、`bilibili`、`github`。默认 `caixin`，即财新两页。
  - `python main.py --source caixin --pages 1,2 --dry-run` → 财新两页预览
  - `python main.py --source eastmoney --pages 1,2 --dry-run` → 东方财富两页
- **第 3-4 页：财新社专栏（新增）** – 独立财新社新闻源，始终为财新。若设 `ENABLED_PAGES="1,2,3,4"`：
  - 1,2 = 热榜（由 HOTLIST_SOURCE 决定）
  - 3,4 = 财新社  
  - 若 HOTLIST_SOURCE=caixin 且 PAGES=1,2,3,4，则 4 页均为财新（自动抓取 40 条分页）

> ✅ 本分支已按需求：**去除日历页（原3）、去除天气页（原4），增加财新社新闻两页**。
> 默认配置：`ENABLED_PAGES="1,2"` + `HOTLIST_SOURCE="caixin"` = 纯财新两页看板。

### 数据源说明
- **财新社**：优先 `gateway.caixin.com/api/dataplatform/scroll/index` / `mapiv5.caixin.com/m/api/getWapIndexListByPage`，参考 RSSHub 财新路由实现，失败回退 HTML，最后内置示例兜底，沙箱离线亦可预览。
- **东方财富**：保留 `np-listapi.eastmoney.com` 接口。

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

### 4. 自定义
编辑 `main.py` 顶部：
- `HOTLIST_SOURCE`：`caixin` / `eastmoney` / `zhihu` / `bilibili` / `github`
- `ENABLED_PAGES`：`1,2` 仅财新两页；`1,2,3,4` 热榜+财新四页

### 5. Actions 频率
`.github/workflows/run.yml` 中 `cron`，UTC。

### 6. 手动运行
Actions → 财新社看板 → Run workflow
- `hotlist_source`：选择 caixin/eastmoney/zhihu...
- `pages`：`1,2` 或 `1,2,3,4`
- `dry_run`：预览

---

## 💻 本地预览

```bash
pip install requests pillow
python main.py --source caixin --pages 1,2 --dry-run   # 生成 page_1.png page_2.png

# 四页模式：1,2热榜 + 3,4财新
python main.py --source eastmoney --pages 1,2,3,4 --dry-run

# 纯财新四页（40条）
python main.py --source caixin --pages 1,2,3,4 --dry-run
```

推送：

```bash
export ZECTRIX_API_KEY=xxx
export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF
python main.py --source caixin --pages 1,2
```

---

## 致谢
- 财新网 https://www.caixin.com
- 东方财富、知乎等数据源
- 极趣云 Zectrix
