#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_board —— 五页全球行情数据面板渲染与 Zectrix 墨水屏推送（A / 港 / 韩 / 日 / 美）。

五页分工（400×300 纯黑白 1-bit，严格按市场顺序）：
    Page 1  [A]  A股行情   上证指数 / 深证成指 / 创业板指 / 沪深300 / 科创50（人民币）
    Page 2  [港] 港股行情  恒生指数 / 恒生科技 / 恒生国企（港元）
    Page 3  [韩] 韩股行情  KOSPI / KOSDAQ（韩元）
    Page 4  [日] 日股行情  日经225 / 东证指数（日元）
    Page 5  [美] 美股行情  道琼斯 / 纳斯达克 / 标普500（美元）

每条指数同时展示三层「变化」：
    1. 日内涨跌：涨跌额（▲+50.46 / ▼-370.95）+ 涨跌幅胶囊（黑底白字=涨，白底黑框=跌）
    2. 分时变化：5分 +14.80 / -18.50（较上一轮抓取的点位变化，默认 5 分钟一轮；首轮无前值显示 --）
    3. 日内区间：今开 / 最高 / 最低 / 振幅 + 低—高位置条（页脚附总成交及较上轮增量）

按页指纹去重推送：
    · 每页独立计算 1-bit 位图 SHA-256，与状态文件比对；休市或行情未变的页不推屏（零多余闪屏）
    · 某页有效行情为 0 时跳过该页推送，保留墨水屏上一版内容（绝不推空屏覆盖）
    · 单页推送失败时不更新该页指纹，下一轮自动重试

退出码：
    0  成功（已推送有变化的页，或全部无变化）
    1  配置错误（缺 ZECTRIX_API_KEY / ZECTRIX_MAC）或排版预检失败
    2  Zectrix 推送出现失败（未成功页指纹未写入，下一轮会重试）
    3  全部启用页均无有效行情（保留墨水屏上一版内容）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

import board_core
from board_core import (
    CANVAS_H,
    CANVAS_W,
    fit_text,
    get_font,
    new_canvas,
    push_image,
    set_board_title,
    set_enabled_pages,
)
import market_quotes as mq
from market_quotes import (
    EMPTY_TEXT,
    MARKET_ORDER,
    MarketPageData,
    PanelData,
    PanelRow,
    Quote,
    TZ_CN,
    apply_delta_state,
    arrow_for,
    collect_panel,
    demo_panel,
    fmt_amount_delta,
    fmt_amp,
    fmt_as_of,
    fmt_change,
    fmt_cn_amount,
    fmt_pct,
    fmt_price,
    fmt_signed,
)

DEFAULT_STATE_FILE = os.environ.get(
    "MARKET_STATE_FILE",
    os.path.join(".octopus", "market_state.json"),
)
DEFAULT_BOARD_TITLE = "章鱼 AI·全景分析"

# 更新节奏：默认 5 分钟一轮（抓取 → 渲染 → 推屏）。分时变化与页脚标签都跟着这个间隔走，
# 由 market_loop.py 通过 --interval 传给每轮渲染，所以改成 1 分钟/15 分钟也不会出现「1分」错标。
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_DELTA_TAG = "5分"
DEFAULT_UPDATE_TAG = "5分钟更新"


def interval_minutes(interval_seconds: Optional[float] = None) -> int:
    """把间隔秒数折算成分钟数（最小 1），用于「N分」与「N分钟更新」文案。"""
    secs = DEFAULT_INTERVAL_SECONDS if interval_seconds is None else float(interval_seconds)
    return max(1, int(round(secs / 60.0)))


def delta_label(interval_seconds: Optional[float] = None) -> str:
    """分时变化前缀，如「5分」/「1分」/「15分」。"""
    return f"{interval_minutes(interval_seconds)}分"


def update_label(interval_seconds: Optional[float] = None) -> str:
    """页脚更新标签，如「5分钟更新」/「1分钟更新」。"""
    return f"{interval_minutes(interval_seconds)}分钟更新"

# 退出码约定（供 market_loop.py 识别）
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PUSH_FAILED = 2
EXIT_NO_LIVE_DATA = 3

# 版面坐标（400×300）
LEFT = 8
RIGHT = CANVAS_W - 8          # 392
INNER_W = RIGHT - LEFT        # 384
HEADER_Y0, HEADER_Y1 = 5, 35
BODY_Y0, BODY_Y1 = 39, 273
FOOTER_RULE_Y = 275
FOOTER_TEXT_Y = 279


class LayoutError(RuntimeError):
    """400×300 排版预检未通过（越界 / 重叠 / 非纯黑白）。"""


@dataclass(frozen=True)
class TextBox:
    label: str
    box: Tuple[int, int, int, int]  # (x0, y0, x1, y1)


class Canvas:
    """带文字边界记录与预检的 400×300 1-bit 画布。"""

    def __init__(self) -> None:
        self.img: Image.Image = new_canvas()
        self.draw: ImageDraw.ImageDraw = ImageDraw.Draw(self.img)
        self.boxes: List[TextBox] = []

    def width(self, text: str, px: int) -> float:
        return float(self.draw.textlength(text, font=get_font(px)))

    def text(
        self,
        x: float,
        y: float,
        text: str,
        px: int,
        fill: int = 0,
        max_w: Optional[float] = None,
        align: str = "left",
        track: bool = True,
    ) -> Tuple[int, int, int, int]:
        """绘制单行文字并记录真实墨迹包围盒，供预检检查越界与重叠。"""
        font = get_font(px)
        rendered = fit_text(self.draw, text, font, max_w) if max_w is not None else text
        if not rendered:
            ix, iy = int(round(x)), int(round(y))
            return (ix, iy, ix, iy)
        w = self.draw.textlength(rendered, font=font)
        if align == "right":
            draw_x = x - w
        elif align == "center":
            draw_x = x - w / 2.0
        else:
            draw_x = x
        ix, iy = int(round(draw_x)), int(round(y))
        self.draw.text((ix, iy), rendered, font=font, fill=fill)
        bbox = self.draw.textbbox((ix, iy), rendered, font=font)
        if track and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            self.boxes.append(TextBox(rendered, bbox))
        return bbox

    def text_vcenter(
        self,
        x: float,
        cy: float,
        text: str,
        px: int,
        fill: int = 0,
        max_w: Optional[float] = None,
        align: str = "left",
        track: bool = True,
    ) -> Tuple[int, int, int, int]:
        """按文字真实墨迹中心对齐到纵坐标 cy。"""
        font = get_font(px)
        probe = fit_text(self.draw, text, font, max_w) if max_w is not None else text
        if not probe:
            ix, iy = int(round(x)), int(round(cy))
            return (ix, iy, ix, iy)
        tb = self.draw.textbbox((0, 0), probe, font=font)
        ty = cy - (tb[1] + tb[3]) / 2.0
        return self.text(x, ty, probe, px, fill=fill, align=align, track=track)

    def dotted_hline(self, x0: int, x1: int, y: int, on: int = 2, off: int = 2) -> None:
        step = on + off
        x = x0
        while x <= x1:
            end = min(x1, x + on - 1)
            self.draw.line([(x, y), (end, y)], fill=0, width=1)
            x += step

    def pct_badge(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        pct_text: str,
        direction: int,
        px: int,
    ) -> None:
        """涨跌幅胶囊：涨=黑底白字，跌=白底黑框，平/空=细点框黑字。"""
        radius = 4
        if direction > 0:
            self.draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=radius, fill=0, outline=0)
            fg = 255
        elif direction < 0:
            self.draw.rounded_rectangle(
                [(x0, y0), (x1, y1)], radius=radius, fill=255, outline=0, width=1
            )
            fg = 0
        else:
            self.draw.rounded_rectangle(
                [(x0, y0), (x1, y1)], radius=radius, fill=255, outline=0, width=1
            )
            fg = 0

        font = get_font(px)
        inner_w = (x1 - x0) - 6
        label = fit_text(self.draw, pct_text, font, inner_w)
        # 按墨迹垂直居中
        tb = self.draw.textbbox((0, 0), label, font=font)
        ink_h = tb[3] - tb[1]
        ty = y0 + ((y1 - y0) - ink_h) / 2.0 - tb[1]
        cx = (x0 + x1) / 2.0
        self.text(cx, ty, label, px, fill=fg, align="center", track=True)

    def range_bar(
        self,
        x0: int,
        x1: int,
        cy: int,
        low: Optional[float],
        high: Optional[float],
        open_p: Optional[float],
        last: Optional[float],
    ) -> None:
        """1-bit 日内高低区间条：端线=低/高，短竖=今开，粗段=开→现，实心块=最新。"""
        if x1 - x0 < 24:
            return
        # 主轴 + 左右端线
        self.draw.line([(x0, cy), (x1, cy)], fill=0, width=1)
        self.draw.line([(x0, cy - 3), (x0, cy + 3)], fill=0, width=1)
        self.draw.line([(x1, cy - 3), (x1, cy + 3)], fill=0, width=1)

        if low is None or high is None or last is None or high <= low:
            return

        span = high - low

        def _pos(val: float) -> int:
            ratio = max(0.0, min(1.0, (val - low) / span))
            return int(round(x0 + ratio * (x1 - x0)))

        lx = _pos(last)
        if open_p is not None and low <= open_p <= high:
            ox = _pos(open_p)
            # 今开竖标
            self.draw.line([(ox, cy - 3), (ox, cy + 3)], fill=0, width=1)
            # 开→现变化段（涨实心粗条，跌双轨空心条）
            a, b = min(ox, lx), max(ox, lx)
            if b - a >= 2:
                if last >= open_p:
                    self.draw.rectangle([(a, cy - 1), (b, cy + 1)], fill=0)
                else:
                    self.draw.rectangle([(a, cy - 2), (b, cy + 2)], fill=255, outline=0)

        # 最新点位实心锚点
        mx0 = max(x0, lx - 2)
        mx1 = min(x1, lx + 2)
        self.draw.rectangle([(mx0, cy - 3), (mx1, cy + 3)], fill=0)

    def validate(self, page_id: int) -> None:
        """保存/推屏前严格预检：尺寸、纯黑白、无越界、无文字重叠。"""
        if self.img.size != (CANVAS_W, CANVAS_H):
            raise LayoutError(f"Page {page_id} 画布尺寸异常: {self.img.size}")
        if self.img.mode != "1":
            raise LayoutError(f"Page {page_id} 非 1-bit 模式: {self.img.mode}")
        colors = {c for _n, c in (self.img.getcolors(maxcolors=4) or [])}
        if not colors.issubset({0, 255}):
            raise LayoutError(f"Page {page_id} 出现灰阶像素: {colors}")

        for tb in self.boxes:
            x0, y0, x1, y1 = tb.box
            if x0 < 2 or y0 < 2 or x1 > CANVAS_W - 2 or y1 > CANVAS_H - 1:
                raise LayoutError(f"Page {page_id} 文字越界 {tb.label!r}: {tb.box}")

        n = len(self.boxes)
        for i in range(n):
            a = self.boxes[i]
            ax0, ay0, ax1, ay1 = a.box
            for j in range(i + 1, n):
                b = self.boxes[j]
                bx0, by0, bx1, by1 = b.box
                # 允许 1px 字形边沿贴靠，>=2px 交叉判为叠字
                ox = min(ax1, bx1) - max(ax0, bx0)
                oy = min(ay1, by1) - max(ay0, by0)
                if ox >= 2 and oy >= 2:
                    raise LayoutError(
                        f"Page {page_id} 文字重叠: {a.label!r}{a.box} vs {b.label!r}{b.box}"
                    )


# =====================================================================
# 文案辅助
# =====================================================================
def _change_with_arrow(q: Quote) -> str:
    if not q.valid or q.change is None:
        return EMPTY_TEXT
    body = fmt_change(q.change)
    arr = arrow_for(q.change if round(q.change, 2) != 0 else q.pct)
    return f"{arr}{body}" if arr else body


def _delta_short(q: Quote, with_pct: bool = False, tag: str = DEFAULT_DELTA_TAG) -> str:
    if not q.valid or q.delta is None:
        return f"{tag} {EMPTY_TEXT}"
    d = fmt_signed(q.delta, decimals=2)
    if with_pct and q.delta_pct is not None:
        dp = fmt_signed(q.delta_pct, decimals=2, suffix="%")
        return f"{tag} {d} ({dp})"
    return f"{tag} {d}"


def _status_tag(q: Quote) -> str:
    if q.status == "OPEN":
        return "盘中"
    if q.status == "CLOSE":
        return "收盘"
    return ""


def _page_title(page: MarketPageData, custom_title: Optional[str]) -> str:
    if custom_title:
        base = custom_title.strip()
        if base and base != DEFAULT_BOARD_TITLE:
            return f"{base}·{page.spec.title}"
    return page.spec.title


def _footer_left(page: MarketPageData) -> str:
    spec = page.spec
    unit = spec.currency if spec.summary_kind == "turnover" else "股"
    val_txt = fmt_cn_amount(page.summary_value, unit)
    base = f"{spec.summary_label} {val_txt}"
    if page.summary_delta and page.summary_delta > 0:
        inc = fmt_amount_delta(page.summary_delta)
        if inc:
            return f"{base} ({inc})"
    return base


def _footer_right(page: MarketPageData, update_tag: str = DEFAULT_UPDATE_TAG) -> str:
    if page.spec.note:
        return f"{page.spec.note} · {update_tag}"
    return update_tag


# =====================================================================
# 顶栏 / 页脚 / 三档自适应正文版式
# =====================================================================
def _draw_header(
    cv: Canvas,
    page: MarketPageData,
    total_pages: int = 5,
    custom_title: Optional[str] = None,
    demo: bool = False,
) -> None:
    cv.draw.rounded_rectangle(
        [(LEFT, HEADER_Y0), (RIGHT - 1, HEADER_Y1)], radius=5, fill=0
    )
    mid_y = (HEADER_Y0 + HEADER_Y1) / 2.0
    # 左侧反白市场徽标：[A] [港] [韩] [日] [美]
    bx0, by0, bx1, by1 = LEFT + 5, HEADER_Y0 + 4, LEFT + 29, HEADER_Y1 - 4
    cv.draw.rounded_rectangle([(bx0, by0), (bx1, by1)], radius=3, fill=255)
    cv.text_vcenter((bx0 + bx1) / 2.0, mid_y, page.spec.code, 15, fill=0, align="center")

    # 右侧：该市场最新行情北京时间 + 页码
    stamp = fmt_as_of(page.as_of)
    if demo and stamp != EMPTY_TEXT:
        right_txt = f"演示 {stamp}  {page.spec.page_id}/{total_pages}"
    else:
        right_txt = f"{stamp}  {page.spec.page_id}/{total_pages}"
    rb = cv.text_vcenter(RIGHT - 7, mid_y, right_txt, 13, fill=255, align="right")

    # 标题
    title_x = bx1 + 7
    avail = max(40, rb[0] - title_x - 8)
    title_txt = _page_title(page, custom_title)
    cv.text_vcenter(title_x, mid_y, title_txt, 17, fill=255, max_w=avail)


def _draw_footer(cv: Canvas, page: MarketPageData, update_tag: str = DEFAULT_UPDATE_TAG) -> None:
    cv.draw.line([(LEFT, FOOTER_RULE_Y), (RIGHT - 1, FOOTER_RULE_Y)], fill=0, width=1)
    r_txt = _footer_right(page, update_tag)
    rb = cv.text(RIGHT - 2, FOOTER_TEXT_Y, r_txt, 12, fill=0, align="right")
    avail_l = max(60, rb[0] - (LEFT + 2) - 8)
    cv.text(LEFT + 2, FOOTER_TEXT_Y, _footer_left(page), 12, fill=0, max_w=avail_l)


def _draw_compact_rows(
    cv: Canvas, page: MarketPageData, tag: str = DEFAULT_DELTA_TAG
) -> None:
    """5 行高密双行卡片（Page 1 · A股：上证 / 深成 / 创业板 / 沪深300 / 科创50）。"""
    rows = page.rows
    n = len(rows)
    slot_h = (BODY_Y1 - BODY_Y0) // n  # 46px
    currency = page.spec.currency

    name_x = LEFT + 2          # 10
    price_r = 192              # 最新点位右对齐
    chg_r = 298                # 涨跌额右对齐
    badge_x0, badge_x1 = 305, RIGHT - 2  # 305..390 (85px)
    hl_col_x = 98              # 第二行高低列固定起点（五行整齐对齐）

    for idx, row in enumerate(rows):
        y0 = BODY_Y0 + idx * slot_h
        q = row.quote

        # 第 1 行：名称 | 最新点位 | ▲/▼涨跌额 | [涨跌幅%]
        cv.text(name_x, y0 + 3, row.spec.name, 14, max_w=72)
        cv.text(price_r, y0 + 2, fmt_price(q.last), 15, max_w=100, align="right")
        cv.text(chg_r, y0 + 4, _change_with_arrow(q), 13, max_w=98, align="right")
        cv.pct_badge(
            badge_x0, y0 + 2, badge_x1, y0 + 21,
            fmt_pct(q.pct), q.direction, px=13,
        )

        # 第 2 行：分时变化（默认 5分）| 高/低（固定列对齐） | 成交额（明确人民币）
        sub_y = y0 + 25
        amt_txt = (
            f"额 {fmt_cn_amount(q.turnover, currency)}"
            if q.turnover is not None
            else f"成交 {fmt_cn_amount(q.volume, '股')}"
        )
        ab = cv.text(RIGHT - 2, sub_y, amt_txt, 12, max_w=125, align="right")

        d_txt = _delta_short(q, with_pct=False, tag=tag)
        db = cv.text(name_x, sub_y, d_txt, 12, max_w=90)

        hl_x = max(hl_col_x, db[2] + 8)
        hl_avail = ab[0] - hl_x - 8
        if hl_avail >= 56:
            hi = fmt_price(q.high) if q.high else EMPTY_TEXT
            lo = fmt_price(q.low) if q.low else EMPTY_TEXT
            hl_txt = f"高 {hi} 低 {lo}"
            if cv.width(hl_txt, 12) > hl_avail and q.high and q.low:
                hl_txt = f"高{q.high:,.0f} 低{q.low:,.0f}"
            cv.text(hl_x, sub_y, hl_txt, 12, max_w=hl_avail)

        if idx < n - 1:
            sep_y = y0 + slot_h - 1
            cv.dotted_hline(LEFT + 2, RIGHT - 2, sep_y)


def _draw_medium_cards(
    cv: Canvas, page: MarketPageData, tag: str = DEFAULT_DELTA_TAG
) -> None:
    """3 行三层卡片（Page 2 · 港股；Page 5 · 美股）。"""
    rows = page.rows
    n = len(rows)
    slot_h = (BODY_Y1 - BODY_Y0) // n  # 78px
    currency = page.spec.currency

    name_x = LEFT + 2
    price_r = 194
    chg_r = 298
    badge_x0, badge_x1 = 305, RIGHT - 2  # 85px

    for idx, row in enumerate(rows):
        y0 = BODY_Y0 + idx * slot_h
        q = row.quote

        # 第 1 行：指数名 | 大号最新点位 | ▲/▼涨跌额 | [涨跌幅%]
        cv.text(name_x, y0 + 5, row.spec.name, 16, max_w=78)
        cv.text(price_r, y0 + 3, fmt_price(q.last), 17, max_w=100, align="right")
        cv.text(chg_r, y0 + 6, _change_with_arrow(q), 13, max_w=96, align="right")
        cv.pct_badge(
            badge_x0, y0 + 3, badge_x1, y0 + 25,
            fmt_pct(q.pct), q.direction, px=13,
        )

        # 第 2 行：分时变化（默认 5分）+ 今开 | 成交股数 + 成交额（或 昨收 + 成交股数）
        mid_y = y0 + 30
        if q.turnover is not None:
            parts = []
            if q.volume is not None:
                parts.append(fmt_cn_amount(q.volume, "股"))
            parts.append(f"额 {fmt_cn_amount(q.turnover, currency)}")
            right_mid = " · ".join(parts)
        else:
            vol_s = fmt_cn_amount(q.volume, "股")
            pc_s = fmt_price(q.prev_close)
            right_mid = f"昨 {pc_s} · {vol_s}"
        rmb = cv.text(RIGHT - 2, mid_y, right_mid, 12, max_w=208, align="right")

        left_mid = f"{_delta_short(q, tag=tag)} · 开 {fmt_price(q.open)}"
        l_avail = rmb[0] - name_x - 8
        if l_avail >= 52:
            if cv.width(left_mid, 12) > l_avail:
                left_mid = _delta_short(q, tag=tag)
            cv.text(name_x, mid_y, left_mid, 12, max_w=l_avail)

        # 第 3 行：低 ├━━●────┤ 高  |  振幅
        rng_y = y0 + 52
        amp_txt = f"振 {fmt_amp(q.amplitude)}"
        amp_b = cv.text(RIGHT - 2, rng_y, amp_txt, 12, align="right")

        lo_txt = f"低 {fmt_price(q.low)}"
        hi_txt = f"高 {fmt_price(q.high)}"
        lo_b = cv.text(name_x, rng_y, lo_txt, 12, max_w=104)
        hi_r = amp_b[0] - 9
        hi_max = max(40, min(108, hi_r - lo_b[2] - 10))
        hi_b = cv.text(hi_r, rng_y, hi_txt, 12, max_w=hi_max, align="right")

        bar_x0 = lo_b[2] + 6
        bar_x1 = hi_b[0] - 6
        if bar_x1 - bar_x0 >= 26:
            cv.range_bar(bar_x0, bar_x1, rng_y + 7, q.low, q.high, q.open, q.last)

        if idx < n - 1:
            cv.dotted_hline(LEFT + 2, RIGHT - 2, y0 + slot_h - 2)


def _draw_hero_cards(
    cv: Canvas, page: MarketPageData, tag: str = DEFAULT_DELTA_TAG
) -> None:
    """2 行大字双卡（Page 3 · 韩股 KOSPI/KOSDAQ；Page 4 · 日股 日经225/东证指数）。"""
    rows = page.rows
    n = len(rows)
    slot_h = (BODY_Y1 - BODY_Y0) // n  # 117px
    currency = page.spec.currency

    name_x = LEFT + 2
    price_r = 286
    badge_x0, badge_x1 = 296, RIGHT - 2  # 94px

    for idx, row in enumerate(rows):
        y0 = BODY_Y0 + idx * slot_h
        q = row.quote

        # 第 1 行：大号指数名（+盘中/收盘） | 超大最新点位 | 大号[涨跌幅%]胶囊
        nb = cv.text(name_x, y0 + 8, row.spec.name, 19, max_w=108)
        st = _status_tag(q)
        price_txt = fmt_price(q.last)
        pb = cv.text(price_r, y0 + 5, price_txt, 22, max_w=145, align="right")
        if st and pb[0] - (nb[2] + 6) >= 34:
            cv.text(nb[2] + 6, y0 + 13, st, 12, max_w=pb[0] - nb[2] - 10)
        cv.pct_badge(
            badge_x0, y0 + 5, badge_x1, y0 + 31,
            fmt_pct(q.pct), q.direction, px=15,
        )

        # 第 2 行：日内涨跌额 | 分时变化（默认 5 分钟，含百分比） | 日内振幅
        chg_y = y0 + 40
        amp_b = cv.text(RIGHT - 2, chg_y + 1, f"振幅 {fmt_amp(q.amplitude)}", 12, align="right")
        cb = cv.text(name_x, chg_y, f"涨跌 {_change_with_arrow(q)}", 13, max_w=135)

        d_x = cb[2] + 12
        d_avail = amp_b[0] - d_x - 10
        if d_avail >= 55:
            d_full = _delta_short(q, with_pct=True, tag=tag)
            if cv.width(d_full, 12) > d_avail:
                d_full = _delta_short(q, with_pct=False, tag=tag)
            cv.text(d_x, chg_y + 1, d_full, 12, max_w=d_avail)

        # 第 3 行：低 ├━━━━●──────┤ 高
        rng_y = y0 + 66
        lo_b = cv.text(name_x, rng_y, f"低 {fmt_price(q.low)}", 12, max_w=108)
        hi_b = cv.text(RIGHT - 2, rng_y, f"高 {fmt_price(q.high)}", 12, max_w=108, align="right")
        bar_x0 = lo_b[2] + 9
        bar_x1 = hi_b[0] - 9
        if bar_x1 - bar_x0 >= 32:
            cv.range_bar(bar_x0, bar_x1, rng_y + 7, q.low, q.high, q.open, q.last)

        # 第 4 行：今开 + 昨收 | 成交股数 + 成交额（本币）
        bot_y = y0 + 91
        if q.turnover is not None:
            parts = []
            if q.volume is not None:
                parts.append(fmt_cn_amount(q.volume, "股"))
            parts.append(f"额 {fmt_cn_amount(q.turnover, currency)}")
            r_bot = " · ".join(parts)
        else:
            r_bot = f"成交 {fmt_cn_amount(q.volume, '股')}"
        rb = cv.text(RIGHT - 2, bot_y, r_bot, 12, max_w=208, align="right")

        l_bot = f"开 {fmt_price(q.open)} · 昨 {fmt_price(q.prev_close)}"
        l_avail = rb[0] - name_x - 8
        if l_avail >= 60:
            if cv.width(l_bot, 12) > l_avail:
                l_bot = f"开 {fmt_price(q.open)}"
            cv.text(name_x, bot_y, l_bot, 12, max_w=l_avail)

        if idx < n - 1:
            cv.draw.line([(LEFT + 2, y0 + slot_h - 2), (RIGHT - 2, y0 + slot_h - 2)], fill=0, width=1)


def render_market_page(
    page: MarketPageData,
    *,
    total_pages: int = 5,
    custom_title: Optional[str] = None,
    demo: bool = False,
    update_tag: Optional[str] = None,
    interval_seconds: Optional[float] = None,
    out_path: Optional[str] = None,
    validate: bool = True,
) -> Image.Image:
    """把单个市场页渲染为 400×300 1-bit 图像并通过严格排版预检。

    interval_seconds 决定「N分」与「N分钟更新」文案（默认 5 分钟）；update_tag 可显式覆盖页脚文案。
    """
    cv = Canvas()
    _draw_header(cv, page, total_pages=total_pages, custom_title=custom_title, demo=demo)

    tag = delta_label(interval_seconds)
    footer_tag = update_tag or update_label(interval_seconds)

    n = len(page.rows)
    if n <= 2:
        _draw_hero_cards(cv, page, tag=tag)
    elif n <= 3:
        _draw_medium_cards(cv, page, tag=tag)
    else:
        _draw_compact_rows(cv, page, tag=tag)

    _draw_footer(cv, page, update_tag=footer_tag)
    if validate:
        cv.validate(page.spec.page_id)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        cv.img.save(out_path)
    return cv.img


def render_all_pages(
    panel: PanelData,
    *,
    page_ids: Sequence[int] = (1, 2, 3, 4, 5),
    custom_title: Optional[str] = None,
    update_tag: Optional[str] = None,
    interval_seconds: Optional[float] = None,
    out_dir: Optional[str] = None,
    validate: bool = True,
) -> Dict[int, Image.Image]:
    """渲染指定页码的全部市场页，返回 {page_id: Image}。"""
    images: Dict[int, Image.Image] = {}
    for pid in page_ids:
        page = panel.pages.get(pid)
        if page is None:
            continue
        out_path = str(Path(out_dir) / f"page_{pid}.png") if out_dir else None
        images[pid] = render_market_page(
            page,
            total_pages=len(panel.pages),
            custom_title=custom_title,
            demo=panel.demo,
            update_tag=update_tag,
            interval_seconds=interval_seconds,
            out_path=out_path,
            validate=validate,
        )
    return images


# =====================================================================
# 位图指纹、状态持久化与按页推送
# =====================================================================
def image_digest(img: Image.Image) -> str:
    """1-bit 像素内容的 SHA-256（与 PNG 压缩头无关，画面不变则指纹恒等）。"""
    h = hashlib.sha256()
    h.update(f"{img.size[0]}x{img.size[1]}:{img.mode}:".encode("ascii"))
    h.update(img.tobytes())
    return h.hexdigest()


def load_state(path: Optional[str]) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Optional[str], state: dict) -> None:
    """原子写入状态文件（中断时不会写坏 JSON）。"""
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".market_state_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def parse_page_ids(pages_arg: str) -> List[int]:
    out: List[int] = []
    for tok in (pages_arg or "").split(","):
        tok = tok.strip()
        if tok in ("1", "2", "3", "4", "5"):
            pid = int(tok)
            if pid not in out:
                out.append(pid)
    return out


def build_meta(
    panel: PanelData,
    page_hashes: Dict[str, str],
    result: "CycleResult",
) -> dict:
    """生成可供排查与产物核验的 JSON 元数据。"""
    pages_meta = []
    for pid in sorted(panel.pages):
        p = panel.pages[pid]
        rows_meta = []
        for r in p.rows:
            q = r.quote
            rows_meta.append({
                "key": r.spec.key,
                "name": r.spec.name,
                "valid": r.valid,
                "last": q.last,
                "change": q.change,
                "pct": q.pct,
                "delta": q.delta,
                "delta_pct": q.delta_pct,
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "prev_close": q.prev_close,
                "amplitude": q.amplitude,
                "volume": q.volume,
                "turnover": q.turnover,
                "currency": p.spec.currency,
                "status": q.status,
                "source": q.source,
                "delayed": q.delayed,
                "time": q.time.isoformat() if q.time else None,
            })
        pages_meta.append({
            "page_id": pid,
            "market": p.spec.code,
            "title": p.spec.title,
            "currency": p.spec.currency,
            "live_rows": p.live_rows,
            "total_rows": p.total_rows,
            "as_of_bjt": fmt_as_of(p.as_of),
            "summary_label": p.spec.summary_label,
            "summary_value": p.summary_value,
            "summary_delta": p.summary_delta,
            "sha256": page_hashes.get(str(pid)),
            "rows": rows_meta,
        })
    return {
        "generated_at": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "market_order": list(MARKET_ORDER),
        "demo": panel.demo,
        "sources": panel.sources,
        "live_rows": panel.live_rows,
        "total_rows": panel.total_rows,
        "pushed_pages": result.pushed,
        "unchanged_pages": result.unchanged,
        "empty_pages": result.empty,
        "failed_pages": result.failed,
        "pages": pages_meta,
    }


@dataclass
class CycleResult:
    exit_code: int
    live_rows: int = 0
    total_rows: int = 0
    pushed: List[int] = None      # type: ignore[assignment]
    unchanged: List[int] = None   # type: ignore[assignment]
    empty: List[int] = None       # type: ignore[assignment]
    failed: List[int] = None      # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.pushed = list(self.pushed or [])
        self.unchanged = list(self.unchanged or [])
        self.empty = list(self.empty or [])
        self.failed = list(self.failed or [])

    def summary(self) -> str:
        parts = [f"行情 {self.live_rows}/{self.total_rows}"]
        if self.pushed:
            labels = "、".join(f"P{p}({mq.MARKET_BY_PAGE[p].code})" for p in self.pushed)
            parts.append(f"推屏 {labels}")
        if self.unchanged:
            parts.append(f"无变化 {len(self.unchanged)} 页")
        if self.empty:
            labels = "、".join(f"P{p}({mq.MARKET_BY_PAGE[p].code})" for p in self.empty)
            parts.append(f"缺行情跳过 {labels}")
        if self.failed:
            labels = "、".join(f"P{p}({mq.MARKET_BY_PAGE[p].code})" for p in self.failed)
            parts.append(f"推送失败 {labels}")
        return " · ".join(parts)


def run_once(
    *,
    pages_arg: str = "1,2,3,4,5",
    custom_title: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
    demo: bool = False,
    demo_delta: bool = False,
    state_file: Optional[str] = DEFAULT_STATE_FILE,
    out_dir: Optional[str] = None,
    meta_file: Optional[str] = None,
    interval_seconds: Optional[float] = None,
    fetcher=None,
    pusher=push_image,
) -> CycleResult:
    """执行一轮完整的「抓取 → 算变化 → 渲染五页 → 预检 → 按页去重推屏」。"""
    page_ids = parse_page_ids(pages_arg)
    if not page_ids:
        print(f"❌ 无效的页面列表: {pages_arg!r}（有效值 1..5）", file=sys.stderr)
        return CycleResult(exit_code=EXIT_FATAL)

    if not dry_run and (not board_core.API_KEY or not board_core.MAC_ADDRESS):
        print("❌ 未配置 ZECTRIX_API_KEY 或 ZECTRIX_MAC（预览请加 --dry-run）", file=sys.stderr)
        return CycleResult(exit_code=EXIT_FATAL)

    set_enabled_pages(",".join(str(p) for p in page_ids))
    if custom_title:
        set_board_title(custom_title)

    # 1. 获取行情（7 路源）并计算较上一轮的变化
    use_demo = bool(demo or demo_delta)
    prev_state = {} if use_demo else load_state(state_file)
    if use_demo:
        panel = demo_panel(with_delta=demo_delta)
        next_state = dict(prev_state)
    else:
        panel = collect_panel(fetch=fetcher) if fetcher is not None else collect_panel()
        next_state = apply_delta_state(panel, prev_state)

    enabled_live = sum(panel.pages[pid].live_rows for pid in page_ids)
    if enabled_live == 0:
        print("⚠️ 本轮全部启用页均未取到有效行情，保留墨水屏上一版内容。", flush=True)
        return CycleResult(
            exit_code=EXIT_NO_LIVE_DATA,
            live_rows=panel.live_rows,
            total_rows=panel.total_rows,
            empty=list(page_ids),
        )

    old_hashes = prev_state.get("page_hashes") if isinstance(prev_state.get("page_hashes"), dict) else {}
    new_hashes: Dict[str, str] = {str(k): str(v) for k, v in old_hashes.items()}

    res = CycleResult(
        exit_code=EXIT_OK,
        live_rows=panel.live_rows,
        total_rows=panel.total_rows,
    )

    # 2. 逐页渲染、预检、指纹比对、推送
    for pid in page_ids:
        page = panel.pages[pid]
        if page.live_rows == 0:
            res.empty.append(pid)
            print(f"⚠️ Page {pid}（{page.spec.code}）本轮无有效行情，跳过推送以保留上一版。", flush=True)
            continue

        out_path = str(Path(out_dir) / f"page_{pid}.png") if out_dir else f"page_{pid}.png"
        try:
            img = render_market_page(
                page,
                total_pages=len(panel.pages),
                custom_title=custom_title,
                demo=panel.demo,
                interval_seconds=interval_seconds,
                out_path=out_path,
                validate=True,
            )
        except LayoutError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return CycleResult(exit_code=EXIT_FATAL, live_rows=panel.live_rows, total_rows=panel.total_rows)

        digest = image_digest(img)
        key = str(pid)
        if not demo and not force and new_hashes.get(key) == digest:
            res.unchanged.append(pid)
            continue

        if dry_run:
            # dry_run 已写入 out_path；若未指定 out_dir 也与 push_image 保持一致落盘 page_{pid}.png
            res.pushed.append(pid)
            if not demo:
                new_hashes[key] = digest
        else:
            if pusher(img, page_id=pid, dry_run=False):
                res.pushed.append(pid)
                new_hashes[key] = digest
            else:
                res.failed.append(pid)

    # 3. 持久化状态（demo 与 dry_run 不污染正式状态）
    if not demo and state_file and not dry_run:
        next_state["page_hashes"] = new_hashes
        next_state["updated_at"] = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
        save_state(state_file, next_state)

    if meta_file:
        meta_target = Path(meta_file)
        meta_target.parent.mkdir(parents=True, exist_ok=True)
        meta_target.write_text(
            json.dumps(build_meta(panel, new_hashes, res), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if res.failed:
        res.exit_code = EXIT_PUSH_FAILED
    print(f"📊 {res.summary()}", flush=True)
    return res


def build_contact_sheet(images: Dict[int, Image.Image], out_path: str) -> Image.Image:
    """把 5 页 400×300 面板拼成 2×3 总览样张（供 README 与人工检视）。"""
    pad, gap = 14, 14
    cols, rows = 2, 3
    sheet_w = pad * 2 + cols * CANVAS_W + (cols - 1) * gap
    sheet_h = pad * 2 + rows * CANVAS_H + (rows - 1) * gap
    sheet = Image.new("L", (sheet_w, sheet_h), color=242)
    draw = ImageDraw.Draw(sheet)
    for idx, pid in enumerate(sorted(images)):
        c, r = idx % cols, idx // cols
        x = pad + c * (CANVAS_W + gap)
        y = pad + r * (CANVAS_H + gap)
        sheet.paste(images[pid].convert("L"), (x, y))
        draw.rectangle([(x - 1, y - 1), (x + CANVAS_W, y + CANVAS_H)], outline=180, width=1)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return sheet


# =====================================================================
# CLI
# =====================================================================
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="五页全球行情数据面板（1=A、2=港、3=韩、4=日、5=美，含变化）→ Zectrix 墨水屏",
    )
    ap.add_argument("--pages", default="1,2,3,4,5", help="推送页面（默认 1,2,3,4,5 = A,港,韩,日,美）")
    ap.add_argument("--title", default=None, help="自定义顶栏前缀（留空直接显示 A股行情/港股行情/…）")
    ap.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="轮间变化与页面指纹状态文件")
    ap.add_argument("--out-dir", default=None, help="PNG 输出目录（默认当前目录 page_1..5.png）")
    ap.add_argument("--meta-file", default=None, help="可选：输出 JSON 行情与状态摘要")
    ap.add_argument("--contact-sheet", default=None, help="可选：输出五页拼版预览图")
    ap.add_argument("--demo", action="store_true", help="离线演示：使用 tests/fixtures/market_* 实测数据")
    ap.add_argument("--demo-delta", action="store_true", help="离线演示时附带上一轮基线以展示 5分变化")
    ap.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
        help=f"更新间隔秒数（默认 {DEFAULT_INTERVAL_SECONDS} = 5 分钟），只影响「N分」「N分钟更新」文案",
    )
    ap.add_argument("--force", action="store_true", help="画面与上次一致也强制重推")
    ap.add_argument("--dry-run", action="store_true", help="仅渲染本地 PNG，不推送到 Zectrix")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    use_demo = args.demo or args.demo_delta
    res = run_once(
        pages_arg=args.pages,
        custom_title=args.title,
        dry_run=args.dry_run or use_demo,
        force=args.force,
        demo=use_demo,
        demo_delta=args.demo_delta,
        state_file=args.state_file,
        out_dir=args.out_dir,
        meta_file=args.meta_file,
        interval_seconds=args.interval,
    )
    if args.contact_sheet and res.exit_code == EXIT_OK:
        base = Path(args.out_dir) if args.out_dir else Path(".")
        imgs = {}
        for pid in parse_page_ids(args.pages):
            p = base / f"page_{pid}.png"
            if p.exists():
                imgs[pid] = Image.open(p).convert("1")
        if imgs:
            build_contact_sheet(imgs, args.contact_sheet)
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
