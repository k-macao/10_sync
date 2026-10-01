# -*- coding: utf-8 -*-
"""
打氧日报看板（octopus_board）的离线回归测试。

全部不联网：解析用 tests/fixtures/latest_sample.html（从仓库 02 真实日报里
裁出来的 4 个栏目：速查卡 / AI 全篇速览 / 今日预判 / 财经日历），推送用 mock。跑法：

    python3 -m unittest discover -s tests -v
    python3 -m unittest tests.test_octopus_board -v
"""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import octopus_board as B          # noqa: E402
import octopus_report as R         # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "latest_sample.html")
BEIJING = R.BEIJING_TZ


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


# =====================================================================
# 解析
# =====================================================================
class TestParse(unittest.TestCase):
    def setUp(self):
        self.report = R.parse_report(load_fixture())

    def test_sections_found(self):
        self.assertEqual(len(self.report.sections), 4)
        for kicker in ("00 · SHORT CARD", "01 · AI DIGEST", "02 · FORECAST", "03 · ECON CALENDAR"):
            self.assertIn(kicker, self.report.sections)

    def test_meta(self):
        self.assertEqual(self.report.date, "20260930")
        self.assertEqual(self.report.generated_at, "2026-09-30 19:47:49")
        self.assertEqual(self.report.today_sources, "15")
        self.assertEqual(self.report.total_sources, "17")
        self.assertIn("章鱼 AI", self.report.title)
        self.assertIn("当天源 15/17", self.report.summary())

    def test_label_value_split(self):
        """日报的「标签<br><b>内容</b>」要拆成 label / value。"""
        card = self.report.section("00 · SHORT CARD")
        item = card.find("水位")
        self.assertIsNotNone(item)
        self.assertIn("45/100", item.value)
        self.assertIn("南向", item.value)

    def test_label_ignores_decoration(self):
        """标签前的 ■ / ⏰ / 💧 / 🔎 装饰符号要被剥掉才能对上。"""
        card = self.report.section("00 · SHORT CARD")
        for label in ("明日剧本", "今明必看", "水位", "数据底"):
            self.assertIsNotNone(card.find(label), f"找不到标签 {label}")

    def test_digest_groups(self):
        """AI 速览里的 4 个分节要能各自取到内容。"""
        digest = self.report.section("01 · AI DIGEST")
        for group in ("市场与资金", "量化与策略", "政策与日程", "资讯与情绪"):
            self.assertIsNotNone(digest.find(group), f"找不到分节 {group}")
        self.assertTrue(R._group_value(digest, "市场与资金"))

    def test_notes_not_in_items(self):
        """「鲜鲜解读 / 阅读提醒」是附注，不该混进正文条目。"""
        digest = self.report.section("01 · AI DIGEST")
        for it in digest.items:
            self.assertNotIn("鲜鲜解读", it.label + it.value)
        self.assertTrue(any("阅读提醒" in n for n in digest.notes))

    def test_multi_value_group_uses_separator(self):
        digest = self.report.section("01 · AI DIGEST")
        value = digest.find("市场与资金").value
        self.assertIn(" ｜ ", value)
        self.assertEqual(len(R._group_value(digest, "市场与资金")), value.count(" ｜ ") + 1)

    def test_garbage_html_raises(self):
        for bad in ("", "<html>没有栏目</html>", "<div>随便一段文字</div>"):
            with self.assertRaises(R.ReportUnavailable):
                R.parse_report(bad)

    def test_structure_change_raises(self):
        """HTML 改版（分隔标记没了）必须显式报错，不能推一个空屏。"""
        with self.assertRaises(R.ReportUnavailable):
            R.parse_report("<html><body><h2>标题</h2><p>正文</p></body></html>")


# =====================================================================
# 财经日历（第 5 页的数据源）
# =====================================================================
class TestCalendarExtraction(unittest.TestCase):
    """表格里「日期只写第一行」的结构必须按行继承，否则时间点会串到别天。"""

    def setUp(self):
        self.report = R.parse_report(load_fixture())
        self.cal = self.report.section("03 · ECON CALENDAR")
        self.rows = R._calendar_rows(self.cal)

    def test_section_keeps_its_node(self):
        """条目已经被拍平成一行行文字，取回表格结构只能靠 Section.node。"""
        self.assertIsNotNone(self.cal.node)
        self.assertTrue(self.cal.node.find_all("table"))

    def test_rows_parsed(self):
        self.assertEqual(len(self.rows), 12)
        first = self.rows[0]
        self.assertEqual((first["date"], first["tag"], first["time"], first["country"]),
                         ("09-30 周三", "今天", "09:30", "中国"))
        self.assertEqual(first["name"], "非制造业PMI:商务活动")
        self.assertEqual(first["month"], "9月")

    def test_date_inherited_to_following_rows(self):
        """20:15 / 20:30 那几行日期列是空的，必须继承「今天」而不是变成明天。"""
        self.assertTrue(all(r["date"] == "09-30 周三" for r in self.rows[:5]), self.rows)
        self.assertTrue(all(r["date"] == "10-01 周四" for r in self.rows[5:]), self.rows)

    def test_decoration_and_flags_stripped(self):
        """★★★ 与「· 数据」是每行都有的装饰，不该占墨水屏的宽度。"""
        self.assertTrue(all("★" not in r["name"] for r in self.rows))
        self.assertTrue(all(r["name"] != "数据" for r in self.rows))

    def test_merge_names_only_rearranges_tokens(self):
        ism = [r["name"] for r in self.rows if r["time"] == "22:00"]
        self.assertEqual(len(ism), 7)
        merged = R._merge_names(ism)
        for part in ("ISM:PMI:供应商交付", "就业", "新订单", "制造业", "产出", "自有库存", "物价"):
            self.assertIn(part, merged)
        self.assertTrue(merged.endswith(":季调"), merged)

    def test_merge_names_keeps_unrelated_names(self):
        self.assertEqual(R._merge_names(["恒指", "上证"]), "恒指、上证")
        self.assertEqual(R._merge_names(["单个"]), "单个")
        self.assertEqual(R._merge_names([]), "")

    def test_day_items_keep_months_apart(self):
        """同一个 20:30 里 GDP 是 6月、PCE 是 8月 —— 不能写成同一个月份。"""
        days = R._calendar_day_groups(self.rows)
        today = [d for d in days if d.label.startswith("今天")][0]
        self.assertIn("年化实际GDP:终值（6月）", today.value)
        self.assertIn("核心PCE物价指数:同比/环比（8月）", today.value)
        self.assertNotIn("年化实际GDP:终值（8月）", today.value)
        # 同一时刻的多条事件月份一致时只写一次，省宽度
        self.assertIn("非制造业PMI:商务活动（9月）", today.value)
        self.assertNotIn("（9月）/", today.value)

    def test_every_rendered_row_is_in_source_html(self):
        """渲染出来的时间点必须逐条能在日报原文里找到，不许自己编。"""
        html = load_fixture()
        for r in self.rows:
            self.assertIn(r["time"], html)
            self.assertIn(r["name"], html)

    def test_busiest_day_rescued_from_notes(self):
        """「最密集日」在日报里算附注，日历页要把它捞回来（句尾口径去掉）。"""
        item = R._busiest_day(self.cal)
        self.assertIsNotNone(item)
        self.assertTrue(item.value.startswith("10-14 周三"))
        self.assertNotIn("规则合成", item.value)


# =====================================================================
# 排版计划
# =====================================================================
class TestBuildPages(unittest.TestCase):
    def setUp(self):
        self.report = R.parse_report(load_fixture())
        self.pages = R.build_pages(self.report)

    def test_five_pages(self):
        self.assertEqual(sorted(self.pages), [1, 2, 3, 4, 5])

    def test_no_empty_page(self):
        for pid, page in self.pages.items():
            self.assertTrue(page["items"], f"Page {pid} 没有内容")

    def test_page_roles(self):
        self.assertIn("短线速查卡", self.pages[1]["section_title"])
        self.assertIn("今日预判", self.pages[2]["section_title"])
        self.assertIn("AI 全篇速览", self.pages[3]["section_title"])
        self.assertIn("AI 全篇速览", self.pages[4]["section_title"])
        self.assertIn("时间节点", self.pages[5]["section_title"])
        self.assertEqual(self.pages[1]["density"], "tight")
        self.assertEqual(self.pages[2]["density"], "normal")
        self.assertEqual(self.pages[3]["density"], "compact")
        self.assertEqual(self.pages[5]["density"], "normal")

    def test_page1_priority(self):
        """P1 是结论面：定调 / 明日剧本 / 七日风 / 水位 必须在，新手小抄垫底。"""
        labels = [R.normalize_label(i.label) for i in self.pages[1]["items"]]
        for want in ("今日定调", "明日剧本", "七日风", "水位"):
            self.assertIn(want, labels)
        self.assertEqual(labels[-1], "新手小抄")

    def test_when_summary_lives_on_page5(self):
        """「今明必看」与日历页是同一件事：只在第 5 页的逐条时间点里出现，P1 不重复。"""
        p1_labels = [R.normalize_label(i.label) for i in self.pages[1]["items"]]
        self.assertNotIn("今明必看", p1_labels)

    def test_page5_is_calendar(self):
        """P5 = 今明两天的时间点 + 30 天窗口摘要（央行议息 / 最密集日）。"""
        labels = [R.normalize_label(i.label) for i in self.pages[5]["items"]]
        self.assertTrue(any(l.startswith("今天") for l in labels), labels)
        self.assertTrue(any(l.startswith("明天") for l in labels), labels)
        self.assertIn("30 天窗口摘要", labels)
        self.assertIn("央行议息 / 重要会议", labels)
        self.assertIn("最密集日", labels)

    def test_page2_priority(self):
        """P2 顺序即优先级：港股要在美股前面（美股滞后，价值低）。"""
        labels = [R.normalize_label(i.label) for i in self.pages[2]["items"]]
        for want in ("量化预测", "七日预测", "市场倾向", "A股", "港股"):
            self.assertIn(want, labels)
        self.assertLess(labels.index("港股"), labels.index("美股"))

    def test_pages_do_not_repeat(self):
        """五页正文不能整段重复。"""
        dumps = []
        for pid in (1, 2, 3, 4, 5):
            dumps.append("".join(i.label + i.value for i in self.pages[pid]["items"]))
        self.assertEqual(len(set(dumps)), 5)
        self.assertGreater(len(set(dumps[2]) & set(dumps[3])), 0)  # P3/P4 同栏目，内容不同

    def test_calendar_not_repeated_in_digest(self):
        """日历页占了「时间节点」，P4 的速览里就不再重复同一行。"""
        p4 = "".join(i.label + i.value for i in self.pages[4]["items"])
        self.assertNotIn(R.CALENDAR_DIGEST_DUP, p4)

    def test_degraded_section_still_builds(self):
        """少了 AI DIGEST / FORECAST 时也要能排（不推空屏）。"""
        import re
        trimmed = re.sub(
            r"<!--SPLIT--><div[^>]*>(?:(?!<!--SPLIT-->).)*?"
            r"<h2[^>]*>【(?:爪爪八爪鱼】AI 全篇速览|回游金枪鱼】今日预判)</h2>.*?(?=<!--SPLIT-->|$)",
            "", load_fixture(), flags=re.S,
        )
        report = R.parse_report(trimmed)
        self.assertIn("00 · SHORT CARD", report.sections)
        self.assertNotIn("01 · AI DIGEST", report.sections)
        pages = R.build_pages(report)
        for pid in (1, 2, 3, 4, 5):
            self.assertTrue(pages[pid]["items"], f"Page {pid} 退化后空了")

    def test_page5_falls_back_without_calendar(self):
        """日报没给日历栏目时，第 5 页要从别的栏目取内容，并且 P4 保留时间节点摘要。"""
        import re
        trimmed = re.sub(
            r"<!--SPLIT--><div[^>]*>(?:(?!<!--SPLIT-->).)*?<h2[^>]*>【探照安康鱼】时间节点</h2>.*?(?=</html>)",
            "", load_fixture(), flags=re.S,
        )
        report = R.parse_report(trimmed)
        self.assertNotIn("03 · ECON CALENDAR", report.sections)
        pages = R.build_pages(report)
        for pid in (1, 2, 3, 4, 5):
            self.assertTrue(pages[pid]["items"], f"没有日历栏目时 Page {pid} 空了")
        p4 = "".join(i.label + i.value for i in pages[4]["items"])
        self.assertIn(R.CALENDAR_DIGEST_DUP, p4)

    def test_almost_empty_raises(self):
        """内容太空要在解析阶段就报错，绝不能推一个空屏出去。"""
        import re
        trimmed = re.sub(r"<!--SPLIT-->.*", "", load_fixture(), flags=re.S)
        with self.assertRaises(R.ReportUnavailable):
            R.parse_report(trimmed)

    def test_single_section_report_raises_on_empty_pages(self):
        """只剩速查卡 + 日历时，build_pages 至少要保证 5 页都有东西（或明确报错）。"""
        import re
        trimmed = re.sub(
            r"<!--SPLIT--><div[^>]*>(?:(?!<!--SPLIT-->).)*?"
            r"<h2[^>]*>【(?:爪爪八爪鱼】AI 全篇速览|回游金枪鱼】今日预判)</h2>.*?(?=<!--SPLIT-->|$)",
            "", load_fixture(), flags=re.S,
        )
        pages = R.build_pages(R.parse_report(trimmed))
        for pid in (1, 2, 3, 4, 5):
            self.assertTrue(pages[pid]["items"], f"Page {pid} 空了")
        # 退化时补的内容必须来自真实日报，不能凭空造
        card = R.parse_report(trimmed).section("00 · SHORT CARD")
        real = {i.value for i in card.items if i.value}
        for pid in (2, 3, 4):
            for item in pages[pid]["items"]:
                self.assertIn(item.value, real)


# =====================================================================
# 新鲜度 / 指纹
# =====================================================================
class TestFreshness(unittest.TestCase):
    def setUp(self):
        self.report = R.parse_report(load_fixture())

    def test_fresh_report_ok(self):
        now = datetime(2026, 9, 30, 20, 0, tzinfo=BEIJING)
        ok, msg = R.check_freshness(self.report, now=now)
        self.assertTrue(ok, msg)
        self.assertIn("距今", msg)

    def test_stale_report_blocked(self):
        now = datetime(2026, 10, 5, 20, 0, tzinfo=BEIJING)
        ok, msg = R.check_freshness(self.report, now=now)
        self.assertFalse(ok)
        self.assertIn("停更", msg)

    def test_custom_limit(self):
        now = datetime(2026, 9, 30, 20, 0, tzinfo=BEIJING)
        self.assertFalse(R.check_freshness(self.report, max_age_hours=0.1, now=now)[0])
        self.assertTrue(R.check_freshness(self.report, max_age_hours=48, now=now)[0])

    def test_future_timestamp_blocked(self):
        now = datetime(2026, 9, 28, 20, 0, tzinfo=BEIJING)
        ok, msg = R.check_freshness(self.report, now=now)
        self.assertFalse(ok)
        self.assertIn("时钟", msg)

    def test_timezone_is_beijing(self):
        """时间戳是北京时间，不能当 UTC 算（否则会算成未来）。"""
        stamp = R.parse_dt("2026-09-30 19:47:49")
        self.assertEqual(stamp.utcoffset(), timedelta(hours=8))

    def test_fingerprint_stable_and_sensitive(self):
        pages = R.build_pages(self.report)
        first = R.report_fingerprint(self.report, pages)
        self.assertEqual(first, R.report_fingerprint(self.report, R.build_pages(self.report)))
        pages[1]["items"][0].value += " 改了"
        self.assertNotEqual(first, R.report_fingerprint(self.report, pages))


# =====================================================================
# 渲染
# =====================================================================
class TestRender(unittest.TestCase):
    def setUp(self):
        self.report = R.parse_report(load_fixture())
        self.pages = R.build_pages(self.report)
        B.mark_dropped(self.pages)

    def test_page_is_400x300_1bit(self):
        for pid, page in self.pages.items():
            img = B.render_page(pid, page, self.report)
            self.assertEqual(img.size, (400, 300))
            self.assertEqual(img.mode, "1")

    def test_pages_differ(self):
        pixels = {pid: B.render_page(pid, p, self.report).tobytes() for pid, p in self.pages.items()}
        self.assertEqual(len(set(pixels.values())), 5)

    def test_page_counter_shows_five(self):
        """顶栏右上角页码必须跟着 5 页走。"""
        img = B.render_page(5, self.pages[5], self.report)
        self.assertEqual(B.render_page(1, self.pages[1], self.report).size, (400, 300))
        self.assertEqual(img.size, (400, 300))

    def test_nothing_drawn_below_body(self):
        """正文不能画到页脚口径行的下面（会盖住 / 溢出）。"""
        for pid, page in self.pages.items():
            ops, *_ = B.layout_items(_draw_ctx(), page["items"], page, B.BODY_BOTTOM)
            for op in ops:
                if op[0] == "paint":
                    bottom = op[2] + len(op[1]) * op[3]
                    self.assertLessEqual(bottom, B.BODY_BOTTOM, f"Page {pid} 正文越界")
                elif op[0] == "group":
                    self.assertLessEqual(op[2], B.BODY_BOTTOM, f"Page {pid} 分节标题越界")

    def test_overflow_note_always_drawn(self):
        """有内容没上屏时，指针行必须真的画在正文区（不能被挤没、也不能压正文）。"""
        page = dict(self.pages[2])
        page["items"] = self.pages[2]["items"] * 6      # 人为超量
        d = _draw_ctx()
        _, dropped, _ = B.draw_items(d, page["items"], page)
        self.assertGreater(dropped, 0)
        ink = _ink_bbox(d, (0, B.BODY_TOP - 5, 400, B.BODY_BOTTOM + 1))
        self.assertIsNotNone(ink, "整页没画出任何东西")
        # 指针行占正文区最底下 ~NOTE_LINE_H*2 的位置，那里必须有黑像素
        self.assertGreater(_ink_bbox(d, (0, B.BODY_BOTTOM - B.NOTE_LINE_H * 2, 400, B.BODY_BOTTOM + 1)), 0,
                           "指针行没画在底部")

    def test_truncation_marked(self):
        """被截断的条目要带「…」，不能悄悄少字。"""
        long_item = R.Item("超长标签", "很长的内容" * 200)
        d = _draw_ctx()
        _, dropped, truncated = B.draw_items(
            d, [long_item], {"density": "normal", "items": []}
        )
        self.assertTrue(truncated)
        self.assertGreater(dropped, 0)
        # 末行必须以省略号结束（说明确实截断了、且如实标记）
        ops, *_ = B.layout_items(d, [long_item], {"density": "normal"}, B.BODY_BOTTOM)
        last = [op for op in ops if op[0] == "paint"][-1]
        self.assertEqual(last[1][-1][-1][0], "…")

    def test_group_header_not_dangling(self):
        """分节标题后面放不下一整条内容时，不该画个光秃秃的标题。"""
        items = [R.Item("市场与资金", "", kind="group")] + [
            R.Item("", "内容" * 60, kind="head") for _ in range(4)
        ]
        d = _draw_ctx()
        ops, *_ = B.layout_items(d, items, {"density": "compact"}, B.BODY_BOTTOM)
        groups = [op for op in ops if op[0] == "group"]
        paints = [op for op in ops if op[0] == "paint"]
        if groups:
            self.assertTrue(paints, "画了分节标题却一条内容都没画")
            self.assertLess(groups[0][2], paints[0][2])

    def test_title_override(self):
        img = B.render_page(1, self.pages[1], self.report, title="测试顶栏")
        self.assertEqual(img.size, (400, 300))


def _draw_ctx():
    from PIL import ImageDraw
    return ImageDraw.Draw(B.new_canvas())


def _ink(ctx, box=None):
    """统计区域内的黑像素数量（0 = 白，255 = 黑）。"""
    img = ctx._image if hasattr(ctx, "_image") else ctx
    crop = img.crop(box) if box else img
    return sum(255 - v for v in crop.convert("L").tobytes())


def _ink_bbox(ctx, box):
    return _ink(ctx, box)


# =====================================================================
# 抓取失败 = 整轮跳过（不推残缺内容）
# =====================================================================
class TestFetchFailClosed(unittest.TestCase):
    def test_all_sources_fail_raises(self):
        class _Boom:
            @staticmethod
            def get(*a, **kw):
                raise OSError("网络不通")

        old_get = R.requests.get
        R.requests.get = _Boom.get
        try:
            with self.assertRaises(R.ReportUnavailable) as ctx:
                R.fetch_report()
        finally:
            R.requests.get = old_get
        self.assertIn("保留墨水屏原有内容", str(ctx.exception))

    def test_http_error_raises(self):
        class _Resp:
            status_code = 404
            text = "404: Not Found"

        old_get = R.requests.get
        R.requests.get = staticmethod(lambda *a, **kw: _Resp())
        try:
            with self.assertRaises(R.ReportUnavailable):
                R.fetch_report()
        finally:
            R.requests.get = old_get

    def test_non_html_body_raises(self):
        class _Resp:
            status_code = 200
            text = "<html>rate limited</html>"

        old_get = R.requests.get
        R.requests.get = staticmethod(lambda *a, **kw: _Resp())
        try:
            with self.assertRaises(R.ReportUnavailable):
                R.fetch_report()
        finally:
            R.requests.get = old_get


# =====================================================================
# 标签归一化
# =====================================================================
class TestNormalizeLabel(unittest.TestCase):
    def test_strips_decoration(self):
        for raw, want in (
            ("■ 明日剧本", "明日剧本"),
            ("⏰ 今明必看", "今明必看"),
            ("💧 水位", "水位"),
            ("🔎 数据底", "数据底"),
            ("  A股", "A股"),
            ("🦐 活鲜度", "活鲜度"),
        ):
            self.assertEqual(R.normalize_label(raw), want)

    def test_keeps_content(self):
        self.assertEqual(R.normalize_label("恒生指数"), "恒生指数")
        self.assertEqual(R.normalize_label("P(周涨)"), "P(周涨)")


# =====================================================================
# 工作流自检
# =====================================================================
class TestWorkflow(unittest.TestCase):
    """工作流与 main.py 的衔接自检。"""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "w.yml"), encoding="utf-8") as fh:
            self.memo = fh.read()
        with open(os.path.join(root, ".github", "workflows", "run.yml"), encoding="utf-8") as fh:
            self.active = fh.read()

    def test_memo_workflow_has_octopus_pipeline(self):
        for key in ("schedule:", "workflow_dispatch:", "concurrency:", "cron:",
                    "--mode", "--max-age-hours", "OCTOPUS_STATE_FILE", "unittest"):
            self.assertIn(key, self.memo, f"w.yml 少了 {key}")

    def test_active_workflow_calls_main_py(self):
        self.assertRegex(self.active, r"(?m)^\s*python3 main\.py\s+\"\$\{ARGS\[@\]\}\"")

    def test_active_workflow_flags_are_tolerated_by_octopus_mode(self):
        """活动工作流传给 main.py 的参数必须受支持，且默认路径使用 octopus 模式。"""
        for flag in ("--mode", "--pages", "--max-age-hours", "--title", "--force", "--dry-run"):
            self.assertIn(flag, self.active, f"活动工作流缺少 {flag}")
        import main
        parsed = _parse_args(main, [
            "main.py", "--mode", "octopus", "--pages", "1,2", "--max-age-hours", "36",
            "--title", "测试", "--force", "--dry-run",
        ])
        self.assertEqual(parsed.mode, "octopus")
        self.assertEqual(parsed.pages, "1,2")
        self.assertEqual(parsed.max_age_hours, 36)
        self.assertEqual(parsed.title, "测试")
        self.assertTrue(parsed.force)
        self.assertTrue(parsed.dry_run)

    def test_board_default_is_five_pages(self):
        """看板自身默认推满 5 页；工作流显式传 --pages 覆盖时，main.py 会明确告警缺了哪页。"""
        import board_core
        self.assertEqual(
            sorted(p.strip() for p in board_core.ENABLED_PAGES.split(",") if p.strip()),
            ["1", "2", "3", "4", "5"],
        )

    def test_active_workflow_has_secrets_and_schedule(self):
        self.assertIn("ZECTRIX_API_KEY", self.active)
        self.assertIn("ZECTRIX_MAC", self.active)
        self.assertIn("concurrency:", self.active)
        self.assertIn("cron:", self.active)

    def test_schedule_is_every_two_hours(self):
        """跟随仓库 02 的更新节奏：模板 w.yml 锁死每 2 小时一次。"""
        self.assertIn("cron: '0 */2 * * *'", self.memo, "w.yml 的 cron 不是每 2 小时")
        self.assertNotIn("*/30 * * * *", self.memo, "w.yml 还留着 30 分钟一次的旧 cron")

    def test_active_workflow_schedule(self):
        """活动工作流要有 cron；还没同步成 2 小时时不下红，但要把待办喊出来。

        `.github/workflows/` 下的文件需要令牌具备 `workflows` 权限才能改，
        Agent 令牌没有这个权限时，只能由人在 GitHub 网页端补上这一行（见 README「Actions 全自动推」）。
        """
        crons = re.findall(r"(?m)^\s*- cron: '([^']+)'", self.active)
        self.assertTrue(crons, "活动工作流缺少 cron")
        if "0 */2 * * *" not in crons:
            print(
                "⚠️ .github/workflows/run.yml 的 cron 还是 "
                f"{crons[0]}，尚未同步成每 2 小时一次"
                "（Agent 令牌缺 workflows 权限，请在 GitHub 网页端把 cron 改成 "
                "'0 */2 * * *'，或整份复制 w.yml 覆盖）"
            )

    def test_synced_workflows_must_match(self):
        """一旦有人把 w.yml 复制到 run.yml，之后两边就必须逐字一致（cron 行除外）。

        `cron` 之所以单独放行：`.github/workflows/` 需要 `workflows` 权限才能改，
        改频率时可以先改 w.yml，再由人在网页端同步那一行（校验见
        `test_schedule_is_every_two_hours` 与 `test_active_workflow_schedule`）。
        除 cron 外的任何差异（例如 pages 默认值）照样当成不同步处理。
        """
        if "OCTOPUS_STATE_FILE" not in self.active:      # 还没同步过，跳过
            return

        def without_cron(text):
            # 整行归一（含行尾注释）：'0 */2 * * *' 与 '*/30 * * * *' 视作同一处「待同步」
            return re.sub(r"(?m)^(\s*)- cron:.*$", r"\1- cron: <CRON>", text).strip()

        self.assertEqual(
            without_cron(self.memo), without_cron(self.active),
            "w.yml 与 .github/workflows/run.yml 除 cron 行外不一致，请同步后提交",
        )

    def test_no_hardcoded_secrets(self):
        for bad in ("ZECTRIX_API_KEY: k-macao", "token: ghp_", "key-1234"):
            self.assertNotIn(bad, self.memo)
            self.assertNotIn(bad, self.active)


def _parse_args(main_module, argv):
    """用 main.parse_args 解析参数（把 sys.argv 换掉再还原）。"""
    import sys
    old = sys.argv
    sys.argv = list(argv)
    try:
        return main_module.parse_args()
    finally:
        sys.argv = old


if __name__ == "__main__":
    unittest.main(verbosity=2)