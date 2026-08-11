# 极趣墨水屏 章鱼 AI+ 看板

**⏱️ 5分钟复刻，专属桌面财经资讯看板。**

本项目为极趣墨水屏 (Zectrix) 打造，看板主标题为 **章鱼 AI+**，内容为 **第1-2页财新社 + 第3-4页东方财富**，共 4 页财经资讯看板。

<img src="./images/preview.jpg" width="60%">

---

## 📌 看板显示内容（已更新）

适配 400×300 分辨率，共 4 页（顶栏标签 + 正文均互不重复）：

| 页 | 顶栏 | 数据源 | 内容策略 |
|---|---|---|---|
| 1 | ◆ 章鱼 AI+·财新社 (一) | `caixin` | 财新前半 |
| 2 | ◆ 章鱼 AI+·财新社 (二) | `caixin` | 接续第1页，条目不重叠 |
| 3 | ◆ 章鱼 AI+·东方财富 (一) | `eastmoney` | 独立抓取 |
| 4 | ◆ 章鱼 AI+·东方财富 (二) | `eastmoney` | 接续第3页；并剔除与1-2重复标题 |

```bash
python main.py --pages 1,2,3,4 --dry-run   # 四页全预览
python main.py --pages 1,2 --dry-run       # 仅财新两页
```

> ✅ 主标题固定 **章鱼 AI+**；顶栏再拼来源标签区分四页，避免原先 1=3、2=4 看起来相同。
> 若第1-2页也选 `eastmoney`，第3-4页自动换栏目/翻页 + 跨组去重，仍保证四页不同。
> 默认：`ENABLED_PAGES="1,2,3,4"`。

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
- `BOARD_TITLE`：看板主标题，默认 `章鱼 AI+`
- `HOTLIST_SOURCE`：第 1,2 页源，`caixin`（默认）/ `eastmoney` / `zhihu` / `bilibili` / `github`
- `ENABLED_PAGES`：`1,2,3,4`（财新+东方财富四页）或 `1,2`（仅财新两页）

### 5. Actions 频率
以根目录 **`w.yml`** 为准（工作流文件，可复制内容到 `.github/workflows/run.yml` 生效），`cron` 为 UTC 时间。
当前默认：**每 30 分钟自动运行一次**（`*/30 * * * *`，即北京时间每个整点与半点），自动抓取数据并推送看板。
如需调整节奏，改 `w.yml` 里的 `cron` 即可（GitHub Actions 最短 5 分钟一次），改完复制到 `.github/workflows/run.yml`。工作流已加防重叠保护，若上一次还没跑完会排队等待，不会并发推送。

### 6. 手动运行
Actions → 章鱼 AI+ 看板 → Run workflow
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
