# 提取自 k-macao/007 pushplus_deepseek.py 的最小片段（离线回归用，勿执行）
DEFAULT_BOFA_BB_DATA: dict = {
    "current": 8.8,
    "previous": 9.3,
    "as_of": "2026-10-01",
    "source": "BofA The Flow Show 2026-10-01",
    "signal": "Sell",
    "signal_cn": "反向卖出",
    "components_as_of": "2026-10-01",
    "components": [
        {"name": "Hedge Fund Positioning", "cn": "对冲基金仓位",
         "percentile": 64, "sentiment": "Neutral", "sentiment_cn": "中性"},
        {"name": "Global Stock Index Breadth", "cn": "全球股指广度",
         "percentile": 21, "sentiment": "Bearish", "sentiment_cn": "看空"},
    ],
}

BOFA_BB_STALE_DAYS = 10


def bofa_bb_signal(value: float) -> tuple[str, str]:
    """按 BofA 惯例把读数映射为信号：<2.0 反向买入，>8.0 反向卖出，其余中性。"""
    return "Neutral", "中性"


caution_re = re.compile(
    r"下调|减持|卖出|中性|谨慎|风险|承压|回调|警告|分歧|放缓|通胀|衰退|"
    r"underweight|sell|neutral|downgrade|cut|risk|headwind",
    re.I,
)


def render_brief_risk_rule():
    lines = ["## ⚠️ 研报 AI 风险点提示", ""]
    if True:
        lines.append(
            "先把丑话说在前面。盘面上热闹归热闹，但下面这几处暗礁眼下还真不能假装看不见——"
            "尤其是机构开始收紧口径的地方，往往藏着资金最真实的顾虑："
        )
    else:
        lines.append(
            "坦白讲，眼下这个时间窗抓到的有效快讯和研报样本偏少——或许正赶上休市空窗，"
            "又或许是部分公开信源暂时卡壳。越是这种像在薄雾里开车的时刻，"
            "越别急着重仓押单边，等跨市场的暗流看清了再落子也不迟。"
        )
    return lines


def build_prompt():
    user = (
        "## ⚠️ 研报 AI 风险点提示\n\n"
        "- 放在最开头：结合研报与市场快讯，用老练分析师提醒客户留意暗礁的口吻，"
        "剖析 3~5 个当前最需警惕的市场级风险、机构评级下调/观点分歧预警，"
        "或接下来需要盯紧的风险触发信号（注明依据来源）\n\n"
        "## 🏛 国外研报\n\n"
    )
    return user
