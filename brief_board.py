#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brief_board —— 「004/003/007 五页简报」常驻看板：拉取三个仓库的最新内容原文，
排成 5 页 400×300 墨水屏并推送到 Zectrix。

5 页分工（顶栏五页统一「章鱼 AI·全景分析」，每屏只留正文：原文正文行 + 小标题，
出处 / 编号 / 权重 / 系统日志 / 图例 / 条形图 / 配置行等非正文行一律不上屏）：
    Page 1  仓库 004 · 00 / 每日 VIX 恐慌指数 + 07 / 核心结论（最后结论）
    Page 2  仓库 003 · 机会点 / 风险点（条目整段原文）
    Page 3  机会节点（每行一个节点，原文原句）
    Page 4  仓库 007 · 研报 AI 风险点提示 · 指标面（美银牛熊 B&B 字符卡 + 六大分项）
    Page 5  仓库 007 · 研报 AI 风险点提示（栏目口径 / 兜底段落 / 尾注）

数据源（每次运行现拉，随三仓库更新自动刷新）：
    004  https://k-macao.github.io/004/                     （GitHub Pages 最新构建报告）
    003  raw k-macao/003 : push.preview.html                （最新推送版式预览）
    007  raw k-macao/007 : pushplus_deepseek.py + README.md （内置最新可核验读数 + 简报规范）

失败即显红、整轮不推（与 octopus 看板同口径）：任一数据源拉不到 / 004 报告过期 /
标记改版（解析不到约定栏目）/ 推送失败，一律退出码 1 —— Actions 显红告警，
墨水屏保留上一次内容。放不下的条目按板式如实截断并写「▼ 另有 N 项」。

用法：
    python3 brief_board.py --dry-run                # 只渲染 page_*.png，不推屏
    python3 brief_board.py                          # 渲染并推送到 Zectrix
    python3 brief_board.py --pages 1,2,3,4,5 --max-age-hours 48
"""

import argparse
import ast
import html as html_mod
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from PIL import ImageDraw, ImageFont

from board_core import (
    CONTENT_W,
    FONT_PATH,
    MARGIN_X,
    clarify_currency_units,
    draw_top_bar,
    font_tiny,
    new_canvas,
    push_image,
    spread_leading,
    wrap_text_by_pixels,
)

BEIJING = timezone(timedelta(hours=8))

SRC_004 = os.environ.get("BRIEF_004_URL", "https://k-macao.github.io/004/")
SRC_003 = os.environ.get(
    "BRIEF_003_URL",
    "https://raw.githubusercontent.com/k-macao/003/main/push.preview.html",
)
SRC_007_PY = os.environ.get(
    "BRIEF_007_PY_URL",
    "https://raw.githubusercontent.com/k-macao/007/main/pushplus_deepseek.py",
)
SRC_007_README = os.environ.get(
    "BRIEF_007_README_URL",
    "https://raw.githubusercontent.com/k-macao/007/main/README.md",
)

TITLE = "章鱼 AI·全景分析"


class BriefError(Exception):
    """数据源拉不到 / 过期 / 改版：整轮失败，显红不推屏。"""


# =====================================================================
# 🌐 拉取与原文提取
# =====================================================================
def fetch(url, timeout=30):
    try:
        res = requests.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise BriefError(f"拉取失败 {url}: {exc}") from exc
    if res.status_code != 200:
        raise BriefError(f"拉取失败 {url}: HTTP {res.status_code}")
    return res.text


def extract_text(page_html):
    """HTML → 纯文本行（剥标签、还原实体、逐行收干净），供原文标记切片。"""
    raw = re.sub(r"<(script|style)[\s\S]*?</\1>", "", page_html, flags=re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|div|h[1-6]|li|tr|table|section|blockquote)>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    text = html_mod.unescape(raw).replace("\u200b", "").replace("\ufeff", "")
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"[ \t\u3000]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines) + "\n"


def _slice(text, start_mark, end_mark, what):
    i = text.find(start_mark)
    if i < 0:
        raise BriefError(f"004 改版：找不到栏目「{what}」起始标记 {start_mark!r}")
    i += len(start_mark)
    j = text.find(end_mark, i)
    if j < 0:
        raise BriefError(f"004 改版：找不到栏目「{what}」结束标记 {end_mark!r}")
    return [ln for ln in text[i:j].splitlines() if ln.strip()]


def parse_004(text):
    """004 报告页 → VIX 块 / 多空方向 / 做多结论 / 核心结论（原文行）。"""
    vix = _slice(text, "00 / 每日 VIX 恐慌指数", "OPENING SIGNAL", "00 VIX")
    if not any("VIX" in ln for ln in vix) or not any("今日判读" in ln for ln in vix):
        raise BriefError("004 改版：VIX 块缺 CBOE 读数或今日判读行")

    opening = _slice(text, "OPENING SIGNAL", "01 /", "OPENING SIGNAL")
    hs_line = next((ln for ln in opening if "恒指涨跌幅" in ln), None)
    if hs_line is None:
        raise BriefError("004 改版：OPENING SIGNAL 块缺恒指涨跌幅行")

    longshort = _slice(text, "◆ 是否可以做多", "02 /", "是否可以做多")
    if not any(ln.startswith("结论") for ln in longshort) or not any(
        "多头有效性参考位" in ln for ln in longshort
    ):
        raise BriefError("004 改版：「是否可以做多」块缺结论或多头有效性参考位")

    tail = text[text.find("07 / 核心结论"):] if "07 / 核心结论" in text else None
    if tail is None:
        raise BriefError("004 改版：找不到栏目「07 / 核心结论」")
    risk_ln = next((ln for ln in tail.splitlines() if ln.startswith("⚠️")), None)
    if risk_ln is None:
        raise BriefError("004 改版：核心结论缺 ⚠️ 风险提示行")
    body = tail[: tail.find(risk_ln)]
    conclusion = [ln for ln in body.splitlines() if ln.strip()]
    if not any(ln.startswith("港股盘面") for ln in conclusion):
        raise BriefError("004 改版：核心结论缺港股盘面条目")
    conclusion.append(risk_ln)

    m = re.search(r"生成时间：\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC", text)
    gen_time = None
    if m:
        gen_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    return {
        "vix": vix,
        "hs_line": hs_line,
        "longshort": longshort,
        "conclusion": conclusion,
        "gen_time": gen_time,
    }


def check_freshness(gen_time, max_age_hours):
    """004 报告超过 max_age_hours 判停更：整轮失败（0 = 不检查）。"""
    if max_age_hours <= 0:
        return
    if gen_time is None:
        raise BriefError("004 改版：找不到生成时间，无法核对新鲜度")
    age = datetime.now(timezone.utc) - gen_time
    if age > timedelta(hours=max_age_hours):
        raise BriefError(
            f"004 停更：报告生成于 {gen_time:%Y-%m-%d %H:%M} UTC，"
            f"已超过 {max_age_hours} 小时（{age.total_seconds() / 3600:.0f}h）"
        )


# =====================================================================
# 📰 003：机会点 / 风险点分类（条目整段原文）
# =====================================================================
_CAUTION = re.compile(
    r"美聯儲|美联储|分歧|下跌|跌幅|風險|风险|恐慌|谨慎|謹慎|賣出|卖出|通胀|衰退|sell|risk",
    re.I,
)


def parse_003(text):
    """003 推送预览 → 机会点 / 风险点 / 机会节点（全部原文行）。"""
    hot = [ln for ln in text.splitlines() if ln.startswith("*")]
    entries = {}
    order = []
    cur = None
    for ln in text.splitlines():
        if re.fullmatch(r"#\d+", ln) or ln.startswith("■ "):
            cur = ln
            order.append(ln)
            entries[cur] = []
        elif ln.startswith(("EDITORIAL_MANIFESTO", "DOS MONITOR", "章鱼 AI · 全景视野", "C:\\OCTOPUS")):
            cur = None  # 尾页装饰区，不进条目
        elif cur is not None:
            entries[cur].append(ln)

    opp, risk = [], []
    for key in order:
        block = [key] + entries[key]
        if key.startswith("■"):
            risk.extend(block)  # SCAN_ERROR.LOG → 风险点
            continue
        bull = None
        for ln in block:
            m = re.search(r"多 (\d+)% / 空 (\d+)%", ln)
            if m:
                bull = int(m.group(1))
                break
        (opp if (bull or 0) >= 50 else risk).extend(block)

    for ln in hot:
        (risk if _CAUTION.search(ln) else opp).append(ln)

    clause_risk = "接近上轨极度贪婪时考虑分批止盈"
    clause_opp = "触及下轨极度恐慌时寻找左侧买点"
    if clause_risk in text:
        risk.append(clause_risk)
    if clause_opp in text:
        opp.append(clause_opp)

    for ln in text.splitlines():
        if ln.startswith("FRESHNESS CHECK") or ln.startswith("超时 / 无法验证"):
            risk.append(ln)

    summary = {}
    m = re.search(r"(大众情绪指数 \d+/100 · [^\n]*)", text) or re.search(
        r"(大众情绪指数 \d+/100（[^）]*）)", text
    )
    if m:
        summary["sentiment"] = m.group(1)
    m = re.search(r"多 \d+% / 空 \d+%", text)
    if m:
        summary["ls"] = m.group(0)
    return {"opp": _dedupe(opp), "risk": _dedupe(risk), "summary": summary}


def _dedupe(lines):
    seen, out = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out


# =====================================================================
# 🏦 007：研报 AI 风险点提示（原文片段）
# =====================================================================
DEFAULT_007 = {
    "bofa": {
        "current": 7.2,
        "previous": 7.4,
        "as_of": "2026-10-21",
        "source": "BofA The Flow Show 2026-10-22",
        "signal": "Neutral",
        "signal_cn": "中性",
        "components_as_of": "2026-10-01",
        "components": [
            {"cn": "对冲基金仓位", "percentile": 64},
            {"cn": "股票资金流向", "percentile": 89},
            {"cn": "债券资金流向", "percentile": 62},
            {"cn": "信贷市场技术面", "percentile": 55},
            {"cn": "全球股指广度", "percentile": 21},
            {"cn": "基金经理调查仓位", "percentile": 100},
        ],
    },
    "risk_paren": "（市场级风险、机构评级下调/分歧预警及触发信号）",
    "keywords": "下调|减持|卖出|中性|谨慎|风险|承压|回调|警告|分歧|放缓|通胀|衰退|"
    "underweight|sell|neutral|downgrade|cut|risk|headwind",
    "signal_rule": "按 BofA 惯例把读数映射为信号：<2.0 反向买入，>8.0 反向卖出，其余中性。",
    "guide": "先把丑话说在前面。盘面上热闹归热闹，但下面这几处暗礁眼下还真不能假装看不见——"
    "尤其是机构开始收紧口径的地方，往往藏着资金最真实的顾虑：",
    "prompt": "放在最开头：结合研报与市场快讯，用老练分析师提醒客户留意暗礁的口吻，"
    "剖析 3~5 个当前最需警惕的市场级风险、机构评级下调/观点分歧预警，"
    "或接下来需要盯紧的风险触发信号（注明依据来源）",
    "thin": "坦白讲，眼下这个时间窗抓到的有效快讯和研报样本偏少——或许正赶上休市空窗，"
    "又或许是部分公开信源暂时卡壳。越是这种像在薄雾里开车的时刻，"
    "越别急着重仓押单边，等跨市场的暗流看清了再落子也不迟。",
    "tail": ["仅供参考，分析研究。", "作者：章鱼 ai"],
}


def _ast_constants(py_src):
    tree = ast.parse(py_src)
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def parse_007(py_src, readme):
    """007 源码/README → 研报 AI 风险点提示原文片段；解析不到自动回退内置副本。"""
    out = dict(DEFAULT_007)
    out.pop("prompt", None)  # 以 007 源码实际提示词为准，解析不到再回退

    tree = ast.parse(py_src)
    for node in ast.walk(tree):
        value, targets = None, []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == "DEFAULT_BOFA_BB_DATA" and value is not None:
                try:
                    out["bofa"] = ast.literal_eval(value)
                except Exception:  # noqa: BLE001
                    pass

    consts = _ast_constants(py_src)
    for val in consts:
        if val.startswith("下调|减持|"):
            out["keywords"] = val
        elif val.startswith("先把丑话说在前面"):
            out["guide"] = val
        elif val.startswith("坦白讲，眼下"):
            out["thin"] = val
        elif "按 BofA 惯例把读数映射为信号" in val:
            out["signal_rule"] = val.split("\n")[0]
        elif "放在最开头：" in val and "prompt" not in out:
            seg = val[val.find("放在最开头："):].split("\n\n")[0].strip()
            out["prompt"] = seg.lstrip("- ")
    out.setdefault("prompt", DEFAULT_007["prompt"])

    m = re.search(r"（市场级风险[^）]*）", readme)
    if m:
        out["risk_paren"] = m.group(0)
    return out


def bofa_card_lines(bofa):
    """B&B 字符卡读数行 + 数据日期行 + 分项行（007 自身输出口径）。"""
    cur = float(bofa.get("current", 0.0))
    prev = bofa.get("previous")
    prev = float(prev) if prev is not None else None
    signal = str(bofa.get("signal") or "Neutral").upper()
    head = f"BofA Bull & Bear (B&B) {cur} {signal}"
    if prev is not None:
        head += f" prev {prev} ({cur - prev:+.1f})"

    as_of = str(bofa.get("as_of") or "")
    age_days = None
    stale = True
    try:
        age_days = (datetime.now(BEIJING).date() - datetime.strptime(as_of[:10], "%Y-%m-%d").date()).days
        stale = age_days > 10
    except ValueError:
        as_of = "日期未知"
    fresh = f"数据日期：截至 {as_of}"
    if age_days is not None and age_days >= 0:
        fresh += f"（{age_days} 天前）"
    fresh += " · ⏳ 已过期" if stale else " · ✅ 周更正常"

    src_line = f"来源：{bofa.get('source', '')}"
    comp = " · ".join(
        f"{c.get('cn', c.get('name', ''))} {c.get('percentile', '')}%" for c in bofa.get("components") or []
    )
    comp_note = f"最近一期公布，可能与聚合读数不同期（{bofa.get('components_as_of', '')}）"
    return [head, fresh, src_line, comp, comp_note]


# =====================================================================
# 🧱 五页内容计划
# =====================================================================
def _body_line(text):
    """一行原文 → 正文行；非正文返回 None（不上屏）。

    过滤口径（每屏只要正文）：条目编号 / 出处行 / 权重尾巴 / 扫描系统行 /
    状态注释 / 日志与观看量等元数据行 / 条形图 / 图例 / 配置行（关键词、prompt）/
    数据日期与来源备注。多空概率（「多 X% / 空 Y%」）保留。
    """
    t = text.strip()
    if re.fullmatch(r"#\d+", t):  # 条目编号 #1 / #2
        return None
    if t.startswith("DIR ▸"):  # 出处行
        return None
    if t.startswith(("FRESHNESS CHECK", "超时 / 无法验证")):  # 扫描系统行
        return None
    if "SCAN_ERROR" in t or "Too Many Requests" in t or (t.startswith("[") and "未完成读取" in t):
        return None  # 读取失败日志
    if t.startswith(("字幕已读取", "AI 多空概率", "仅供参考，不做推荐")):  # 状态 / 标签 / 注释行
        return None
    if t.startswith("C:\\"):  # 日志行（3 小时前 · 观看量 · 粉丝量 · 时效验证…）
        return None
    if "█" in t or "░" in t:  # 条形图（多空比例图等）
        return None
    if re.match(r"^<\d+ 极低", t):  # VIX 分档图例
        return None
    if t.startswith("按 BofA 惯例"):  # BofA 信号映射图例
        return None
    if t.startswith(("数据日期：", "来源：", "最近一期公布")):  # 007 备注行
        return None
    if t.startswith("下调|减持|") or t.startswith("放在最开头："):  # 关键词 / prompt 配置行
        return None
    return re.sub(r" ?· 权重 \d+%", "", t)  # 权重尾巴去掉，多空概率保留


def body_only_rows(rows):
    """(kind, 原文行) 列表 → 只留正文的 rows（head 小标题原样保留，仅正名补全币种）。"""
    out = []
    for kind, t in rows:
        if kind == "head":
            out.append((kind, t))
            continue
        line = _body_line(t)
        if line is not None:
            out.append((kind, clarify_currency_units(line)))
    return out


def build_pages(d004, d003, d007):
    """三仓库数据 → 5 页 rows（正文一律原文行，每屏只留正文）。"""
    opp = [t for t in (_body_line(ln) for ln in d003["opp"]) if t is not None]
    risk = [t for t in (_body_line(ln) for ln in d003["risk"]) if t is not None]

    p1_rows = (
        [("head", "00 / 每日 VIX 恐慌指数")]
        + [("small", ln) for ln in d004["vix"]]
        + [("small", d004["hs_line"])]
        + [("head", "07 / 核心结论")]
        + [("small", ln) for ln in d004["conclusion"]]
    )

    p2_rows = [("head", "机会点")] + [("small", ln) for ln in opp]
    p2_rows += [("head", "风险点")] + [("small", ln) for ln in risk]

    nodes = []
    for ln in opp:
        if ln.startswith("*") or ln.startswith(">") or ln.startswith("偏多") or re.match(r"\d{2}:\d{2} ", ln):
            nodes.append(ln)
        elif ln == "触及下轨极度恐慌时寻找左侧买点":
            nodes.append(ln)
    if d003["summary"].get("ls"):
        nodes.append(d003["summary"]["ls"])
    if d003["summary"].get("sentiment"):
        nodes.append(d003["summary"]["sentiment"])
    for ln in d004["longshort"]:
        if ln.startswith("多头有效性参考位"):
            nodes.append(ln)
    for ln in d004["conclusion"]:
        if ln.startswith("社区多空分布"):
            nodes.append(ln)
        m = re.search(r"现货黄金[^；]*", ln)
        if m:
            nodes.append(m.group(0))
    p3_rows = [("small", ln) for ln in _dedupe(nodes)]

    card = bofa_card_lines(d007["bofa"])
    p4_rows = (
        [("head", "📊 美银牛熊指标 · BofA Bull & Bear (B&B)")]
        + [("small", ln) for ln in card]
        + [("small", d007["signal_rule"])]
        + [("head", "简报固定章节顺序")]
        + [("small", f"2. 风险提示：⚠️ 研报 AI 风险点提示{d007['risk_paren']}")]
    )

    p5_rows = (
        [("head", "⚠️ 研报 AI 风险点提示")]
        + [("small", d007["risk_paren"])]
        + [("small", d007["keywords"])]
    )
    if d007.get("prompt"):
        p5_rows.append(("small", d007["prompt"]))
    p5_rows += [
        ("small", d007["guide"]),
        ("small", d007["thin"]),
    ] + [("small", ln) for ln in d007["tail"]]

    return {
        1: {"sub": "004 · 00 VIX + 07 核心结论", "tail": "仓库 004", "rows": body_only_rows(p1_rows)},
        2: {"sub": "003 · 机会点 / 风险点", "tail": "仓库 003", "rows": body_only_rows(p2_rows)},
        3: {"sub": "机会节点 · 每行一个", "tail": "004+003", "rows": body_only_rows(p3_rows)},
        4: {"sub": "007 · 研报 AI 风险点提示", "tail": "仓库 007", "rows": body_only_rows(p4_rows)},
        5: {"sub": "007 · 研报 AI 风险点提示", "tail": "仓库 007", "rows": body_only_rows(p5_rows)},
    }


# =====================================================================
# 🖼️ 渲染 400×300（全屏自适应排版；放不下如实截断 + 「▼ 另有 N 项」）
# =====================================================================
BODY_TOP = 44
BODY_BOTTOM = 288
NOTE_RESERVE = 15          # 装不下时给「▼ 另有 N 项」指针行预留的高度
HEAD_MARK_W = 12           # 小标题前的黑色竖条 + 缩进

# 全屏自适应档位：先大后小，选第一套装得下的；最小档仍装不下就如实截断。
# 每档 = 正文字号/行高 + 小标题字号/行高/段前留白。最小档比旧版更密，
# 保证内容容量不缩水（宁可字号小，也不多丢条目）。
_STYLES = [
    {"body_px": 16, "body_lh": 22, "head_px": 19, "head_lh": 25, "head_gap": 10},
    {"body_px": 15, "body_lh": 20, "head_px": 18, "head_lh": 23, "head_gap": 9},
    {"body_px": 14, "body_lh": 19, "head_px": 17, "head_lh": 21, "head_gap": 8},
    {"body_px": 13, "body_lh": 17, "head_px": 15, "head_lh": 19, "head_gap": 6},
    {"body_px": 12, "body_lh": 15, "head_px": 14, "head_lh": 18, "head_gap": 5},
    {"body_px": 12, "body_lh": 13, "head_px": 13, "head_lh": 16, "head_gap": 4},
]

_FONT_CACHE = {}


def _font(px):
    if px not in _FONT_CACHE:
        _FONT_CACHE[px] = ImageFont.truetype(FONT_PATH, px)
    return _FONT_CACHE[px]


def _layout_rows(draw, rows, style):
    """折行并量高：返回 (blocks, used_h, n_lines)。

    block = {"kind", "lines", "font", "lh", "gap", "h"}；gap 为该块上方留白。
    小标题（head）与重点行（text）用大一号字，正文（small）用正文字号。
    """
    body_font = _font(style["body_px"])
    head_font = _font(style["head_px"])
    blocks, used, n_lines = [], 0.0, 0
    for kind, text in rows:
        if kind == "head":
            font, lh, gap = head_font, style["head_lh"], style["head_gap"]
            lines = wrap_text_by_pixels(draw, text, font, CONTENT_W - HEAD_MARK_W)
        elif kind == "text":
            font, lh, gap = head_font, style["head_lh"], 0
            lines = wrap_text_by_pixels(draw, text, font, CONTENT_W)
        else:
            font, lh, gap = body_font, style["body_lh"], 0
            lines = wrap_text_by_pixels(draw, text, font, CONTENT_W)
        h = gap + len(lines) * lh
        blocks.append({"kind": kind, "lines": lines, "font": font, "lh": lh, "gap": gap, "h": h})
        used += h
        n_lines += len(lines)
    return blocks, used, n_lines


def render_page(rows, page_id, sub, tail, meta, out_path=None, title=TITLE, total=5):
    """一页 400×300 1-bit：统一顶栏 + 全屏自适应正文（只留正文）。

    按「每屏只要正文」口径：不画副标题 / 来源 / 页脚（sub、tail、meta 形参保留兼容旧调用）。
    条目少时自动升大字号、行距摊满整屏；最小档仍装不下的，按顺序截断并写
    「▼ 另有 N 项」。
    """
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    draw_top_bar(draw, title or TITLE, page_id, total)

    avail = BODY_BOTTOM - BODY_TOP
    chosen = None
    for style in _STYLES:
        blocks, used, n_lines = _layout_rows(draw, rows, style)
        if used <= avail:
            chosen = (blocks, used, n_lines)
            break

    dropped = 0
    if chosen is None:
        blocks, used, n_lines = _layout_rows(draw, rows, _STYLES[-1])
        limit = avail - NOTE_RESERVE
        keep, y = [], 0.0
        for b in blocks:
            if y + b["h"] > limit:
                break
            keep.append(b)
            y += b["h"]
        dropped = len(rows) - len(keep)
        chosen = (keep, y, sum(len(b["lines"]) for b in keep))

    blocks, used, n_lines = chosen
    extra, top = spread_leading(used, avail, n_lines) if not dropped else (0.0, 0.0)

    y = BODY_TOP + top
    for b in blocks:
        y += b["gap"]
        for ln in b["lines"]:
            if b["kind"] == "head":
                draw.rectangle([MARGIN_X, y + 2, MARGIN_X + 3, y + b["font"].size - 2], fill=0)
                draw.text((MARGIN_X + HEAD_MARK_W, y), ln, font=b["font"], fill=0)
            else:
                draw.text((MARGIN_X, y), ln, font=b["font"], fill=0)
            y += b["lh"] + extra

    if dropped:
        draw.text((MARGIN_X, BODY_BOTTOM - 13), f"▼ 另有 {dropped} 项 · 全文见五页简报", font=font_tiny, fill=0)

    if out_path:
        img.save(out_path)
    return img, dropped


# =====================================================================
# 🚀 主流程
# =====================================================================
def run(pages_arg="1,2,3,4,5", dry_run=False, title=None, max_age_hours=48):
    print("① 拉取仓库 004 报告页 …", flush=True)
    text004 = extract_text(fetch(SRC_004))
    d004 = parse_004(text004)
    check_freshness(d004["gen_time"], max_age_hours)

    print("② 拉取仓库 003 推送预览 …", flush=True)
    d003 = parse_003(extract_text(fetch(SRC_003)))

    print("③ 拉取仓库 007 简报规范与读数 …", flush=True)
    d007 = parse_007(fetch(SRC_007_PY), fetch(SRC_007_README))

    pages = build_pages(d004, d003, d007)

    want = [p.strip() for p in pages_arg.split(",") if p.strip()]
    meta = f"004/003/007 · {datetime.now(BEIJING):%Y-%m-%d %H:%M}"
    ok = True
    for pid in sorted(pages):
        if str(pid) not in want:
            continue
        spec = pages[pid]
        out = f"page_{pid}.png"
        img, dropped = render_page(
            spec["rows"], pid, spec["sub"], spec["tail"], meta,
            out_path=out, title=title or TITLE,
        )
        note = f"（▼ 另有 {dropped} 项）" if dropped else ""
        print(f"✅ Page {pid} → {out}{note}", flush=True)
        if not push_image(img, page_id=pid, dry_run=dry_run):
            ok = False
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="004/003/007 五页简报 → 墨水屏")
    ap.add_argument("--pages", default="1,2,3,4,5", help="推送页面（默认五页全推）")
    ap.add_argument("--title", default=None, help="覆盖顶栏文案（五页统一）")
    ap.add_argument("--max-age-hours", type=float, default=48,
                    help="004 报告新鲜度上限（小时，<=0 不检查；默认 48）")
    ap.add_argument("--dry-run", action="store_true", help="只渲染不推屏")
    args = ap.parse_args(argv)
    try:
        return run(
            pages_arg=args.pages,
            dry_run=args.dry_run,
            title=args.title,
            max_age_hours=args.max_age_hours,
        )
    except BriefError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
