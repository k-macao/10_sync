# -*- coding: utf-8 -*-
"""
五页简报看板（brief_board）的离线回归测试。

全部不联网：三仓库各用 tests/fixtures/brief_* 最小片段（从真实来源裁出的
原文行 / 源码片段），跑法：

    python3 -m unittest discover -s tests -v
    python3 -m unittest tests.test_brief_board -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brief_board as BB  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class TestExtractText(unittest.TestCase):
    def test_strip_tags_and_entities(self):
        html = (
            "<html><script>x()</script>"
            "<h2>00 / 每日 VIX 恐慌指数</h2>"
            "<p>CBOE VIX <strong>19.30</strong> +0.60<br>&lt;12 极低&amp;常态</p>"
            "<style>a{}</style></html>"
        )
        text = BB.extract_text(html)
        self.assertIn("00 / 每日 VIX 恐慌指数", text)
        self.assertIn("CBOE VIX 19.30 +0.60", text)
        self.assertIn("<12 极低&常态", text)
        self.assertNotIn("x()", text)
        self.assertNotIn("a{}", text)


class TestParse004(unittest.TestCase):
    def setUp(self):
        self.d = BB.parse_004(load("brief_004.txt"))

    def test_vix_block(self):
        self.assertTrue(any("CBOE VIX 19.30" in ln for ln in self.d["vix"]))
        self.assertTrue(any("今日判读" in ln for ln in self.d["vix"]))

    def test_opening_and_longshort(self):
        self.assertEqual(self.d["hs_line"], "●恒指涨跌幅：−1.55%")
        self.assertTrue(any(ln.startswith("结论") for ln in self.d["longshort"]))
        self.assertTrue(any("多头有效性参考位" in ln for ln in self.d["longshort"]))

    def test_conclusion(self):
        self.assertTrue(any(ln.startswith("港股盘面") for ln in self.d["conclusion"]))
        self.assertTrue(any(ln.startswith("社区多空分布") for ln in self.d["conclusion"]))
        self.assertTrue(self.d["conclusion"][-1].startswith("⚠️"))
        self.assertFalse(any(ln.startswith("生成时间") for ln in self.d["conclusion"]))

    def test_gen_time(self):
        self.assertEqual(
            self.d["gen_time"],
            datetime(2026, 11, 15, 0, 59, 3, tzinfo=timezone.utc),
        )

    def test_missing_marker_raises(self):
        with self.assertRaises(BB.BriefError):
            BB.parse_004("完全无关的文本")


class TestFreshness(unittest.TestCase):
    def test_fresh_passes(self):
        BB.check_freshness(datetime.now(timezone.utc), 48)

    def test_stale_raises(self):
        old = datetime.now(timezone.utc) - timedelta(hours=49)
        with self.assertRaises(BB.BriefError):
            BB.check_freshness(old, 48)

    def test_missing_time_raises_and_zero_disables(self):
        with self.assertRaises(BB.BriefError):
            BB.check_freshness(None, 48)
        BB.check_freshness(None, 0)


class TestParse003(unittest.TestCase):
    def setUp(self):
        self.d = BB.parse_003(load("brief_003.txt"))

    def test_opportunity_points(self):
        opp = self.d["opp"]
        self.assertIn("#1", opp)
        self.assertIn("> 港股午後跌幅收窄 恒指重上18000點 內房板塊反彈", opp)
        self.assertIn("偏多：反弹 / 內房 / 恒指", opp)
        self.assertIn("*恒指重返萬八關，內房板塊集體反彈", opp)
        self.assertIn("*台股創新高，權值股法說會成焦點", opp)
        self.assertIn("触及下轨极度恐慌时寻找左侧买点", opp)
        self.assertNotIn("中性：分歧 / 利率路径", opp)

    def test_risk_points(self):
        risk = self.d["risk"]
        self.assertIn("#2", risk)
        self.assertIn("> 美聯儲議息紀錄顯示官員分歧擴大", risk)
        self.assertIn("中性：分歧 / 利率路径", risk)
        self.assertIn("*美聯儲官員對利率路徑分歧加大", risk)
        self.assertIn("接近上轨极度贪婪时考虑分批止盈", risk)
        self.assertIn("■ SCAN_ERROR.LOG", risk)
        self.assertIn("> YouTube 返回 429 Too Many Requests", risk)
        self.assertIn("FRESHNESS CHECK ▸ 只保留最近 72 小时内发布的内容", risk)
        self.assertIn("超时 / 无法验证发布时间的内容已隐藏 · 本次共 2 条", risk)

    def test_tail_decorations_stay_out(self):
        all_lines = self.d["opp"] + self.d["risk"]
        self.assertNotIn("作者：章鱼 ai 仅供参考，分析研究", all_lines)
        self.assertNotIn("章鱼 AI · 全景视野", all_lines)

    def test_summary(self):
        self.assertEqual(self.d["summary"]["sentiment"], "大众情绪指数 58/100 · 谨慎乐观")
        self.assertEqual(self.d["summary"]["ls"], "多 58% / 空 42%")


class TestParse007(unittest.TestCase):
    def setUp(self):
        self.d = BB.parse_007(load("brief_007_pushplus.py"), load("brief_007_readme.md"))

    def test_bofa_extracted_from_source(self):
        self.assertEqual(self.d["bofa"]["current"], 8.8)
        self.assertEqual(self.d["bofa"]["previous"], 9.3)
        self.assertEqual(self.d["bofa"]["signal"], "Sell")

    def test_texts_extracted_from_source(self):
        self.assertTrue(self.d["keywords"].startswith("下调|减持|"))
        self.assertTrue(self.d["keywords"].endswith("headwind"))
        self.assertTrue(self.d["guide"].startswith("先把丑话说在前面"))
        self.assertTrue(self.d["guide"].endswith("顾虑："))
        self.assertTrue(self.d["thin"].startswith("坦白讲，眼下"))
        self.assertTrue(self.d["thin"].endswith("落子也不迟。"))
        self.assertTrue(self.d["prompt"].startswith("放在最开头："))
        self.assertTrue(self.d["prompt"].endswith("（注明依据来源）"))
        self.assertTrue(self.d["signal_rule"].startswith("按 BofA 惯例"))
        self.assertEqual(self.d["risk_paren"], "（市场级风险、机构评级下调/分歧预警及触发信号）")

    def test_fallback_when_source_unparseable(self):
        d = BB.parse_007("x = 1\n", "# 空 readme\n")
        self.assertEqual(d["bofa"]["current"], 7.2)
        self.assertTrue(d["guide"].startswith("先把丑话说在前面"))

    def test_bofa_card_lines(self):
        lines = BB.bofa_card_lines(self.d["bofa"])
        self.assertTrue(lines[0].startswith("BofA Bull & Bear (B&B) 8.8 SELL"))
        self.assertIn("prev 9.3 (-0.5)", lines[0])
        self.assertTrue(lines[1].startswith("数据日期：截至 2026-10-01"))
        self.assertIn("⏳ 已过期", lines[1])
        self.assertIn("对冲基金仓位 64%", lines[3])
        self.assertIn("全球股指广度 21%", lines[3])


class TestBuildPages(unittest.TestCase):
    def setUp(self):
        self.pages = BB.build_pages(
            BB.parse_004(load("brief_004.txt")),
            BB.parse_003(load("brief_003.txt")),
            BB.parse_007(load("brief_007_pushplus.py"), load("brief_007_readme.md")),
        )

    def test_five_pages_with_heads(self):
        self.assertEqual(sorted(self.pages), [1, 2, 3, 4, 5])
        p1 = [ln for _, ln in self.pages[1]["rows"]]
        self.assertIn("00 / 每日 VIX 恐慌指数", p1)
        self.assertIn("07 / 核心结论", p1)
        p2 = [ln for _, ln in self.pages[2]["rows"]]
        self.assertIn("机会点", p2)
        self.assertIn("风险点", p2)

    def test_nodes_one_per_line(self):
        nodes = [ln for _, ln in self.pages[3]["rows"]]
        for expect in [
            "*恒指重返萬八關，內房板塊集體反彈",
            "> 港股午後跌幅收窄 恒指重上18000點 內房板塊反彈",
            "偏多：反弹 / 內房 / 恒指",
            "03:12 內房股集體反彈，碧桂園升幅領先。",
            "触及下轨极度恐慌时寻找左侧买点",
            "多 58% / 空 42%",
            "大众情绪指数 58/100 · 谨慎乐观",
            "现货黄金 3,886（+0.03%）",
        ]:
            self.assertIn(expect, nodes)

    def test_page4_page5_body_only(self):
        p4 = [ln for _, ln in self.pages[4]["rows"]]
        self.assertIn("📊 美银牛熊指标 · BofA Bull & Bear (B&B)", p4)
        self.assertTrue(any(ln.startswith("BofA Bull & Bear (B&B) 8.8 SELL") for ln in p4))
        self.assertTrue(any(ln.startswith("对冲基金仓位 64%") for ln in p4))
        self.assertTrue(any(ln.startswith("2. 风险提示：⚠️ 研报 AI 风险点提示") for ln in p4))
        for noise in ("数据日期", "来源：", "按 BofA 惯例", "最近一期公布"):
            self.assertFalse(any(noise in ln for ln in p4))
        p5 = [ln for _, ln in self.pages[5]["rows"]]
        self.assertIn("⚠️ 研报 AI 风险点提示", p5)
        self.assertIn("（市场级风险、机构评级下调/分歧预警及触发信号）", p5)
        self.assertTrue(any(ln.startswith("先把丑话说在前面") for ln in p5))
        self.assertTrue(any(ln.startswith("坦白讲，眼下") for ln in p5))
        self.assertIn("仅供参考，分析研究。", p5)
        self.assertIn("作者：章鱼 ai", p5)
        self.assertFalse(any(ln.startswith("下调|减持|") for ln in p5))
        self.assertFalse(any(ln.startswith("放在最开头：") for ln in p5))


class TestBodyFilter(unittest.TestCase):
    """每屏只要正文：非正文行不上屏（B 口径）。"""

    def setUp(self):
        self.pages = BB.build_pages(
            BB.parse_004(load("brief_004.txt")),
            BB.parse_003(load("brief_003.txt")),
            BB.parse_007(load("brief_007_pushplus.py"), load("brief_007_readme.md")),
        )

    def test_p2_noise_gone_body_kept(self):
        p2 = [ln for _, ln in self.pages[2]["rows"]]
        joined = "\n".join(p2)
        for noise in (
            "#1", "#2", "DIR ▸", "字幕已读取", "AI 多空概率", "仅供参考，不做推荐",
            "C:\\", "SCAN_ERROR", "FRESHNESS CHECK", "超时 / 无法验证", "Too Many Requests",
            "█", "░", "· 权重",
        ):
            self.assertNotIn(noise, joined)
        for body in (
            "多 64% / 空 36%", "多 47% / 空 53%",
            "> 港股午後跌幅收窄 恒指重上18000點 內房板塊反彈",
            "> 美聯儲議息紀錄顯示官員分歧擴大",
            "偏多：反弹 / 內房 / 恒指", "中性：分歧 / 利率路径",
            "00:00 開場", "大家好，今日大市焦點係恒指重返萬八關。",
            "03:12 內房股集體反彈，碧桂園升幅領先。",
            "接近上轨极度贪婪时考虑分批止盈", "触及下轨极度恐慌时寻找左侧买点",
        ):
            self.assertIn(body, p2)

    def test_p1_legend_gone(self):
        p1 = [ln for _, ln in self.pages[1]["rows"]]
        self.assertFalse(any(ln.startswith("<12") for ln in p1))
        self.assertTrue(any("CBOE VIX 19.30" in ln for ln in p1))
        self.assertTrue(any(ln.startswith("今日判读") for ln in p1))
        self.assertTrue(any(ln.startswith("港股盘面") for ln in p1))
        self.assertTrue(any(ln.startswith("⚠️ 风险提示") for ln in p1))

    def test_body_only_rows_keeps_heads(self):
        rows = [("head", "机会点"), ("small", "DIR ▸ 信報財經新聞"), ("small", "多 64% / 空 36% · 权重 64%")]
        out = BB.body_only_rows(rows)
        self.assertEqual(out, [("head", "机会点"), ("small", "多 64% / 空 36%")])


class TestRender(unittest.TestCase):
    def test_render_page_size_and_overflow(self):
        rows = [("head", "栏目")] + [("small", f"原文行 {i}") for i in range(60)]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page_1.png")
            img, dropped = BB.render_page(rows, 1, "副标题", "仓库 004", "meta", out_path=out)
            self.assertEqual(img.size, (400, 300))
            self.assertEqual(img.mode, "1")
            self.assertTrue(os.path.exists(out))
            self.assertGreater(dropped, 0)

    def test_render_page_no_overflow(self):
        rows = [("small", "一行原文")]
        img, dropped = BB.render_page(rows, 2, "副标题", "仓库 003", "meta")
        self.assertEqual(dropped, 0)


if __name__ == "__main__":
    unittest.main()
