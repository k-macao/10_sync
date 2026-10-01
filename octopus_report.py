#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
octopus_report —— 调用「仓库 02 · 章鱼 AI · 打氧日报」的推送页，解析成墨水屏可显示的条目。

只做三件事，全程不引入新依赖（requests 之外只用标准库）：
  1. fetch_report()  拉取仓库 02 的 output/latest.html（raw → GitHub API 兜底）
  2. parse_report()  把 HTML 拆成 18 个栏目（`<!--SPLIT-->` 分隔），每栏再拆成「标签 / 内容」条目
  3. build_pages()   按 P1~P5 排版计划，产出 5 页墨水屏要显示的条目列表

防自欺约定（沿用仓库 02 的规矩）：
  · 数字 / 结论全部来自本次真实拉到的 HTML，不另算一套、不编、不用旧数据充数；
  · 仓库 02 自己没数据的区块不会出现在这里，缺失即缺失，不猜；
  · 任何一步失败（拉不到 / 解析不出栏目 / 内容为空 / 超过新鲜度上限）一律抛
    ReportUnavailable，由调用方决定「整轮跳过、保留旧画面」而不是推一份残缺内容。
"""

import base64
import os
import re
import json
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

# 仓库 02 用北京时间（Asia/Shanghai）打时间戳，所有「新鲜度」判断统一按东八区
BEIJING_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------
# 配置区（可用环境变量覆盖，方便在 Actions 里临时切源 / 调阈值）
# ---------------------------------------------------------------------
# 仓库 02：章鱼 AI · 打氧日报
DEFAULT_REPO = os.environ.get("OCTOPUS_REPO", "k-macao/02")
DEFAULT_REF = os.environ.get("OCTOPUS_REF", "main")
DEFAULT_PATH = os.environ.get("OCTOPUS_PATH", "output/latest.html")
# 日报更新是「每天一次」级别，2 小时一次的推送里内容基本不会变。
# 超过这个小时数还没更新 → 判定 02 侧已停更，整轮跳过（不推残缺/过期内容）。
MAX_AGE_HOURS = float(os.environ.get("OCTOPUS_MAX_AGE_HOURS", "36"))
HTTP_TIMEOUT = int(os.environ.get("OCTOPUS_HTTP_TIMEOUT", "25"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 页面/区块尺寸（与 main.py 看板一致）
CANVAS_W, CANVAS_H = 400, 300


class ReportUnavailable(Exception):
    """仓库 02 的日报拉不到 / 不新鲜 / 解析不出内容 —— 调用方应整轮跳过并报错。"""


# =====================================================================
# 第一部分：极简 HTML → 树（够用即可，不引第三方解析器）
# =====================================================================
VOID_TAGS = {
    "br", "img", "hr", "meta", "link", "input", "area", "base",
    "col", "embed", "source", "track", "wbr",
}
# 这些标签会强制换行（渲染时按块处理）
BLOCK_TAGS = {
    "div", "p", "tr", "li", "ul", "ol", "table", "thead", "tbody",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote",
}
BOLD_TAGS = {"b", "strong"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = None

    @property
    def text(self):
        out = []
        _collect_text(self, out)
        return "".join(out)

    def style(self, key):
        return self.attrs.get("style", "") + ";" + self.attrs.get("bgcolor", "")

    def find_all(self, tag):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    found.append(child)
                found.extend(child.find_all(tag))
        return found

    def element_children(self):
        return [c for c in self.children if isinstance(c, Node)]


def _collect_text(node, out):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag in ("script", "style"):
                continue
            _collect_text(child, out)
        else:
            out.append(child)


class _DomBuilder(HTMLParser):
    """把 HTML 解析成一棵够用的树；注释（<!--SPLIT--> / <!--BODY-->）也进树，方便定位。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def _append(self, node):
        node.parent = self.cur
        self.cur.children.append(node)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self._append(node)
        if tag not in VOID_TAGS:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self._append(Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if data:
            # 文本节点直接存字符串（和 DOM 的 text node 一个意思）
            self.cur.children.append(data)

    def handle_comment(self, data):
        self._append(Node("#comment", {"data": data}))


def parse_dom(html_text):
    builder = _DomBuilder()
    builder.feed(html_text)
    builder.close()
    return builder.root


# =====================================================================
# 第二部分：把一个区块的 HTML 拆成「片段流」（保留是否加粗 + 表格行）
# =====================================================================
class Segment:
    """一段行内文本。bold=True 表示落在 <b>/<strong> 里（日报里 = 该栏的「内容值」）。"""

    __slots__ = ("text", "bold")

    def __init__(self, text, bold):
        self.text = text
        self.bold = bold

    def __repr__(self):
        return f"Segment({self.text!r}, bold={self.bold})"


def _flatten(node, bold, out):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag in ("script", "style"):
                continue
            if child.tag == "br":
                out.append(Segment("\n", bold))
                continue
            if child.tag in BLOCK_TAGS:
                out.append(Segment("\n", bold))
                _flatten(child, bold or child.tag in BOLD_TAGS, out)
                out.append(Segment("\n", bold))
                continue
            _flatten(child, bold or child.tag in BOLD_TAGS, out)
        else:
            if child.strip("\n") == "" and ("\n" in child):
                out.append(Segment("\n", bold))
            else:
                out.append(Segment(child, bold))


def flatten_segments(node):
    out = []
    _flatten(node, False, out)
    return [s for s in out if s.text != ""]


def segments_to_lines(segments):
    """把片段流按换行切成 [(文本, 是否加粗)] 的行列表。"""
    lines, buf, buf_bold = [], [], None
    for seg in segments:
        if seg.text == "\n":
            text = "".join(buf)
            if text.strip():
                lines.append((text.strip(), bool(buf_bold)))
            buf, buf_bold = [], None
        else:
            if buf_bold is None:
                buf_bold = seg.bold
            buf.append(seg.text)
    text = "".join(buf)
    if text.strip():
        lines.append((text.strip(), bool(buf_bold)))
    return lines


# =====================================================================
# 第三部分：数据结构
# =====================================================================
class Item:
    """墨水屏上一条要显示的内容。"""

    __slots__ = ("label", "value", "kind")

    def __init__(self, label, value, kind="kv"):
        self.label = (label or "").strip()
        self.value = (value or "").strip()
        self.kind = kind

    def __repr__(self):
        return f"Item({self.label!r}, {self.value[:40]!r}, {self.kind})"


# 日报会在标签前面挂符号（■ 明日剧本 / ⏰ 今明必看 / 💧 水位 / ⬤ 政策 / 🔎 数据底），
# 统一按「剥掉开头所有非中英文数字字符」处理，标签名才对得上排版计划。
_LABEL_LEAD = re.compile(r"^[^0-9A-Za-z㐀-䶿一-鿿぀-ヿ가-힯]+")


def normalize_label(text):
    """去掉标签前的装饰符号与空白，便于和排版计划里的标签名对上。"""
    return _LABEL_LEAD.sub("", (text or "").strip()).strip()


class Section:
    # node = 这个栏目的 DOM 节点。正文条目已被拍平（表格也变成一行行文字），
    # 财经日历要靠它拿回「表格里哪一行属于哪一天」这种结构信息。
    __slots__ = ("kicker", "title", "items", "notes", "node")

    def __init__(self, kicker, title, items, notes, node=None):
        self.kicker = kicker
        self.title = title
        self.items = items
        self.notes = notes
        self.node = node

    def find(self, *labels):
        """按标签精确取条目（忽略日报给标签加的 ■ / ⏰ / 💧 等前缀符号）。"""
        wanted = {normalize_label(l) for l in labels}
        for it in self.items:
            if normalize_label(it.label) in wanted:
                return it
        return None

    def find_prefix(self, *prefixes):
        wanted = [normalize_label(p) for p in prefixes]
        for it in self.items:
            key = normalize_label(it.label)
            if any(key.startswith(p) for p in wanted):
                return it
        return None


class Report:
    __slots__ = ("date", "generated_at", "today_sources", "total_sources",
                 "theme", "title", "sections", "raw_sha")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def section(self, kicker):
        return self.sections.get(kicker)

    def summary(self):
        bits = []
        stamp = parse_dt(self.generated_at)
        if stamp:
            bits.append(stamp.strftime("%m-%d %H:%M"))
        elif self.date:
            bits.append(str(self.date))
        if self.today_sources and self.total_sources:
            bits.append(f"当天源 {self.today_sources}/{self.total_sources}")
        return " · ".join(bits)


# =====================================================================
# 第四部分：抓取
# =====================================================================
def _decode_github_contents(payload_json):
    if not isinstance(payload_json, dict) or "content" not in payload_json:
        raise ReportUnavailable("GitHub API 返回里没有 content 字段")
    content = payload_json.get("content") or ""
    encoding = payload_json.get("encoding") or ""
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8", "replace")
    return content


def fetch_report(repo=None, ref=None, path=None, timeout=None, token=None):
    """
    拉取仓库 02 的当日推送页 HTML。
    先走 raw.githubusercontent.com（快、无需鉴权），失败再走 GitHub Contents API。
    """
    repo = repo or DEFAULT_REPO
    ref = ref or DEFAULT_REF
    path = path or DEFAULT_PATH
    timeout = timeout or HTTP_TIMEOUT
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    raw_url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    api_url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"

    errors = []
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}

    # ① raw 直链
    try:
        resp = requests.get(raw_url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and "<h2" in resp.text:
            print(f"🌐 已拉取仓库 {repo} 的 {path}（raw，{len(resp.text)} 字符）")
            return resp.text
        errors.append(f"raw 返回 {resp.status_code}（{len(resp.text)} 字符）")
    except Exception as exc:  # noqa: BLE001 - 网络异常一律兜住，交给调用方跳过
        errors.append(f"raw 异常: {exc}")

    # ② GitHub Contents API 兜底（raw 被墙 / 限流时）
    api_headers = dict(headers)
    api_headers["Accept"] = "application/vnd.github+json"
    if token:
        api_headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(api_url, headers=api_headers, timeout=timeout)
        if resp.status_code == 200:
            text = _decode_github_contents(resp.json())
            if "<h2" in text:
                print(f"🌐 已拉取仓库 {repo} 的 {path}（Contents API，{len(text)} 字符）")
                return text
        errors.append(f"Contents API 返回 {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Contents API 异常: {exc}")

    raise ReportUnavailable(
        "拉取仓库 02 的日报失败 —— " + "；".join(errors) + "（整轮跳过，保留墨水屏原有内容）"
    )


# =====================================================================
# 第五部分：解析
# =====================================================================
# 这些标记的区块是「附注 / 免责」性质，不占墨水屏正文（信息量低、重复率高）
# 只认仓库 02 固定的那几个角标词，不要凭「口径」这类字眼猜——正文明明会有「统计口径」。
NOTE_MARKERS = (
    "鲜鲜解读", "活鲜度", "规则合成", "一句话攻略", "阅读提醒", "非投资建议",
)
# 区块首行若是这些字样，视为分节标题而不是「标签 / 内容」
GROUP_HEADINGS = (
    "市场与资金", "量化与策略", "政策与日程", "资讯与情绪",
)


def _is_note(node_text):
    return any(m in node_text for m in NOTE_MARKERS)


def _labelish(text):
    """像不像一个「标签行」：短、不以句读收尾、不是完整句子。"""
    if not text:
        return False
    if len(text) > 20:
        return False
    if text[-1] in "。；;，,！!？?":
        return False
    # 纯数字/百分号开头的是内容不是标签
    return True


def _table_items(node):
    items = []
    for tr in node.find_all("tr"):
        cells = []
        for cell in tr.element_children():
            txt = " ".join(cell.text.split())
            if txt:
                cells.append(txt)
        if cells:
            items.append(Item("", " · ".join(cells), kind="head"))
    return items


def parse_block_items(node):
    """
    把一个区块 div 拆成条目：
      「非加粗短行 + 后面跟着一串加粗行」= 标签 / 内容
      其余单独成行 = 标题式内容（kind='head'）
    """
    lines = segments_to_lines(flatten_segments(node))
    items, i = [], 0
    while i < len(lines):
        text, bold = lines[i]
        if (not bold) and _labelish(text) and i + 1 < len(lines) and lines[i + 1][1]:
            label = text
            j = i + 1
            values = []
            while j < len(lines) and lines[j][1]:
                values.append(lines[j][0])
                j += 1
            items.append(Item(label, " ｜ ".join(v for v in values if v)))
            i = j
        else:
            kind = "head" if not bold and text in GROUP_HEADINGS else ("kv" if not bold else "kv")
            items.append(Item("", text, kind=kind))
            i += 1
    return items


def _find_section_containers(root):
    """
    找出每个栏目的容器节点。

    仓库 02 的日报 HTML 用 `<!--SPLIT-->` 把栏目切开，注释写在容器 div **外面**
    （`…</div>\n<!--SPLIT--><div …>`），而且整篇被 <body> / 外层 div 包了好几层。
    所以这里不认死层级：先找出所有 SPLIT 注释，再取它在父节点里的下一个兄弟元素；
    找不到兄弟（万一以后注释挪进容器内部）就退回用注释的父节点。
    """
    containers = []
    for comment in root.find_all("#comment"):
        if "SPLIT" not in comment.attrs.get("data", "").upper():
            continue
        parent = comment.parent
        if parent is None:
            continue
        idx = parent.children.index(comment)
        nxt = None
        for sibling in parent.children[idx + 1:]:
            if isinstance(sibling, Node):
                nxt = sibling
                break
        target = nxt if nxt is not None else parent
        if target is not root and target not in containers:
            containers.append(target)
    return containers


def parse_report(html_text):
    """把日报 HTML 解析成 Report。解析不出内容 → 抛 ReportUnavailable。"""
    if not html_text or "<h2" not in html_text:
        raise ReportUnavailable("拉到的内容不是日报 HTML（缺少栏目标题）")

    root = parse_dom(html_text)

    meta = {}
    for node in root.find_all("meta"):
        name = node.attrs.get("name")
        if name and name.startswith("octopus-"):
            meta[name] = node.attrs.get("content", "")
    for node in root.find_all("title"):
        title = " ".join(node.text.split())
        if title:
            meta["title"] = title
            break

    containers = _find_section_containers(root)
    if not containers:
        raise ReportUnavailable("日报里没有 `<!--SPLIT-->` 栏目分隔标记（HTML 结构可能已改版）")

    sections = {}
    for child in containers:
        h2 = None
        body_seen = False
        for c in child.children:
            if isinstance(c, Node) and c.tag == "#comment" and c.attrs.get("data", "").strip() == "BODY":
                body_seen = True
                continue
            if not body_seen:
                if isinstance(c, Node) and c.tag == "h2" and h2 is None:
                    h2 = c
                continue
        if h2 is None:
            continue
        kicker = ""
        for c in child.children:
            if isinstance(c, Node) and c is not h2 and c.tag == "div":
                txt = " ".join(c.text.split())
                if txt and not txt.startswith("http"):
                    kicker = txt
                    break
        section_title = " ".join(h2.text.split())

        # 正文 = <!--BODY--> 之后的顶层子节点
        items, notes = [], []
        seen_body = False
        for c in child.children:
            if isinstance(c, Node) and c.tag == "#comment" and c.attrs.get("data", "").strip() == "BODY":
                seen_body = True
                continue
            if not seen_body or not isinstance(c, Node):
                continue
            if c.tag in ("script", "style", "small"):
                continue
            text = " ".join(c.text.split())
            if not text:
                continue
            if c.tag == "table":
                items.extend(_table_items(c))
                continue
            # 先按块拆成条目，再逐条判断是不是「附注」——
            # 整块判附注会误伤：AI 速览那一大块里 4 个分节 + 阅读提醒同在一个 div。
            for item in parse_block_items(c):
                if _is_note(item.label) or _is_note(item.value):
                    notes.append(" ".join(x for x in (item.label, item.value) if x))
                else:
                    items.append(item)

        if not items and not notes:
            continue
        # 栏目头（「00 · SHORT CARD」）本身也可能落在 items 里，剔掉
        items = [it for it in items if not (it.label == kicker or it.value == kicker)]
        sections[kicker] = Section(kicker, section_title, items, notes, node=child)

    if not sections:
        raise ReportUnavailable("日报里没有解析出任何栏目（HTML 结构可能已改版）")

    import hashlib
    report = Report(
        date=meta.get("octopus-report-date", ""),
        generated_at=meta.get("octopus-generated-at", ""),
        today_sources=meta.get("octopus-today-sources", ""),
        total_sources=meta.get("octopus-total-sources", ""),
        theme=meta.get("octopus-theme", ""),
        title=meta.get("title", ""),
        sections=sections,
        raw_sha=hashlib.sha1(html_text.encode("utf-8", "replace")).hexdigest()[:16],
    )
    return report


# =====================================================================
# 第六部分：新鲜度（防「推一份过期日报」）
# =====================================================================
def parse_dt(value):
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
        except (ValueError, TypeError):
            continue
    return None


def beijing_now():
    """仓库 02 的时间戳是北京时间（Asia/Shanghai），这里统一按东八区比对。"""
    return datetime.now(timezone.utc).astimezone(BEIJING_TZ)


def check_freshness(report, max_age_hours=None, now=None):
    """返回 (是否可用, 说明)。过期 → False（调用方整轮跳过，不推过期内容）。"""
    limit = max_age_hours if max_age_hours is not None else MAX_AGE_HOURS
    stamp = parse_dt(report.generated_at) or parse_dt(report.date)
    if stamp is None:
        # 没有时间戳就不硬猜，只要求内容非空
        return True, "日报没有时间戳，跳过新鲜度校验（仅按内容非空判断）"
    now = now or beijing_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    age_h = (now - stamp).total_seconds() / 3600.0
    if age_h > limit:
        return False, (
            f"日报生成于 {stamp:%Y-%m-%d %H:%M}，已过 {age_h:.1f} 小时"
            f"（上限 {limit:.0f} 小时）→ 判定仓库 02 已停更，整轮跳过、保留墨水屏原有内容"
        )
    if age_h < -1:  # 未来时间戳：容许 1 小时的时区/时钟误差
        return False, (
            f"日报时间戳 {stamp:%Y-%m-%d %H:%M} 比当前时间还晚 {-age_h:.1f} 小时，"
            f"疑似时钟/时区异常 → 整轮跳过、保留墨水屏原有内容"
        )
    return True, f"日报生成于 {stamp:%Y-%m-%d %H:%M}，距今 {age_h:.1f} 小时（上限 {limit:.0f} 小时）"


# =====================================================================
# 第七部分：排版计划 —— 5 页各放哪些内容
# =====================================================================
# 每页 = (页码, 栏目标题, 排版小标题, 选取规则)
#   P1 短线速查卡：30 秒读完的结论面（定调 / 明日剧本 / 七日风 / 水位 / 板块强弱 / 数据底 + 新手小抄）
#   P2 今日预判：   方向与关键数字（量化预测 / 七日预测 / 倾向 / 核心判断 / A股 / 港股 / 美股）
#   P3 AI 速览·上： 市场与资金 + 量化与策略
#   P4 AI 速览·下： 政策与日程 + 资讯与情绪
#   P5 时间节点：   03 · ECON CALENDAR 的今明 ★★★ 时间点逐条 + 30 天窗口摘要 + 政策 / 数据口径
# 「数据底」不在这儿重复：页脚已经固定写「当天源 15/17 · 数字与正文同源」，
# 腾出来的那行留给新手小抄。
P1_KEEP = [
    "今日定调", "明日剧本", "七日风", "板块强弱", "水位", "风声",
]
# 「今明必看」贴在 P5 的日历页顶部：那里同样的事有逐条时间点，这条是它的摘要。
P1_WHEN_LABEL = "今明必看"
# 顺序即优先级：墨水屏放不下时从尾部整条舍弃，所以先放「今天怎么办事」的，
# 最后才放「为什么要打折看」的口径类长句。
P2_KEEP = [
    "量化预测", "七日预测", "市场倾向", "核心判断", "A股", "港股", "美股",
]
# 注：P2 的「政策 / 数据提示」不再单列一条 —— 政策方向由 P4 的【深海肥蓝鲸】政策因子
# 覆盖，行情滞后的口径写在 P2 的「核心判断」里（“…美股行情滞后未计入”）。
DIGEST_GROUPS = {
    3: ("市场与资金", "量化与策略"),
    4: ("政策与日程", "资讯与情绪"),
}
# 财经日历（第 5 页的数据源）
CALENDAR_KICKER = "03 · ECON CALENDAR"
# P4 的「政策与日程」里有一行【探照安康鱼】时间节点摘要，日历页会把它全文展开；
# 有日历页时就把这行剔掉，别让同一件事占两张屏。
CALENDAR_DIGEST_DUP = "【探照安康鱼】时间节点"
# 窗口摘要按「今天怎么办事」的价值排序（顺序即优先级，放不下从尾部舍弃）：
# 具体的央行议息日期 > 最密集日（解析时落在附注里）> 窗口规模 > 关键读数
# 见 _calendar_window_items()。
# 新手三句话：固定小抄，压成一条
NEWBIE_MARKS = ("①", "②", "③")


def _pick(section, labels):
    if section is None:
        return []
    out = []
    for label in labels:
        item = section.find(label) or section.find_prefix(label)
        if item is not None and item.value:
            out.append(item)
    return out


def _newbie_line(section):
    """把「新手三句话」三条小抄压成一行，固定文案按日报原样取，不改写。"""
    if section is None:
        return None
    picked = [it for it in section.items if it.label[:1] in NEWBIE_MARKS and it.value]
    if not picked:
        return None
    return Item("新手小抄", " · ".join(f"{it.label} {it.value}" for it in picked))


def _group_value(section, group, skip_prefixes=()):
    """
    AI 速览里按分节（市场与资金 / 量化与策略 / 政策与日程 / 资讯与情绪）取内容。
    解析时同一分节的多个摘要已经用 ' ｜ ' 串在一起，这里再拆回一条条。
    skip_prefixes 里的条目已有专门的一页在讲（例如时间节点 → 第 5 页），不再重复上屏。
    """
    item = section.find(group) if section is not None else None
    if item is None or not item.value:
        return []
    out = []
    for part in item.value.split(" ｜ "):
        part = part.strip()
        if not part or any(part.startswith(p) for p in skip_prefixes):
            continue
        out.append(Item("", part, kind="head"))
    return out


def _headline_of(section, default_label="核心判断", split_label=None):
    """
    取栏目的第一行大结论（日报里那行加大加粗的整句）。
    split_label 给了就把「今日定调 中性（信号 -1）」这种
    「标签 + 内容」挤在一行的情况拆成标签 / 内容两段。
    """
    if section is None:
        return None
    for it in section.items:
        if not it.label and it.value:
            value = it.value
            if split_label:
                m = re.match(rf"^[^\w]*{re.escape(split_label)}\s*(.+)$", value, re.S)
                if m:
                    return Item(split_label, m.group(1).strip(), kind="head")
            return Item(default_label, value, kind="head")
    return None


def _when_item(card):
    """「今明必看」这条（P1 的结论面 / P5 日历页共用，谁有地方谁放）。"""
    if card is None:
        return None
    item = card.find(P1_WHEN_LABEL) or card.find_prefix(P1_WHEN_LABEL)
    if item is None or not item.value:
        return None
    return Item(P1_WHEN_LABEL, item.value)


def _clean_cell(text):
    return " ".join((text or "").split())


def _calendar_rows(section):
    """
    从财经日历的表格里取「日期 / 时间 / ★★★ 事件」。

    日报表格只在每天第一行写日期（如「09-30 周三今天」），后面几行日期列是空的，
    所以日期必须按行顺序继承 —— 否则「20:15 ADP」这种行就不知道该算今天还是明天。
    返回 [{"date": "09-30 周三", "tag": "今天", "time": "09:30",
           "country": "中国", "name": "非制造业PMI:商务活动", "month": "9月"}]。
    """
    if section is None or section.node is None:
        return []
    rows, cur_date, cur_tag = [], "", ""
    for table in section.node.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [_clean_cell(c.text) for c in tr.element_children()]
            if len(cells) < 3:
                continue
            date_cell, time_cell, event_cell = cells[0], cells[1], cells[2]
            if date_cell in ("日期", "时间"):            # 表头行
                continue
            if date_cell:
                m = re.match(
                    r"^(\d{2}-\d{2})\s*(周[一二三四五六日])?\s*(今天|明天|昨天)?\s*$",
                    date_cell,
                )
                if not m:
                    continue
                cur_date = m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
                cur_tag = m.group(3) or ""
            if not time_cell or not event_cell or not cur_date:
                continue
            parsed = _parse_calendar_event(event_cell)
            if parsed is None:
                continue
            country, name, month = parsed
            rows.append({
                "date": cur_date, "tag": cur_tag, "time": time_cell,
                "country": country, "name": name, "month": month,
            })
    return rows


def _parse_calendar_event(text):
    """「★★★ 中国 · 非制造业PMI:商务活动 · 9月 · 数据」→ (国家, 事件名, 月份)。"""
    raw = _clean_cell(text)
    raw = re.sub(r"^★+\s*", "", raw)                    # 星级：整张表都是 ★★★，不用重复占位
    parts = [p.strip() for p in raw.split("·") if p.strip()]
    if not parts:
        return None
    month = ""
    if len(parts) >= 2 and re.fullmatch(r"\d+月", parts[-2]):
        month = parts.pop(-2)
    if parts and parts[-1] in ("数据", "事件", "会议", "报告", "讲话"):
        parts.pop()
    if not parts:
        return None
    country = parts[0]
    name = ":".join(parts[1:]) if len(parts) > 1 else ""
    if not name:
        return None
    return country, name, month


def _merge_names(names):
    """
    同一时刻同一国家的多条事件压成一个名字（ISM:PMI 一口气 7 条就是这么来的）：
      ISM:PMI:制造业:季调 ×7 → ISM:PMI:制造业/就业/新订单/产出/物价/自有库存/供应商交付:季调
    只做「公共前后缀」这种保守合并，不认识的写法原样用「、」连起来，绝不改写内容。
    """
    names = list(dict.fromkeys(n for n in names if n))
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    toks = [n.split(":") for n in names]
    pre = []
    while all(len(t) > len(pre) for t in toks) and len({t[len(pre)] for t in toks}) == 1:
        pre.append(toks[0][len(pre)])
    suf = []
    while (
        all(len(t) > len(pre) + len(suf) for t in toks)
        and len({t[-1 - len(suf)] for t in toks}) == 1
    ):
        suf.append(toks[0][-1 - len(suf)])
    suf.reverse()
    if not pre and not suf:
        # 没有任何公共前后缀 = 本来就是两件不同的事，用顿号并列（“/” 留给同一指标的不同口径）
        return "、".join(names)
    mids = []
    for t in toks:
        mid = t[len(pre):len(t) - len(suf)]
        text = ":".join(mid)
        if text and text not in mids:
            mids.append(text)
    if not mids:
        return names[0]
    return ":".join(pre + ["/".join(mids)] + suf)


def _calendar_day_groups(rows):
    """
    一天一条：「日期分节 + 该天所有时间点合并成一段正文」。

    同一天同一时刻同一国家的多条事件先并成一条（ISM:PMI 一口气 7 条就是这么来的），
    再按时间串起来 —— 比「一条时刻一个条目」省下整两行，小屏上就是能不能多放一条摘要的差别。
    """
    days = {}
    for r in rows:
        day = days.setdefault((r["date"], r["tag"]), {})
        day.setdefault((r["time"], r["country"]), []).append((r["name"], r["month"]))

    out = []
    for (date, tag), slots in days.items():
        head = " · ".join(x for x in (tag, date) if x)
        parts = []
        for (time, country), events in slots.items():
            names = [n for n, _ in events]
            months = [m for _, m in events]
            if len(set(months)) == 1 and months[0]:
                # 同一时刻的多条事件月份一致，只挂一个「（9月）」，别每条都写一遍
                text = f"{_merge_names(names)}（{months[0]}）"
            elif any(months):
                # 月份不一致（GDP 终值 6月 / PCE 8月）：逐条带上，别让人误以为是同一个月
                text = "/".join(f"{n}（{m}）" if m else n for n, m in events)
            else:
                text = _merge_names(names)
            parts.append(f"{time} " + " ".join(x for x in (country, text) if x))
        out.append(Item(head, "；".join(parts)))
    return out


def _busiest_day(section):
    """「最密集日」在日报里被归为附注（句尾带「规则合成」字样），单独捞回来当一条。"""
    if section is None:
        return None
    for note in section.notes:
        if "最密集日" not in note:
            continue
        # 末尾的「（规则合成，非方向判断）」是口径说明，不上小屏
        text = re.split(r"（规则合成[^）]*）", note)[0].strip(" ·；;")
        text = re.sub(r"^最密集日\s*", "", text)
        if text:
            return Item("最密集日", text)
    return None


def _calendar_window_items(section):
    """30 天窗口摘要：央行议息 / 重要会议（具体日期最值钱）→ 最密集日 → 窗口规模 → 关键读数。"""
    if section is None:
        return []
    out = _pick(section, ("央行议息 / 重要会议",))
    busiest = _busiest_day(section)
    if busiest is not None:
        out.append(busiest)
    return out


def build_pages(report, fallback_from_disk=False):
    """
    产出 5 页内容：{页码: {"subtitle": 小标题, "items": [Item...]}}
    缺内容的页会退到相邻栏目，保证 5 页都有东西看；实在没有就抛错（不推空白屏）。
    """
    card = report.section("00 · SHORT CARD") or _find_section_by(report, "SHORT CARD")
    digest = report.section("01 · AI DIGEST") or _find_section_by(report, "AI DIGEST")
    forecast = report.section("02 · FORECAST") or _find_section_by(report, "FORECAST")
    calendar = report.section(CALENDAR_KICKER) or _find_section_by(report, "ECON CALENDAR")

    rows = _calendar_rows(calendar)
    window = _calendar_window_items(calendar)
    has_calendar = bool(rows or window)

    pages = {}

    # ---- Page 1：短线速查卡（结论面） ----
    p1 = _pick(card, P1_KEEP)
    hl = _headline_of(card, split_label="今日定调")
    if hl:
        p1 = [Item("今日定调", hl.value, kind="head")] + p1
    newbie = _newbie_line(card)
    if newbie:
        p1.append(newbie)
    pages[1] = {
        "subtitle": "30 秒读完 · 结论面",
        "section_title": card.title if card else "短线速查卡",
        # 结论面条目多（定调 / 剧本 / 七日风 / 今明必看 / 水位 / 新手小抄），
        # 用小一号字换行数；但仍然「宁可整条舍弃，绝不把句子截半」。
        "density": "tight",
        "items": p1,
    }

    # ---- Page 2：今日预判（只留数字面） ----
    p2 = _pick(forecast, P2_KEEP)
    hl2 = _headline_of(forecast)
    if hl2 and p2 and normalize_label(p2[0].label) != normalize_label(hl2.label):
        p2 = [hl2] + p2
    pages[2] = {
        "subtitle": "方向与关键数字",
        "section_title": forecast.title if forecast else "今日预判",
        "density": "normal",
        "items": p2,
    }

    # ---- Page 3 / 4：AI 全篇速览（按分节切开） ----
    for page_id, groups in DIGEST_GROUPS.items():
        items = []
        for g in groups:
            skip = (CALENDAR_DIGEST_DUP,) if (has_calendar and g == "政策与日程") else ()
            got = _group_value(digest, g, skip_prefixes=skip)
            if got:
                items.append(Item(g, "", kind="group"))
                items.extend(got)
        pages[page_id] = {
            "subtitle": f"{'上' if page_id == 3 else '下'} · " + " / ".join(groups),
            "section_title": digest.title if digest else "AI 全篇速览",
            # 速览本身已是「每栏一行摘要」，条目多，用紧凑字号换覆盖面
            "density": "compact",
            "items": items,
        }

    # ---- Page 5：时间节点（今明 ★★★ 逐条 + 30 天窗口摘要 + 政策/口径） ----
    p5 = _calendar_day_groups(rows)
    if window:
        p5.append(Item("30 天窗口摘要", "", kind="group"))
        p5.extend(window)
    pages[5] = {
        "subtitle": "今明 ★★★ 时间点 · 30 天窗口摘要",
        "section_title": calendar.title if calendar else "时间节点",
        # 日历是一天一段整句（今天 09:30 …；20:15 …），容不得截半句 —— 用 normal
        "density": "normal",
        "items": p5,
    }

    # ---- 兜底：某页空了，就从别的栏目挪内容，绝不留白屏 ----
    spares = (
        _pick(digest, list(DIGEST_GROUPS.get(3, ())) + list(DIGEST_GROUPS.get(4, ())))
        or _group_value(digest, DIGEST_GROUPS[3][0])
        or [it for it in (forecast.items if forecast else []) if it.value][:8]
        or [it for it in (card.items if card else []) if it.value][:8]
    )
    for page_id in (1, 2, 3, 4, 5):
        if pages[page_id]["items"]:
            continue
        if spares:
            print(f"⚠️ Page {page_id} 原本无内容，已从相邻栏目补 {len(spares)} 条")
            pages[page_id]["items"] = list(spares)

    empty = [pid for pid in (1, 2, 3, 4, 5) if not pages[pid]["items"]]
    if len(empty) >= 3:
        raise ReportUnavailable(
            f"日报 5 页里有 {len(empty)} 页解析不出内容（{empty}）→ 整轮跳过、保留墨水屏原有内容"
        )
    return pages


def _find_section_by(report, keyword):
    for kicker, section in report.sections.items():
        if keyword in kicker:
            return section
    return None


# =====================================================================
# 第八部分：内容指纹（用于「内容没变就别重复刷屏」）
# =====================================================================
def report_fingerprint(report, pages):
    """页面内容 + 生成时间的稳定指纹；两次一致说明日报没更新，可跳过推送。"""
    payload = {
        "date": report.date,
        "generated_at": report.generated_at,
        "today_sources": report.today_sources,
        "pages": {
            str(pid): [[it.label, it.value, it.kind] for it in p["items"]]
            for pid, p in sorted(pages.items())
        },
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    import hashlib
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# =====================================================================
if __name__ == "__main__":
    # 自检：python3 octopus_report.py [html路径|URL]
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else None
    text = open(src, encoding="utf-8").read() if src and os.path.exists(src) else fetch_report()
    rep = parse_report(text)
    ok, msg = check_freshness(rep)
    print(f"📄 {rep.title} | {rep.summary()} | 主题 {rep.theme}")
    print(f"🕒 新鲜度：{'✅' if ok else '❌'} {msg}")
    print(f"🗂 栏目 {len(rep.sections)} 个：{'、'.join(list(rep.sections)[:6])} …")
    pgs = build_pages(rep)
    for pid in sorted(pgs):
        page = pgs[pid]
        print(f"\n──── Page {pid} · {page['subtitle']} · {len(page['items'])} 条 ────")
        for it in page["items"]:
            print(f"  [{it.kind:5}] {it.label} :: {it.value[:120]}")
    print(f"\n🔑 指纹：{report_fingerprint(rep, pgs)}")
