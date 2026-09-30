#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
octopus_board —— 把「仓库 02 · 章鱼 AI · 打氧日报」的当日推送页，渲染成 4 页 400×300 墨水屏。

4 页分工（顶栏四页统一「章鱼 AI·全景分析」，正文互不重复）：
    Page 1  【闪电飞鱼】短线速查卡      —— 30 秒读完：定调 / 明日剧本 / 七日风 / 今明必看 / 水位 / 数据底
    Page 2  【回游金枪鱼】今日预判      —— 方向与关键数字
    Page 3  【爪爪八爪鱼】AI 全篇速览（上）—— 市场与资金 / 量化与策略
    Page 4  【爪爪八爪鱼】AI 全篇速览（下）—— 政策与日程 / 资讯与情绪

排版约定（400×300、1-bit，放不下是常态，所以绝不假装放得下）：
    · 放不下的条目按顺序截断并加「…」，整条挤掉的如实写「▼ 另有 N 项 · 全文见微信打氧日报」，
      这行指针永远保留位置，不会被正文挤没；
    · 顶栏下方固定一行栏目原名（对着微信日报能核对是哪一栏），底部固定一行口径
      （日报日期 / 更新时间 / 当天源 / 数字与正文同源）；
    · 数字全部来自仓库 02 的当次 HTML，不另算一套、不用旧数据充数。
"""

import os
import sys

from PIL import ImageDraw

import octopus_report as R
from board_core import (
    CANVAS_W,
    ENABLED_PAGES,
    BOARD_TITLE,
    font_bar,
    font_bar_num,
    font_foot,
    font_head,
    font_head_s,
    font_label,
    font_label_s,
    font_sub,
    font_tiny,
    font_value,
    font_value_s,
    new_canvas,
    push_image,
    wrap_text_by_pixels,
)

# ---------------------------------------------------------------------
# 版面参数（都在这里，方便按屏调）
# ---------------------------------------------------------------------
MARGIN_X = 9
CONTENT_W = CANVAS_W - MARGIN_X * 2      # 382
BODY_TOP = 60
BODY_BOTTOM = 272                          # 正文可用区 60~272
FOOTER_Y = 277
NOTE_LINE_H = 15                           # 底部指针行的高度，永远给它留位
GAP = 4                                    # 条目之间的留白
TRUNCATE_MARK = "…"

# 每页底部固定的口径行
FOOTER_NOTE = "数字与正文同源 · 非投资建议"

# 两档密度：normal 适合条目少、每条要读清的 P1/P2；compact 适合条目多的 P3/P4
DENSITY = {
    "normal": {
        "label": font_label, "value": font_value, "head": font_head,
        "line": 17, "line_head": 19, "gap": 3, "cap": 0, "keep_going": False,
    },
    "compact": {
        "label": font_label_s, "value": font_value_s, "head": font_head_s,
        "line": 15, "line_head": 16, "gap": 3, "cap": 2, "keep_going": True,
    },
}


# ---------------------------------------------------------------------
# 富文本折行：同一条目里标签用大一号字、内容用小一号字，挤在同一行里排
# ---------------------------------------------------------------------
class _Seg:
    __slots__ = ("text", "font")

    def __init__(self, text, font):
        self.text = text
        self.font = font


def _rich_lines(draw, segs, max_width):
    """把 [(文字, 字体)…] 折成 [[(文字, 字体)…], …] 行；逐字断行，中文正好。"""
    lines, cur, cur_w = [], [], 0
    for seg in segs:
        for ch in seg.text:
            if ch == "\n":
                lines.append(cur)
                cur, cur_w = [], 0
                continue
            w = draw.textlength(ch, font=seg.font)
            if cur_w + w > max_width and cur:
                lines.append(cur)
                cur, cur_w = [], 0
            cur.append((ch, seg.font))
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


def _line_width(draw, line):
    return sum(draw.textlength(t, font=f) for t, f in line)


def _shrink_to_width(draw, line, max_width):
    """把一行裁到指定像素宽。"""
    out, w = [], 0
    for t, f in line:
        tw = draw.textlength(t, font=f)
        if w + tw > max_width:
            break
        out.append((t, f))
        w += tw
    return out


def _clip_lines_to_width(draw, lines, max_width, reserve=0):
    """整体裁到 max_width（末行额外给 reserve 像素留给省略号）。"""
    out = []
    for i, line in enumerate(lines):
        limit = max_width - (reserve if i == len(lines) - 1 else 0)
        out.append(line if _line_width(draw, line) <= limit else _shrink_to_width(draw, line, limit))
    return out


def _trim_ellipsis(draw, line, max_width):
    """给一行末尾挂上「…」，保证不超宽。"""
    if not line:
        return line
    font = line[-1][1]
    mw = draw.textlength(TRUNCATE_MARK, font=font)
    body = line if _line_width(draw, line) + mw <= max_width else _shrink_to_width(draw, line, max_width - mw)
    return body + [(TRUNCATE_MARK, font)]


# ---------------------------------------------------------------------
# 顶栏 / 副标题 / 底部口径
# ---------------------------------------------------------------------
def _fit_text(draw, text, font, max_width):
    """超宽就按像素截断并加省略号（墨水屏宁可少字也不要溢出圆角条）。"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    mark = draw.textlength(TRUNCATE_MARK, font=font)
    out = ""
    for ch in text:
        if draw.textlength(out + ch, font=font) > max_width - mark:
            break
        out += ch
    return out + TRUNCATE_MARK


def draw_header(draw, title, page_id, total=4):
    draw.rounded_rectangle([(MARGIN_X, 6), (CANVAS_W - MARGIN_X - 1, 40)], radius=7, fill=0)
    num = f"{page_id}/{total}"
    num_w = draw.textlength(num, font=font_bar_num)
    draw.text(
        (MARGIN_X + 8, 13),
        _fit_text(draw, title, font_bar, CONTENT_W - 24 - num_w),
        font=font_bar, fill=255,
    )
    draw.text((CANVAS_W - MARGIN_X - 8 - num_w, 16), num, font=font_bar_num, fill=255)


def draw_subtitle(draw, section_title, subtitle, page_id):
    """顶栏下面一行：左边日报栏目原名（对着微信日报能核对），右边出自哪一页。"""
    tail = f"仓库 02 · {page_id} 页"
    tail_w = draw.textlength(tail, font=font_tiny)
    draw.text((CANVAS_W - MARGIN_X - tail_w, 46), tail, font=font_tiny, fill=0)
    left = " · ".join(x for x in (section_title, subtitle) if x)
    draw.text((MARGIN_X, 45), _fit_text(draw, left, font_sub, CONTENT_W - tail_w - 8), font=font_sub, fill=0)


def draw_footer(draw, meta, note=FOOTER_NOTE):
    draw.line([(MARGIN_X, FOOTER_Y - 3), (CANVAS_W - MARGIN_X, FOOTER_Y - 3)], fill=0, width=1)
    right = _fit_text(draw, note, font_foot, CONTENT_W // 2)
    rw = draw.textlength(right, font=font_foot)
    draw.text((CANVAS_W - MARGIN_X - rw, FOOTER_Y), right, font=font_foot, fill=0)
    draw.text(
        (MARGIN_X, FOOTER_Y),
        _fit_text(draw, meta, font_foot, CONTENT_W - rw - 10),
        font=font_foot, fill=0,
    )


# ---------------------------------------------------------------------
# 正文排版
# ---------------------------------------------------------------------
def _item_segments(item, style):
    """条目 → 富文本片段。标签前加一个实心方块做视觉锚点。"""
    label = R.normalize_label(item.label)
    if item.kind == "group":
        return [_Seg(label, style["label"])]
    if item.kind == "head" or not label:
        return [_Seg(item.value, style["head"])]
    return [
        _Seg("■ ", style["label"]),
        _Seg(label, style["label"]),
        _Seg("  ", style["value"]),
        _Seg(item.value, style["value"]),
    ]


def _paint(draw, lines, y, line_h):
    for line in lines:
        x = MARGIN_X
        for text, font in line:
            draw.text((x, y), text, font=font, fill=0)
            x += draw.textlength(text, font=font)
        y += line_h


def _note_height():
    """指针行预留高度（2 行 10px 字 + 余量）。"""
    return NOTE_LINE_H * 2


def layout_items(draw, items, page, body_bottom):
    """
    只算不画：把条目按顺序铺进正文区，产出绘制指令。

    两种版面策略（都在 DENSITY 里配置）：
      · normal（Page 1/2）：条目少、要读清 → 宁可整条舍弃，也不把中间的条目截半句；
      · compact（Page 3/4）：每条本来就是一行摘要 → 每条最多 cap 行，保证覆盖面。

    返回 (ops, drawn, dropped, truncated)
    """
    style = DENSITY.get(page.get("density", "normal"), DENSITY["normal"])
    line_h, line_h_head = style["line"], style["line_head"]
    gap, cap, keep_going = style["gap"], style["cap"], style.get("keep_going", False)

    ops, y, drawn, dropped, truncated = [], BODY_TOP, 0, 0, False
    remaining = list(items)

    while remaining:
        item = remaining.pop(0)
        segs = _item_segments(item, style)
        is_group_head = item.kind == "group" and not item.value

        if is_group_head:
            # 分节标题只在「后面还放得下一整条内容」时才画，
            # 否则会出现一个光秃秃的标题悬在页脚上方
            if y + line_h + 4 + line_h_head > body_bottom:
                dropped += 1 + len(remaining)
                break
            ops.append(("group", segs[0].text, y, style["label"]))
            # 分隔线要落在文字下方，不能压到字上
            ops.append(("rule", y + line_h + 1))
            y += line_h + 4
            drawn += 1
            continue

        lines = _rich_lines(draw, segs, CONTENT_W)
        lh = line_h_head if item.kind == "head" else line_h

        # 紧凑档：每条先按 cap 封顶，保证后面的条目也有机会上屏
        if cap and len(lines) > cap:
            lines = _clip_lines_to_width(draw, lines[:cap], CONTENT_W, reserve=12)
            lines[-1] = _trim_ellipsis(draw, lines[-1], CONTENT_W)
            truncated = True

        need = len(lines) * lh + gap
        if need <= body_bottom - y:
            ops.append(("paint", lines, y, lh))
            y += need
            drawn += 1
            continue

        # 放不下：够「截断后至少两行」就截断（compact 继续铺，normal 到此为止）
        max_lines = max(0, (body_bottom - y - gap) // lh)
        if max_lines >= 2 and len(lines) > max_lines:
            shown = _clip_lines_to_width(draw, lines[:max_lines], CONTENT_W, reserve=12)
            shown[-1] = _trim_ellipsis(draw, shown[-1], CONTENT_W)
            ops.append(("paint", shown, y, lh))
            truncated = True
            y += max_lines * lh + gap
            drawn += 1
            if not keep_going:
                dropped += 1 + len(remaining)
                break
            continue

        # 空间不够放两行 → 整条舍弃，如实计数，后面不再硬塞
        dropped += 1 + len(remaining)
        break

    return ops, drawn, dropped, truncated


def overflow_note(draw, dropped, truncated):
    """放不下的如实说明：截断了就说截断，整条没上屏就给条数 + 全文在哪看。"""
    parts = []
    if truncated:
        parts.append("…部分条目已按版面截断")
    parts.append(f"▼ 另有 {dropped} 项 · 全文见微信打氧日报")
    return wrap_text_by_pixels(draw, " ".join(parts), font_foot, CONTENT_W)


def draw_items(draw, items, page, body_bottom=None):
    """
    反复排版直到「让出的指针行高度」不再变化（最多 4 轮，正常 1~2 轮就收敛）：

      第 1 遍按满版高度算；只有在「有内容没上屏 / 被截断」时，
      才把底部让出指针行的高度再算一遍 —— 保证那行说明永远画得出来、
      不会压在正文上，同时也不会白白浪费正文空间。
    """
    body_bottom = BODY_BOTTOM if body_bottom is None else body_bottom
    reserve = 0
    ops = drawn = dropped = truncated = None
    note_lines = []
    for _ in range(4):
        ops, drawn, dropped, truncated = layout_items(draw, items, page, body_bottom - reserve)
        if not dropped and not truncated:
            note_lines = []
            break
        note_lines = overflow_note(draw, dropped, truncated)
        need = len(note_lines) * NOTE_LINE_H
        if need == reserve:
            break
        reserve = need

    _run_ops(draw, ops)
    if note_lines:
        y = body_bottom - len(note_lines) * NOTE_LINE_H
        for line in note_lines:
            ops_y = y
            _paint(draw, [_plain(line, font_foot)], ops_y, NOTE_LINE_H)
            y += NOTE_LINE_H
    return drawn, dropped, truncated


def _plain(text, font):
    return [(ch, font) for ch in text]


def _run_ops(draw, ops):
    for op in ops:
        if op[0] == "paint":
            _paint(draw, op[1], op[2], op[3])
        elif op[0] == "group":
            draw.text(
                (MARGIN_X, op[2]),
                _fit_text(draw, op[1], op[3], CONTENT_W),
                font=op[3], fill=0,
            )
        elif op[0] == "rule":
            draw.line([(MARGIN_X, op[1]), (CANVAS_W - MARGIN_X, op[1])], fill=0, width=1)


# ---------------------------------------------------------------------
# 整页渲染
# ---------------------------------------------------------------------
def render_page(page_id, page, report, total_pages=4, title=None):
    """把一页内容画成 400×300 的 1-bit 图。"""
    img = new_canvas()
    d = ImageDraw.Draw(img)
    draw_header(d, title or BOARD_TITLE, page_id, total_pages)
    draw_subtitle(d, page.get("section_title", ""), page.get("subtitle", ""), page_id)
    draw_items(d, page["items"], page)
    draw_footer(d, report.summary() or "打氧日报", FOOTER_NOTE)
    return img


def push_octopus_board(report, pages, dry_run=False, title=None, total_pages=4):
    """
    渲染并推送 4 页。返回 {页码: True 成功 / False 失败 / None 该页无内容未推}。
    """
    results = {}
    for pid in [p for p in ("1", "2", "3", "4") if p in ENABLED_PAGES]:
        page = pages.get(int(pid)) or pages.get(pid)
        if not page or not page.get("items"):
            print(f"⏩ Page {pid} 无内容，跳过（不覆盖墨水屏原有内容）")
            results[pid] = None
            continue
        img = render_page(pid, page, report, total_pages=total_pages, title=title)
        print(
            f"🖼 Page {pid}：{page.get('section_title','')} · {page.get('subtitle','')}"
            f" · {len(page['items'])} 条 · 舍弃 {page.get('dropped', 0)} 条"
            f"{'（有截断）' if page.get('truncated') else ''}"
        )
        results[pid] = push_image(img, pid, dry_run=dry_run)
    return results


def mark_dropped(pages):
    """渲染前先跑一遍排版预算，把「这页会丢掉几条」记进 pages，便于日志如实披露。"""
    img = new_canvas()
    d = ImageDraw.Draw(img)
    for pid, page in pages.items():
        _, dropped, truncated = draw_items(d, page["items"], page)
        page["dropped"] = dropped
        page["truncated"] = truncated


# ---------------------------------------------------------------------
# 推送状态：内容没变就别反复刷墨水屏
# ---------------------------------------------------------------------
# 日报是「一天一份」，工作流是「30 分钟一次」。不记状态的话一天会往屏上
# 推 48 张一模一样的图 —— 墨水屏每推一次就闪一次，纯属浪费。
# 状态文件放在 Actions cache 里；缓存冷了就退化成「照推」，绝不会漏推。
STATE_FILE_ENV = "OCTOPUS_STATE_FILE"


def _state_path():
    return os.environ.get(STATE_FILE_ENV, "").strip()


def read_state(path=None):
    path = path or _state_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 推送状态文件读不出来（{exc}），按「没推过」处理")
        return {}


def write_state(fingerprint, path=None, extra=None):
    path = path or _state_path()
    if not path:
        return
    import json
    payload = {
        "fingerprint": fingerprint,
        "pushed_at": R.beijing_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    payload.update(extra or {})
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        # 状态写不进去只是「下次多推一次」，绝不能因此让整轮失败
        print(f"⚠️ 推送状态写不进去（{exc}），下轮会重复推送一次（不影响本次推送结果）")


def already_pushed(fingerprint, path=None, force=False):
    """指纹一致 = 墨水屏上已经是这份内容了。"""
    if force:
        print("🔁 --force：忽略「内容未变」判断，照推")
        return False
    state = read_state(path)
    if not state:
        return False
    if state.get("fingerprint") == fingerprint:
        print(f"⏭ 内容指纹与上次推送一致（{fingerprint}），墨水屏已是这份内容，跳过推送")
        print(f"   （上次推送：{state.get('pushed_at', '未知')}；要强制刷新就加 --force）")
        return True
    return False


# ---------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------
def run(html_path=None, dry_run=False, title=None, max_age_hours=None, require_fresh=True):
    """
    完整跑一遍：拉取仓库 02 日报 → 解析 → 新鲜度校验 → 排 4 页。

    失败一律抛 R.ReportUnavailable（调用方 / Actions 据此让整轮显红并保留旧画面）。
    返回 (report, pages, fingerprint)
    """
    if html_path:
        with open(html_path, encoding="utf-8") as fh:
            html_text = fh.read()
        print(f"📂 使用本地日报文件：{html_path}")
    else:
        html_text = R.fetch_report()

    report = R.parse_report(html_text)
    print(f"📄 {report.title} | {report.summary()} | 主题 {report.theme}")
    print(f"🗂 解析到 {len(report.sections)} 个栏目 · HTML 指纹 {report.raw_sha}")

    ok, msg = R.check_freshness(report, max_age_hours=max_age_hours)
    print(f"🕒 {'✅' if ok else '❌'} {msg}")
    if not ok and require_fresh:
        raise R.ReportUnavailable(msg)

    pages = R.build_pages(report)
    mark_dropped(pages)
    for pid in sorted(pages):
        page = pages[pid]
        print(
            f"🗂 Page {pid} · {page.get('section_title','')} · {len(page['items'])} 条"
            f" · 排版舍弃 {page['dropped']} 条{'（有截断）' if page['truncated'] else ''}"
        )
    return report, pages, R.report_fingerprint(report, pages)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="把仓库 02 的打氧日报推到墨水屏 4 页")
    ap.add_argument("--from-file", dest="html_path", default=None,
                    help="用本地日报 HTML 预览（离线自测，不联网）")
    ap.add_argument("--title", dest="title", default=None, help="覆盖顶栏文案")
    ap.add_argument("--max-age-hours", dest="max_age_hours", type=float, default=None,
                    help="日报新鲜度上限（小时）")
    ap.add_argument("--allow-stale", action="store_true",
                    help="即使超过新鲜度上限也照推（默认整轮跳过并报错）")
    ap.add_argument("--dry-run", action="store_true", help="只生成本地预览图，不推 Zectrix")
    args = ap.parse_args()

    dry = args.dry_run or not os.environ.get("ZECTRIX_API_KEY")
    try:
        rep, pgs, fp = run(
            html_path=args.html_path,
            dry_run=dry,
            title=args.title,
            max_age_hours=args.max_age_hours,
            require_fresh=not args.allow_stale,
        )
    except R.ReportUnavailable as exc:
        print(f"❌ {exc}")
        print("   → 本轮不推送，墨水屏保留上一次的内容（不覆盖成空白或残缺内容）")
        sys.exit(1)

    results = push_octopus_board(rep, pgs, dry_run=dry, title=args.title)
    print(f"🔑 内容指纹：{fp}")
    failed = [pid for pid, ok in results.items() if ok is False]
    if failed:
        print(f"❌ 有页面推送失败：{failed}")
        sys.exit(1)
    print("🎉 打氧日报 4 页推送完成")
