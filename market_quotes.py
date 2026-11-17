#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_quotes —— 五页全球行情数据面板的数据层（A / 港 / 韩 / 日 / 美）。

五页严格按市场顺序分页：
    Page 1  A（A股）  上证指数、深证成指、创业板指、沪深300、科创50（人民币）
    Page 2  港（港股） 恒生指数、恒生科技、恒生国企（港元）
    Page 3  韩（韩股） KOSPI、KOSDAQ（韩元）
    Page 4  日（日股） 日经225、东证指数（日元）
    Page 5  美（美股） 道琼斯、纳斯达克、标普500（美元）

每条指数字段（全部来自公开行情接口原始字段，只正名、不换算）：
    last        最新点位
    change      较昨收涨跌额（日内点位变化）
    pct         较昨收涨跌幅（%）
    delta       较上一轮抓取的点位变化（默认 5 分钟变化；首轮无前值时为 None）
    delta_pct   较上一轮抓取的变化幅度（%）
    prev_close  昨收
    open        今开
    high / low  日内最高 / 最低
    amplitude   日内振幅（%）
    volume      成交量（股）
    turnover    成交额（本币：人民币 / 港元 / 韩元；美股与日股无可靠成交额，置 None）
    time        行情时间（带交易所时区；展示统一转北京时间）
    status      市场状态（OPEN / CLOSE / ""）

公开免密钥数据源（7 路并发互备，单源故障不影响其余市场）：
    1. 腾讯证券 qt.gtimg.cn          A / 港（r_hk* 实时）/ 美 全字段；日韩实时点位与涨跌
    2. 新浪财经 hq.sinajs.cn         港股成交股数 + 实时全字段；A / 美独立备用源（需 Referer）
    3. Naver 金融 domestic/index     KOSPI / KOSDAQ 实时全字段（含成交量、成交额）
    4. Naver 金融 worldstock/index   日经225 / 东证指数全字段（东京交易所延迟约 15 分钟）
    5. 新浪国际指数 int_*            港 / 日 / 韩 / 美 指数备用源（纯文本，无成交量）
    6. 网易财经 api.money.126.net     A 股 5 大指数备用源（JSONP，纯文本可解析）
    7. 东方财富 push2.eastmoney.com   A / 港 / 美 指数备用源（JSON，成交额含股数）

聚合规则（多源一致性投票）：
    · 同一指数取「最新且最可靠」的两路为主备：主源补字段，备份只补缺失字段；
    · 两路点位偏差在容差内视为一致，直接采用主源；偏差超容差（如环球源口径异常、
      延迟源卡住）则按「时间更新者优先、时间相同优先主源」的规则取舍；
    · 所有源全部缺失时才显示 --，绝不推空屏覆盖上一版内容。

零第三方依赖；解析与状态演进全部是纯函数，便于离线回归测试。
"""
from __future__ import annotations

import copy
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FIXTURE_DIR = Path(__file__).resolve().parent / "tests" / "fixtures"

TZ_CN = ZoneInfo("Asia/Shanghai")
TZ_HK = ZoneInfo("Asia/Hong_Kong")
TZ_KR = ZoneInfo("Asia/Seoul")
TZ_JP = ZoneInfo("Asia/Tokyo")
TZ_US = ZoneInfo("America/New_York")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FETCH_TIMEOUT = 8
FETCH_ATTEMPTS = 2

# 腾讯港股必须带 r_ 前缀取实时行情（不带 r_ 的 hkHSI / hkHSTECH 延迟 15 分钟）；
# hkHSCEI 额外附带作兜底，仅当 r_hkHSCEI 与新浪 rt_hkHSCEI 都缺失时才回退。
TENCENT_URL = (
    "https://qt.gtimg.cn/q="
    "sh000001,sz399001,sz399006,sh000300,sh000688,"
    "r_hkHSI,r_hkHSTECH,r_hkHSCEI,hkHSCEI,"
    "usDJI,usIXIC,usINX,"
    "gzN225,gzKS11,gzKOSDAQ"
)
TENCENT_RT_PREFIX = "r_"
TENCENT_DELAYED_PREFIX = "delayed_"

SINA_URL = (
    "https://hq.sinajs.cn/list="
    "rt_hkHSI,rt_hkHSTECH,rt_hkHSCEI,"
    "sh000001,sz399001,sz399006,sh000300,sh000688,"
    "gb_$dji,gb_$ixic,gb_$inx"
)
SINA_REFERER = "https://finance.sina.com.cn/"

NAVER_KR_URL = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
NAVER_JP_URL = "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225,.TOPX"

# 新浪国际指数（纯文本，形如 var hq_str_int_dji="道琼斯,47916.57,85.09,0.18";）：
# 只有「点位 / 涨跌额 / 涨跌幅」三项，用作港/日/韩/美指数的轻量备用源。
SINA_INTL_URL = (
    "https://hq.sinajs.cn/list="
    "int_hangseng,int_nikkei,int_kospi,int_dji,int_nasdaq,int_sp500"
)
SINA_INTL_MAP = {
    "int_hangseng": "hkHSI",
    "int_nikkei": "N225",
    "int_kospi": "KOSPI",
    "int_dji": "usDJI",
    "int_nasdaq": "usIXIC",
    "int_sp500": "usINX",
}

# 网易财经 A 股指数（JSONP，形如 _ntes_quote_callback({"0000001":{"price":...}});）：
# A 股 5 大指数的稳定备用源，字段含今开/昨收/最高/最低/成交额，无成交股数。
NETEASE_URL = (
    "https://api.money.126.net/data/feed/"
    "0000001,1399001,1399006,0000300,0000688?callback=_ntes_quote_callback"
)
NETEASE_MAP = {
    "0000001": "sh000001",
    "1399001": "sz399001",
    "1399006": "sz399006",
    "0000300": "sh000300",
    "0000688": "sh000688",
}

# 东方财富 push2（JSON）：A / 港 / 美 指数备用源，成交额（f6 元）与成交量（f5 手/股）可用。
EASTMONEY_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&invt=2&fields=f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18"
    "&secids=1.000001,0.399001,0.399006,1.000300,1.000688,"
    "100.HSI,124.HSTECH,100.HSCEI,100.DJIA,100.NDX,100.SPX"
)
EASTMONEY_MAP = {
    "1.000001": "sh000001",
    "0.399001": "sz399001",
    "0.399006": "sz399006",
    "1.000300": "sh000300",
    "1.000688": "sh000688",
    "100.HSI": "hkHSI",
    "124.HSTECH": "hkHSTECH",
    "100.HSCEI": "hkHSCEI",
    "100.DJIA": "usDJI",
    "100.NDX": "usIXIC",
    "100.SPX": "usINX",
}

SOURCE_TENCENT = "tencent"
SOURCE_SINA = "sina"
SOURCE_NAVER_KR = "naver_kr"
SOURCE_NAVER_JP = "naver_jp"
SOURCE_SINA_INTL = "sina_intl"
SOURCE_NETEASE = "netease"
SOURCE_EASTMONEY = "eastmoney"

SOURCE_LABELS = {
    SOURCE_TENCENT: "腾讯证券",
    SOURCE_SINA: "新浪财经",
    SOURCE_NAVER_KR: "Naver金融(韩)",
    SOURCE_NAVER_JP: "Naver金融(日)",
    SOURCE_SINA_INTL: "新浪国际指数",
    SOURCE_NETEASE: "网易财经",
    SOURCE_EASTMONEY: "东方财富",
}

# 点位一致性容差：两路源的同一指数偏差在容差内视为同一笔行情。
# 各市场波动率不同：A 股/美股取 1%，港股/韩股 1.5%，日经环球源口径差异稍大取 3%（与原 N225 阈值一致）。
SOURCE_TOLERANCE = 0.01

# 日经225：盘中优先用腾讯实时点位+涨跌，但若与 Naver 偏差 >3% 视为环球源异常，改信 Naver。
N225_SOURCE_TOLERANCE = 0.03
# 各市场点位一致性容差（两路源偏差在此范围内视为同一笔行情，取主源）
MARKET_TOLERANCE = {
    "A": 0.01,
    "港": 0.015,
    "韩": 0.015,
    "日": 0.03,
    "美": 0.01,
}
MAX_ABS_PCT = 50.0
MAX_PRICE_VALUE = 1e9
MAX_PCT_VALUE = 1e4
MAX_AMOUNT_VALUE = 1e18
EMPTY_TEXT = "--"

SINA_US_MAP = {
    "gb_$dji": "usDJI",
    "gb_$ixic": "usIXIC",
    "gb_$inx": "usINX",
}

# 成交量单位换算（不同源口径不同，统一折算成「股」）。
# 注意：网易与东方财富返回的已经是成交量「股数」（新浪深市口径同样是股数），不要再乘 100。


# =====================================================================
# 数据模型与五页规格（严格按：1=A，2=港，3=韩，4=日，5=美）
# =====================================================================
@dataclass(frozen=True)
class RowSpec:
    key: str       # 稳定唯一键
    market: str    # 市场短标：A / 港 / 韩 / 日 / 美
    name: str      # 显示名称


@dataclass(frozen=True)
class MarketSpec:
    page_id: int
    code: str               # A / 港 / 韩 / 日 / 美
    title: str              # A股行情 / 港股行情 / 韩股行情 / 日股行情 / 美股行情
    currency: str           # 计价币种全称（只正名、不换算）
    tz: ZoneInfo            # 交易所当地时区（判定交易日切换）
    summary_label: str      # 页脚汇总项名称
    summary_kind: str       # "turnover" | "volume"
    summary_keys: Tuple[str, ...]
    rows: Tuple[RowSpec, ...]
    note: str = ""          # 页脚补充说明（如东证延迟提示）


MARKET_PAGES: Tuple[MarketSpec, ...] = (
    MarketSpec(
        page_id=1,
        code="A",
        title="A股行情",
        currency="人民币",
        tz=TZ_CN,
        summary_label="沪深成交",
        summary_kind="turnover",
        summary_keys=("sh000001", "sz399001"),
        rows=(
            RowSpec("sh000001", "A", "上证指数"),
            RowSpec("sz399001", "A", "深证成指"),
            RowSpec("sz399006", "A", "创业板指"),
            RowSpec("sh000300", "A", "沪深300"),
            RowSpec("sh000688", "A", "科创50"),
        ),
    ),
    MarketSpec(
        page_id=2,
        code="港",
        title="港股行情",
        currency="港元",
        tz=TZ_HK,
        summary_label="主板成交",
        summary_kind="turnover",
        summary_keys=("hkHSI",),
        rows=(
            RowSpec("hkHSI", "港", "恒生指数"),
            RowSpec("hkHSTECH", "港", "恒生科技"),
            RowSpec("hkHSCEI", "港", "恒生国企"),
        ),
    ),
    MarketSpec(
        page_id=3,
        code="韩",
        title="韩股行情",
        currency="韩元",
        tz=TZ_KR,
        summary_label="两板成交",
        summary_kind="turnover",
        summary_keys=("KOSPI", "KOSDAQ"),
        rows=(
            RowSpec("KOSPI", "韩", "KOSPI"),
            RowSpec("KOSDAQ", "韩", "KOSDAQ"),
        ),
    ),
    MarketSpec(
        page_id=4,
        code="日",
        title="日股行情",
        currency="日元",
        tz=TZ_JP,
        summary_label="两指成交",
        summary_kind="volume",
        summary_keys=("N225", "TOPX"),
        rows=(
            RowSpec("N225", "日", "日经225"),
            RowSpec("TOPX", "日", "东证指数"),
        ),
        note="东证延迟15m",
    ),
    MarketSpec(
        page_id=5,
        code="美",
        title="美股行情",
        currency="美元",
        tz=TZ_US,
        summary_label="三指成交",
        summary_kind="volume",
        summary_keys=("usDJI", "usIXIC", "usINX"),
        rows=(
            RowSpec("usDJI", "美", "道琼斯"),
            RowSpec("usIXIC", "美", "纳斯达克"),
            RowSpec("usINX", "美", "标普500"),
        ),
    ),
)

MARKET_ORDER: Tuple[str, ...] = tuple(m.code for m in MARKET_PAGES)  # ("A", "港", "韩", "日", "美")
MARKET_BY_PAGE: Dict[int, MarketSpec] = {m.page_id: m for m in MARKET_PAGES}
MARKET_BY_CODE: Dict[str, MarketSpec] = {m.code: m for m in MARKET_PAGES}
ALL_ROWS: Tuple[RowSpec, ...] = tuple(r for m in MARKET_PAGES for r in m.rows)


@dataclass
class Quote:
    last: Optional[float] = None
    change: Optional[float] = None        # 较昨收涨跌额
    pct: Optional[float] = None           # 较昨收涨跌幅（%）
    prev_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    amplitude: Optional[float] = None     # 日内振幅（%）
    volume: Optional[float] = None        # 成交量（股）
    turnover: Optional[float] = None      # 成交额（本币元）
    time: Optional[datetime] = None       # 行情时刻（带时区）
    status: str = ""                      # OPEN / CLOSE / ""
    source: str = ""
    delayed: bool = False
    # 分时变化（由 apply_delta_state 根据上一轮同交易日行情计算）
    prev_last: Optional[float] = None
    delta: Optional[float] = None         # 较上轮点位变化
    delta_pct: Optional[float] = None     # 较上轮变化百分比

    @property
    def valid(self) -> bool:
        return self.last is not None and self.pct is not None

    @property
    def direction(self) -> int:
        """涨跌方向：+1 涨 / -1 跌 / 0 平或未知（以显示精度为准）。"""
        if self.pct is not None and math.isfinite(self.pct):
            rounded = round(self.pct, 2)
            if rounded > 0:
                return 1
            if rounded < 0:
                return -1
        if self.change is not None and math.isfinite(self.change):
            rounded = round(self.change, 2)
            if rounded > 0:
                return 1
            if rounded < 0:
                return -1
        return 0


@dataclass
class PanelRow:
    spec: RowSpec
    quote: Quote = field(default_factory=Quote)

    @property
    def valid(self) -> bool:
        return self.quote.valid


@dataclass
class MarketPageData:
    spec: MarketSpec
    rows: List[PanelRow]
    summary_value: Optional[float] = None
    summary_delta: Optional[float] = None

    @property
    def live_rows(self) -> int:
        return sum(1 for r in self.rows if r.valid)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def as_of(self) -> Optional[datetime]:
        times = [r.quote.time for r in self.rows if r.valid and r.quote.time]
        return max(times) if times else None

    def compute_summary(self) -> Optional[float]:
        by_key = {r.spec.key: r.quote for r in self.rows}
        attr = "turnover" if self.spec.summary_kind == "turnover" else "volume"
        vals = []
        for k in self.spec.summary_keys:
            q = by_key.get(k)
            val = getattr(q, attr, None) if q and q.valid else None
            if val is None or val <= 0:
                return None
            vals.append(val)
        return float(sum(vals)) if vals else None


@dataclass
class PanelData:
    pages: Dict[int, MarketPageData]
    sources: Dict[str, bool]
    demo: bool = False

    @property
    def rows(self) -> List[PanelRow]:
        out: List[PanelRow] = []
        for pid in sorted(self.pages):
            out.extend(self.pages[pid].rows)
        return out

    @property
    def live_rows(self) -> int:
        return sum(p.live_rows for p in self.pages.values())

    @property
    def total_rows(self) -> int:
        return sum(p.total_rows for p in self.pages.values())

    @property
    def live_pages(self) -> int:
        return sum(1 for p in self.pages.values() if p.live_rows > 0)

    @property
    def as_of(self) -> Optional[datetime]:
        times = [p.as_of for p in self.pages.values() if p.as_of is not None]
        return max(times) if times else None


# =====================================================================
# 数值清洗与格式化
# =====================================================================
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def _to_float(text) -> Optional[float]:
    if text is None:
        return None
    cleaned = str(text).replace(",", "").strip(" \t\r\n")
    if not _DECIMAL.fullmatch(cleaned):
        return None
    val = float(cleaned)
    return val if math.isfinite(val) else None


def _positive(val: Optional[float]) -> Optional[float]:
    return val if val is not None and 0 < val < MAX_AMOUNT_VALUE else None


def _clean_price(val: Optional[float]) -> Optional[float]:
    return val if val is not None and 0 < val < MAX_PRICE_VALUE else None


def _clean_pct(val: Optional[float]) -> Optional[float]:
    if val is None or not math.isfinite(val) or abs(val) > MAX_ABS_PCT:
        return None
    return val


def _finalize_quote(q: Quote) -> Optional[Quote]:
    """补齐可由原始字段严格推出的差值/振幅，并剔除脏数据。"""
    q.last = _clean_price(q.last)
    if q.last is None:
        return None
    q.prev_close = _clean_price(q.prev_close)
    q.open = _clean_price(q.open)
    q.high = _clean_price(q.high)
    q.low = _clean_price(q.low)

    if q.change is not None and (not math.isfinite(q.change) or abs(q.change) >= q.last):
        q.change = None
    if q.change is None and q.prev_close is not None:
        q.change = round(q.last - q.prev_close, 4)
    if q.prev_close is None and q.change is not None:
        cand = q.last - q.change
        if 0 < cand < MAX_PRICE_VALUE:
            q.prev_close = round(cand, 4)

    q.pct = _clean_pct(q.pct)
    if q.pct is None and q.change is not None and q.prev_close and q.prev_close > 0:
        q.pct = _clean_pct(round(q.change / q.prev_close * 100.0, 4))
    if q.pct is None:
        return None

    if q.high is not None and q.low is not None:
        if q.high < q.low:
            q.high, q.low = None, None
        else:
            q.high = max(q.high, q.last)
            q.low = min(q.low, q.last)
            if q.open is not None:
                q.high = max(q.high, q.open)
                q.low = min(q.low, q.open)

    if q.amplitude is not None:
        if not math.isfinite(q.amplitude) or q.amplitude < 0 or q.amplitude > 100:
            q.amplitude = None
    if q.amplitude is None and q.high is not None and q.low is not None and q.prev_close:
        q.amplitude = round((q.high - q.low) / q.prev_close * 100.0, 2)

    return q


def fmt_price(val: Optional[float], decimals: int = 2) -> str:
    """点位：千分位 + 固定小数位，如 23,637.60。"""
    if val is None or not math.isfinite(val) or val <= 0 or val >= MAX_PRICE_VALUE:
        return EMPTY_TEXT
    return f"{val:,.{decimals}f}"


def fmt_signed(val: Optional[float], decimals: int = 2, suffix: str = "") -> str:
    """带符号变化值：+50.46 / -370.95 / 0.00 / --。"""
    if val is None or not math.isfinite(val) or abs(val) >= MAX_PRICE_VALUE:
        return EMPTY_TEXT
    body = f"{abs(val):,.{decimals}f}"
    if float(body.replace(",", "")) == 0:
        return f"0.{'0' * decimals}{suffix}" if decimals > 0 else f"0{suffix}"
    sign = "+" if val > 0 else "-"
    return f"{sign}{body}{suffix}"


def fmt_change(val: Optional[float]) -> str:
    return fmt_signed(val, decimals=2)


def fmt_pct(val: Optional[float]) -> str:
    if val is None or not math.isfinite(val) or abs(val) >= MAX_PCT_VALUE:
        return EMPTY_TEXT
    return fmt_signed(val, decimals=2, suffix="%")


def fmt_amp(val: Optional[float]) -> str:
    if val is None or not math.isfinite(val) or val < 0 or val > 100:
        return EMPTY_TEXT
    return f"{val:.2f}%"


def _fmt_scaled(scaled: float, unit: str) -> str:
    if scaled < 9.995:
        return f"{scaled:.2f}{unit}"
    if scaled < 99.95:
        return f"{scaled:.1f}{unit}"
    return f"{scaled:,.0f}{unit}"


def fmt_cn_amount(val: Optional[float], unit_suffix: str = "") -> str:
    """成交量 / 成交额：按 万 / 亿 / 万亿 缩写，可附明确单位（股 / 人民币 / 港元 / 韩元）。"""
    if val is None or not math.isfinite(val) or val <= 0 or val >= MAX_AMOUNT_VALUE:
        return EMPTY_TEXT
    if val >= 9999.5e8:
        core = _fmt_scaled(val / 1e12, "万亿")
    elif val >= 9999.5e4:
        core = _fmt_scaled(val / 1e8, "亿")
    elif val >= 9999.5:
        core = _fmt_scaled(val / 1e4, "万")
    else:
        core = f"{val:,.0f}"
    return f"{core}{unit_suffix}"


def fmt_amount_delta(val: Optional[float], unit_suffix: str = "") -> str:
    """成交额/成交量较上轮增量（累计量只展示正增量）：+68亿人民币。"""
    if val is None or not math.isfinite(val) or val <= 0 or val >= MAX_AMOUNT_VALUE:
        return ""
    return f"+{fmt_cn_amount(val, unit_suffix)}"


def fmt_as_of(moment: Optional[datetime]) -> str:
    """行情时间统一格式化为北京时间 MM-DD HH:MM。"""
    if moment is None:
        return EMPTY_TEXT
    return moment.astimezone(TZ_CN).strftime("%m-%d %H:%M")


def arrow_for(val: Optional[float]) -> str:
    """1-bit 墨水屏涨跌方向符号。"""
    if val is None or not math.isfinite(val):
        return ""
    rounded = round(val, 2)
    if rounded > 0:
        return "▲"
    if rounded < 0:
        return "▼"
    return ""


# =====================================================================
# 解析器（纯函数：原始文本 → {symbol: Quote}）
# =====================================================================
_TENCENT_LINE = re.compile(r'v_([A-Za-z0-9_.]+)="([^"]*)"')
_SINA_LINE = re.compile(r'hq_str_([A-Za-z0-9_$]+)="([^"]*)"')


def _parse_time(text: str, fmt: str, tz: ZoneInfo) -> Optional[datetime]:
    try:
        dt = datetime.strptime((text or "").strip(), fmt).replace(tzinfo=tz)
    except (ValueError, AttributeError):
        return None
    return dt if dt.year >= 1970 else None


def _parse_iso_time(text) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    if dt.tzinfo is None or dt.year < 1970:
        return None
    return dt


def parse_tencent(text: str) -> Dict[str, Quote]:
    """解析腾讯证券 qt.gtimg.cn 响应。

    长格式（>=38 字段，沪深 / 港 / 美）：
        [3] 最新  [4] 昨收  [5] 今开  [30] 时间  [31] 涨跌额  [32] 涨跌幅%
        [33] 最高 [34] 最低 [36] 成交量 [37] 成交额 [43] 振幅%
      · 沪深：[36] 单位手（×100→股），[37] 单位万元（×1e4→元人民币）
      · 港股：[37] 单位万港元（×1e4→港元），无成交股数；优先 r_hk* 实时代码
      · 美股：[36] 单位股，[37] 为无意义值丢弃
    环球简格式（gz 前缀，>=6 字段）：
        [2] 北京时间  [3] 最新  [4] 涨跌额  [5] 涨跌幅%
    """
    out: Dict[str, Quote] = {}
    for symbol, payload in _TENCENT_LINE.findall(text or ""):
        if not payload:
            continue
        fields = payload.split("~")
        q = Quote(source=SOURCE_TENCENT)
        key = symbol

        if symbol.startswith("gz"):
            if len(fields) < 6:
                continue
            q.time = _parse_time(fields[2], "%Y-%m-%d %H:%M:%S", TZ_CN)
            q.last = _to_float(fields[3])
            q.change = _to_float(fields[4])
            q.pct = _to_float(fields[5])
        else:
            if len(fields) < 38:
                continue
            q.last = _to_float(fields[3])
            q.prev_close = _to_float(fields[4])
            q.open = _to_float(fields[5])
            q.change = _to_float(fields[31])
            q.pct = _to_float(fields[32])
            q.high = _to_float(fields[33])
            q.low = _to_float(fields[34])
            vol = _positive(_to_float(fields[36]))
            amt = _positive(_to_float(fields[37]))
            if len(fields) > 43:
                q.amplitude = _to_float(fields[43])

            if symbol.startswith(("sh", "sz")):
                q.volume = vol * 100.0 if vol else None
                q.turnover = amt * 1e4 if amt else None
                q.time = _parse_time(fields[30], "%Y%m%d%H%M%S", TZ_CN)
            elif symbol.startswith(TENCENT_RT_PREFIX + "hk"):
                q.turnover = amt * 1e4 if amt else None
                q.time = _parse_time(fields[30], "%Y/%m/%d %H:%M:%S", TZ_HK)
                key = symbol[len(TENCENT_RT_PREFIX):]
            elif symbol.startswith("hk"):
                q.turnover = amt * 1e4 if amt else None
                q.time = _parse_time(fields[30], "%Y/%m/%d %H:%M:%S", TZ_HK)
                q.delayed = True
                key = TENCENT_DELAYED_PREFIX + symbol
            elif symbol.startswith("us"):
                q.volume = vol
                q.time = _parse_time(fields[30], "%Y-%m-%d %H:%M:%S", TZ_US)
            else:
                continue

        clean = _finalize_quote(q)
        if clean is not None:
            out[key] = clean
    return out


def parse_sina(text: str) -> Dict[str, Quote]:
    """解析新浪财经 hq.sinajs.cn 响应（港股 rt_hk* + A股 sh/sz* + 美股 gb_$*）。"""
    out: Dict[str, Quote] = {}
    for symbol, payload in _SINA_LINE.findall(text or ""):
        if not payload:
            continue
        fields = payload.split(",")
        q = Quote(source=SOURCE_SINA)
        key: Optional[str] = None

        if symbol.startswith("rt_hk"):
            if len(fields) < 19:
                continue
            q.open = _to_float(fields[2])
            q.prev_close = _to_float(fields[3])
            q.high = _to_float(fields[4])
            q.low = _to_float(fields[5])
            q.last = _to_float(fields[6])
            q.change = _to_float(fields[7])
            q.pct = _to_float(fields[8])
            amt = _positive(_to_float(fields[11]))
            q.turnover = amt * 1e3 if amt else None  # 千港元 → 港元
            q.volume = _positive(_to_float(fields[12]))
            q.time = _parse_time(f"{fields[17]} {fields[18]}", "%Y/%m/%d %H:%M:%S", TZ_HK)
            key = symbol[len("rt_"):]
        elif symbol.startswith(("sh", "sz")):
            if len(fields) < 32:
                continue
            q.open = _to_float(fields[1])
            q.prev_close = _to_float(fields[2])
            q.last = _to_float(fields[3])
            q.high = _to_float(fields[4])
            q.low = _to_float(fields[5])
            vol = _positive(_to_float(fields[8]))
            # 新浪沪市指数 [8] 为手，深市指数 [8] 为股；[9] 均为元人民币
            q.volume = (vol * 100.0 if symbol.startswith("sh") else vol) if vol else None
            q.turnover = _positive(_to_float(fields[9]))
            q.time = _parse_time(f"{fields[30]} {fields[31]}", "%Y-%m-%d %H:%M:%S", TZ_CN)
            key = symbol
        elif symbol in SINA_US_MAP:
            if len(fields) < 11:
                continue
            q.last = _to_float(fields[1])
            q.pct = _to_float(fields[2])
            q.time = _parse_time(fields[3], "%Y-%m-%d %H:%M:%S", TZ_CN)
            q.change = _to_float(fields[4])
            q.open = _to_float(fields[5])
            q.high = _to_float(fields[6])
            q.low = _to_float(fields[7])
            q.volume = _positive(_to_float(fields[10]))
            if len(fields) > 26:
                q.prev_close = _to_float(fields[26])
            key = SINA_US_MAP[symbol]
        else:
            continue

        clean = _finalize_quote(q)
        if clean is not None and key:
            out[key] = clean
    return out


def parse_sina_intl(text: str) -> Dict[str, Quote]:
    """解析新浪国际指数 int_*（港 / 日 / 韩 / 美指数备用源）。

    字段：名称,最新点位,涨跌额,涨跌幅%（无成交量、无行情时间）。
    """
    out: Dict[str, Quote] = {}
    for symbol, payload in _SINA_LINE.findall(text or ""):
        key = SINA_INTL_MAP.get(symbol)
        if key is None or not payload:
            continue
        fields = payload.split(",")
        if len(fields) < 4:
            continue
        q = Quote(
            source=SOURCE_SINA_INTL,
            last=_to_float(fields[1]),
            change=_to_float(fields[2]),
            pct=_to_float(fields[3]),
        )
        clean = _finalize_quote(q)
        if clean is not None:
            out[key] = clean
    return out


def parse_netease(text: str) -> Dict[str, Quote]:
    """解析网易财经 A 股指数 JSONP（time 字段为 2026/11/13 15:00:00，北京时间）。

    字段：price / yesterday / open / high / low / turnover（成交额，元）/ volume（手）。
    """
    out: Dict[str, Quote] = {}
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return out
    try:
        doc = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return out
    if not isinstance(doc, dict):
        return out
    for code, item in doc.items():
        key = NETEASE_MAP.get(str(code))
        if key is None or not isinstance(item, dict):
            continue
        q = Quote(
            source=SOURCE_NETEASE,
            last=_to_float(item.get("price")),
            prev_close=_to_float(item.get("yesterday")),
            open=_to_float(item.get("open")),
            high=_to_float(item.get("high")),
            low=_to_float(item.get("low")),
            turnover=_positive(_to_float(item.get("turnover"))),
            time=_parse_time(str(item.get("time") or ""), "%Y/%m/%d %H:%M:%S", TZ_CN),
        )
        q.volume = _positive(_to_float(item.get("volume")))   # 股
        clean = _finalize_quote(q)
        if clean is not None:
            out[key] = clean
    return out


def parse_eastmoney(text: str) -> Dict[str, Quote]:
    """解析东方财富 push2 ulist（fltt=2 已是最小单位，f2 最新 / f4 涨跌额 / f3 涨跌幅%）。

    字段：f2 最新点位 / f3 涨跌幅% / f4 涨跌额 / f5 成交量 / f6 成交额（元）/
          f15 最高 / f16 最低 / f17 今开 / f18 昨收。A 股 f5 单位为手，其余为股。
    东方财富不返回行情时间，time 留空（不参与「时间更新者优先」的投票）。
    """
    out: Dict[str, Quote] = {}
    try:
        doc = json.loads(text or "")
    except (TypeError, ValueError):
        return out
    data = doc.get("data") if isinstance(doc, dict) else None
    items = data.get("diff") if isinstance(data, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        secid = f"{item.get('f13')}.{item.get('f12')}"
        key = EASTMONEY_MAP.get(secid)
        if key is None:
            continue
        q = Quote(
            source=SOURCE_EASTMONEY,
            last=_to_float(item.get("f2")),
            pct=_to_float(item.get("f3")),
            change=_to_float(item.get("f4")),
            high=_to_float(item.get("f15")),
            low=_to_float(item.get("f16")),
            open=_to_float(item.get("f17")),
            prev_close=_to_float(item.get("f18")),
            turnover=_positive(_to_float(item.get("f6"))),
        )
        q.volume = _positive(_to_float(item.get("f5")))       # 股
        clean = _finalize_quote(q)
        if clean is not None:
            out[key] = clean
    return out


def parse_naver(text: str) -> Dict[str, Quote]:
    """解析 Naver 金融 polling JSON（韩国 KOSPI/KOSDAQ + 日本 .N225/.TOPX）。"""
    out: Dict[str, Quote] = {}
    try:
        doc = json.loads(text or "")
    except (TypeError, ValueError):
        return out
    items = doc.get("datas") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return out

    for item in items:
        if not isinstance(item, dict):
            continue
        domestic = bool(item.get("itemCode"))
        code = str(item.get("itemCode") or item.get("reutersCode") or "").lstrip(".")
        if not code:
            continue
        q = Quote(
            source=SOURCE_NAVER_KR if domestic else SOURCE_NAVER_JP,
            delayed=not domestic,
        )
        q.last = _to_float(item.get("closePriceRaw"))
        q.change = _to_float(item.get("compareToPreviousClosePriceRaw"))
        q.pct = _to_float(item.get("fluctuationsRatioRaw"))
        q.open = _to_float(item.get("openPriceRaw"))
        q.high = _to_float(item.get("highPriceRaw"))
        q.low = _to_float(item.get("lowPriceRaw"))
        q.volume = _positive(_to_float(item.get("accumulatedTradingVolumeRaw")))
        # 仅韩国国内指数成交额单位可靠（韩元）；日本指数成交额单位失真/为空，丢弃。
        if domestic:
            q.turnover = _positive(_to_float(item.get("accumulatedTradingValueRaw")))
        q.status = str(item.get("marketStatus") or "").strip().upper()
        q.time = _parse_iso_time(item.get("localTradedAt"))

        clean = _finalize_quote(q)
        if clean is not None:
            out[code] = clean
    return out


# =====================================================================
# 多源合并规则
# =====================================================================
def _fill_missing(primary: Quote, backup: Optional[Quote]) -> Quote:
    """以 primary 为主，仅用 backup 补齐缺失字段（不覆盖主源点位/涨跌）。"""
    merged = copy.copy(primary)
    if backup is None:
        return merged
    for attr in ("volume", "turnover", "open", "high", "low", "prev_close", "amplitude", "time"):
        if getattr(merged, attr) is None and getattr(backup, attr) is not None:
            setattr(merged, attr, getattr(backup, attr))
    return _finalize_quote(merged) or merged


def _merge_primary_backup(primary: Optional[Quote], backup: Optional[Quote]) -> Quote:
    if primary is not None:
        return _fill_missing(primary, backup)
    if backup is not None:
        return copy.copy(backup)
    return Quote()


def _merge_n225(
    naver: Optional[Quote],
    tx_gz: Optional[Quote],
    sina_intl: Optional[Quote] = None,
) -> Quote:
    """日经225：盘中用腾讯实时点位/涨跌 + Naver 开高低/成交量；收盘或偏差过大用 Naver。

    Naver 与腾讯都缺行时才回退新浪国际指数（仅点位/涨跌，无开高低与成交量）。
    """
    if naver is None:
        if tx_gz is not None:
            return copy.copy(tx_gz)
        return copy.copy(sina_intl) if sina_intl is not None else Quote()
    if tx_gz is None or naver.status == "CLOSE":
        return _fill_missing(naver, sina_intl)
    if abs(tx_gz.last - naver.last) > naver.last * N225_SOURCE_TOLERANCE:
        return _fill_missing(naver, sina_intl)
    merged = copy.copy(tx_gz)
    merged.open = naver.open
    merged.high = max(naver.high, tx_gz.last) if naver.high is not None else None
    merged.low = min(naver.low, tx_gz.last) if naver.low is not None else None
    merged.volume = naver.volume
    merged.status = naver.status
    merged.delayed = False
    merged.amplitude = None  # 让 _finalize_quote 按修正后的 high/low 重算
    return _finalize_quote(merged) or merged


def _merge_candidates(candidates: List[Optional[Quote]], tolerance: float) -> Quote:
    """多源一致性聚合：按优先级取第一路有效行情，其余源补缺失字段。

    规则：
      1. 候选按可靠性排序（主源在前），第一路有效行情为基准；
      2. 与基准点位偏差在容差内 → 视为同一笔行情，只用来补缺失字段（不覆盖主源点位/涨跌）；
      3. 偏差超容差 → 不直接覆盖，只有在「行情时间严格更新」时才改信该源
         （例如主源卡住时，更新的备用源接管），避免延迟源/口径异常的源把好数据带偏；
      4. 全部无效 → 空 Quote（该行显示 --，整页无有效行情时跳过推送）。
    """
    valid = [q for q in candidates if q is not None and q.valid]
    if not valid:
        return Quote()
    best = copy.copy(valid[0])
    for cand in valid[1:]:
        if abs(cand.last - best.last) <= best.last * tolerance:
            best = _fill_missing(best, cand)
            continue
        if cand.time is not None and (best.time is None or cand.time > best.time):
            best = copy.copy(cand)
    return best


def merge_pages(
    tencent: Dict[str, Quote],
    sina: Dict[str, Quote],
    naver_kr: Dict[str, Quote],
    naver_jp: Dict[str, Quote],
    sina_intl: Optional[Dict[str, Quote]] = None,
    netease: Optional[Dict[str, Quote]] = None,
    eastmoney: Optional[Dict[str, Quote]] = None,
) -> Dict[int, MarketPageData]:
    """把 7 路源按指数聚合成五页。后三路（新浪国际 / 网易 / 东方财富）为可选备用源。"""
    sina_intl = sina_intl or {}
    netease = netease or {}
    eastmoney = eastmoney or {}

    pages: Dict[int, MarketPageData] = {}
    for mspec in MARKET_PAGES:
        rows: List[PanelRow] = []
        tol = MARKET_TOLERANCE.get(mspec.code, SOURCE_TOLERANCE)
        for rspec in mspec.rows:
            k = rspec.key
            if mspec.code == "A":
                # 腾讯 → 新浪 → 网易 → 东方财富（四路互为备份）
                q = _merge_candidates(
                    [tencent.get(k), sina.get(k), netease.get(k), eastmoney.get(k)], tol
                )
            elif mspec.code == "港":
                # 腾讯 r_hk* 实时 → 新浪 rt_hk* → 新浪国际 → 东方财富 → 腾讯延迟行
                q = _merge_candidates(
                    [
                        tencent.get(k),
                        sina.get(k),
                        sina_intl.get(k),
                        eastmoney.get(k),
                        tencent.get(TENCENT_DELAYED_PREFIX + k),
                    ],
                    tol,
                )
            elif k == "KOSPI":
                q = _merge_candidates(
                    [naver_kr.get("KOSPI"), tencent.get("gzKS11"), sina_intl.get("KOSPI")], tol
                )
            elif k == "KOSDAQ":
                q = _merge_candidates(
                    [naver_kr.get("KOSDAQ"), tencent.get("gzKOSDAQ")], tol
                )
            elif k == "N225":
                q = _merge_n225(
                    naver_jp.get("N225"), tencent.get("gzN225"), sina_intl.get("N225")
                )
            elif k == "TOPX":
                q = copy.copy(naver_jp["TOPX"]) if "TOPX" in naver_jp else Quote()
            elif mspec.code == "美":
                # 腾讯 → 新浪 → 新浪国际 → 东方财富
                q = _merge_candidates(
                    [tencent.get(k), sina.get(k), sina_intl.get(k), eastmoney.get(k)], tol
                )
            else:
                q = Quote()
            rows.append(PanelRow(spec=rspec, quote=q))
        page = MarketPageData(spec=mspec, rows=rows)
        page.summary_value = page.compute_summary()
        pages[mspec.page_id] = page
    return pages


# =====================================================================
# 轮间变化（delta）状态演进：默认 5 分钟变化
# =====================================================================
def apply_delta_state(panel: PanelData, prev_state: Optional[dict]) -> dict:
    """把当前行情与上一轮状态比对，填充每行的 delta / delta_pct 与市场成交增量。

    性质：
      1. 首次运行或跨交易日（按各交易所当地日期判定）：delta=None，不把隔夜跳空当成 5 分钟变化；
      2. 行情 (last, time) 未变（午休 / 收盘 / 周末）：沿用上一笔有效 delta，渲染结果逐位不变，
         墨水屏零多余刷屏；
      3. 盘中出现新行情（time 推进或 last 变动）：delta = 本次 last - 上次 last；
      4. 单轮偶发缺行或备用源时间倒退：保留原状态记录，恢复后不丢连续性。
    """
    prev_state = prev_state if isinstance(prev_state, dict) else {}
    old_quotes = prev_state.get("quotes") if isinstance(prev_state.get("quotes"), dict) else {}
    old_totals = prev_state.get("totals") if isinstance(prev_state.get("totals"), dict) else {}

    new_quotes: Dict[str, dict] = {
        k: dict(v) for k, v in old_quotes.items() if isinstance(v, dict)
    }
    new_totals: Dict[str, dict] = {
        k: dict(v) for k, v in old_totals.items() if isinstance(v, dict)
    }

    for page in panel.pages.values():
        tz = page.spec.tz
        for row in page.rows:
            q = row.quote
            if not q.valid or q.last is None:
                continue
            key = row.spec.key
            time_iso = q.time.isoformat() if q.time else ""
            trade_date = q.time.astimezone(tz).strftime("%Y-%m-%d") if q.time else ""
            old = old_quotes.get(key) if isinstance(old_quotes.get(key), dict) else None
            old_last = _clean_price(_to_float(old.get("last"))) if old else None
            old_time = str(old.get("time") or "") if old else ""
            old_date = str(old.get("trade_date") or "") if old else ""

            # 新交易日或无历史记录：建立基线
            if old is None or old_last is None or (trade_date and old_date and trade_date != old_date):
                q.prev_last = None
                q.delta = None
                q.delta_pct = None
                new_quotes[key] = {
                    "last": q.last,
                    "time": time_iso,
                    "trade_date": trade_date or old_date,
                    "prev_last": None,
                    "prev_time": "",
                    "delta": None,
                    "delta_pct": None,
                }
                continue

            # 行情时间与点位都未变：休市/午休，保持上一笔变化不变（幂等）
            if abs(q.last - old_last) < 1e-6 and time_iso == old_time:
                q.prev_last = _clean_price(_to_float(old.get("prev_last")))
                q.delta = _to_float(old.get("delta"))
                q.delta_pct = _to_float(old.get("delta_pct"))
                continue

            # 备用源切换导致行情时间倒退：不产生反向伪变化，保留较新的旧基线
            old_dt = _parse_iso_time(old_time) if old_time else None
            if q.time is not None and old_dt is not None and q.time < old_dt:
                q.prev_last = None
                q.delta = None
                q.delta_pct = None
                continue

            # 同一交易日内的新一笔行情：计算较上轮变化
            delta = round(q.last - old_last, 4)
            delta_pct = round(delta / old_last * 100.0, 4) if old_last > 0 else None
            q.prev_last = old_last
            q.delta = delta
            q.delta_pct = delta_pct
            new_quotes[key] = {
                "last": q.last,
                "time": time_iso,
                "trade_date": trade_date or old_date,
                "prev_last": old_last,
                "prev_time": old_time,
                "delta": delta,
                "delta_pct": delta_pct,
            }

        # 页脚汇总量额的轮间增量
        mcode = page.spec.code
        cur_val = page.summary_value
        as_of = page.as_of
        if cur_val is not None and cur_val > 0:
            t_iso = as_of.isoformat() if as_of else ""
            t_date = as_of.astimezone(tz).strftime("%Y-%m-%d") if as_of else ""
            old_t = old_totals.get(mcode) if isinstance(old_totals.get(mcode), dict) else None
            old_val = _positive(_to_float(old_t.get("value"))) if old_t else None
            old_t_iso = str(old_t.get("time") or "") if old_t else ""
            old_t_date = str(old_t.get("trade_date") or "") if old_t else ""

            if old_t is None or old_val is None or (t_date and old_t_date and t_date != old_t_date):
                page.summary_delta = None
                new_totals[mcode] = {
                    "value": cur_val,
                    "time": t_iso,
                    "trade_date": t_date or old_t_date,
                    "delta": None,
                }
            elif abs(cur_val - old_val) < 1.0 and t_iso == old_t_iso:
                prev_d = _positive(_to_float(old_t.get("delta")))
                page.summary_delta = prev_d
            else:
                diff = cur_val - old_val
                # 当日累计成交额/成交量单调递增；负差只可能来自源切换，忽略不展示
                clean_diff = round(diff, 2) if diff > 0 else None
                page.summary_delta = clean_diff
                new_totals[mcode] = {
                    "value": cur_val,
                    "time": t_iso,
                    "trade_date": t_date or old_t_date,
                    "delta": clean_diff,
                }

    out_state = dict(prev_state)
    out_state["quotes"] = new_quotes
    out_state["totals"] = new_totals
    return out_state


# =====================================================================
# 并发抓取与离线样例
# =====================================================================
def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, "replace")


def fetch_text(
    url: str,
    encoding: str = "utf-8",
    headers: Optional[dict] = None,
    timeout: int = FETCH_TIMEOUT,
    attempts: int = FETCH_ATTEMPTS,
) -> str:
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    last_err: Optional[Exception] = None
    for _ in range(max(1, attempts)):
        try:
            with urlopen(Request(url, headers=merged), timeout=timeout) as resp:
                return _decode(resp.read(524288), encoding)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise last_err if last_err else RuntimeError(f"fetch failed: {url}")


SOURCES = (
    (SOURCE_TENCENT, TENCENT_URL, "gb18030", None, parse_tencent),
    (SOURCE_SINA, SINA_URL, "gb18030", {"Referer": SINA_REFERER}, parse_sina),
    (SOURCE_NAVER_KR, NAVER_KR_URL, "utf-8", None, parse_naver),
    (SOURCE_NAVER_JP, NAVER_JP_URL, "utf-8", None, parse_naver),
    (SOURCE_SINA_INTL, SINA_INTL_URL, "gb18030", {"Referer": SINA_REFERER}, parse_sina_intl),
    (SOURCE_NETEASE, NETEASE_URL, "utf-8", None, parse_netease),
    (SOURCE_EASTMONEY, EASTMONEY_URL, "utf-8", None, parse_eastmoney),
)

FIXTURE_FILES = {
    SOURCE_TENCENT: "market_tencent.txt",
    SOURCE_SINA: "market_sina.txt",
    SOURCE_NAVER_KR: "market_naver_kr.json",
    SOURCE_NAVER_JP: "market_naver_jp.json",
    SOURCE_SINA_INTL: "market_sina_intl.txt",
    SOURCE_NETEASE: "market_netease.json",
    SOURCE_EASTMONEY: "market_eastmoney.json",
}


def build_panel(texts: Dict[str, Optional[str]], demo: bool = False) -> PanelData:
    parsed: Dict[str, Dict[str, Quote]] = {}
    for src, _url, _enc, _hdr, parser in SOURCES:
        raw = texts.get(src)
        try:
            parsed[src] = parser(raw) if raw else {}
        except Exception:  # noqa: BLE001
            parsed[src] = {}
    pages = merge_pages(
        parsed[SOURCE_TENCENT],
        parsed[SOURCE_SINA],
        parsed[SOURCE_NAVER_KR],
        parsed[SOURCE_NAVER_JP],
        parsed.get(SOURCE_SINA_INTL, {}),
        parsed.get(SOURCE_NETEASE, {}),
        parsed.get(SOURCE_EASTMONEY, {}),
    )
    sources = {src: bool(parsed[src]) for src in parsed}
    return PanelData(pages=pages, sources=sources, demo=demo)


def demo_panel(with_delta: bool = False, with_backup_sources: bool = True) -> PanelData:
    """离线样例面板（tests/fixtures/market_*）；with_delta=True 时附带模拟上一轮基线。

    with_backup_sources=True（默认，与线上一致）时 7 路源全开：
    备用源（新浪国际 / 网易 / 东方财富）点位与主源一致，只用来补齐缺失字段。
    """
    texts = {
        src: (FIXTURE_DIR / fname).read_text(encoding="utf-8")
        for src, fname in FIXTURE_FILES.items()
        if with_backup_sources or src not in (
            SOURCE_SINA_INTL, SOURCE_NETEASE, SOURCE_EASTMONEY,
        )
    }
    panel = build_panel(texts, demo=True)
    if with_delta:
        # 构造同一交易日一轮（默认 5 分钟）前的真实量级基线，方便离线检视「分变」展示效果
        offsets = {
            "sh000001": -2.35,
            "sz399001": 14.80,
            "sz399006": 5.12,
            "sh000300": -1.45,
            "sh000688": 3.26,
            "hkHSI": -18.50,
            "hkHSTECH": 6.40,
            "hkHSCEI": -4.20,
            "KOSPI": -3.85,
            "KOSDAQ": 1.15,
            "N225": 48.20,
            "TOPX": 1.62,
            "usDJI": 36.40,
            "usIXIC": -28.15,
            "usINX": -4.30,
        }
        seed_quotes = {}
        for page in panel.pages.values():
            for row in page.rows:
                q = row.quote
                if not q.valid or q.last is None or q.time is None:
                    continue
                off = offsets.get(row.spec.key, 0.0)
                prev_dt = q.time.replace(second=0) if q.time.second else q.time
                # 确保时间早于当前
                from datetime import timedelta
                prev_dt = q.time - timedelta(minutes=1)
                seed_quotes[row.spec.key] = {
                    "last": round(q.last - off, 2),
                    "time": prev_dt.isoformat(),
                    "trade_date": prev_dt.astimezone(page.spec.tz).strftime("%Y-%m-%d"),
                    "prev_last": None,
                    "prev_time": "",
                    "delta": None,
                    "delta_pct": None,
                }
        seed_totals = {}
        for page in panel.pages.values():
            if page.summary_value and page.as_of:
                from datetime import timedelta
                prev_dt = page.as_of - timedelta(minutes=1)
                seed_totals[page.spec.code] = {
                    "value": round(page.summary_value * 0.996, 2),
                    "time": prev_dt.isoformat(),
                    "trade_date": prev_dt.astimezone(page.spec.tz).strftime("%Y-%m-%d"),
                    "delta": None,
                }
        apply_delta_state(panel, {"quotes": seed_quotes, "totals": seed_totals})
    return panel


def collect_panel(fetch: Callable[..., str] = fetch_text) -> PanelData:
    """并发抓取 7 路公开行情接口并组装 5 页面板；单路失败只影响对应行。"""
    def _load(entry) -> Optional[str]:
        _src, url, enc, hdrs, _parser = entry
        try:
            return fetch(url, encoding=enc, headers=hdrs)
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        texts = list(pool.map(_load, SOURCES))
    return build_panel({entry[0]: txt for entry, txt in zip(SOURCES, texts)})
