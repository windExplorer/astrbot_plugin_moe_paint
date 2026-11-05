"""调度判定（纯函数）+ 薄循环壳。

教训全部来自星语 v1.11.3（PRD 已记）：**目标时刻必须每轮现场重算**，绝不能
「先算延迟、返回正值就睡」——时刻已过顺延到明天、恒为正，推送/生成永远不会触发。
分片睡 ≤30 分钟，改配置能及时生效；异常兜底睡 300s 自愈；CancelledError 直接上抛。
"""

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime

try:
    from astrbot.api import logger
except Exception:  # 独立测试环境没有 astrbot 运行时，退回标准 logging
    import logging

    logger = logging.getLogger("astrbot")

# 连续失败多少次后自动暂停（成功即复位）；暂停后管理员可 /绘卷 继续 或 WebUI 恢复
PAUSE_AFTER_FAILURES = 3

REASON_QUIET = "quiet"
REASON_BUSY = "busy"
REASON_DISABLED = "disabled"


@dataclass
class ScheduleState:
    next_due: "datetime | None" = None
    consecutive_failures: int = 0
    auto_paused: bool = False
    last_run_note: str = ""
    last_run_ts: int = 0


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #

def parse_quiet_hours(text: str) -> "tuple[dtime, dtime] | None":
    """解析 "HH:MM-HH:MM"；空/非法返回 None。支持跨零点（23:00-06:00）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    m = raw.replace("～", "-").replace("—", "-").replace("–", "-")
    parts = m.split("-")
    if len(parts) != 2:
        return None

    def _t(seg: str) -> "dtime | None":
        seg = seg.strip().replace("：", ":")
        hh, _, mm = seg.partition(":")
        try:
            h, mi = int(hh), int(mm or 0)
        except ValueError:
            return None
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        return dtime(hour=h, minute=mi)

    start, end = _t(parts[0]), _t(parts[1])
    if start is None or end is None:
        return None
    return start, end


def in_quiet_hours(now_t: dtime, span: "tuple[dtime, dtime] | None") -> bool:
    if span is None:
        return False
    start, end = span
    if start == end:
        return False
    if start < end:
        return start <= now_t < end
    # 跨零点
    return now_t >= start or now_t < end


def parse_daily_times(text: str) -> list[tuple[int, int]]:
    """解析 "09:30,20:00" → [(9,30),(20,0)]；非法项丢弃。"""
    out = []
    for seg in str(text or "").replace("，", ",").split(","):
        seg = seg.strip().replace("：", ":")
        if not seg:
            continue
        hh, _, mm = seg.partition(":")
        try:
            h, mi = int(hh), int(mm or 0)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= mi <= 59:
            out.append((h, mi))
    return sorted(set(out))


def next_daily_slot(times: list[tuple[int, int]], now: datetime) -> datetime:
    """今天的下一个时刻（严格晚于 now）；今天没有了取明天最早的一个。"""
    for h, mi in times:
        cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand > now:
            return cand
    if times:
        h, mi = times[0]
        cand = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        return cand
    return now + timedelta(days=1)


def interval_next(
    now: datetime, minutes: float, jitter_minutes: float, rng: random.Random
) -> datetime:
    """interval 模式的下一次：now + N 分钟 ± jitter（jitter 只加不减，往保守方向偏）。

    分钟粒度：配置 interval_minutes，最低 1 分钟。单张要画几十秒，而循环是
    「跑完一张再从当下起算下轮」，间隔低于单张耗时只会自然退化为连画，不会堆积并发。
    """
    try:
        minutes = max(1.0, float(minutes if minutes is not None else 360))
    except (TypeError, ValueError):
        minutes = 360.0
    jitter = 0.0
    try:
        jitter = max(0.0, float(jitter_minutes or 0))
    except (TypeError, ValueError):
        jitter = 0.0
    if jitter > 0:
        minutes += rng.uniform(0, jitter)
    return now + timedelta(minutes=minutes)


def advance(cfg: dict, base: datetime, rng: random.Random) -> datetime:
    """从 base 起算下一个计划点（跑完一轮 / 安静时段跳过时用）。"""
    if str(cfg.get("mode") or "interval").strip() == "daily":
        return next_daily_slot(parse_daily_times(cfg.get("daily_times")), base)
    minutes = cfg.get("interval_minutes")
    if minutes is None or str(minutes).strip() == "":
        # v0.1.1 及更早的配置键是 interval_hours（小时）：旧档兼容，×60 换算
        try:
            minutes = float(cfg.get("interval_hours") or 6) * 60
        except (TypeError, ValueError):
            minutes = 360
    return interval_next(base, minutes, cfg.get("jitter_minutes"), rng)


def initial_due(cfg: dict, now: datetime, rng: random.Random) -> datetime:
    """启动时的首个计划点：interval 模式从「现在 + 间隔」起（启动不立即出图）。"""
    return advance(cfg, now, rng)


def cap_reached(cap, success_today: int) -> bool:
    """每日上限判定（纯函数）：cap<=0 = 不限；只数成功的（失败轮不占名额）。"""
    try:
        cap = int(cap or 0)
    except (TypeError, ValueError):
        cap = 0
    return cap > 0 and int(success_today or 0) >= cap


# --------------------------------------------------------------------------- #
# 循环壳
# --------------------------------------------------------------------------- #

async def run_loop(plugin) -> None:
    """插件自管调度循环。配置每轮现读（管理页改完即时生效）。"""
    state: ScheduleState = plugin.sched
    rng = random.Random()
    while True:
        try:
            root = plugin._cfg()
            cfg = plugin._schedule_cfg()
            paused_manual = bool(cfg.get("paused_manual", False))
            if not root.get("enabled", True) or paused_manual or state.auto_paused:
                await asyncio.sleep(60)
                continue
            now = datetime.now()
            if state.next_due is None:
                state.next_due = initial_due(cfg, now, rng)
            if now >= state.next_due:
                quiet = parse_quiet_hours(cfg.get("quiet_hours"))
                if quiet and in_quiet_hours(now.time(), quiet):
                    # 安静时段到点：跳过本次，顺延下个计划点（从原 due 起算）
                    state.next_due = advance(cfg, state.next_due, rng)
                    state.last_run_note = f"安静时段跳过（{cfg.get('quiet_hours')}）"
                    logger.info(
                        f"[萌萌绘卷] 安静时段跳过本次生成，下次计划 "
                        f"{state.next_due.strftime('%m-%d %H:%M')}"
                    )
                    continue
                if plugin._gen_lock.locked():
                    # 上一张还没画完（手动触发/WebUI 触发占着）：顺延，不排队
                    state.next_due = advance(cfg, state.next_due, rng)
                    state.last_run_note = "上一张仍在生成，本次顺延"
                    continue
                # 每日上限（max_per_day，0=不限）：只数今天成功的张数；手动
                # 「/绘卷 画一张」不走这里，不受限。达限顺延到下个计划点
                # （明天 0 点后 count_day_success 自然归零，恢复生成）。
                today_n = 0
                if plugin.store is not None:
                    try:
                        today_n = await plugin.store.count_day_success(plugin._today())
                    except Exception as e:
                        logger.warning(f"[萌萌绘卷] 今日张数查询失败（本次不执行上限）: {e}")
                if cap_reached(cfg.get("max_per_day"), today_n):
                    state.next_due = advance(cfg, state.next_due, rng)
                    state.last_run_note = (
                        f"已达每日上限（{today_n}/{cfg.get('max_per_day')}），"
                        f"今日不再定时生成，下次计划 "
                        f"{state.next_due.strftime('%m-%d %H:%M')}"
                    )
                    logger.info(f"[萌萌绘卷] {state.last_run_note}")
                    continue
                summary = await plugin.generate_one(reason="schedule")
                state.last_run_ts = int(now.timestamp())
                if summary.get("skipped"):
                    state.last_run_note = "生成被跳过（占用/无模板）"
                elif summary.get("ok"):
                    state.consecutive_failures = 0
                    state.last_run_note = "生成成功"
                else:
                    state.consecutive_failures += 1
                    state.last_run_note = f"生成失败：{summary.get('error', '')[:120]}"
                    if state.consecutive_failures >= PAUSE_AFTER_FAILURES:
                        state.auto_paused = True
                        logger.error(
                            f"[萌萌绘卷] 连续失败 {state.consecutive_failures} 次，"
                            "自动暂停调度（/绘卷 继续 或 WebUI 可恢复）"
                        )
                        await plugin.notify_admin(
                            f"萌萌绘卷连续 {state.consecutive_failures} 次生成失败，"
                            f"已自动暂停调度。最近错误：{summary.get('error', '')[:160]}"
                        )
                # 从「现在」起算下一轮（跑了一张图本身耗时也要算进间隔）
                state.next_due = advance(cfg, datetime.now(), rng)
                continue
            wait = (state.next_due - now).total_seconds()
            await asyncio.sleep(min(max(wait, 1.0), 1800))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[萌萌绘卷] 调度循环异常（300 秒后自愈重试）")
            state.last_run_note = "循环异常，300 秒后重试"
            await asyncio.sleep(300)
