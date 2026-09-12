#!/usr/bin/env python3
"""
财新社新闻 → Zectrix 墨水屏 手动推送脚本
用法：
  预览：
    python manual_caixin_push.py --dry-run
    python manual_caixin_push.py --pages 1,2 --dry-run
    python manual_caixin_push.py --pages 1,2,3,4 --dry-run

  推送：
    export ZECTRIX_API_KEY="你的Key"
    export ZECTRIX_MAC="AA:BB:CC:DD:EE:FF"
    python manual_caixin_push.py
    python manual_caixin_push.py --pages 1,2,3,4

  自定义标题：
    python manual_caixin_push.py --titles "标题1|标题2|标题3" --dry-run
"""
import os, sys, argparse, requests, re, json, time, random
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "font.ttf"
try:
    font_title = ImageFont.truetype(FONT_PATH, 24)
    font_item = ImageFont.truetype(FONT_PATH, 18)
    font_small = ImageFont.truetype(FONT_PATH, 14)
except Exception as e:
    print(f"❌ 找不到 {FONT_PATH}: {e}")
    sys.exit(1)

HEADERS_CAIXIN = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.caixin.com/',
    'Accept': 'application/json, text/plain, */*',
}

def get_caixin_news(page_size=20):
    titles=[]
    timestamp=str(int(time.time()*1000))
    api_candidates=[
        f"https://gateway.caixin.com/api/dataplatform/scroll/index?count={page_size}&_={timestamp}",
        f"https://mapiv5.caixin.com/m/api/getWapIndexListByPage?page=1&count={page_size}&callback=&_={timestamp}",
    ]
    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:80]}...")
            res=requests.get(url, headers=HEADERS_CAIXIN, timeout=10)
            text=res.text.strip()
            if text.startswith("jQuery") or "callback" in text[:30]:
                s=text.find("{"); e=text.rfind("}")
                if s!=-1 and e!=-1: text=text[s:e+1]
            data=json.loads(text)
            if "data" in data:
                d=data["data"]
                lst=None
                if isinstance(d, dict):
                    if "articleList" in d: lst=d["articleList"]
                    elif "list" in d: lst=d["list"]
                    elif "datas" in d: lst=d["datas"]
                if lst:
                    for item in lst:
                        if isinstance(item, dict):
                            t=item.get("title") or item.get("desc") or ""
                            if t: titles.append(re.sub(r"\s+"," ",t.strip()))
                    if len(titles)>=5:
                        print(f"  ✅ API 成功 {len(titles)} 条")
                        break
        except Exception as e:
            print(f"  ⚠️ API 失败: {e}")
            continue

    if len(titles)<5:
        print("  ⚠️ 使用内置示例兜底")
        sample=[
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
        ]
        titles=sample[:page_size]
    seen=set(); uniq=[]
    for t in titles:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return uniq[:page_size]

def wrap_text_by_pixels(draw, text, font, max_width):
    lines=[]; cur=""
    for ch in text:
        test=cur+ch
        try: w=draw.textlength(test, font=font)
        except: w=draw.textbbox((0,0), test, font=font)[2]
        if w<=max_width: cur=test
        else: lines.append(cur); cur=ch
    if cur: lines.append(cur)
    return lines

def draw_pages(titles, enabled_pages="1,2", header="章鱼 AI·全景分析"):
    def draw_list(draw, page_title, items, start_idx):
        draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
        draw.text((20, 15), page_title, font=font_title, fill=255)
        y, last_idx = 55, start_idx
        item_gap=12; line_height=23
        for i in range(start_idx, len(items)):
            lines=wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
            required_h=len(lines)*line_height
            if y+required_h>295: break
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
    enabled=set(enabled_pages.split(","))
    next_s=0
    if "1" in enabled:
        print(f"生成 Page 1: 顶栏「{header}」...")
        img1=Image.new('1',(400,300),color=255)
        next_s=draw_list(ImageDraw.Draw(img1), header, titles, 0)
        img1.save("page_1.png"); files.append("page_1.png")
        print("💾 已保存 page_1.png")
    if "2" in enabled:
        print(f"生成 Page 2: 顶栏「{header}」...")
        img2=Image.new('1',(400,300),color=255)
        start_idx=next_s if "1" in enabled else 7
        draw_list(ImageDraw.Draw(img2), header, titles, start_idx)
        img2.save("page_2.png"); files.append("page_2.png")
        print("💾 已保存 page_2.png")
    if "3" in enabled:
        print(f"生成 Page 3: 顶栏「{header}」...")
        img3=Image.new('1',(400,300),color=255)
        # 3,4页可复用后20条
        offset = 20 if len(titles)>=40 else next_s
        draw_list(ImageDraw.Draw(img3), header, titles, offset)
        img3.save("page_3.png"); files.append("page_3.png")
        print("💾 已保存 page_3.png")
    if "4" in enabled:
        print(f"生成 Page 4: 顶栏「{header}」...")
        img4=Image.new('1',(400,300),color=255)
        start_idx = 30 if len(titles)>=40 else (next_s+7)
        draw_list(ImageDraw.Draw(img4), header, titles, start_idx if len(titles)>=40 else 14)
        img4.save("page_4.png"); files.append("page_4.png")
        print("💾 已保存 page_4.png")
    return files

def push_to_zectrix(files, dry_run=False):
    api_key=os.environ.get("ZECTRIX_API_KEY")
    mac=os.environ.get("ZECTRIX_MAC")
    if dry_run:
        print("🔍 dry_run 模式：仅本地预览，不推送")
        return True
    if not api_key or not mac:
        print("❌ 未配置 ZECTRIX_API_KEY / ZECTRIX_MAC")
        return False
    push_url=f"https://cloud.zectrix.com/open/v1/devices/{mac}/display/image"
    headers={"X-API-Key": api_key}
    ok=True
    for filepath in files:
        page_id=filepath.split("_")[1].split(".")[0]
        try:
            with open(filepath, "rb") as f:
                files_payload={"images": (filepath, f, "image/png")}
                data={"dither":"true", "pageId": str(page_id)}
                print(f"🚀 推送 {filepath} -> page {page_id} ...")
                res=requests.post(push_url, headers=headers, files=files_payload, data=data, timeout=15)
                print(f"  响应: {res.status_code} {res.text[:200]}")
                if res.status_code not in (200,201,204): ok=False
        except Exception as e:
            print(f"❌ 推送 {filepath} 失败: {e}")
            ok=False
    return ok

def main():
    parser=argparse.ArgumentParser(description="财新社 → Zectrix 手动推送")
    parser.add_argument("--dry-run", action="store_true", help="仅生成本地预览，不推送")
    parser.add_argument("--pages", type=str, default="1,2", help="推送页面，例如 1,2 或 1,2,3,4")
    parser.add_argument("--titles", type=str, default=None, help="自定义标题，用|分隔")
    parser.add_argument("--title", type=str, default="章鱼 AI·全景分析", help="顶栏文案（各页统一），默认 \"章鱼 AI·全景分析\"")
    args=parser.parse_args()

    if args.titles:
        titles=[t.strip() for t in args.titles.split("|") if t.strip()]
        print(f"使用自定义标题 {len(titles)} 条")
    else:
        need = 40 if "3" in args.pages or "4" in args.pages else 20
        titles=get_caixin_news(page_size=need)
        print(f"共获取 {len(titles)} 条财新新闻")

    files=draw_pages(titles, enabled_pages=args.pages, header=args.title)

    if not os.environ.get("ZECTRIX_API_KEY") or not os.environ.get("ZECTRIX_MAC"):
        if not args.dry_run:
            print("⚠️ 未检测到密钥，自动切换为 dry_run 预览模式")
            args.dry_run=True

    push_to_zectrix(files, dry_run=args.dry_run)

if __name__=="__main__":
    main()
