#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_loop —— 五页全球行情数据面板（A / 港 / 韩 / 日 / 美）的每分钟常驻更新循环。

为什么用常驻循环而不是 cron：
    GitHub Actions 的 schedule 最短只允许 */5，且公开仓库实际常延迟数十分钟到数小时，
    做不到稳定的「一分钟更新」。因此采用常驻循环 + 自动接力 + 看门狗：

    1. 每分钟一轮（默认 --interval 60，单调时钟对齐整周期，慢轮不补跑）：
       并发抓取 A/港/韩/日/美 实时行情 → 计算日内涨跌与 1 分钟变化 → 渲染 5 页 →
       仅把画面发生变化的页推送到 Zectrix 墨水屏（休市市场零刷屏，也不向仓库产生机器人提交）。
    2. 自动接力（GitHub Actions 单任务上限 6 小时，默认一轮跑 330 分钟 = 5.5 小时）：
       正常运行满 5 分钟且至少成功 1 轮后，通过 workflow_dispatch 预约下一轮（排队等待）；
       当前轮结束（跑满 / 异常 / 取消）后排队轮立即接棒，收尾时若无排队后继再补触发一次。
    3. 自动让位：
       检测到同分支有更新且未结束的非机器人接力运行（新代码 push 或人工手动触发）时，
       当前轮在 1 分钟内收尾退出，把执行权交给新运行。
    4. 防失控：
       启动未满 5 分钟或从未成功采样时不自触发；缺 ZECTRIX 密钥时首轮立即报错退出，
       交由 market-watchdog.yml 定时兜底。

本地 / 服务器用法：
    python3 market_loop.py --loop-seconds 0 --dry-run   # 单轮预览
    python3 market_loop.py --forever --interval 60      # 本地常驻每分钟更新推屏
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from market_board import (
    DEFAULT_STATE_FILE,
    EXIT_FATAL,
    EXIT_NO_LIVE_DATA,
    EXIT_OK,
    EXIT_PUSH_FAILED,
)
from market_quotes import TZ_CN

HERE = Path(__file__).resolve().parent

DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 30.0
DEFAULT_LOOP_SECONDS = 330 * 60     # 5.5 小时（留 30 分钟余量给 6 小时上限）
MAX_LOOP_SECONDS = 340 * 60
MIN_RELAY_LOOP_SECONDS = 5 * 60
STANDBY_AFTER_SECONDS = 5 * 60      # 正常运行满 5 分钟后预约下一轮
STANDBY_RETRY_SECONDS = 15 * 60     # 排队的后继消失后隔 15 分钟重约
DEFAULT_WORKFLOW = "run.yml"

RELAY_EVENT = "workflow_dispatch"
BOT_SUFFIX = "[bot]"

CYCLE_TIMEOUT_SECONDS = 50.0
GH_TIMEOUT_SECONDS = 30.0
DISPATCH_ATTEMPTS = 3
DISPATCH_RETRY_SECONDS = 5.0
FAIL_STREAK_LIMIT = 5
MIN_CYCLES_FOR_ZERO_OK_FAILURE = 3
MAX_ANNOTATIONS = 8
SUCCESSOR_PAGE_SIZE = 10

OUTCOME_OK = "ok"
OUTCOME_SKIPPED = "skipped"
OUTCOME_PUSH_ERR = "push_error"
OUTCOME_ERROR = "error"
OUTCOME_CONFIG_ERR = "config_error"


@dataclass
class LoopConfig:
    repo_dir: Path = HERE
    pages: str = "1,2,3,4,5"
    title: Optional[str] = None
    state_file: str = DEFAULT_STATE_FILE
    out_dir: Optional[str] = None
    meta_file: Optional[str] = None
    interval: float = DEFAULT_INTERVAL_SECONDS
    loop_seconds: float = DEFAULT_LOOP_SECONDS
    forever: bool = False
    dry_run: bool = False
    force_first: bool = False
    demo: bool = False
    relay: bool = False
    branch: str = ""
    workflow: str = DEFAULT_WORKFLOW
    repository: str = ""
    run_id: int = 0
    board_cmd: List[str] = field(
        default_factory=lambda: [sys.executable, str(HERE / "market_board.py")]
    )


@dataclass
class CmdResult:
    code: int
    out: str = ""
    err: str = ""
    timed_out: bool = False

    def last_line(self) -> str:
        for stream in (self.err, self.out):
            lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
            if lines:
                return lines[-1]
        return ""

    def summary_line(self) -> str:
        lines = [ln.strip() for ln in (self.out or "").splitlines() if ln.strip()]
        for ln in reversed(lines):
            if ln.startswith("📊"):
                return ln
        return lines[-1] if lines else self.last_line()


@dataclass
class Successors:
    standby: bool = False
    takeover: bool = False

    @property
    def any(self) -> bool:
        return self.standby or self.takeover


@dataclass
class LoopStats:
    iterations: int = 0
    ok_cycles: int = 0
    pushed_pages: int = 0
    unchanged_pages: int = 0
    skipped: int = 0
    push_errors: int = 0
    render_errors: int = 0
    unexpected_errors: int = 0
    tail_errors: int = 0
    config_failed: bool = False
    interrupted: bool = False
    yielded: bool = False
    standby_dispatches: int = 0
    successor_queued: bool = False
    relayed: bool = False
    relay_failed: bool = False
    annotations: int = 0


def _as_text(val) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val)


def run_command(cmd: Sequence[str], *, cwd: Optional[Path] = None, timeout: float) -> CmdResult:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CmdResult(124, _as_text(exc.stdout), _as_text(exc.stderr) or "timed out", True)
    except OSError as exc:
        return CmdResult(127, "", str(exc), False)
    return CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "", False)


def log(msg: str) -> None:
    stamp = datetime.now(TZ_CN).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def annotate(stats: LoopStats, level: str, msg: str) -> None:
    log(f"{level.upper()}: {msg}")
    if os.environ.get("GITHUB_ACTIONS") != "true" or stats.annotations >= MAX_ANNOTATIONS:
        return
    stats.annotations += 1
    print(f"::{level}::{msg}", flush=True)


# =====================================================================
# 单轮执行
# =====================================================================
_RE_PUSHED = re.compile(r"推屏\s+([^·]+)")
_RE_UNCHANGED = re.compile(r"无变化\s+(\d+)\s*页")


def _count_summary_pages(line: str) -> Tuple[int, int]:
    pushed = 0
    m = _RE_PUSHED.search(line or "")
    if m:
        pushed = len([x for x in m.group(1).strip().split("、") if x.strip()])
    unchanged = 0
    m2 = _RE_UNCHANGED.search(line or "")
    if m2:
        unchanged = int(m2.group(1))
    return pushed, unchanged


def build_cycle_cmd(config: LoopConfig, *, force: bool = False) -> List[str]:
    cmd = list(config.board_cmd) + [
        "--pages", config.pages,
        "--state-file", config.state_file,
    ]
    if config.title:
        cmd += ["--title", config.title]
    if config.out_dir:
        cmd += ["--out-dir", config.out_dir]
    if config.meta_file:
        cmd += ["--meta-file", config.meta_file]
    if config.demo:
        cmd.append("--demo-delta")
    if config.dry_run:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    return cmd


def execute_cycle(
    config: LoopConfig,
    *,
    force: bool = False,
    runner: Callable[..., CmdResult] = run_command,
) -> Tuple[str, str]:
    cmd = build_cycle_cmd(config, force=force)
    res = runner(cmd, cwd=config.repo_dir, timeout=CYCLE_TIMEOUT_SECONDS)
    if res.timed_out:
        return OUTCOME_SKIPPED, f"本轮抓取/推送超时（{CYCLE_TIMEOUT_SECONDS:.0f}s）"
    if res.code == EXIT_OK:
        return OUTCOME_OK, res.summary_line()
    if res.code == EXIT_NO_LIVE_DATA:
        return OUTCOME_SKIPPED, res.last_line() or "本轮无有效行情"
    if res.code == EXIT_PUSH_FAILED:
        return OUTCOME_PUSH_ERR, res.summary_line() or "部分页面推送失败"
    detail = res.last_line() or f"退出码 {res.code}"
    if res.code == EXIT_FATAL and ("ZECTRIX_API_KEY" in detail or "无效的页面列表" in detail):
        return OUTCOME_CONFIG_ERR, detail
    return OUTCOME_ERROR, f"渲染退出码 {res.code}：{detail}"


# =====================================================================
# GitHub Actions 接力与让位
# =====================================================================
def gh_available(config: LoopConfig) -> bool:
    return bool(config.repository and config.branch and config.workflow)


def relay_enabled(config: LoopConfig) -> bool:
    return (
        config.relay
        and not config.dry_run
        and not config.forever
        and gh_available(config)
        and config.loop_seconds >= MIN_RELAY_LOOP_SECONDS
    )


def is_standby_run(item: dict) -> bool:
    """判定是否为机器人预约的接力/看门狗运行（GITHUB_TOKEN → github-actions[bot]）。"""
    if not isinstance(item, dict) or item.get("event") != RELAY_EVENT:
        return False
    for field_name in ("triggering_actor", "actor"):
        actor = item.get(field_name)
        login = actor.get("login") if isinstance(actor, dict) else None
        if login:
            return str(login).endswith(BOT_SUFFIX)
    return False


def find_successors(
    config: LoopConfig,
    runner: Callable[..., CmdResult] = run_command,
) -> Optional[Successors]:
    """查询同工作流、同分支上比当前 run_id 更新且尚未结束的运行；失败返回 None。"""
    if not gh_available(config) or config.run_id <= 0:
        return Successors()
    endpoint = (
        f"repos/{config.repository}/actions/workflows/{config.workflow}/runs"
        f"?branch={quote(config.branch, safe='')}&per_page={SUCCESSOR_PAGE_SIZE}"
    )
    res = runner(["gh", "api", endpoint], timeout=GH_TIMEOUT_SECONDS)
    if res.code != 0:
        return None
    found = Successors()
    try:
        runs = json.loads(res.out).get("workflow_runs", [])
        for item in runs:
            if int(item.get("id", 0)) <= config.run_id or item.get("status") == "completed":
                continue
            if is_standby_run(item):
                found.standby = True
            else:
                found.takeover = True
    except (ValueError, TypeError, AttributeError):
        return None
    return found


def dispatch_once(
    config: LoopConfig,
    runner: Callable[..., CmdResult] = run_command,
) -> CmdResult:
    cmd = [
        "gh", "workflow", "run", config.workflow,
        "--repo", config.repository,
        "--ref", config.branch,
        "-f", "mode=market",
        "-f", f"pages={config.pages}",
        "-f", f"interval_seconds={int(round(config.interval))}",
        "-f", "relay=true",
    ]
    if config.title:
        cmd += ["-f", f"custom_title={config.title}"]
    return runner(cmd, timeout=GH_TIMEOUT_SECONDS)


def dispatch_next(
    config: LoopConfig,
    sleep: Callable[[float], None],
    runner: Callable[..., CmdResult] = run_command,
) -> bool:
    for attempt in range(1, DISPATCH_ATTEMPTS + 1):
        res = dispatch_once(config, runner=runner)
        if res.code == 0:
            return True
        log(f"触发下一轮接力失败（{attempt}/{DISPATCH_ATTEMPTS}）：{res.last_line()}")
        if attempt < DISPATCH_ATTEMPTS:
            sleep(DISPATCH_RETRY_SECONDS * attempt)
    return False


# =====================================================================
# 主循环
# =====================================================================
def run_loop(
    config: LoopConfig,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable[..., CmdResult] = run_command,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> LoopStats:
    stats = LoopStats()
    started = clock()
    deadline = started + config.loop_seconds
    slot = 0
    standby_at: Optional[float] = None

    while True:
        if stop_requested and stop_requested():
            stats.interrupted = True
            log("收到停止信号，本轮循环收尾退出")
            break

        stats.iterations += 1
        use_force = config.force_first and stats.iterations == 1
        try:
            outcome, detail = execute_cycle(config, force=use_force, runner=runner)
        except Exception as exc:  # noqa: BLE001
            stats.unexpected_errors += 1
            stats.tail_errors += 1
            annotate(stats, "warning", f"本周期意外异常，下一分钟重试：{type(exc).__name__}: {exc}")
        else:
            if outcome == OUTCOME_CONFIG_ERR:
                stats.config_failed = True
                stats.render_errors += 1
                stats.tail_errors += 1
                annotate(stats, "error", detail)
                break
            elif outcome == OUTCOME_OK:
                stats.ok_cycles += 1
                stats.tail_errors = 0
                p, u = _count_summary_pages(detail)
                stats.pushed_pages += p
                stats.unchanged_pages += u
                log(detail)
            elif outcome == OUTCOME_SKIPPED:
                stats.skipped += 1
                stats.tail_errors = 0
                log(f"跳过：{detail}")
            elif outcome == OUTCOME_PUSH_ERR:
                stats.push_errors += 1
                stats.tail_errors += 1
                p, u = _count_summary_pages(detail)
                stats.pushed_pages += p
                stats.unchanged_pages += u
                annotate(stats, "warning", f"推屏部分失败（下轮重试）：{detail}")
            else:
                stats.render_errors += 1
                stats.tail_errors += 1
                annotate(stats, "warning", detail)

        # 检查是否有代码更新或手动触发在排队（让位）
        found = find_successors(config, runner=runner) if config.relay else None
        if found is not None and found.takeover:
            stats.yielded = True
            log("检测到新的运行在排队（代码推送或手动触发），立即收尾让位")
            break

        now = clock()
        # 正常运行满 5 分钟且至少成功 1 轮后预约下一轮排队接力
        if (
            relay_enabled(config)
            and config.run_id > 0
            and found is not None
            and not found.standby
            and stats.ok_cycles > 0
            and now - started >= STANDBY_AFTER_SECONDS
            and (standby_at is None or now - standby_at >= STANDBY_RETRY_SECONDS)
        ):
            disp = dispatch_once(config, runner=runner)
            if disp.code == 0:
                standby_at = now
                stats.standby_dispatches += 1
                log("已预约下一轮接力运行（排队等待本轮结束后自动接棒）")
            else:
                log(f"预约下一轮失败，后续周期重试：{disp.last_line()}")

        # 对齐到下一个整周期（单轮慢了不补跑）
        slot = max(slot + 1, int((now - started) // config.interval) + 1)
        next_tick = started + slot * config.interval
        if not config.forever and next_tick >= deadline:
            break

        wait_s = max(0.0, next_tick - now)
        if stop_requested:
            # 分段睡眠，保证收到 SIGTERM/SIGINT 后 1 秒内响应
            end_sleep = clock() + wait_s
            while clock() < end_sleep:
                if stop_requested():
                    stats.interrupted = True
                    break
                sleep(min(1.0, max(0.0, end_sleep - clock())))
            if stats.interrupted:
                log("收到停止信号，停止等待并收尾")
                break
        else:
            sleep(wait_s)

    # 收尾补接力（仅在正常跑满且非让位、非配置错误、非信号中断、且本轮曾成功采样时）
    if (
        relay_enabled(config)
        and not stats.yielded
        and not stats.config_failed
        and not stats.interrupted
        and stats.ok_cycles > 0
    ):
        found = find_successors(config, runner=runner)
        if found is not None and found.any:
            stats.successor_queued = True
            log("后继运行已在排队，本轮结束后自动接棒")
        elif dispatch_next(config, sleep, runner=runner):
            stats.relayed = True
            log("已在收尾时触发下一轮接力")
        else:
            stats.relay_failed = True
            annotate(stats, "error", "触发下一轮接力失败，等待看门狗重新拉起")

    return stats


def compute_exit_code(stats: LoopStats) -> int:
    if stats.config_failed or stats.relay_failed:
        return 1
    if stats.iterations == 1 and (stats.render_errors > 0 or stats.push_errors > 0):
        return 1
    if stats.iterations and stats.tail_errors >= min(FAIL_STREAK_LIMIT, stats.iterations):
        return 1
    if stats.iterations >= MIN_CYCLES_FOR_ZERO_OK_FAILURE and stats.ok_cycles == 0:
        return 1
    return 0


def summary_text(stats: LoopStats) -> str:
    if stats.config_failed:
        ending = "配置错误中止"
    elif stats.interrupted:
        ending = "收到信号停止"
    elif stats.yielded:
        ending = "让位给新运行"
    elif stats.successor_queued:
        ending = "后继已在排队"
    elif stats.relayed:
        ending = "已触发下一轮"
    elif stats.relay_failed:
        ending = "接力失败"
    else:
        ending = "正常结束"
    extra = f"，意外异常 {stats.unexpected_errors}" if stats.unexpected_errors else ""
    return (
        f"共 {stats.iterations} 轮：成功 {stats.ok_cycles} 轮"
        f"（推屏 {stats.pushed_pages} 页次，无变化跳过 {stats.unchanged_pages} 页次），"
        f"行情不足/超时 {stats.skipped}，推屏失败 {stats.push_errors}，"
        f"渲染出错 {stats.render_errors}{extra}；{ending}"
    )


def write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"五页行情数据面板（A/港/韩/日/美）：{text}\n")
    except OSError:
        pass


def parse_args(argv: Optional[Sequence[str]] = None) -> LoopConfig:
    ap = argparse.ArgumentParser(
        description="五页全球行情数据面板（A/港/韩/日/美）每分钟常驻更新循环"
    )
    ap.add_argument("--pages", default="1,2,3,4,5", help="推送页面（默认 1,2,3,4,5 = A,港,韩,日,美）")
    ap.add_argument("--title", default=None, help="自定义顶栏前缀")
    ap.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="每分钟变化与指纹状态文件")
    ap.add_argument("--out-dir", default=None, help="PNG 输出目录")
    ap.add_argument("--meta-file", default=None, help="可选：JSON 元数据输出文件")
    ap.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
        help=f"更新间隔秒数（默认 {DEFAULT_INTERVAL_SECONDS:.0f}，最小 {MIN_INTERVAL_SECONDS:.0f}）",
    )
    ap.add_argument(
        "--loop-seconds", type=float, default=DEFAULT_LOOP_SECONDS,
        help=f"本轮持续秒数（默认 {DEFAULT_LOOP_SECONDS} = 5.5 小时；0 表示只跑一轮）",
    )
    ap.add_argument("--forever", action="store_true", help="本地/服务器常驻无限循环（Actions 中禁用）")
    ap.add_argument("--relay", action="store_true", help="GitHub Actions 自动预约/触发下一轮接力")
    ap.add_argument("--force", action="store_true", help="首轮忽略页面指纹强制推送五页")
    ap.add_argument("--dry-run", action="store_true", help="只渲染本地 PNG，不推送到 Zectrix")
    ap.add_argument("--demo", action="store_true", help="使用离线样例行情（演示/自测）")
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", ""))
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    ap.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID") or 0))
    args = ap.parse_args(argv)

    if args.interval < MIN_INTERVAL_SECONDS:
        ap.error(f"--interval 不能小于 {MIN_INTERVAL_SECONDS:.0f} 秒")
    if args.forever:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            ap.error("--forever 不能在 GitHub Actions 中使用（请用 --loop-seconds + --relay）")
    elif not 0 <= args.loop_seconds <= MAX_LOOP_SECONDS:
        ap.error(f"--loop-seconds 必须在 0..{MAX_LOOP_SECONDS} 之间（单任务上限 6 小时）")

    return LoopConfig(
        repo_dir=HERE,
        pages=args.pages,
        title=args.title,
        state_file=args.state_file,
        out_dir=args.out_dir,
        meta_file=args.meta_file,
        interval=args.interval,
        loop_seconds=args.loop_seconds,
        forever=args.forever,
        dry_run=args.dry_run or args.demo,
        force_first=args.force,
        demo=args.demo,
        relay=args.relay,
        branch=args.branch,
        workflow=args.workflow,
        repository=args.repository,
        run_id=args.run_id,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    config = parse_args(argv)
    stopped = False

    def _on_signal(_signum, _frame) -> None:
        nonlocal stopped
        stopped = True

    old_int = signal.signal(signal.SIGINT, _on_signal)
    old_term = signal.signal(signal.SIGTERM, _on_signal)
    try:
        span = "常驻" if config.forever else f"{config.loop_seconds:.0f}s"
        action = "预览(dry_run)" if config.dry_run else "推屏"
        log(
            f"启动五页行情面板（A/港/韩/日/美）：{action}，页面 {config.pages}，"
            f"间隔 {config.interval:.0f}s，时长 {span}，接力 {'开' if relay_enabled(config) else '关'}"
        )
        stats = run_loop(config, stop_requested=lambda: stopped)
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    text = summary_text(stats)
    log(text)
    write_step_summary(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice title=五页行情面板::{text}", flush=True)
    code = compute_exit_code(stats)
    if code == 0 and stats.ok_cycles == 0 and not stats.interrupted:
        annotate(stats, "warning", "本轮未取到有效行情，墨水屏保留上一版内容")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
