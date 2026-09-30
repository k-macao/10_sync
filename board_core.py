"""
board_core —— 墨水屏看板的公共底层（两块看板共用）

抽出来是为了让「新闻看板 main.py」和「打氧日报看板 octopus_board.py」共用同一套
字体、顶栏文案、启用页和 Zectrix 推送通道，避免各写一份推送逻辑走偏。

对外提供：
    CANVAS / FONT_*            画布尺寸与各号字体
    push_image()               推一张 1-bit PNG 到 Zectrix 指定页
    wrap_text_by_pixels()      按像素宽度折行
    make_page_header()         顶栏文案（五页统一）
    set_enabled_pages() / set_board_title()   供 main.py 的命令行覆盖调用
"""

import os
import requests
from PIL import Image, ImageFont

# =====================================================================
# 🔒 核心密钥区（⚠️ 绝对不要写死在代码里，请在 GitHub Secrets 里配置）
# =====================================================================
API_KEY = os.environ.get("ZECTRIX_API_KEY")
MAC_ADDRESS = os.environ.get("ZECTRIX_MAC")
# AMAP_KEY 已废弃（天气页已去除），保留兼容读取但不再使用
AMAP_KEY = os.environ.get("AMAP_WEATHER_KEY")

# =====================================================================
# 🌟 顶栏 / 页面配置
# =====================================================================
BOARD_TITLE = "章鱼 AI·全景分析"
# 顶栏是否额外拼接来源标签 / 分页序号（默认全关 = 五页统一只显示 BOARD_TITLE）
HEADER_SHOW_SOURCE = False
HEADER_SHOW_PART = False
HEADER_PREFIX = ""

# 控制推送哪几页（墨水屏共 5 页）
ENABLED_PAGES = "1,2,3,4,5"

# =====================================================================
# 🎨 画布与字体
# =====================================================================
CANVAS_W, CANVAS_H = 400, 300
FONT_PATH = os.environ.get("BOARD_FONT", "font.ttf")

try:
    font_huge = ImageFont.truetype(FONT_PATH, 65)
    font_title = ImageFont.truetype(FONT_PATH, 24)
    font_item = ImageFont.truetype(FONT_PATH, 18)
    font_small = ImageFont.truetype(FONT_PATH, 14)
    font_tiny = ImageFont.truetype(FONT_PATH, 11)
    font_48 = ImageFont.truetype(FONT_PATH, 48)
    font_36 = ImageFont.truetype(FONT_PATH, 36)
    # 打氧日报看板专用（1-bit 小屏，标签比正文大一号做视觉分层）
    font_bar = ImageFont.truetype(FONT_PATH, 19)
    font_bar_num = ImageFont.truetype(FONT_PATH, 13)
    font_sub = ImageFont.truetype(FONT_PATH, 12)
    font_label = ImageFont.truetype(FONT_PATH, 15)
    font_value = ImageFont.truetype(FONT_PATH, 14)
    font_head = ImageFont.truetype(FONT_PATH, 16)
    font_foot = ImageFont.truetype(FONT_PATH, 10)
    # 紧凑档：第 1/3/4 页（结论面、AI 全篇速览）条目多、每条已经是「一行摘要」，用小一号字换行数
    font_label_s = ImageFont.truetype(FONT_PATH, 13)
    font_value_s = ImageFont.truetype(FONT_PATH, 12)
    font_head_s = ImageFont.truetype(FONT_PATH, 13)
except Exception:  # noqa: BLE001
    print(f"❌ 错误: 找不到字体 {FONT_PATH}（请把 .ttf 字体重命名为 font.ttf 放在根目录）")
    raise SystemExit(1)


def set_enabled_pages(pages):
    """供 main.py 的 --pages 命令行覆盖调用。"""
    global ENABLED_PAGES
    ENABLED_PAGES = pages


def set_board_title(title):
    """供 main.py 的 --title 命令行覆盖调用。"""
    global BOARD_TITLE
    BOARD_TITLE = title


def enabled_page_ids(*candidates):
    """把候选页码过滤成「本次真正要推」的页，顺序保持传入顺序。"""
    out = []
    for pid in candidates:
        pid = str(pid)
        if pid in ENABLED_PAGES and pid not in out:
            out.append(pid)
    return out


# =====================================================================
# 📤 推送
# =====================================================================
def push_image(img, page_id, dry_run=False):
    """把 PIL 图像推到 Zectrix 云指定页；dry_run 时只落地本地 PNG。"""
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
    except Exception as e:  # noqa: BLE001
        print(f"❌ Page {page_id} 推送失败: {e}")
        return False


# =====================================================================
# ✏️ 画布小工具
# =====================================================================
def new_canvas():
    """1-bit 白底画布（墨水屏只需黑白两色）。"""
    return Image.new("1", (CANVAS_W, CANVAS_H), color=255)


def wrap_text_by_pixels(draw, text, font, max_width):
    """按像素宽度折行（中文逐字断行，英文/数字不拆词效果一般但够用）。"""
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


def make_page_header(label, part):
    """
    顶栏文案：默认五页统一，只显示 BOARD_TITLE（「章鱼 AI·全景分析」）。
    如需恢复来源标签 / 分页序号，把 HEADER_SHOW_SOURCE / HEADER_SHOW_PART 改成 True。
    过长时调用方会按像素截断。
    """
    main = (BOARD_TITLE or "").strip() or "章鱼 AI·全景分析"
    text = f"{HEADER_PREFIX}{main}"
    label = (label or "").strip()
    if HEADER_SHOW_SOURCE and label and label != main:
        text = f"{text}·{label}"
    if HEADER_SHOW_PART and part:
        text = f"{text} ({part})"
    return text
