# 极趣墨水屏 章鱼AI全景分析看板

**⏱️ 5分钟复刻，专属桌面财经资讯看板。**

本项目为极趣墨水屏 (Zectrix) 打造，看板标题为 **章鱼AI全景分析**，内容为 **第1-2页财新社 + 第3-4页东方财富**，共 4 页财经资讯看板。

<img src="./images/preview.jpg" width="60%">

---

## 📌 看板显示内容（已更新）

适配 400×300 分辨率，共 4 页：

- **第 1-2 页：财新社热榜**（`caixin`）– 财新社（财新网）财经新闻两页。
  - `python main.py --pages 1,2 --dry-run` → 财新两页预览
- **第 3-4 页：东方财富**（`eastmoney`）– 东方财富财经新闻两页。
  - `python main.py --source eastmoney --pages 1,2 --dry-run` → 东方财富两页预览

> ✅ 本分支已按需求：**标题改为「章鱼AI全景分析」**，**新增东方财富两页**（3,4），**1,2 保持财新社**。
> 默认配置：`ENABLED_PAGES="1,2,3,4"` = 财新两页 + 东方财富两页。

### 数据源说明
- **财新社（第1,2页）**：优先 `gateway.caixin.com/api/dataplatform/scroll/index` / `mapiv5.caixin.com/m/api/getWapIndexListByPage`，参考 RSSHub 财新路由实现，失败回退 HTML，最后内置示例兜底，沙箱离线亦可预览。
- **东方财富（第3,4页）**：使用 `np-listapi.eastmoney.com` 接口（栏目 345 财经导读），失败回退 HTML，最后内置示例兜底。

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
- `BOARD_TITLE`：看板标题，默认 `章鱼AI全景分析`
- `HOTLIST_SOURCE`：第 1,2 页源，`caixin`（默认）/ `eastmoney` / `zhihu` / `bilibili` / `github`
- `ENABLED_PAGES`：`1,2,3,4`（财新+东方财富四页）或 `1,2`（仅财新两页）

### 5. Actions 频率
`.github/workflows/run.yml` 中 `cron`，UTC。

### 6. 手动运行
Actions → 章鱼AI全景分析看板 → Run workflow
- `hotlist_source`：第 1,2 页源 caixin/eastmoney/zhihu...
- `pages`：`1,2,3,4`（默认）或 `1,2`
- `dry_run`：预览

---

## 💻 本地预览

```bash
pip install requests pillow
python main.py --pages 1,2,3,4 --dry-run   # 生成 page_1..4.png（1,2财新 + 3,4东方财富）

# 仅财新两页
python main.py --pages 1,2 --dry-run

# 四页（把第1,2页也换成东方财富）
python main.py --source eastmoney --pages 1,2,3,4 --dry-run
```

推送：

```bash
export ZECTRIX_API_KEY=xxx
export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF
python main.py --pages 1,2,3,4
```

---

## 致谢
- 财新网 https://www.caixin.com
- 东方财富、知乎等数据源
- 极趣云 Zectrix
