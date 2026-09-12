#!/usr/bin/env python3
"""
东方财富新闻 → Zectrix 墨水屏 手动推送脚本

用法：
  1) 预览（不推送，仅本地生成 page_*.png）：
     python manual_eastmoney_push.py --dry-run

  2) 推送到墨水屏（需先设置环境变量）：
     export ZECTRIX_API_KEY="你的API Key"
     export ZECTRIX_MAC="AA:BB:CC:DD:EE:FF"
     python manual_eastmoney_push.py

  3) 仅推送热榜页 1,2（不含日历/天气）：
     python manual_eastmoney_push.py --pages 1,2

  4) 指定东方财富栏目（默认345财经导读）：
     python manual_eastmoney_push.py --column 345
     python manual_eastmoney_push.py --column 344  # 要闻

  5) 直接指定完整自定义标题（调试用）：
     python manual_eastmoney_push.py --titles "标题1|标题2|标题3"

GitHub Actions 手动触发也支持选择 eastmoney 源，见 Actions → Run workflow → 选择 source
"""

import os
import sys
import argparse
import requests
from PIL import Image, ImageDraw, ImageFont

# 复用主程序的字体与推送逻辑，尽量保持单一逻辑源
# 为了独立性，这里实现最小化版本，避免循环导入
FONT_PATH = "font.ttf"
try:
    font_title = ImageFont.truetype(FONT_PATH, 24)
    font_item = ImageFont.truetype(FONT_PATH, 18)
    font_small = ImageFont.truetype(FONT_PATH, 14)
except Exception as e:
    print(f"❌ 找不到 {FONT_PATH}: {e}")
    sys.exit(1)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://finance.eastmoney.com/'
}

# 复用主程序的东方财富获取逻辑（简化版，避免依赖 main）
import time, random, re, json

def get_eastmoney_news(page_size=20, column="345", biz="web_news_col"):
    titles = []
    print(f"正在从 东方财富 获取数据 (column={column}, biz={biz})...")
    timestamp = str(int(time.time() * 1000))
    req_trace_base = str(int(time.time()*1000)) + str(random.randint(100,999))
    api_candidates = [
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={req_trace_base}&fields=code,showTime,title,mediaName,summary,image,url,uniqueUrl",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news&column=24&order=1&page_index=1&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=344&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&needInteractData=0&page_index=1&page_size={page_size}&req_trace={timestamp}",
    ]
    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:70]}...")
            res = requests.get(url, headers=HEADERS, timeout=10)
            text = res.text.strip()
            if text.startswith("jQuery") or "callback" in text[:20]:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    text = text[start:end+1]
            data = json.loads(text)
            if isinstance(data, dict) and data.get("code") == "1" and isinstance(data.get("data"), dict):
                lst = data["data"].get("list") or data["data"].get("newsList") or []
                for item in lst:
                    t = (item.get("title") or "").strip()
                    if t:
                        titles.append(re.sub(r"\s+", " ", t))
                if len(titles) >= 5:
                    print(f"  ✅ API 成功获取 {len(titles)} 条")
                    break
        except Exception as e:
            print(f"  ⚠️ API 失败: {e}")
            continue

    if len(titles) < 5:
        print("  API 未获取到足够数据，尝试 HTML 抓取...")
        try:
            html = requests.get("https://finance.eastmoney.com/", headers=HEADERS, timeout=10).text
            pattern1 = r'<a[^>]*href="https?://finance\.eastmoney\.com/a/[^"]*"[^>]*>([^<]{5,80})</a>'
            matches = re.findall(pattern1, html)
            seen=set()
            for m in matches:
                t=re.sub(r"\s+"," ",m.strip())
                if len(t)>=5 and t not in seen:
                    seen.add(t)
                    titles.append(t)
                if len(titles)>=page_size: break
        except Exception as e:
            print(f"  HTML 抓取失败: {e}")

    if len(titles) < 5:
        print("  ⚠️ 使用内置示例兜底")
        sample=[
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
    seen=set()
    uniq=[]
    for t in titles:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:page_size]

def wrap_text_by_pixels(draw, text, font, max_width):
    lines=[]
    cur=""
    for ch in text:
        test=cur+ch
        try:
            w=draw.textlength(test, font=font)
        except:
            w=draw.textbbox((0,0), test, font=font)[2]
        if w<=max_width:
            cur=test
        else:
            lines.append(cur)
            cur=ch
    if cur:
        lines.append(cur)
    return lines

def draw_eastmoney_pages(titles, enabled_pages="1,2", header="章鱼 AI·全景分析"):
    """生成东方财富墨水屏分页，并返回生成的文件列表"""
    def draw_list(draw, page_title, items, start_idx):
        draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
        draw.text((20, 15), page_title, font=font_title, fill=255)
        y, last_idx = 55, start_idx
        item_gap=12
        line_height=23
        for i in range(start_idx, len(items)):
            lines=wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
            required_h=len(lines)*line_height
            if y+required_h>295:
                break
            cur_num=i+1
            draw.rounded_rectangle([(10, y), (36, y+24)], radius=6, fill=0)
            num_x=18 if cur_num<10 else 11
            draw.text((num_x, y+3), str(cur_num), font=font_small, fill=255)
            curr_y=y+1
            for line in lines:
                draw.text((45, curr_y), line, font=font_item, fill=0)
                curr_y+=line_height
            y+=max(24, required_h)+item_gap
            last_idx=i+1
            if y<290:
                draw.line([(45, y-item_gap/2),(380,y-item_gap/2)], fill=0, width=1)
        return last_idx

    files=[]
    enabled = set(enabled_pages.split(","))
    next_s=0
    if "1" in enabled:
        print(f"生成 Page 1: 顶栏「{header}」...")
        img1=Image.new('1',(400,300),color=255)
        next_s=draw_list(ImageDraw.Draw(img1), header, titles, 0)
        img1.save("page_1.png")
        print("💾 已保存 page_1.png")
        files.append("page_1.png")
    if "2" in enabled:
        print(f"生成 Page 2: 顶栏「{header}」...")
        img2=Image.new('1',(400,300),color=255)
        start_idx=next_s if "1" in enabled else 7
        draw_list(ImageDraw.Draw(img2), header, titles, start_idx)
        img2.save("page_2.png")
        print("💾 已保存 page_2.png")
        files.append("page_2.png")
    return files

def push_to_zectrix(files, dry_run=False):
    api_key=os.environ.get("ZECTRIX_API_KEY")
    mac=os.environ.get("ZECTRIX_MAC")
    if dry_run:
        print("🔍 dry_run 模式：仅本地预览，不推送")
        for f in files:
            print(f"  预览文件: {f} 已生成，尺寸 400x300")
        return True
    if not api_key or not mac:
        print("❌ 未配置 ZECTRIX_API_KEY / ZECTRIX_MAC")
        print("请先设置环境变量：")
        print("  export ZECTRIX_API_KEY=你的Key")
        print("  export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF")
        print("然后重试，或使用 --dry-run 仅预览")
        return False
    push_url=f"https://cloud.zectrix.com/open/v1/devices/{mac}/display/image"
    headers={"X-API-Key": api_key}
    ok=True
    for idx, filepath in enumerate(files, start=1):
        page_id = filepath.split("_")[1].split(".")[0]
        # 映射 page_1.png -> pageId 1, page_2.png -> pageId2
        try:
            with open(filepath, "rb") as f:
                files_payload={"images": (filepath, f, "image/png")}
                data={"dither":"true", "pageId": str(page_id)}
                print(f"🚀 推送 {filepath} -> page {page_id} ...")
                res=requests.post(push_url, headers=headers, files=files_payload, data=data, timeout=15)
                print(f"  响应: {res.status_code} {res.text[:200]}")
                if res.status_code not in (200,201,204):
                    ok=False
        except Exception as e:
            print(f"❌ 推送 {filepath} 失败: {e}")
            ok=False
    if ok:
        print("✅ 全部推送成功！请查看墨水屏")
    else:
        print("⚠️ 部分推送失败，请检查日志")
    return ok

def main():
    parser=argparse.ArgumentParser(description="东方财富 → Zectrix 手动推送")
    parser.add_argument("--dry-run", action="store_true", help="仅生成本地预览，不推送")
    parser.add_argument("--pages", type=str, default="1,2", help="推送页面，例如 1,2 默认为 1,2")
    parser.add_argument("--column", type=str, default="345", help="东方财富栏目ID，默认345")
    parser.add_argument("--biz", type=str, default="web_news_col", help="东方财富biz参数")
    parser.add_argument("--titles", type=str, default=None, help="自定义标题，用|分隔，例如 \"标题1|标题2|标题3\" ")
    parser.add_argument("--title", type=str, default="章鱼 AI·全景分析", help="顶栏文案（两页统一），默认 \"章鱼 AI·全景分析\"")
    parser.add_argument("--header", type=str, default=None, help="同 --title，兼容旧参数")
    args=parser.parse_args()

    # 获取标题
    if args.titles:
        titles=[t.strip() for t in args.titles.split("|") if t.strip()]
        print(f"使用自定义标题 {len(titles)} 条")
    else:
        titles=get_eastmoney_news(page_size=20, column=args.column, biz=args.biz)
        print(f"共获取 {len(titles)} 条东方财富新闻:")
        for i,t in enumerate(titles[:10],1):
            print(f" {i:2d}. {t}")
        if len(titles)>10:
            print(f" ... 还有 {len(titles)-10} 条")

    # 生成图片（支持自定义标题如 章鱼 AI·全景分析）
    header = args.header if args.header else args.title
    files=draw_eastmoney_pages(titles, enabled_pages=args.pages, header=header)

    # 推送或预览
    # 若未设置 key，自动 dry-run 预览
    if not os.environ.get("ZECTRIX_API_KEY") or not os.environ.get("ZECTRIX_MAC"):
        if not args.dry_run:
            print("⚠️ 未检测到密钥，自动切换为 dry_run 预览模式")
            args.dry_run=True
    push_to_zectrix(files, dry_run=args.dry_run)

    if args.dry_run:
        print("\n💡 预览完成：请查看 page_1.png / page_2.png")
        print("确认排版无误后，设置密钥并去掉 --dry-run 即可真正推送到墨水屏：")
        print("  export ZECTRIX_API_KEY=xxx")
        print("  export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF")
        print("  python manual_eastmoney_push.py")

if __name__=="__main__":
    main()
