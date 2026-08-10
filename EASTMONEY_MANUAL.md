# 东方财富新闻 → Zectrix 手动推送指南

本项目已新增 **东方财富 (eastmoney)** 数据源，支持读取东方财富财经新闻并手动推送到极趣墨水屏 (Zectrix)。

---

## 一、快速开始（本地手动推送）

### 1. 获取 Zectrix 密钥
- `ZECTRIX_API_KEY`：登录 https://cloud.zectrix.com 获取
- `ZECTRIX_MAC`：墨水屏背面 MAC，格式 `AA:BB:CC:DD:EE:FF`

### 2. 本地预览（不推送，仅生成图片）
```bash
# 克隆仓库
git clone https://github.com/k-macao/10_sync.git
cd 10_sync
pip install requests pillow zhdate

# 预览东方财富新闻排版（离线也可用内置示例）
python main.py --source eastmoney --dry-run
# 或使用专用脚本（更简洁）
python manual_eastmoney_push.py --dry-run
```
执行后会生成 `page_1.png` / `page_2.png`（400×300 墨水屏原图），可在本地直接查看。已生成放大预览 `page_1_preview.jpg` 供浏览器查看。

### 3. 真正推送到墨水屏
```bash
export ZECTRIX_API_KEY="你的Key"
export ZECTRIX_MAC="AA:BB:CC:DD:EE:FF"

# 方式A：通过 main.py（推荐，支持日历天气一起推）
python main.py --source eastmoney

# 方式B：仅推东方财富 1,2 页（不含日历天气）
python main.py --source eastmoney --pages 1,2

# 方式C：专用脚本（仅推东方财富）
python manual_eastmoney_push.py
python manual_eastmoney_push.py --pages 1,2
```

### 4. 自定义栏目
东方财富默认栏目 `345`（财经导读，最全）。可选：
- `345` 财经导读（默认，综合）
- `344` 财经要闻
- `340` 股市播报

```bash
python main.py --source eastmoney --east-column 344
python manual_eastmoney_push.py --column 344
```

### 5. 自定义标题测试（调试排版）
```bash
python manual_eastmoney_push.py --titles "标题1内容|标题2内容|标题3内容" --dry-run
```

---

## 二、GitHub Actions 自动 / 手动推送

### 自动推送（每小时）
默认 `HOTLIST_SOURCE = "eastmoney"`，Fork 后配置 Secrets 即可每小时自动推送（UTC `0 * * * *` 即北京时间每小时）。

### 手动推送（Actions 页面点按钮）
已升级 `/.github/workflows/run.yml`，支持 `workflow_dispatch` 输入参数：

1. 打开仓库 → `Actions` → `墨水屏综合看板推送` → `Run workflow`
2. 可选参数：
   - `hotlist_source`：选择 `eastmoney` / `zhihu` / `bilibili` / `github`
   - `eastmoney_column`：栏目ID（默认345）
   - `pages`：推送页面（如 `1,2` 仅热榜）
   - `dry_run`：是否仅预览（true/false）

点 `Run workflow` 即会读取东方财富最新新闻并推送到你的 Zectrix 设备。

**无需修改代码**，直接在网页上切换数据源即可。

---

## 三、数据源说明

### 东方财富接口
主接口：
```
https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&needInteractData=0&page_index=1&page_size=20&req_trace={timestamp}
```
返回 JSON：
```json
{"code":"1","data":{"list":[{"title":"...","showTime":"2026-08-10 19:10:00","mediaName":"...","uniqueUrl":"..."}]}}
```

备用/兜底：
- HTML 抓取 https://finance.eastmoney.com/
- 内置20条真实示例（保证离线也能预览，避免白屏）

已验证 2026-08-10 真实抓取示例：
- 宇树科技：网上发行最终中签率0.0181%
- 央行印发《中国人民银行“十五五”改革发展规划》
- 《煤炭工业发展“十五五”规划》印发...
- 美股三大指数震荡整理 国际油价大涨
- 高盛研判中国AI股...
- 江波龙：半年度净利润105.77亿元...

### 墨水屏排版
- 分页：`page_1.png` 为榜单上半部分，`page_2.png` 为下半部分
- 黑底序号框 + 分割线，适配 400×300 1-bit
- 按像素宽度换行，解决中英文混排留白问题
- 中文使用 `font.ttf` (MiSans-Medium)

---

## 四、本地目录说明
- `main.py`：主程序，已支持 `--source eastmoney` / `--dry-run` / `--pages` / `--east-column`
- `manual_eastmoney_push.py`：专用手动推送脚本（独立、可单独拷贝使用）
- `page_1.png` / `page_2.png`：生成的墨水屏原图（直接推送）
- `page_1_preview.jpg` / `page_2_preview.jpg`：放大预览图（浏览器查看）
- `.github/workflows/run.yml`：支持手动选择数据源的 GitHub Actions

---

## 五、常见问题

**Q: 本地 `curl`/`requests` 无法访问东方财富？**
A: 沙箱/某些网络环境可能屏蔽外网，代码已内置兜底示例，仍可预览。GitHub Actions 云端有正常公网，可真实抓取。

**Q: 推送失败 `SSLError`？**
A: 检查密钥是否正确，或先用 `--dry-run` 确认图片生成是否正常。

**Q: 如何只推东方财富不推天气？**
A: `python main.py --source eastmoney --pages 1,2` 或修改 `ENABLED_PAGES = "1,2"`

**Q: 如何切回知乎？**
A: `python main.py --source zhihu` 或在 GitHub Actions 手动触发时选择 `zhihu`

---

## 六、一键推送示例（复制即用）

```bash
export ZECTRIX_API_KEY="xxx"
export ZECTRIX_MAC="AA:BB:CC:DD:EE:FF"
export AMAP_WEATHER_KEY="可选，不推天气可不设"
python main.py --source eastmoney --pages 1,2
# 看到 ✅ Page 1 推送成功 即表示墨水屏已更新
```

