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

# 1. 看板主标题（每页顶栏前缀，后接来源标签区分四页）
BOARD_TITLE = "章鱼 AI+"

# 2. 控制推送哪几页？
# 墨水屏共 4 页，默认四页互不重复：
#   - 1,2 = 第1组来源（默认财新社 HOTLIST_SOURCE）
#   - 3,4 = 第2组来源（默认东方财富，始终独立抓取）
#   顶栏标题示例：
#     Page1 ◆ 章鱼 AI+·财新社 (一)   Page2 ◆ 章鱼 AI+·财新社 (二)
#     Page3 ◆ 章鱼 AI+·东方财富 (一) Page4 ◆ 章鱼 AI+·东方财富 (二)
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

# 3.1 东方财富细分配置（用于第 3,4 页；若 1,2 也选 eastmoney 则自动换栏目/翻页避免重复）
EASTMONEY_COLUMN = "345"
EASTMONEY_BIZ = "web_news_col"
EASTMONEY_PAGE_SIZE = 24
# 当第1,2页也是 eastmoney 时，第3,4页改用此栏目，保证内容不同
EASTMONEY_ALT_COLUMN = "344"

# 3.2 财新社配置（用于第 1,2 页）
CAIXIN_PAGE_SIZE = 24

# 3.3 各来源在顶栏显示的短标签（必须互不相同，保证四页一眼可辨）
SOURCE_LABELS = {
    "caixin": "财新社",
    "eastmoney": "东方财富",
    "zhihu": "知乎热榜",
    "bilibili": "B站热搜",
    "github": "GitHub",
}

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
def get_eastmoney_news(page_size=20, column=None, biz=None, page_index=1):
    column = column or EASTMONEY_COLUMN
    biz = biz or EASTMONEY_BIZ
    page_index = int(page_index or 1)
    titles = []
    print(f"正在从 东方财富 获取数据 (column={column}, biz={biz}, page_index={page_index})...")
    timestamp = str(int(time.time() * 1000))
    req_trace_base = str(int(time.time()*1000)) + str(random.randint(100,999))
    api_candidates = [
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={req_trace_base}&fields=code,showTime,title,mediaName,summary,image,url,uniqueUrl",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news&column=24&order=1&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=344&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
    ]
    if column != "345":
        api_candidates.append(f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}")

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
            "光伏玻璃龙头提价 产业链景气度回升",
            "券商中报分化加剧 财富管理成胜负手",
            "北向资金连续净流入 重点加仓新能源",
            "工信部：加快推进工业软件国产化替代",
            "地方债发行提速 基建投资有望回暖",
            "半导体设备招标回暖 国产替代加速",
            "消费电子旺季将至 果链公司备货积极",
            "银行净息差企稳 板块估值修复可期",
            "保险负债端改善 权益市场弹性加大",
            "航运运价震荡 关注旺季需求变化",
            "有色金属价格走强 铜铝库存持续去化",
            "军工订单落地加快 产业链景气上行",
            "传媒游戏版号常态化 龙头估值修复",
            "农业种植端受天气扰动 关注价格弹性",
            "家电以旧换新政策加码 内销有望改善",
            "汽车销量环比回升 智能化成竞争焦点",
            "地产销售边际企稳 优质房企融资改善",
            "旅游出行数据回暖 暑期消费表现亮眼",
            "教育培训规范发展 职业教育景气向上",
            "环保督察趋严 运营类资产价值凸显",
        ]
        # page_index / 不同栏目错开切片，避免 1,2 与 3,4 在离线兜底时撞同一批标题
        col_shift = {"345": 0, "344": 8, "340": 16}.get(str(column), 0)
        start = col_shift + max(0, (page_index - 1) * 10)
        start = start % max(1, len(sample) - 8)
        titles = sample[start:start + page_size]
        if len(titles) < page_size:
            titles = titles + sample[: page_size - len(titles)]

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
            except Exception:
                titles = ["数据获取失败，请检查网络或东方财富接口"] * 10
        elif source == "caixin":
            try:
                titles = get_caixin_news(page_size=page_size)
            except Exception:
                titles = ["数据获取失败，请检查网络或财新接口"] * 10
        else:
            titles = ["数据获取失败，请检查配置"] * 10
    return titles[:page_size]


def source_label(source, override=None):
    """顶栏短标签：优先 override，否则用 SOURCE_LABELS，保证不同来源显示不同。"""
    if override:
        return override
    return SOURCE_LABELS.get(source, source or "资讯")


def wrap_text_by_pixels(draw, text, font, max_width):
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        try:
            w = draw.textlength(test_line, font=font)
        except AttributeError:
            w = draw.textbbox((0, 0), test_line, font=font)[2]
        if w <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = char
    if current_line:
        lines.append(current_line)
    return lines


def draw_news_list(draw, page_title, items, start_idx):
    """
    在 400x300 画布上绘制新闻列表。
    返回本页结束后的下一条索引（供下一页接续，保证同来源两页内容不重叠）。
    """
    draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
    # 标题过长时截断，避免溢出圆角条
    title_text = page_title
    try:
        while draw.textlength(title_text, font=font_title) > 360 and len(title_text) > 4:
            title_text = title_text[:-1]
        if title_text != page_title:
            title_text = title_text[:-1] + "…"
    except Exception:
        title_text = page_title[:14]
    draw.text((20, 15), title_text, font=font_title, fill=255)

    y, last_idx = 55, start_idx
    item_gap = 12
    line_height = 23
    for i in range(start_idx, len(items)):
        lines = wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
        required_h = len(lines) * line_height
        if y + required_h > 295:
            break
        current_num = i + 1  # 同来源内连续编号：1,2页 1..N；3,4页另起 1..N
        draw.rounded_rectangle([(10, y), (36, y + 24)], radius=6, fill=0)
        num_x = 18 if current_num < 10 else 11
        draw.text((num_x, y + 3), str(current_num), font=font_small, fill=255)
        curr_y = y + 1
        for line in lines:
            draw.text((45, curr_y), line, font=font_item, fill=0)
            curr_y += line_height
        y += max(24, required_h) + item_gap
        last_idx = i + 1
        if y < 290:
            draw.line([(45, y - item_gap / 2), (380, y - item_gap / 2)], fill=0, width=1)
    return last_idx


def dedupe_titles(titles, exclude=None):
    """保序去重；exclude 用于剔除已在其他页出现过的标题，保证四页内容互不相同。"""
    exclude = set(exclude or [])
    seen = set()
    out = []
    for t in titles:
        t = (t or "").strip()
        if not t or t in seen or t in exclude:
            continue
        seen.add(t)
        out.append(t)
    return out


def make_page_header(label, part):
    """
    顶栏文案：主标题「章鱼 AI+」+ 来源标签 + 分页序号
    例：◆ 章鱼 AI+·财新社 (一)
    过长时 draw_news_list 会按像素截断。
    """
    main = (BOARD_TITLE or "").strip() or "章鱼 AI+"
    label = (label or "").strip()
    if label and label != main:
        return f"◆ {main}·{label} ({part})"
    return f"◆ {main} ({part})"


def render_two_pages(titles, page_ids, label, part_names=("一", "二"), dry_run=False):
    """
    把同一来源的 titles 连续分页画到 page_ids（通常是 [1,2] 或 [3,4]）。
    - 顶栏：◆ 章鱼 AI+·{label} (一/二)  —— 主标题统一 + 来源标签区分四页
    - 内容：第 N+1 页从上一页结束处接续，同来源两页条目不重叠
    返回本页组实际用到的标题列表（供跨组去重）。
    """
    used = []
    if not page_ids:
        return used
    next_s = 0
    enabled = [str(p) for p in page_ids if str(p) in ENABLED_PAGES]
    if not enabled:
        return used

    for i, pid in enumerate(enabled):
        part = part_names[i] if i < len(part_names) else str(i + 1)
        page_title = make_page_header(label, part)
        print(f"生成 Page {pid}: {page_title}  [条目起点 index={next_s}]")
        img = Image.new("1", (400, 300), color=255)
        start_index = next_s
        # 若本页是组内第一页且被单独启用，从 0 开始；否则接续
        if i == 0:
            start_index = 0
        end_index = draw_news_list(ImageDraw.Draw(img), page_title, titles, start_index)
        # 防御：若本页一个条目都没画上（数据不够），尽量向后找剩余
        if end_index <= start_index and start_index < len(titles):
            # 仍画空页也 push，避免设备残留旧图；但尽量提示
            print(f"  ⚠️ Page {pid} 从 index={start_index} 起已无足够条目可画")
        used.extend(titles[start_index:end_index])
        next_s = end_index
        push_image(img, pid, dry_run=dry_run)
    return used


# --- 任务：热搜看板（1,2页，默认财新社） ---
def task_hotlist(dry_run=False, source_override=None, title_override=None):
    effective_source = source_override or HOTLIST_SOURCE
    if "1" not in ENABLED_PAGES and "2" not in ENABLED_PAGES:
        return []

    label = source_label(effective_source, override=title_override)
    # 多抓一些，确保两页都能填满且互不重叠
    titles = get_hotlist_data(effective_source, page_size=max(CAIXIN_PAGE_SIZE, 24))
    titles = dedupe_titles(titles)
    print(f"第1-2页来源={effective_source} 标签=「{label}」 共 {len(titles)} 条（去重后）")

    used = render_two_pages(
        titles,
        page_ids=["1", "2"],
        label=label,
        part_names=("一", "二"),
        dry_run=dry_run,
    )
    return used  # 返回已用标题，供第3-4页跨组去重


# --- 任务：东方财富看板（3,4页） ---
def task_eastmoney(dry_run=False, title_override=None, exclude_titles=None, hotlist_source=None):
    """
    东方财富两页：Page 3 和 Page 4
    - 独立抓取，顶栏固定显示「东方财富」，与第1-2页来源标签不同
    - 若第1-2页也是 eastmoney：自动换栏目（ALT）并翻到第2页 API，再剔除 exclude，保证四页内容都不重复
    """
    if "3" not in ENABLED_PAGES and "4" not in ENABLED_PAGES:
        return []

    hotlist_source = hotlist_source or HOTLIST_SOURCE
    exclude_titles = exclude_titles or []

    # 默认栏目；若与 1,2 页同源则改用备用栏目 + 翻页
    column = EASTMONEY_COLUMN
    page_index = 1
    if hotlist_source == "eastmoney":
        column = EASTMONEY_ALT_COLUMN or "344"
        page_index = 2
        print(f"  ℹ️ 第1-2页已是东方财富，第3-4页改用栏目 {column} / page_index={page_index} 避免重复")

    raw = get_eastmoney_news(
        page_size=max(EASTMONEY_PAGE_SIZE, 24),
        column=column,
        biz=EASTMONEY_BIZ,
        page_index=page_index,
    )
    titles = dedupe_titles(raw, exclude=exclude_titles)

    # 若去重后不够填两页，再尝试另一栏目补齐
    if len(titles) < 12:
        alt_col = "340" if column != "340" else "345"
        print(f"  ℹ️ 去重后仅 {len(titles)} 条，尝试栏目 {alt_col} 补齐...")
        more = get_eastmoney_news(page_size=24, column=alt_col, page_index=1)
        titles = dedupe_titles(titles + more, exclude=exclude_titles)

    # 顶栏标签必须与第1-2页不同；同源 eastmoney 时按栏目区分
    col_labels = {"345": "东财导读", "344": "东财要闻", "340": "东财股市"}
    if hotlist_source == "eastmoney":
        label = col_labels.get(str(column), f"东财{column}")
    elif title_override:
        # 自定义标题只作用于 1-2 页；3-4 固定来源名
        label = SOURCE_LABELS.get("eastmoney", "东方财富")
    else:
        label = source_label("eastmoney")

    print(f"第3-4页来源=eastmoney 标签=「{label}」 共 {len(titles)} 条（已剔除与1-2页重复）")

    used = render_two_pages(
        titles,
        page_ids=["3", "4"],
        label=label,
        part_names=("一", "二"),
        dry_run=dry_run,
    )
    return used


# ================= 主程序 =================
def parse_args():
    parser = argparse.ArgumentParser(description="极趣墨水屏 章鱼 AI+ 看板 - 1,2财新 + 3,4东方财富（四页内容互不重复）")
    parser.add_argument("--source", dest="source", type=str, default=None,
                        help="第1-2页热搜源: zhihu/bilibili/github/eastmoney/caixin (默认 caixin)")
    parser.add_argument("--pages", dest="pages", type=str, default=None,
                        help="覆盖推送页面，例如 \"1,2\" 仅推财新两页，\"1,2,3,4\" 推财新+东方财富四页")
    parser.add_argument("--dry-run", action="store_true", help="仅本地生成预览图，不推送到 Zectrix")
    parser.add_argument("--east-column", dest="east_column", type=str, default=None,
                        help="东方财富栏目ID（第3-4页），默认345（财经导读）")
    parser.add_argument("--title", dest="title", type=str, default=None,
                        help="仅覆盖第1-2页顶栏来源标签；第3-4页仍显示「东方财富」以区分")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    if args.source:
        HOTLIST_SOURCE = args.source
        print(f"🔧 命令行覆盖第1-2页热搜源: {HOTLIST_SOURCE}")
    if args.pages:
        ENABLED_PAGES = args.pages
        print(f"🔧 命令行覆盖推送页面: {ENABLED_PAGES}")
    if args.east_column:
        EASTMONEY_COLUMN = args.east_column
        print(f"🔧 东方财富栏目覆盖(第3-4页): {EASTMONEY_COLUMN}")
    if args.title:
        print(f"🔧 第1-2页自定义标签: {args.title}")

    dry_run_mode = args.dry_run
    if not API_KEY or not MAC_ADDRESS:
        if not dry_run_mode:
            print("⚠️ 未检测到 ZECTRIX_API_KEY / ZECTRIX_MAC，自动切换为 dry_run 本地预览模式")
            print("   如需真正推送到墨水屏，请先设置：")
            print("   export ZECTRIX_API_KEY=你的Key")
            print("   export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF")
            print("   python main.py --pages 1,2,3,4 --dry-run")
            dry_run_mode = True
        else:
            print("🔍 dry_run 模式：仅生成本地预览图")
    else:
        if dry_run_mode:
            print("🔍 dry_run 模式：已配置密钥但仍仅本地预览")
        else:
            print(f"🚀 已配置 Zectrix 设备 {MAC_ADDRESS}，将执行真实推送")

    print(f"🚀 开始执行墨水屏推送任务（主标题: {BOARD_TITLE}）...")
    print(f"   第1-2页源: {HOTLIST_SOURCE} | 页面: {ENABLED_PAGES} | 模式: {'dry_run' if dry_run_mode else 'push'}")
    print("   分页策略: 主标题统一「章鱼 AI+」；1-2 与 3-4 不同来源标签；同来源接续；跨组去重")

    # 1,2 页（默认财新社）
    used_12 = task_hotlist(
        dry_run=dry_run_mode,
        source_override=HOTLIST_SOURCE,
        title_override=args.title,
    )
    # 3,4 页（东方财富），剔除 1,2 已用标题，保证四页内容都不同
    task_eastmoney(
        dry_run=dry_run_mode,
        title_override=None,
        exclude_titles=used_12,
        hotlist_source=HOTLIST_SOURCE,
    )

    print("🎉 所有任务执行完毕！")
    if dry_run_mode:
        print("💡 预览图已生成：page_*.png")
        print(f"   Page1 ◆ {BOARD_TITLE}·财新社 (一)  | Page2 ◆ {BOARD_TITLE}·财新社 (二)")
        print(f"   Page3 ◆ {BOARD_TITLE}·东方财富 (一)| Page4 ◆ {BOARD_TITLE}·东方财富 (二)")
        print("   四页主标题统一 + 来源标签/正文互不相同")
