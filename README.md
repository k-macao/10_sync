# 极趣墨水屏 NewsNow 风格看板

**⏱️ 复刻本项目仅需 5 分钟，即可快速拥有专属于你的桌面智能信息看板。**

这是一个专为极趣墨水屏 (Zectrix) 打造的自动化信息看板项目。

无需自行编写代码，只需按照下方步骤配置，即可让你的墨水屏实现全自动推送。完全免费，无需自建服务器。

<img src="./images/preview.jpg" width="60%">
<img src="./images/zhihu.jpg" width="60%">
<img src="./images/calendar.jpg" width="60%">
<img src="./images/weather.jpg" width="60%">

---

## 📌 看板显示内容

本项目适配 400×300 分辨率墨水屏，共包含以下页面：

- **第 1-2 页：多源热榜 (支持知乎、B站、GitHub、东方财富)** – 自动抓取实时热点并分页排版。你可以自由切换不同的信息源，**作为桌面第一手资讯看板，极大减少频繁打开手机的次数**。新增 `eastmoney`东方财富财经新闻源，支持 `python main.py --source eastmoney` 手动推送（详见 `EASTMONEY_MANUAL.md`）。
- **第 3 页：日历历法** – 显示公历日期、农历、24节气及传统节假日。排版清晰直观，**可直接作为一款优秀的桌面电子日历使用**。
- **第 4 页：综合天气** – 聚合高德地图与气象数据，包含实时温湿度、未来预报及穿衣建议。**由于采用高德核心数据，预报极其精确，与手机原生天气软件的体验非常接近**。

---

## 🛠️ 部署指南

小白用户请依次按照以下 6 个步骤进行操作：

### 1. 复制项目 (Fork)
点击本页面右上角的 **`Fork`** 按钮，将本项目复制到你自己的 GitHub 账号下。

### 2. 上传中文字体文件(可选)
如果你想更换自定义字体：
1. 准备一个中文字体文件（后缀为 `.ttf`，默认使用的是`MiSans-Medium.ttf`）。
2. 将其重命名为 **`font.ttf`** （注意必须全部为小写）。
3. 在你的仓库首页，点击 **`Add file`** -> **`Upload files`** 上传并覆盖原文件，点击 **`Commit changes`** 保存。

### 3. 配置隐私密钥 (Secrets)
由于涉及个人设备和 API 额度，需要将密钥配置在 GitHub 隐藏设置中。
1. 点击仓库顶部的 **`Settings`** 选项卡。
2. 在左侧菜单栏找到 **`Secrets and variables`**，点击展开后选择 **`Actions`**。
3. 点击 **`New repository secret`** 按钮，**分别添加**以下 3 个密钥：

| 填在 Name 里 | 填在 Secret 里 | 获取方式 |
|---|---|---|
| `ZECTRIX_API_KEY` | 你的极趣云 API Key | 登录 [极趣云控制台](https://cloud.zectrix.com) 获取 |
| `ZECTRIX_MAC` | 墨水屏 MAC 地址 | 格式如 `AA:BB:CC:DD:EE:FF` |
| `AMAP_WEATHER_KEY` | 高德 Web服务 Key | 在 [高德开放平台](https://lbs.amap.com/) 免费注册获取 |

### 4. 自定义城市与页面
你需要修改代码中的几个参数，把天气换成你所在的城市。
1. 在仓库首页，点击打开 **`main.py`** 文件。
2. 点击右上角的 ✏️ (编辑图标)。
3. 在代码顶部的**用户自定义区**，修改引号内的内容：
   - `HOTLIST_SOURCE`：修改为你想要的信息源（可选 `zhihu`, `bilibili`, `github`, `eastmoney` 东方财富）。
   - `CITY_ADCODE`：修改为你所在城市的 6 位 Adcode（如北京填 `110000`）。
   - `WTTR_LOCATION`：修改为你所在城市的拼音（如 `Beijing`）。
   - `CITY_DISPLAY_NAME`：屏幕左上角显示的标题（如 `北京市 | 我的桌面`）。
   - `ENABLED_PAGES`：控制显示的页面。如果不想要热搜页，可将 `"1,2,3,4"` 修改为 `"3,4"`。
4. 修改完成后，点击右上角 **`Commit changes`** 保存。

### 5. 修改推送频率 (可选)
默认情况下，系统**每小时**自动推送一次。如需修改：
1. 进入仓库的 `.github/workflows/` 文件夹，点击编辑里面的 `.yml` 文件。
2. 找到 `cron: '0 * * * *'` 这一行进行修改。
3. **注意：此处使用的是 UTC 时间，比北京时间慢 8 小时。**
   - 每 2 小时更新一次：`cron: '0 */2 * * *'`
   - 每天早上 8 点更新一次（北京时间 8点 = UTC 0点）：`cron: '0 0 * * *'`
4. 修改后点击 **`Commit changes`** 保存。

### 6. 手动运行并激活
配置完成后，我们需要手动让它运行一次。
1. 点击仓库顶部的 **`Actions`** 选项卡。
2. 若弹出绿色提示框，请点击 **`I understand my workflows, go ahead and enable them`**。
3. 在左侧列表找到推送任务，点击选中它。
4. 点击右侧的 **`Run workflow`** 按钮，再点击弹出框中的确认按钮。

---

## 🚀 未来规划

本项目将持续迭代，计划增加以下功能：

1. 📈 **B 站粉丝看板**：增加专门的页面，实时显示 B 站粉丝数及动态。

---

## 💖 致谢
- 天气数据支持：[高德开放平台](https://lbs.amap.com/) & [wttr.in](https://wttr.in)
- 信息源支持：[知乎](https://www.zhihu.com) & [Bilibili](https://www.bilibili.com) & [GitHub](https://github.com)
- 硬件及推送接口：[极趣云 Zectrix](https://cloud.zectrix.com)

---
如果觉得这个项目有用，欢迎给个 ⭐ 支持一下！
