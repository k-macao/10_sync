import os
import requests
import re
import json
import time
import random
import argparse
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta

# =====================================================================
# 🌟 第一部分：用户自定义区（想改什么，直接在这里改文字和数字） 🌟
# =====================================================================

# 1. 看板标题（显示在每页顶部标题栏）
BOARD_TITLE = "章鱼AI全景分析"

# 2. 控制推送哪几页？
# 墨水屏共 4 页：
#   - 1,2 = 财新社热榜（由 HOTLIST_SOURCE 决定）
#   - 3,4 = 东方财富新闻（始终为 eastmoney）
#   若只要 2 页，设 ENABLED_PAGES="1,2"
#   完整 4 页：ENABLED_PAGES="1,2,3,4"
ENABLED_PAGES = "1,2,3,4"

# 3. 第 1,2 页热搜源设置：支持 'zhihu', 'bilibili', 'github', 'eastmoney', 'caixin'
#   - zhihu: 知乎热榜
#   - bilibili: B站热搜
#   - github: GitHub热门仓库
#   - eastmoney: 东方财富财经新闻
#   - caixin: 财新社（财新网最新）
HOTLIST_SOURCE = "caixin"  # 第 1,2 页默认财新社

# 3.1 东方财富细分配置（用于第 3,4 页）
EASTMONEY_COLUMN = "345"
EASTMONEY_BIZ = "web_news_col"
EASTMONEY_PAGE_SIZE = 20

# 3.2 财新社配置（用于第 1,2 页）
CAIXIN_PAGE_SIZE = 20

# =====================================================================
# 🔒 第二部分：核心密钥区（⚠️绝对不要改这里，请在 GitHub Secrets 里配置） 🔒
# =====================================================================
API_KEY = os.environ.get("ZECTRIX_API_KEY")
MAC_ADDRESS = os.environ.get("ZECTRIX_MAC")
# AMAP_KEY 已废弃（天气页已去除），保留兼容读取但不再使用
AMAP_KEY = os.environ.get("AMAP_WEATHER_KEY")

PUSH_URL = f"https://cloud.zectrix.com/open/v1/devices/{MAC_ADDRESS}/display/image" if MAC_ADDRESS else ""

# =====================================================================
# ⚙️ 第三部分：底层运行逻辑
# =====================================================================

FONT_PATH = "font.ttf"
try:
    font_huge = ImageFont.truetype(FONT_PATH, 65)
    font_title = ImageFont.truetype(FONT_PATH, 24)
    font_item = ImageFont.truetype(FONT_PATH, 18)
    font_small = ImageFont.truetype(FONT_PATH, 14)
    font_tiny = ImageFont.truetype(FONT_PATH, 11)
    font_48 = ImageFont.truetype(FONT_PATH, 48)
    font_36 = ImageFont.truetype(FONT_PATH, 36)
except:
    print("❌ 错误: 找不到 font.ttf")
    exit(1)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://finance.eastmoney.com/'
}
HEADERS_CAIXIN = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.caixin.com/',
    'Accept': 'application/json, text/plain, */*',
}

def push_image(img, page_id, dry_run=False):
    if str(page_id) not in ENABLED_PAGES:
        print(f"⏩ Page {page_id} 未启用，跳过推送。")
        return True
    filename = f"page_{page_id}.png"
    img.save(filename)
    print(f"💾 Page {page_id} 已保存为 {filename}")
    if dry_run:
        print(f"🔍 dry_run 模式：Page {page_id} 仅本地预览，不推送到 Zectrix")
        return True
    if not API_KEY or not MAC_ADDRESS:
        print(f"⚠️ 未配置 ZECTRIX_API_KEY / ZECTRIX_MAC，Page {page_id} 仅本地保存，跳过推送。")
        return False
    api_headers = {"X-API-Key": API_KEY}
    push_url = f"https://cloud.zectrix.com/open/v1/devices/{MAC_ADDRESS}/display/image"
    try:
        with open(filename, "rb") as f:
            files = {"images": (filename, f, "image/png")}
            data = {"dither": "true", "pageId": str(page_id)}
            res = requests.post(push_url, headers=api_headers, files=files, data=data, timeout=15)
            print(f"✅ Page {page_id} 推送成功: {res.status_code} - {res.text[:200]}")
            return res.status_code in (200, 201, 204)
    except Exception as e:
        print(f"❌ Page {page_id} 推送失败: {e}")
        return False

# --- 财新社专用获取 ---
def get_caixin_news(page_size=20):
    """
    获取财新社（财新网）新闻标题列表
    优先使用 gateway.caixin.com / mapiv5.caixin.com API，失败回退 HTML，最后内置示例兜底
    参考 RSSHub 实现：gateway.caixin.com/api/dataplatform/scroll/index
                      mapiv5.caixin.com/m/api/getWapIndexListByPage
    """
    titles = []
    print(f"正在从 财新社 获取数据 (目标 {page_size} 条)...")
    timestamp = str(int(time.time() * 1000))
    api_candidates = [
        f"https://gateway.caixin.com/api/dataplatform/scroll/index?count={page_size}&_={timestamp}",
        f"https://mapiv5.caixin.com/m/api/getWapIndexListByPage?page=1&count={page_size}&callback=&_={timestamp}",
        f"https://gateway.caixin.com/api/extapi/homeInterface.jsp?subject=100990318;100990314;100990311&start=0&count={page_size}&type=2&_={timestamp}",
        f"https://gateway.caixin.com/api/dataplatform/scroll/index?count={page_size}",
        f"https://mapiv5.caixin.com/m/api/getWapIndexListByPage?page=1",
    ]

    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:80]}...")
            res = requests.get(url, headers=HEADERS_CAIXIN, timeout=10)
            text = res.text.strip()
            if not text:
                continue
            # 处理 JSONP
            if text.startswith("jQuery") or (text.startswith("(") and text.endswith(")")) or "callback" in text[:30].lower():
                s = text.find("{")
                e = text.rfind("}")
                if s != -1 and e != -1 and e > s:
                    text = text[s:e+1]
            try:
                data = json.loads(text)
            except:
                # 有时返回带引号的非标准，尝试宽松解析
                continue

            if not isinstance(data, dict):
                continue

            # 结构1: {"data":{"articleList":[{"title":...}]}}
            if "data" in data:
                d = data["data"]
                if isinstance(d, dict):
                    article_list = None
                    if "articleList" in d and isinstance(d["articleList"], list):
                        article_list = d["articleList"]
                    elif "list" in d and isinstance(d["list"], list):
                        article_list = d["list"]
                    elif "datas" in d and isinstance(d["datas"], list):
                        article_list = d["datas"]
                    if article_list:
                        for item in article_list:
                            if not isinstance(item, dict):
                                continue
                            t = item.get("title") or item.get("desc") or item.get("TITLE") or ""
                            t = str(t).strip()
                            if t:
                                t = re.sub(r"\s+", " ", t)
                                titles.append(t)
                        if len(titles) >= 5:
                            print(f"  ✅ API 成功获取 {len(titles)} 条")
                            break
                elif isinstance(d, list):
                    for item in d:
                        if isinstance(item, dict):
                            t = item.get("title") or item.get("desc") or ""
                            t = str(t).strip()
                            if t:
                                titles.append(re.sub(r"\s+", " ", t))
                    if len(titles) >= 5:
                        print(f"  ✅ API 成功获取 {len(titles)} 条 (list)")
                        break

            # 兜底直接 data.list
            if "list" in data and isinstance(data["list"], list) and len(titles) < 5:
                for item in data["list"]:
                    if isinstance(item, dict):
                        t = item.get("title","").strip()
                        if t:
                            titles.append(t)
                if titles:
                    break

            if len(titles) >= 5:
                break
        except Exception as e:
            print(f"  ⚠️ API 尝试失败: {e}")
            continue

    # HTML 抓取回退
    if len(titles) < 5:
        print("  API 未获取到足够数据，尝试 HTML 抓取 https://www.caixin.com/ ...")
        try:
            html = requests.get("https://www.caixin.com/", headers=HEADERS_CAIXIN, timeout=10).text
            patterns = [
                r'<a[^>]*href="https?://www\.caixin\.com/[^"]*"[^>]*>([^<]{8,80})</a>',
                r'<a[^>]*href="//www\.caixin\.com/[^"]*"[^>]*>([^<]{8,80})</a>',
                r'"title"\s*:\s*"([^"]{8,80})"',
            ]
            seen_tmp = set()
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    t = re.sub(r"<.*?>", "", m).strip()
                    t = re.sub(r"\s+", " ", t)
                    if len(t) < 8:
                        continue
                    # 过滤明显噪音
                    if t in ["财新网", "财新网 - 财新网", "登录", "订阅", "下载"] or "Copyright" in t:
                        continue
                    if t not in seen_tmp:
                        seen_tmp.add(t)
                        titles.append(t)
                    if len(titles) >= page_size:
                        break
                if len(titles) >= page_size:
                    break
            print(f"  HTML 抓取获得 {len(titles)} 条")
        except Exception as e:
            print(f"  HTML 抓取失败: {e}")

    # 最终兜底：内置真实风格示例（保证离线可预览，避免白屏）
    if len(titles) < 5:
        print("  ⚠️ 仍未获取到数据，使用内置财新社示例数据兜底（离线预览）")
        sample = [
            "财新中国制造业PMI回落至49.5 需求端承压明显",
            "中央政治局会议定调下半年经济 稳增长信号明确",
            "美联储降息预期升温 全球市场震荡分化加剧",
            "央行公开市场净投放3000亿元 流动性保持充裕",
            "沪指重返3100点 机构看好科技成长主线",
            "财政部拟发行超长期特别国债 支持重大项目建设",
            "新能源车出口创单月新高 欧洲市场成主要增量",
            "证监会：加大对财务造假打击力度 坚持零容忍",
            "地方化债进度加快 特殊再融资债券发行提速",
            "医疗反腐持续深入 多家药企主动下调药品价格",
            "离岸人民币汇率升破7.1关口 创近半年新高",
            "香港金管局推进数字港元试点 涵盖零售支付场景",
            "监管发文规范私募行业 强化信息披露与托管要求",
            "万科中报：销售额下滑但经营性现金流改善",
            "碧桂园境外债务重组取得进展 债权人达成初步共识",
            "新一轮稳外贸政策发布 助力外贸企业拓市场",
            "统计局解读7月CPI：食品价格季节性上涨",
            "上海自贸区发布多项制度创新 扩大金融开放",
            "AI大模型商业化提速 多家科技巨头加码布局",
            "OpenAI发布新一代多模态模型 能力大幅提升",
            "证监会优化IPO辅导监管 压实中介机构责任",
            "农产品价格波动加剧 市场关注天气因素影响",
            "跨境电商新规落地 平台责任进一步明确",
            "公募基金二季报披露 加仓电子医药减持白酒",
            "碳市场成交活跃 推动绿色转型加速",
            "多地推出购房支持政策 楼市成交边际改善",
            "大厂半年报：云计算成增长新引擎",
            "数字经济核心产业规模突破12万亿 占比提升",
            "财新调查：居民消费意愿回升 但储蓄倾向仍高",
            "高盛：看好中国AI产业链 三细分领域最具潜力",
            "京津冀协同发展十年 产业转移成效显著",
            "ChatGPT用户破5亿 AI应用渗透率持续提升",
            "光伏产业链价格探底 部分环节出现复苏信号",
            "人民币国际化稳步推进 跨境支付规模创新高",
            "互联网医疗政策优化 线上诊疗纳入医保试点",
            "地方政府启动新一轮汽车以旧换新补贴",
            "财新数据：二季度企业盈利改善 但分化明显",
            "国际油价震荡走高 供需紧平衡格局延续",
            "北京发布促进民营经济高质量发展若干措施",
            "长三角一体化示范区推出80项新举措",
        ]
        titles = sample[:page_size]

    # 去重保序、截断
    seen = set()
    uniq = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:page_size]


# --- 东方财富专用获取 ---
def get_eastmoney_news(page_size=20, column=None, biz=None):
    column = column or EASTMONEY_COLUMN
    biz = biz or EASTMONEY_BIZ
    titles = []
    print(f"正在从 东方财富 获取数据 (column={column}, biz={biz})...")
    timestamp = str(int(time.time() * 1000))
    req_trace_base = str(int(time.time()*1000)) + str(random.randint(100,999))
    api_candidates = [
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={req_trace_base}&fields=code,showTime,title,mediaName,summary,image,url,uniqueUrl",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news&column=24&order=1&page_index=1&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=344&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}",
    ]
    if column != "345":
        api_candidates.append(f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}")

    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:80]}...")
            res = requests.get(url, headers=HEADERS, timeout=10)
            text = res.text.strip()
            if text.startswith("jQuery") or text.startswith("(") or "callback" in text[:20]:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    text = text[start:end+1]
                else:
                    s = text.find("(")
                    e = text.rfind(")")
                    if s != -1 and e != -1:
                        text = text[s+1:e]
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("code") == "1" and isinstance(data.get("data"), dict):
                    lst = data["data"].get("list") or data["data"].get("newsList") or []
                    for item in lst:
                        t = item.get("title") or item.get("TITLE") or ""
                        t = t.strip()
                        if t:
                            t = re.sub(r"\s+", " ", t)
                            titles.append(t)
                    if len(titles) >= 5:
                        print(f"  ✅ API 成功获取 {len(titles)} 条")
                        break
                elif isinstance(data.get("data"), list):
                    for item in data["data"]:
                        t = item.get("title", "").strip()
                        if t:
                            titles.append(t)
                    if titles:
                        break
            if len(titles) >= 5:
                break
        except Exception as e:
            print(f"  ⚠️ API 尝试失败: {e}")
            continue

    if len(titles) < 5:
        print("  API 未获取到足够数据，尝试 HTML 抓取 https://finance.eastmoney.com/ ...")
        try:
            html = requests.get("https://finance.eastmoney.com/", headers=HEADERS, timeout=10).text
            pattern1 = r'<a[^>]*href="https?://finance\.eastmoney\.com/a/[^"]*"[^>]*>([^<]{5,80})</a>'
            matches = re.findall(pattern1, html)
            seen = set()
            for m in matches:
                t = re.sub(r"<.*?>", "", m).strip()
                t = re.sub(r"\s+", " ", t)
                if len(t) >= 5 and t not in seen:
                    seen.add(t)
                    titles.append(t)
                if len(titles) >= page_size:
                    break
            print(f"  HTML 抓取1 获得 {len(titles)} 条")
        except Exception as e:
            print(f"  HTML 抓取1 失败: {e}")

    if len(titles) < 5:
        try:
            html2 = requests.get("https://kuaixun.eastmoney.com/", headers=HEADERS, timeout=10).text
            pattern2 = r'title["\']?\s*[:=]\s*["\']([^"\']{8,80})["\']'
            m2 = re.findall(pattern2, html2)
            for t in m2:
                if "eastmoney" not in t.lower() and len(t) > 8:
                    titles.append(t.strip())
                if len(titles) >= page_size:
                    break
        except Exception as e:
            print(f"  HTML 抓取2 失败: {e}")

    if len(titles) < 5:
        print("  ⚠️ 仍未获取到数据，使用内置东方财富示例数据兜底（离线预览）")
        sample = [
            "宇树科技：网上发行最终中签率0.0181%",
            "央行印发《中国人民银行“十五五”改革发展规划》",
            "《煤炭工业发展“十五五”规划》印发：到2030年大型现代化煤矿产能比重提升至87%",
            "美股三大指数震荡整理 国际油价大涨",
            "高盛研判中国AI股：近期回调已释放核心风险 建议多元布局四大主线",
            "江波龙：半年度净利润105.77亿元 同比增长71528.66% 拟回购股份",
            "8月10日东方财富财经晚报（附新闻联播）",
            "12天11板爱丽家居：股价11个交易日涨185.56% 明起停牌核查",
            "阿里云计划将全球数据中心产能提升两倍以上",
            "年内最贵新股频准激光中签号出炉：共有6423个",
            "4800亿龙头迎利好！CRO概念股梳理",
            "景林最新美股持仓曝光！英伟达等惨遭清仓",
            "越跌越买！央行加速抄底黄金 单月增持19.9吨",
            "8月10日晚间沪深上市公司重大事项公告最新快递",
            "多只LOF今日重挫！三类标的将迎退市",
            "韩国资金抢筹中际旭创！H股上市7天净买4339万美元",
            "中金公司持有的中际旭创H股多头头寸比例增至5.22%",
            "创新药强势霸屏 三重利好持续发酵 超70万手封单",
            "商务部：初步认定原产于墨西哥和美国的进口碧根果存在倾销",
            "高盛：中国AI板块不存在整体泡沫 三大细分领域最具投资价值",
        ]
        titles = sample[:page_size]

    seen = set()
    uniq = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:page_size]

# --- 获取数据的逻辑 (支持切换源) ---
def get_hotlist_data(source, page_size=20):
    titles = []
    print(f"正在从 {source} 获取数据...")
    try:
        if source == "zhihu":
            url = "https://api.zhihu.com/topstory/hot-list"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            titles = [item['target']['title'] for item in res['data']]
        elif source == "bilibili":
            url = "https://api.bilibili.com/x/web-interface/wbi/search/square?limit=20"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            titles = [item['show_name'] for item in res['data']['trending']['list']]
        elif source == "github":
            date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            url = f"https://api.github.com/search/repositories?q=stars:>500+created:>{date_str}&sort=stars&order=desc"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            titles = [f"{item['full_name']}: {item['description'][:50] if item['description'] else 'No desc'}" for item in res['items']]
        elif source == "eastmoney":
            titles = get_eastmoney_news(page_size=page_size, column=EASTMONEY_COLUMN, biz=EASTMONEY_BIZ)
        elif source == "caixin":
            titles = get_caixin_news(page_size=page_size)
        else:
            titles = [f"不支持的数据源 {source}，请检查 HOTLIST_SOURCE 配置（支持 zhihu/bilibili/github/eastmoney/caixin）"]
    except Exception as e:
        print(f"获取失败: {e}")
        if source == "eastmoney":
            try:
                titles = get_eastmoney_news(page_size=page_size)
            except:
                titles = ["数据获取失败，请检查网络或东方财富接口"] * 10
        elif source == "caixin":
            try:
                titles = get_caixin_news(page_size=page_size)
            except:
                titles = ["数据获取失败，请检查网络或财新接口"] * 10
        else:
            titles = ["数据获取失败，请检查配置"] * 10
    return titles[:page_size]


# --- 任务：热搜看板（1,2页，默认财新社） ---
def task_hotlist(dry_run=False, source_override=None, title_override=None):
    effective_source = source_override or HOTLIST_SOURCE
    if "1" not in ENABLED_PAGES and "2" not in ENABLED_PAGES:
        return []

    # 标题栏统一显示 BOARD_TITLE；title_override 可临时覆盖
    if title_override:
        title_display = title_override
    else:
        title_display = BOARD_TITLE

    titles = get_hotlist_data(effective_source, page_size=20)

    def wrap_text_by_pixels(draw, text, font, max_width):
        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            try:
                w = draw.textlength(test_line, font=font)
            except AttributeError:
                w = draw.textbbox((0,0), test_line, font=font)[2]
            if w <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
        return lines

    def draw_list(draw, page_title, items, start_idx):
        draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
        draw.text((20, 15), page_title, font=font_title, fill=255)
        y, last_idx = 55, start_idx
        item_gap = 12
        line_height = 23
        for i in range(start_idx, len(items)):
            lines = wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
            required_h = len(lines) * line_height
            if y + required_h > 295:
                break
            current_num = i + 1
            draw.rounded_rectangle([(10, y), (36, y+24)], radius=6, fill=0)
            num_x = 18 if current_num < 10 else 11
            draw.text((num_x, y+3), str(current_num), font=font_small, fill=255)
            curr_y = y + 1
            for line in lines:
                draw.text((45, curr_y), line, font=font_item, fill=0)
                curr_y += line_height
            y += max(24, required_h) + item_gap
            last_idx = i + 1
            if y < 290:
                draw.line([(45, y - item_gap/2), (380, y - item_gap/2)], fill=0, width=1)
        return last_idx

    next_s = 0
    if "1" in ENABLED_PAGES:
        print(f"生成 Page 1: {title_display} (一)...")
        img1 = Image.new('1', (400, 300), color=255)
        next_s = draw_list(ImageDraw.Draw(img1), f"◆ {title_display} (一)", titles, 0)
        push_image(img1, 1, dry_run=dry_run)

    if "2" in ENABLED_PAGES:
        print(f"生成 Page 2: {title_display} (二)...")
        img2 = Image.new('1', (400, 300), color=255)
        start_index = next_s if "1" in ENABLED_PAGES else 7
        # 如果 titles 只有 20 条且 1,2 已用完 10 条左右，2页会自动接续
        # 如果 titles 有 40 条（caixin + 4页模式），2页也接续前20范围内
        draw_list(ImageDraw.Draw(img2), f"◆ {title_display} (二)", titles, start_index)
        push_image(img2, 2, dry_run=dry_run)

    return titles  # 返回供财新分页复用

# --- 任务：东方财富看板（3,4页） ---
def task_eastmoney(dry_run=False, title_override=None):
    """
    东方财富两页：Page 3 和 Page 4
    - 独立抓取东方财富财经新闻 20 条
    """
    if "3" not in ENABLED_PAGES and "4" not in ENABLED_PAGES:
        return

    titles = get_eastmoney_news(page_size=EASTMONEY_PAGE_SIZE)

    title_display = title_override if title_override else BOARD_TITLE

    def wrap_text_by_pixels(draw, text, font, max_width):
        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            try:
                w = draw.textlength(test_line, font=font)
            except AttributeError:
                w = draw.textbbox((0,0), test_line, font=font)[2]
            if w <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
        return lines

    def draw_list(draw, page_title, items, start_idx):
        draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
        draw.text((20, 15), page_title, font=font_title, fill=255)
        y, last_idx = 55, start_idx
        item_gap = 12
        line_height = 23
        for i in range(start_idx, len(items)):
            lines = wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
            required_h = len(lines) * line_height
            if y + required_h > 295:
                break
            current_num = i + 1 + (20 if start_idx >= 10 else 0)  # 3,4页序号延续20+
            # 但为保持与1,2区分，3页序号从1开始也可；这里用真实序号更直观还是从1开始？按财新独立榜从1开始
            current_num_display = i + 1
            draw.rounded_rectangle([(10, y), (36, y+24)], radius=6, fill=0)
            num_x = 18 if current_num_display < 10 else 11
            draw.text((num_x, y+3), str(current_num_display), font=font_small, fill=255)
            curr_y = y + 1
            for line in lines:
                draw.text((45, curr_y), line, font=font_item, fill=0)
                curr_y += line_height
            y += max(24, required_h) + item_gap
            last_idx = i + 1
            if y < 290:
                draw.line([(45, y - item_gap/2), (380, y - item_gap/2)], fill=0, width=1)
        return last_idx

    next_s = 0
    if "3" in ENABLED_PAGES:
        print(f"生成 Page 3: {title_display} (一)...")
        img3 = Image.new('1', (400, 300), color=255)
        next_s = draw_list(ImageDraw.Draw(img3), f"◆ {title_display} (一)", titles, 0)
        push_image(img3, 3, dry_run=dry_run)

    if "4" in ENABLED_PAGES:
        print(f"生成 Page 4: {title_display} (二)...")
        img4 = Image.new('1', (400, 300), color=255)
        start_index = next_s if "3" in ENABLED_PAGES else 7
        draw_list(ImageDraw.Draw(img4), f"◆ {title_display} (二)", titles, start_index)
        push_image(img4, 4, dry_run=dry_run)


# ================= 主程序 =================
def parse_args():
    parser = argparse.ArgumentParser(description="极趣墨水屏 章鱼AI全景分析看板 - 1,2财新 + 3,4东方财富")
    parser.add_argument("--source", dest="source", type=str, default=None,
                        help="热搜源: zhihu/bilibili/github/eastmoney/caixin (默认跟随 HOTLIST_SOURCE，默认 caixin)")
    parser.add_argument("--pages", dest="pages", type=str, default=None,
                        help="覆盖推送页面，例如 \"1,2\" 仅推财新两页，\"1,2,3,4\" 推财新+东方财富四页")
    parser.add_argument("--dry-run", action="store_true", help="仅本地生成预览图，不推送到 Zectrix")
    parser.add_argument("--east-column", dest="east_column", type=str, default=None,
                        help="东方财富栏目ID，默认345（财经导读）")
    parser.add_argument("--title", dest="title", type=str, default=None,
                        help="自定义推送标题，例如 \"财新社·深度\"，将覆盖默认标题")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    if args.source:
        HOTLIST_SOURCE = args.source
        print(f"🔧 命令行覆盖热搜源: {HOTLIST_SOURCE}")
    if args.pages:
        ENABLED_PAGES = args.pages
        print(f"🔧 命令行覆盖推送页面: {ENABLED_PAGES}")
    if args.east_column:
        EASTMONEY_COLUMN = args.east_column
        print(f"🔧 东方财富栏目覆盖: {EASTMONEY_COLUMN}")
    if args.title:
        print(f"🔧 自定义标题: {args.title}")

    dry_run_mode = args.dry_run
    if not API_KEY or not MAC_ADDRESS:
        if not dry_run_mode:
            print("⚠️ 未检测到 ZECTRIX_API_KEY / ZECTRIX_MAC，自动切换为 dry_run 本地预览模式")
            print("   如需真正推送到墨水屏，请先设置：")
            print("   export ZECTRIX_API_KEY=你的Key")
            print("   export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF")
            print("   python main.py --source caixin --dry-run")
            dry_run_mode = True
        else:
            print("🔍 dry_run 模式：仅生成本地预览图")
    else:
        if dry_run_mode:
            print("🔍 dry_run 模式：已配置密钥但仍仅本地预览")
        else:
            print(f"🚀 已配置 Zectrix 设备 {MAC_ADDRESS}，将执行真实推送")

    print("🚀 开始执行墨水屏推送任务（章鱼AI全景分析看板）...")
    print(f"   热搜源: {HOTLIST_SOURCE} | 页面: {ENABLED_PAGES} | 模式: {'dry_run' if dry_run_mode else 'push'}")

    # 执行热榜任务（1,2页，默认财新社）
    task_hotlist(dry_run=dry_run_mode, source_override=HOTLIST_SOURCE, title_override=args.title)
    # 执行东方财富任务（3,4页）
    task_eastmoney(dry_run=dry_run_mode, title_override=None)

    print("🎉 所有任务执行完毕！")
    if dry_run_mode:
        print("💡 预览图已生成：page_*.png 请在文件浏览器查看效果")
        print("   - 若 ENABLED_PAGES=1,2 : 生成 page_1.png, page_2.png 为财新两页")
        print("   - 若 ENABLED_PAGES=1,2,3,4 : 1,2为财新热榜，3,4为东方财富")
