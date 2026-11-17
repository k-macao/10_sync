# -*- coding: utf-8 -*-
"""五页全球行情数据面板（1=A / 2=港 / 3=韩 / 4=日 / 5=美）离线单元测试。

所有测试完全离线，不发起任何真实网络请求或 Zectrix 推送。
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from PIL import Image

import board_core
import main
import market_board
import market_quotes as mq

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _fixture_texts(jp_open: bool = False, backups: bool = False) -> dict:
    jp = _read_fixture("market_naver_jp.json")
    if jp_open:
        jp = jp.replace('"CLOSE"', '"OPEN"')
    texts = {
        mq.SOURCE_TENCENT: _read_fixture("market_tencent.txt"),
        mq.SOURCE_SINA: _read_fixture("market_sina.txt"),
        mq.SOURCE_NAVER_KR: _read_fixture("market_naver_kr.json"),
        mq.SOURCE_NAVER_JP: jp,
    }
    if backups:
        texts.update({
            mq.SOURCE_SINA_INTL: _read_fixture("market_sina_intl.txt"),
            mq.SOURCE_NETEASE: _read_fixture("market_netease.json"),
            mq.SOURCE_EASTMONEY: _read_fixture("market_eastmoney.json"),
        })
    return texts


def _fixture_panel(jp_open: bool = False, backups: bool = False) -> mq.PanelData:
    return mq.build_panel(_fixture_texts(jp_open=jp_open, backups=backups), demo=False)


class TestMarketSpecAndParsers(unittest.TestCase):
    """锁定五页顺序、原生币种标注与四路公开接口解析。"""

    def test_five_pages_exact_order_and_currencies(self):
        self.assertEqual([m.page_id for m in mq.MARKET_PAGES], [1, 2, 3, 4, 5])
        self.assertEqual(list(mq.MARKET_ORDER), ["A", "港", "韩", "日", "美"])
        self.assertEqual([m.code for m in mq.MARKET_PAGES], ["A", "港", "韩", "日", "美"])
        self.assertEqual(
            [m.currency for m in mq.MARKET_PAGES],
            ["人民币", "港元", "韩元", "日元", "美元"],
        )
        self.assertEqual([len(m.rows) for m in mq.MARKET_PAGES], [5, 3, 2, 2, 3])
        self.assertEqual(len(mq.ALL_ROWS), 15)

    def test_parse_tencent_all_regions_and_units(self):
        tx = mq.parse_tencent(_read_fixture("market_tencent.txt"))
        # A 股：成交量 手*100 -> 股，成交额 万元*1e4 -> 人民币
        sh = tx["sh000001"]
        self.assertAlmostEqual(sh.last, 3634.35)
        self.assertAlmostEqual(sh.change, -21.48)
        self.assertAlmostEqual(sh.pct, -0.59)
        self.assertAlmostEqual(sh.volume, 472293657 * 100.0)
        self.assertAlmostEqual(sh.turnover, 77061417 * 10_000.0)
        self.assertEqual(mq.fmt_as_of(sh.time), "11-13 16:14")
        self.assertFalse(sh.delayed)

        # 港股：r_hk* 归一到 hk*（实时），不带 r_ 的 hkHSCEI 存入 delayed_hkHSCEI 兜底；万港元*1e4 -> 港元
        self.assertAlmostEqual(tx["hkHSI"].last, 23637.60)
        self.assertAlmostEqual(tx["hkHSI"].turnover, 19585700.714 * 10_000.0)
        self.assertIsNone(tx["hkHSI"].volume)
        self.assertEqual(mq.fmt_as_of(tx["hkHSI"].time), "11-13 18:31")
        self.assertFalse(tx["hkHSI"].delayed)
        self.assertAlmostEqual(tx["hkHSCEI"].last, 7937.53)
        # 不带 r_ 的 hkHSCEI 必须打上 delayed 标记并隔离存储，不能覆盖实时 r_hkHSCEI
        delayed_sample = mq.parse_tencent(
            'v_hkHSCEI="100~国企指数~HSCEI~7900.00~8056.76~8010.68~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~2026/11/13 15:53:40~-156.76~-1.95~8042.01~7890.00~0~0~7100000~0~0~~0~0~1.88";'
        )
        self.assertIn("delayed_hkHSCEI", delayed_sample)
        self.assertTrue(delayed_sample["delayed_hkHSCEI"].delayed)

        # 美股：11月美东标准时 EST(-05:00) +13h 转北京时间；字段[37]丢弃不作美元成交额
        dji = tx["usDJI"]
        self.assertAlmostEqual(dji.last, 52347.55)
        self.assertAlmostEqual(dji.change, 50.46)
        self.assertEqual(mq.fmt_as_of(dji.time), "11-14 05:43")
        self.assertEqual(mq.fmt_as_of(tx["usIXIC"].time), "11-14 06:15")
        self.assertAlmostEqual(dji.volume, 645244111.0)
        self.assertIsNone(dji.turnover)

        # 全球简版指数：日经、KOSPI、KOSDAQ
        self.assertAlmostEqual(tx["gzN225"].last, 61142.13)
        self.assertAlmostEqual(tx["gzKS11"].last, 5290.53)
        self.assertAlmostEqual(tx["gzKOSDAQ"].last, 699.66)

    def test_parse_tencent_us_dst_summer_conversion(self):
        # 7月为美东夏令时 EDT(-04:00)，16:00 EDT = 次日 04:00 北京时间
        line = (
            'v_usDJI="200~道琼斯~DJI~50100.00~50000.00~50010.00~100000000~0~0~'
            '0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~'
            '2026-07-15 16:00:05~100.00~0.20~50200.00~49950.00~0~100000000~999999999~'
            '0~0~0~0~0~0.50";'
        )
        tx = mq.parse_tencent(line)
        self.assertEqual(mq.fmt_as_of(tx["usDJI"].time), "07-16 04:00")
        self.assertEqual(tx["usDJI"].time.date().isoformat(), "2026-07-15")
        self.assertIsNone(tx["usDJI"].turnover)

    def test_parse_tencent_ignores_garbage_and_zero_price(self):
        bad = 'garbage\nv_sh000001="1~上证~000001~0.00~0~0";\nv_unknown="1~x~y~12";'
        self.assertEqual(mq.parse_tencent(bad), {})

    def test_parse_sina_all_regions_and_units(self):
        sn = mq.parse_sina(_read_fixture("market_sina.txt"))
        # 上证成交量手*100，深证成交量已是股
        self.assertAlmostEqual(sn["sh000001"].volume, 472293657 * 100.0)
        self.assertAlmostEqual(sn["sz399001"].volume, 55925520800.0)
        self.assertAlmostEqual(sn["sh000001"].turnover, 770614169984.0)
        # 港股：[11]千港元*1e3 -> 港元，[12]股数
        self.assertAlmostEqual(sn["hkHSI"].turnover, 195857007.140 * 1000.0)
        self.assertAlmostEqual(sn["hkHSI"].volume, 11525719783.0)
        self.assertEqual(mq.fmt_as_of(sn["hkHSI"].time), "11-13 16:08")
        # 美股：gb_$inx -> usINX
        self.assertAlmostEqual(sn["usINX"].last, 7561.67)
        self.assertEqual(mq.fmt_as_of(sn["usINX"].time), "11-14 05:43")
        self.assertAlmostEqual(sn["usINX"].volume, 2727332784.0)
        self.assertEqual(mq.parse_sina('var hq_str_rt_hkHSI="";'), {})

    def test_parse_naver_kr_and_jp(self):
        kr = mq.parse_naver(_read_fixture("market_naver_kr.json"))
        self.assertAlmostEqual(kr["KOSPI"].last, 5290.53)
        self.assertAlmostEqual(kr["KOSPI"].change, -52.20)
        self.assertAlmostEqual(kr["KOSPI"].prev_close, 5342.73)
        self.assertAlmostEqual(kr["KOSPI"].turnover, 16718835000000.0)
        # 20:15 KST (+09:00) = 19:15 北京时间 (+08:00)
        self.assertEqual(mq.fmt_as_of(kr["KOSPI"].time), "11-13 19:15")
        self.assertEqual(kr["KOSPI"].status, "CLOSE")
        self.assertFalse(kr["KOSPI"].delayed)

        jp = mq.parse_naver(_read_fixture("market_naver_jp.json"))
        self.assertAlmostEqual(jp["N225"].last, 61142.35)
        self.assertAlmostEqual(jp["TOPX"].last, 3941.57)
        # 日股不采用 Naver 未确认口径的 accumulatedTradingValueRaw
        self.assertIsNone(jp["N225"].turnover)
        self.assertIsNone(jp["TOPX"].turnover)
        self.assertAlmostEqual(jp["N225"].volume, 1785916000.0)
        # 15:45 JST = 14:45 北京时间
        self.assertEqual(mq.fmt_as_of(jp["N225"].time), "11-13 14:45")
        self.assertTrue(jp["N225"].delayed)
        self.assertEqual(mq.parse_naver("{not json"), {})


class TestMergeFallbackAndDelta(unittest.TestCase):
    """验证多源互补、单源断流兜底、日经盘中实时叠加与 1 分钟变化演进。"""

    def test_merge_full_and_hk_volume_backfill(self):
        panel = _fixture_panel(jp_open=False)
        self.assertEqual(panel.live_rows, 15)
        self.assertEqual(panel.total_rows, 15)
        # 港股：腾讯实时点位/成交额 + 新浪成交股数互补
        hsi = panel.pages[2].rows[0].quote
        self.assertAlmostEqual(hsi.last, 23637.60)
        self.assertAlmostEqual(hsi.volume, 11525719783.0)
        self.assertAlmostEqual(hsi.turnover, 19585700.714 * 10_000.0)
        # 收盘态日经不叠加 gzN225，保留 Naver 官方收盘价
        n225 = panel.pages[4].rows[0].quote
        self.assertAlmostEqual(n225.last, 61142.35)
        # 五页底部汇总均有值且单位明确
        for pid in (1, 2, 3, 4, 5):
            self.assertIsNotNone(panel.pages[pid].summary_value)
        self.assertIn("人民币", mq.fmt_cn_amount(panel.pages[1].summary_value, "人民币"))
        self.assertIn("港元", mq.fmt_cn_amount(panel.pages[2].summary_value, "港元"))
        self.assertIn("韩元", mq.fmt_cn_amount(panel.pages[3].summary_value, "韩元"))

    def test_jp_open_overlays_realtime_gzn225_and_clamps_high_low(self):
        tx = mq.parse_tencent(_read_fixture("market_tencent.txt"))
        tx["gzN225"].last = 61950.00  # 突破 Naver 15m 延迟高点 61902.50，但在 3% 阈值内
        tx["gzN225"].change = 1130.64
        tx["gzN225"].pct = 1.86
        jp = mq.parse_naver(_read_fixture("market_naver_jp.json").replace('"CLOSE"', '"OPEN"'))
        pages = mq.merge_pages(tx, {}, {}, jp)
        n225 = pages[4].rows[0].quote
        self.assertAlmostEqual(n225.last, 61950.00)
        self.assertGreaterEqual(n225.high, 61950.00)
        self.assertFalse(n225.delayed)

        # 若 gzN225 异常偏离超过 3%，拒绝叠加，改信 Naver
        tx["gzN225"].last = 70000.00
        jp2 = mq.parse_naver(_read_fixture("market_naver_jp.json").replace('"CLOSE"', '"OPEN"'))
        pages2 = mq.merge_pages(tx, {}, {}, jp2)
        self.assertAlmostEqual(pages2[4].rows[0].quote.last, 61142.35)

    def test_single_source_outages_keep_all_five_pages_alive(self):
        texts = _fixture_texts()

        # 腾讯全挂 -> 新浪 + Naver 依然 15/15
        p_no_tx = mq.build_panel({**texts, mq.SOURCE_TENCENT: None})
        self.assertEqual(p_no_tx.live_rows, 15)

        # 新浪全挂 -> 腾讯 + Naver 依然 15/15
        p_no_sn = mq.build_panel({**texts, mq.SOURCE_SINA: None})
        self.assertEqual(p_no_sn.live_rows, 15)

        # Naver 韩股全挂 -> 腾讯 gzKS11 / gzKOSDAQ 兜底 Page 3（2/2）
        p_no_kr = mq.build_panel({**texts, mq.SOURCE_NAVER_KR: None})
        self.assertEqual(p_no_kr.pages[3].live_rows, 2)

        # Naver 日股全挂 -> 腾讯 gzN225 兜底 Page 4 日经225（1/2）
        p_no_jp = mq.build_panel({**texts, mq.SOURCE_NAVER_JP: None})
        self.assertEqual(p_no_jp.pages[4].live_rows, 1)
        self.assertAlmostEqual(p_no_jp.pages[4].rows[0].quote.last, 61142.13)

        # 四路全挂 -> 0/15
        p_empty = mq.build_panel({k: None for k in texts})
        self.assertEqual(p_empty.live_rows, 0)

    def test_delta_state_idempotent_tick_and_new_day_reset(self):
        state: dict = {}
        p1 = _fixture_panel()
        state = mq.apply_delta_state(p1, state)
        # 首轮仅建立基线，不伪造 1 分钟变化
        self.assertIsNone(p1.pages[1].rows[0].quote.delta)
        self.assertIsNone(p1.pages[1].summary_delta)

        # 第二轮：同日行情跳动（上证 +5.65，两市成交额 +12亿人民币）
        p2 = _fixture_panel()
        sh2 = p2.pages[1].rows[0].quote
        sh2.last = round(sh2.last + 5.65, 2)
        sh2.time = datetime(2026, 11, 13, 16, 15, 0, tzinfo=mq.TZ_CN)
        p2.pages[1].summary_value += 1_200_000_000.0
        state = mq.apply_delta_state(p2, state)
        self.assertAlmostEqual(sh2.delta, 5.65, places=2)
        self.assertAlmostEqual(sh2.prev_last, 3634.35, places=2)
        self.assertGreater(sh2.delta_pct, 0.0)
        self.assertAlmostEqual(p2.pages[1].summary_delta, 1_200_000_000.0, places=0)

        # 第三轮：休市后重复抓到同一笔行情 -> 保留最后一跳 delta，状态完全幂等
        p3 = copy.deepcopy(p2)
        for r in p3.pages[1].rows:
            r.quote.delta = None
            r.quote.delta_pct = None
            r.quote.prev_last = None
        p3.pages[1].summary_delta = None
        state_before = copy.deepcopy(state)
        state = mq.apply_delta_state(p3, state)
        self.assertAlmostEqual(p3.pages[1].rows[0].quote.delta, 5.65, places=2)
        self.assertAlmostEqual(p3.pages[1].summary_delta, 1_200_000_000.0, places=0)
        self.assertEqual(state["quotes"], state_before["quotes"])
        self.assertEqual(state["totals"], state_before["totals"])

        # 第四轮：跨交易日开盘 -> 重置 1 分钟基线，不把隔夜跳空误算成 1 分钟变化
        p4 = _fixture_panel()
        for r in p4.pages[1].rows:
            r.quote.time = datetime(2026, 11, 16, 9, 31, 0, tzinfo=mq.TZ_CN)
        p4.pages[1].rows[0].quote.last = 3680.00
        state = mq.apply_delta_state(p4, state)
        self.assertIsNone(p4.pages[1].rows[0].quote.delta)
        self.assertIsNone(p4.pages[1].summary_delta)

    def test_formatters_explicit_units_and_signs(self):
        for cur in ("人民币", "港元", "韩元", "日元", "美元"):
            for val in (85_000_000, 770_600_000_000, 16_700_000_000_000):
                s = mq.fmt_cn_amount(val, cur)
                self.assertIn(cur, s)
        self.assertEqual(mq.fmt_cn_amount(11_520_000_000, "股"), "115亿股")
        self.assertEqual(mq.fmt_change(0.0), "0.00")
        self.assertEqual(mq.fmt_change(12.345), "+12.35")
        self.assertEqual(mq.fmt_change(-3.4), "-3.40")
        self.assertEqual(mq.fmt_pct(1.234), "+1.23%")
        self.assertEqual(mq.fmt_pct(-0.5), "-0.50%")
        self.assertEqual(mq.arrow_for(1.0), "▲")
        self.assertEqual(mq.arrow_for(-1.0), "▼")
        self.assertEqual(mq.arrow_for(0.0), "")


class TestBackupSourcesAggregation(unittest.TestCase):
    """7 路备用源：解析正确、多源一致性投票、主源全挂时兜底不空屏。"""

    def test_three_new_parsers_units_and_mapping(self):
        intl = mq.parse_sina_intl(_read_fixture("market_sina_intl.txt"))
        self.assertEqual(
            sorted(intl),
            ["KOSPI", "N225", "hkHSI", "usDJI", "usINX", "usIXIC"],
        )
        self.assertAlmostEqual(intl["hkHSI"].last, 23655.20)
        self.assertAlmostEqual(intl["usIXIC"].pct, -0.97)
        self.assertEqual(intl["hkHSI"].source, mq.SOURCE_SINA_INTL)
        # 新浪国际指数无成交量/成交额，不能凭空造数
        self.assertIsNone(intl["hkHSI"].volume)
        self.assertIsNone(intl["hkHSI"].turnover)

        ne = mq.parse_netease(_read_fixture("market_netease.json"))
        self.assertEqual(len(ne), 5)
        self.assertAlmostEqual(ne["sh000001"].last, 3634.3529)
        self.assertAlmostEqual(ne["sh000001"].prev_close, 3655.8295)
        # 成交量口径是「股」：与腾讯（手×100）同量级，不能再次 ×100
        self.assertAlmostEqual(ne["sh000001"].volume, 472293657.0)
        self.assertAlmostEqual(ne["sh000001"].turnover, 770614169984.0)
        self.assertEqual(mq.fmt_as_of(ne["sh000001"].time), "11-13 15:43")

        em = mq.parse_eastmoney(_read_fixture("market_eastmoney.json"))
        self.assertEqual(len(em), 11)
        self.assertAlmostEqual(em["hkHSTECH"].last, 3813.19)
        self.assertAlmostEqual(em["usDJI"].pct, 0.10)
        self.assertAlmostEqual(em["sh000001"].volume, 472293657.0)
        self.assertAlmostEqual(em["hkHSI"].volume, 11525719783.0)
        # 脏数据（null / "-"）不产生有效行情
        dirty = '{"data":{"diff":[{"f2":"-","f3":"-","f12":"000001","f13":1}]}}'
        self.assertEqual(mq.parse_eastmoney(dirty), {})
        self.assertEqual(mq.parse_netease("_ntes_quote_callback();"), {})
        self.assertEqual(mq.parse_netease("not json"), {})

    def test_sources_tuple_covers_seven_feeds_with_fixtures(self):
        self.assertEqual(
            [src for src, *_ in mq.SOURCES],
            [
                mq.SOURCE_TENCENT, mq.SOURCE_SINA, mq.SOURCE_NAVER_KR,
                mq.SOURCE_NAVER_JP, mq.SOURCE_SINA_INTL, mq.SOURCE_NETEASE,
                mq.SOURCE_EASTMONEY,
            ],
        )
        for src, *_ in mq.SOURCES:
            self.assertTrue((FIXTURES and os.path.exists(
                os.path.join(FIXTURES, mq.FIXTURE_FILES[src])
            )), f"{src} 缺少离线 fixture")

    def test_backup_sources_alone_keep_five_pages_alive(self):
        backups_only = {
            mq.SOURCE_TENCENT: None,
            mq.SOURCE_SINA: None,
            mq.SOURCE_NAVER_KR: None,
            mq.SOURCE_NAVER_JP: None,
            mq.SOURCE_SINA_INTL: _read_fixture("market_sina_intl.txt"),
            mq.SOURCE_NETEASE: _read_fixture("market_netease.json"),
            mq.SOURCE_EASTMONEY: _read_fixture("market_eastmoney.json"),
        }
        panel = mq.build_panel(backups_only)
        self.assertEqual(panel.pages[1].live_rows, 5)   # 网易 + 东方财富
        self.assertEqual(panel.pages[2].live_rows, 3)   # 新浪国际 + 东方财富
        self.assertEqual(panel.pages[3].live_rows, 1)   # 新浪国际（KOSPI）
        self.assertEqual(panel.pages[4].live_rows, 1)   # 新浪国际（日经225）
        self.assertEqual(panel.pages[5].live_rows, 3)   # 新浪国际 + 东方财富
        self.assertEqual(panel.sources[mq.SOURCE_EASTMONEY], True)

        # 腾讯单挂：A/港/美 由其余 6 路补齐，依旧 15/15
        texts = _fixture_texts(backups=True)
        p_no_tx = mq.build_panel({**texts, mq.SOURCE_TENCENT: None})
        self.assertEqual(p_no_tx.live_rows, 15)
        self.assertEqual(p_no_tx.pages[1].rows[0].quote.source, mq.SOURCE_SINA)

    def test_consistency_vote_prefers_fresh_source_only(self):
        tx = mq.parse_tencent(_read_fixture("market_tencent.txt"))
        em = mq.parse_eastmoney(_read_fixture("market_eastmoney.json"))

        # 1) 偏差在容差内（东方财富比腾讯高 0.4%）：取主源腾讯，不被备用源改写点位
        em["usDJI"].last = round(tx["usDJI"].last * 1.004, 2)
        pages = mq.merge_pages(tx, {}, {}, {}, eastmoney=em)
        self.assertAlmostEqual(pages[5].rows[0].quote.last, tx["usDJI"].last)

        # 2) 偏差超容差且备用源时间更旧/无时间：仍信主源（防延迟源把数据带偏）
        em["usDJI"].last = round(tx["usDJI"].last * 1.2, 2)
        pages2 = mq.merge_pages(tx, {}, {}, {}, eastmoney=em)
        self.assertAlmostEqual(pages2[5].rows[0].quote.last, tx["usDJI"].last)

        # 3) 偏差超容差，但备用源时间严格更新（主源卡住）：改信备用源
        stale_tx = mq.parse_tencent(_read_fixture("market_tencent.txt"))
        stale_tx["usDJI"].time = datetime(2026, 11, 13, 16, 43, 26, tzinfo=mq.TZ_US)
        em2 = mq.parse_eastmoney(_read_fixture("market_eastmoney.json"))
        em2["usDJI"].last = 53999.99
        em2["usDJI"].time = datetime(2026, 11, 13, 17, 30, 0, tzinfo=mq.TZ_US)
        pages3 = mq.merge_pages(stale_tx, {}, {}, {}, eastmoney=em2)
        self.assertAlmostEqual(pages3[5].rows[0].quote.last, 53999.99)

    def test_backup_source_fills_missing_fields_only(self):
        tx = mq.parse_tencent(_read_fixture("market_tencent.txt"))
        # 腾讯 A 股行缺成交额时，用网易/东方财富补上（不改点位与涨跌）
        for k in ("sh000001",):
            tx[k].turnover = None
        ne = mq.parse_netease(_read_fixture("market_netease.json"))
        pages = mq.merge_pages(tx, {}, {}, {}, netease=ne)
        q = pages[1].rows[0].quote
        self.assertAlmostEqual(q.last, tx["sh000001"].last)
        self.assertAlmostEqual(q.turnover, ne["sh000001"].turnover)
        self.assertEqual(q.source, mq.SOURCE_TENCENT)


class TestFiveMinuteCadence(unittest.TestCase):
    """推送节奏默认 5 分钟：文案随间隔变化，循环与工作流默认值一致。"""

    def test_labels_follow_interval(self):
        self.assertEqual(market_board.interval_minutes(None), 5)
        self.assertEqual(market_board.delta_label(None), "5分")
        self.assertEqual(market_board.update_label(None), "5分钟更新")
        self.assertEqual(market_board.delta_label(60), "1分")
        self.assertEqual(market_board.update_label(900), "15分钟更新")

        panel = mq.demo_panel(with_delta=True)
        q = panel.pages[1].rows[0].quote
        self.assertIn("5分", market_board._delta_short(q))
        self.assertIn("1分", market_board._delta_short(q, tag="1分"))
        with_pct = market_board._delta_short(q, with_pct=True)
        self.assertTrue(with_pct.startswith("5分"))

    def test_board_page_shows_five_minute_tags(self):
        panel = mq.demo_panel(with_delta=True)
        img5 = market_board.render_market_page(panel.pages[1], demo=True, validate=True)
        img1 = market_board.render_market_page(
            panel.pages[1], demo=True, validate=True, interval_seconds=60,
        )
        self.assertNotEqual(
            market_board.image_digest(img5), market_board.image_digest(img1),
            "间隔不同应导致「N分 / N分钟更新」文案不同，画面指纹随之变化",
        )
        self.assertEqual(market_board.DEFAULT_UPDATE_TAG, "5分钟更新")

    def test_loop_defaults_to_300s_and_passes_interval_to_cycle(self):
        cfg = ml_parse(["--dry-run"])
        self.assertEqual(cfg.interval, 300.0)
        cmd = market_board_delta_cmd(cfg)
        self.assertIn("--interval", cmd)
        self.assertEqual(cmd[cmd.index("--interval") + 1], "300")


def ml_parse(argv):
    import market_loop
    return market_loop.parse_args(list(argv))


def market_board_delta_cmd(cfg):
    import market_loop
    return market_loop.build_cycle_cmd(cfg)


class TestRenderAndPreflight(unittest.TestCase):
    """验证全部 5 页 400×300 1-bit 渲染、LayoutError 零越界校验、极端值与像素哈希。"""

    def _assert_clean_image(self, img: Image.Image, label: str = ""):
        self.assertEqual(img.size, (400, 300), label)
        self.assertEqual(img.mode, "1", label)
        hist = img.histogram()
        black_ratio = hist[0] / (400 * 300)
        self.assertGreater(black_ratio, 0.05, f"{label} 画面过空 ({black_ratio:.1%})")
        self.assertLess(black_ratio, 0.45, f"{label} 画面过黑 ({black_ratio:.1%})")

    def test_all_five_pages_baseline_and_delta_render_cleanly(self):
        base = mq.demo_panel(with_delta=False)
        delt = mq.demo_panel(with_delta=True)
        for pid in (1, 2, 3, 4, 5):
            im_b = market_board.render_market_page(base.pages[pid], demo=True, validate=True)
            im_d = market_board.render_market_page(delt.pages[pid], demo=True, validate=True)
            im_live = market_board.render_market_page(delt.pages[pid], demo=False, validate=True)
            self._assert_clean_image(im_b, f"P{pid}-baseline")
            self._assert_clean_image(im_d, f"P{pid}-delta")
            self._assert_clean_image(im_live, f"P{pid}-live")
            # 同一数据重复渲染像素指纹必须完全一致（休市零刷屏）
            im_d2 = market_board.render_market_page(delt.pages[pid], demo=True, validate=True)
            self.assertEqual(market_board.image_digest(im_d), market_board.image_digest(im_d2))
            # 加入 1 分钟变化后，5 页画面都必须真实发生变化
            self.assertNotEqual(
                market_board.image_digest(im_b),
                market_board.image_digest(im_d),
                f"Page {pid} 加入 1 分钟变化后画面未变",
            )

    def test_extreme_numbers_open_status_and_custom_title_never_overflow(self):
        panel = mq.demo_panel(with_delta=True)
        for pid, page in panel.pages.items():
            page.summary_value = (page.summary_value or 1e12) * 8.5
            page.summary_delta = -98_765_432_100.0
            for i, row in enumerate(page.rows):
                q = row.quote
                sign = 1 if i % 2 == 0 else -1
                q.last = 128456.78
                q.open = 115234.56
                q.prev_close = 114000.00
                q.high = 132999.99
                q.low = 111000.01
                q.change = sign * 14456.78
                q.pct = sign * 12.68
                q.amplitude = 19.30
                q.delta = sign * 1234.56
                q.delta_pct = sign * 0.97
                q.volume = 987_654_321_000.0
                if q.turnover is not None:
                    q.turnover = 98_765_432_100_000.0
                q.status = "OPEN"
            img = market_board.render_market_page(
                page, custom_title="章鱼全球实时看板", validate=True
            )
            self._assert_clean_image(img, f"P{pid}-extreme")

    def test_partial_minimal_and_empty_pages_render_cleanly(self):
        panel = _fixture_panel(jp_open=True)
        for pid, page in panel.pages.items():
            # 仅保留第 1 条且只含点位/涨跌（模拟简版兜底行情），其余行缺失
            first = page.rows[0].quote
            first.open = None
            first.high = None
            first.low = None
            first.amplitude = None
            first.volume = None
            first.turnover = None
            for row in page.rows[1:]:
                row.quote = mq.Quote()
            page.summary_value = None
            page.summary_delta = None
            img = market_board.render_market_page(page, validate=True)
            self._assert_clean_image(img, f"P{pid}-minimal")

    def test_contact_sheet_generation(self):
        panel = mq.demo_panel(with_delta=True)
        images = market_board.render_all_pages(panel, validate=True)
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "sheet.png")
            sheet = market_board.build_contact_sheet(images, out)
            self.assertIn(sheet.mode, ("L", "RGB"))
            self.assertGreater(sheet.width, 800)
            self.assertGreater(sheet.height, 900)
            self.assertTrue(os.path.exists(out))


class TestRunOnceDedupAndResilience(unittest.TestCase):
    """验证按页哈希去重、单市场跳动局部刷新、失败自动补推与退出码。"""

    def test_missing_credentials_and_bad_pages_return_fatal(self):
        with mock.patch.object(board_core, "API_KEY", None), \
             mock.patch.object(board_core, "MAC_ADDRESS", None):
            res = market_board.run_once(dry_run=False, demo=True, state_file=None)
        self.assertEqual(res.exit_code, market_board.EXIT_FATAL)

        res_bad = market_board.run_once(pages_arg="9", dry_run=True, demo=True, state_file=None)
        self.assertEqual(res_bad.exit_code, market_board.EXIT_FATAL)

    def test_per_page_dedup_selective_tick_and_push_failure_retry(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = os.path.join(td, "market_state.json")
            out_dir = os.path.join(td, "out")
            meta_file = os.path.join(td, "meta.json")
            pushed_calls: list[str] = []

            def ok_pusher(path, page_id, dry_run=False):
                pushed_calls.append(str(page_id))
                return True

            with mock.patch.object(board_core, "API_KEY", "k"), \
                 mock.patch.object(board_core, "MAC_ADDRESS", "AA:BB"):
                # 第 1 轮：五页全部首次上屏
                with mock.patch.object(market_board, "collect_panel", return_value=_fixture_panel()):
                    r1 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir, meta_file=meta_file,
                        pusher=ok_pusher,
                    )
                self.assertEqual(r1.exit_code, market_board.EXIT_OK)
                self.assertEqual(r1.pushed, [1, 2, 3, 4, 5])
                self.assertEqual(pushed_calls, ["1", "2", "3", "4", "5"])

                # 第 2 轮：五页行情均未变化 -> 0 次推送（零闪屏）
                pushed_calls.clear()
                with mock.patch.object(market_board, "collect_panel", return_value=_fixture_panel()):
                    r2 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir, pusher=ok_pusher,
                    )
                self.assertEqual(r2.exit_code, market_board.EXIT_OK)
                self.assertEqual(r2.pushed, [])
                self.assertEqual(r2.unchanged, [1, 2, 3, 4, 5])
                self.assertEqual(pushed_calls, [])

                # 第 3 轮：仅美股（Page 5）跳动 +12.50 点 -> 只推 Page 5，1-4 页跳过
                pushed_calls.clear()
                p3 = _fixture_panel()
                dji3 = p3.pages[5].rows[0].quote
                dji3.last = round(dji3.last + 12.50, 2)
                dji3.time = datetime(2026, 11, 13, 17, 16, 0, tzinfo=mq.TZ_US)
                with mock.patch.object(market_board, "collect_panel", return_value=p3):
                    r3 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir, meta_file=meta_file,
                        pusher=ok_pusher,
                    )
                self.assertEqual(r3.exit_code, market_board.EXIT_OK)
                self.assertEqual(r3.pushed, [5])
                self.assertEqual(r3.unchanged, [1, 2, 3, 4])
                self.assertEqual(pushed_calls, ["5"])
                with open(meta_file, encoding="utf-8") as fh:
                    meta = json.load(fh)
                p5_meta = [p for p in meta["pages"] if p["market"] == "美"][0]
                self.assertAlmostEqual(
                    p5_meta["rows"][0]["delta"], 12.50, places=2
                )

                # 第 4 轮：港股（Page 2）跳动，但 Zectrix API 临时失败 -> 不记录新哈希
                p4 = _fixture_panel()
                p4.pages[5].rows[0].quote.last = dji3.last
                p4.pages[5].rows[0].quote.time = dji3.time
                hsi4 = p4.pages[2].rows[0].quote
                hsi4.last = 23700.00
                hsi4.time = datetime(2026, 11, 13, 18, 32, 0, tzinfo=mq.TZ_HK)
                with mock.patch.object(market_board, "collect_panel", return_value=p4):
                    r4 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir,
                        pusher=lambda *_a, **_k: False,
                    )
                self.assertEqual(r4.exit_code, market_board.EXIT_PUSH_FAILED)
                self.assertEqual(r4.failed, [2])

                # 第 5 轮：行情未再动，但 Page 2 上轮未推成功 -> 自动补推 Page 2 并恢复绿灯
                pushed_calls.clear()
                p5 = copy.deepcopy(p4)
                for pg in p5.pages.values():
                    pg.summary_delta = None
                    for r in pg.rows:
                        r.quote.delta = None
                        r.quote.delta_pct = None
                        r.quote.prev_last = None
                with mock.patch.object(market_board, "collect_panel", return_value=p5):
                    r5 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir, pusher=ok_pusher,
                    )
                self.assertEqual(r5.exit_code, market_board.EXIT_OK)
                self.assertEqual(r5.pushed, [2])

                # 第 6 轮：--force 强制全推 5 页
                pushed_calls.clear()
                p6 = copy.deepcopy(p5)
                with mock.patch.object(market_board, "collect_panel", return_value=p6):
                    r6 = market_board.run_once(
                        state_file=state_file, out_dir=out_dir, force=True,
                        pusher=ok_pusher,
                    )
                self.assertEqual(r6.pushed, [1, 2, 3, 4, 5])

    def test_single_page_outage_skips_page_and_all_down_returns_no_live_data(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = os.path.join(td, "state.json")
            out_dir = os.path.join(td, "out")
            p_no_kr = _fixture_panel()
            for r in p_no_kr.pages[3].rows:
                r.quote = mq.Quote()
            with mock.patch.object(board_core, "API_KEY", "k"), \
                 mock.patch.object(board_core, "MAC_ADDRESS", "AA:BB"), \
                 mock.patch.object(market_board, "collect_panel", return_value=p_no_kr):
                res = market_board.run_once(
                    state_file=state_file, out_dir=out_dir,
                    pusher=lambda *_a, **_k: True,
                )
            self.assertEqual(res.exit_code, market_board.EXIT_OK)
            self.assertEqual(res.pushed, [1, 2, 4, 5])
            self.assertEqual(res.empty, [3])

            # 四路全空 -> 不推任何页，返回 EXIT_NO_LIVE_DATA
            pushed: list = []
            p_empty = mq.build_panel({s: None for s, *_ in mq.SOURCES})
            with mock.patch.object(board_core, "API_KEY", "k"), \
                 mock.patch.object(board_core, "MAC_ADDRESS", "AA:BB"), \
                 mock.patch.object(market_board, "collect_panel", return_value=p_empty):
                res2 = market_board.run_once(
                    state_file=state_file, out_dir=out_dir,
                    pusher=lambda *_a, **_k: pushed.append(1) or True,
                )
            self.assertEqual(res2.exit_code, market_board.EXIT_NO_LIVE_DATA)
            self.assertEqual(pushed, [])

    def test_corrupt_state_file_recovers_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            bad_state = os.path.join(td, "broken.json")
            with open(bad_state, "w", encoding="utf-8") as fh:
                fh.write("{broken json")
            res = market_board.run_once(
                dry_run=True, demo_delta=True, state_file=bad_state, out_dir=td,
            )
            self.assertEqual(res.exit_code, market_board.EXIT_OK)
            self.assertEqual(res.pushed, [1, 2, 3, 4, 5])

    def test_main_defaults_to_market_and_runs_demo(self):
        with mock.patch("sys.argv", ["main.py", "--demo-delta", "--dry-run"]):
            args = main.parse_args()
        self.assertEqual(args.mode, "market")
        self.assertEqual(args.interval, 300.0)
        with tempfile.TemporaryDirectory() as td:
            orig_run = market_board.run_once

            def redirected(**kwargs):
                kwargs["out_dir"] = td
                kwargs["state_file"] = None
                return orig_run(**kwargs)

            with mock.patch.object(market_board, "run_once", side_effect=redirected):
                code = main.run_market(args, dry_run_mode=True)
            self.assertEqual(code, 0)
            for pid in (1, 2, 3, 4, 5):
                self.assertTrue(os.path.exists(os.path.join(td, f"page_{pid}.png")))


if __name__ == "__main__":
    unittest.main()
