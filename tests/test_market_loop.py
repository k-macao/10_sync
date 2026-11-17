# -*- coding: utf-8 -*-
"""1 分钟常驻更新与 GitHub Actions 自动接力（market_loop.py）离线单元测试。"""
import json
import os
import tempfile
import unittest
from unittest import mock

import market_board
import market_loop as ml


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, sec: float) -> None:
        self.sleeps.append(sec)
        self.t += sec


def _relay_cfg(**overrides) -> ml.LoopConfig:
    base = dict(
        pages="1,2,3,4,5",
        interval=60.0,
        loop_seconds=420.0,
        relay=True,
        branch="main",
        workflow="run.yml",
        repository="k-macao/10_sync",
        run_id=100,
        board_cmd=["board"],
    )
    base.update(overrides)
    return ml.LoopConfig(**base)


class TestMarketLoop(unittest.TestCase):
    def setUp(self):
        # 避免单元测试模拟的缺密钥/告警在 CI 日志里打出真实的 ::error:: 注解
        patcher = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_count_summary_pages_and_standby_classification(self):
        self.assertEqual(
            ml._count_summary_pages("📊 行情 15/15 · 推屏 P1(A)、P5(美) · 无变化 3 页"),
            (2, 3),
        )
        self.assertEqual(
            ml._count_summary_pages("📊 行情 15/15 · 无变化 5 页"),
            (0, 5),
        )
        self.assertTrue(ml.is_standby_run({
            "event": "workflow_dispatch",
            "triggering_actor": {"login": "github-actions[bot]"},
        }))
        self.assertFalse(ml.is_standby_run({
            "event": "workflow_dispatch",
            "actor": {"login": "k-macao"},
        }))
        self.assertFalse(ml.is_standby_run({
            "event": "push",
            "actor": {"login": "k-macao"},
        }))

    def test_force_only_on_first_cycle_and_monotonic_slot_alignment(self):
        clock = FakeClock()
        seen_force: list[bool] = []

        def runner(cmd, *, cwd=None, timeout=0):
            has_force = "--force" in cmd
            seen_force.append(has_force)
            # 第 1 轮慢到 65 秒（超过一个 60s 槽位），验证不补跑直接对齐 +120s
            clock.t += 65.0 if len(seen_force) == 1 else 2.0
            msg = (
                "📊 行情 15/15 · 推屏 P1(A)、P2(港)、P3(韩)、P4(日)、P5(美)\n"
                if has_force
                else "📊 行情 15/15 · 无变化 5 页\n"
            )
            return ml.CmdResult(market_board.EXIT_OK, out=msg)

        cfg = ml.LoopConfig(
            interval=60.0, loop_seconds=200.0, force_first=True,
            dry_run=True, board_cmd=["board"],
        )
        stats = ml.run_loop(cfg, clock=clock.now, sleep=clock.sleep, runner=runner)
        # 槽位：0s（结束于+65s）-> 跳到+120s（睡55s）-> +180s（睡58s）-> +240>=200 结束
        self.assertEqual(stats.iterations, 3)
        self.assertEqual(stats.ok_cycles, 3)
        self.assertEqual(seen_force, [True, False, False])
        self.assertAlmostEqual(clock.sleeps[0], 55.0)
        self.assertAlmostEqual(clock.sleeps[1], 58.0)
        self.assertEqual(stats.pushed_pages, 5)
        self.assertEqual(stats.unchanged_pages, 10)
        self.assertEqual(ml.compute_exit_code(stats), 0)

    def test_standby_booked_after_5min_and_recognized_on_exit(self):
        clock = FakeClock()
        dispatched: list[list[str]] = []

        def runner(cmd, *, cwd=None, timeout=0):
            if cmd[0] == "board":
                clock.t += 1.0
                return ml.CmdResult(market_board.EXIT_OK, out="📊 行情 15/15 · 推屏 P5(美) · 无变化 4 页\n")
            if cmd[:2] == ["gh", "api"]:
                runs = []
                if dispatched:
                    runs.append({
                        "id": 101,
                        "status": "queued",
                        "event": "workflow_dispatch",
                        "triggering_actor": {"login": "github-actions[bot]"},
                    })
                return ml.CmdResult(0, out=json.dumps({"workflow_runs": runs}))
            if cmd[:3] == ["gh", "workflow", "run"]:
                dispatched.append(list(cmd))
                return ml.CmdResult(0)
            raise AssertionError(f"unexpected cmd: {cmd}")

        cfg = _relay_cfg(loop_seconds=420.0, title="我的行情")
        stats = ml.run_loop(cfg, clock=clock.now, sleep=clock.sleep, runner=runner)
        self.assertEqual(stats.standby_dispatches, 1)
        self.assertTrue(stats.successor_queued)
        self.assertFalse(stats.relayed)
        self.assertEqual(len(dispatched), 1)
        self.assertIn("mode=market", dispatched[0])
        self.assertIn("relay=true", dispatched[0])
        self.assertIn("interval_seconds=60", dispatched[0])
        self.assertIn("custom_title=我的行情", dispatched[0])
        self.assertEqual(ml.compute_exit_code(stats), 0)

    def test_exit_relay_fallback_when_standby_disappeared(self):
        clock = FakeClock()
        dispatch_calls = {"n": 0}

        def runner(cmd, *, cwd=None, timeout=0):
            if cmd[0] == "board":
                return ml.CmdResult(market_board.EXIT_OK, out="📊 行情 15/15 · 无变化 5 页\n")
            if cmd[:2] == ["gh", "api"]:
                return ml.CmdResult(0, out=json.dumps({"workflow_runs": []}))
            if cmd[:3] == ["gh", "workflow", "run"]:
                dispatch_calls["n"] += 1
                # 前两次失败，第 3 次成功（覆盖重试逻辑）
                return ml.CmdResult(0 if dispatch_calls["n"] >= 3 else 1, err="502")
            raise AssertionError(cmd)

        cfg = _relay_cfg(loop_seconds=300.0, run_id=0)  # run_id=0 跳过盘中预约，直接测收尾补接力
        stats = ml.run_loop(cfg, clock=clock.now, sleep=clock.sleep, runner=runner)
        self.assertTrue(stats.relayed)
        self.assertFalse(stats.relay_failed)
        self.assertEqual(dispatch_calls["n"], 3)
        self.assertEqual(ml.compute_exit_code(stats), 0)

    def test_zero_ok_cycles_never_relays_and_exits_nonzero(self):
        clock = FakeClock()
        dispatches: list = []

        def runner(cmd, *, cwd=None, timeout=0):
            if cmd[0] == "board":
                return ml.CmdResult(market_board.EXIT_NO_LIVE_DATA, out="⚠️ 无有效行情\n")
            if cmd[:2] == ["gh", "api"]:
                return ml.CmdResult(0, out=json.dumps({"workflow_runs": []}))
            dispatches.append(cmd)
            return ml.CmdResult(0)

        stats = ml.run_loop(_relay_cfg(loop_seconds=360.0), clock=clock.now, sleep=clock.sleep, runner=runner)
        self.assertEqual(stats.ok_cycles, 0)
        self.assertEqual(dispatches, [])
        self.assertEqual(ml.compute_exit_code(stats), 1)

    def test_yields_to_push_or_manual_run_within_one_cycle(self):
        clock = FakeClock()
        dispatches: list = []

        def runner(cmd, *, cwd=None, timeout=0):
            if cmd[0] == "board":
                return ml.CmdResult(market_board.EXIT_OK, out="📊 行情 15/15 · 无变化 5 页\n")
            if cmd[:2] == ["gh", "api"]:
                return ml.CmdResult(0, out=json.dumps({"workflow_runs": [
                    # 自身运行忽略
                    {"id": 100, "status": "in_progress", "event": "push", "actor": {"login": "k-macao"}},
                    # 新代码 push 排队 -> 立即让位
                    {"id": 105, "status": "queued", "event": "push", "actor": {"login": "k-macao"}},
                ]}))
            dispatches.append(cmd)
            return ml.CmdResult(0)

        stats = ml.run_loop(_relay_cfg(loop_seconds=19800.0), clock=clock.now, sleep=clock.sleep, runner=runner)
        self.assertEqual(stats.iterations, 1)
        self.assertTrue(stats.yielded)
        self.assertEqual(dispatches, [])
        self.assertEqual(ml.compute_exit_code(stats), 0)

    def test_config_error_aborts_immediately_without_sleep_or_relay(self):
        clock = FakeClock()
        dispatches: list = []

        def runner(cmd, *, cwd=None, timeout=0):
            if cmd[0] == "board":
                return ml.CmdResult(
                    market_board.EXIT_FATAL, err="❌ 未配置 ZECTRIX_API_KEY 或 ZECTRIX_MAC\n"
                )
            dispatches.append(cmd)
            return ml.CmdResult(0)

        stats = ml.run_loop(_relay_cfg(loop_seconds=19800.0), clock=clock.now, sleep=clock.sleep, runner=runner)
        self.assertEqual(stats.iterations, 1)
        self.assertTrue(stats.config_failed)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(dispatches, [])
        self.assertEqual(ml.compute_exit_code(stats), 1)

    def test_timeout_and_stop_signal_handled_cleanly(self):
        outcome, detail = ml.execute_cycle(
            ml.LoopConfig(board_cmd=["board"]),
            runner=lambda *_a, **_k: ml.CmdResult(124, timed_out=True),
        )
        self.assertEqual(outcome, ml.OUTCOME_SKIPPED)
        self.assertIn("超时", detail)

        clock = FakeClock()
        stats = ml.run_loop(
            _relay_cfg(loop_seconds=19800.0),
            clock=clock.now,
            sleep=clock.sleep,
            runner=lambda *_a, **_k: ml.CmdResult(0),
            stop_requested=lambda: True,
        )
        self.assertTrue(stats.interrupted)
        self.assertEqual(stats.iterations, 0)
        self.assertEqual(ml.compute_exit_code(stats), 0)

    def test_cli_guards_and_end_to_end_demo(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(SystemExit) as ctx1:
                ml.parse_args(["--forever", "--dry-run"])
            self.assertEqual(ctx1.exception.code, 2)

        with self.assertRaises(SystemExit) as ctx2:
            ml.parse_args(["--interval", "10", "--dry-run"])
        self.assertEqual(ctx2.exception.code, 2)

        with tempfile.TemporaryDirectory() as td:
            summary_path = os.path.join(td, "summary.md")
            out_dir = os.path.join(td, "preview")
            state_path = os.path.join(td, "state.json")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}):
                code = ml.main([
                    "--demo", "--dry-run", "--loop-seconds", "0",
                    "--interval", "60", "--out-dir", out_dir,
                    "--state-file", state_path,
                ])
            self.assertEqual(code, 0)
            for pid in (1, 2, 3, 4, 5):
                self.assertTrue(os.path.exists(os.path.join(out_dir, f"page_{pid}.png")))
            with open(summary_path, encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("五页行情数据面板（A/港/韩/日/美）", md)
            self.assertIn("推屏 5 页次", md)


if __name__ == "__main__":
    unittest.main()
