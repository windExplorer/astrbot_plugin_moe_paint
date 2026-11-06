"""调度判定（纯函数）+ 薄循环壳。

教训全部来自星语 v1.11.3（PRD 已记）：**目标时刻必须每轮现场重算**，绝不能
「先算延迟、返回正值就睡」——时刻已过顺延到明天、恒为正，推送/生成永远不会触发。
分片睡 ≤30 分钟，改配置能及时生效；异常兜底睡 300s 自愈；CancelledError 直接上抛。
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime

try:
    from astrbot.api import logger
except Exception:  # 独立测试环境没有 astrbot 运行时，退回标准 logging
    import logging

    logger = logging.getLogger("astrbot")

# 连续失败多少次后自动暂停（成功即复位）；暂停后管理员可 /绘卷 继续 或 WebUI 恢复
PAUSE_AFTER_FAILURES = 3

# 远程队列拥塞时的顺延间隔（秒）：先让路给萌绘的交互出图，5 分钟后再试
DEFER_WAIT_SECONDS = 300

# 单轮生成的总硬超时（秒）：内部各环节都有自己的预算，这层是防「彻底卡死」的兜底
GEN_TIMEOUT_SECONDS = 1800

# 陈旧「生成中」兜底：多久扫一次 / 超过多久算卡住（必须大于单轮硬超时，
# 否则会把还在跑的轮次误判成中断）
SWEEP_INTERVAL_SECONDS = 600
STALE_SWEEP_SECONDS = GEN_TIMEOUT_SECONDS + 300

# 按「上一张 + 间隔」算出来已经过期时，稍等几秒再跑（避免同一 tick 里空转）
IMMEDIATE_DELAY_SECONDS = 3

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
    # 正在生成（v0.7.1）：next_due 在出图期间仍是「已过期」的旧值，
    # UI 必须靠这两个字段区分「正在画」和「调度卡住」。
    running_since: "datetime | None" = None
    running_note: str = ""
    # 上一张的「开始时刻」（v0.7.2）：间隔一律以它为基准算，
    # 这样出图耗时不会把周期拉长，改配置也不会从保存时刻重新起算。
    last_started_at: "datetime | None" = None

    def running_for_sec(self) -> int:
        if self.running_since is None:
            return 0
        return max(0, int((datetime.now() - self.running_since).total_seconds()))


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


def _truthy(v, default: bool = True) -> bool:
    """配置布尔容错：bool / "true"/"1"/"on" 为真；None/空为默认。"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "开启", "开")


def quiet_span_of(cfg: dict) -> "tuple[dtime, dtime] | None":
    """安静时段开关 + 时间串 → 生效区间。

    这条规则是用户明确要求的：**关掉安静时段用开关，不要靠清空时间**。
    开关关闭时即使时间串还在也一律不生效（下次打开立即恢复原时间）。
    """
    cfg = cfg or {}
    if not _truthy(cfg.get("quiet_enabled", True), True):
        return None
    return parse_quiet_hours(cfg.get("quiet_hours"))


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


def skip_next(cfg: dict, base: datetime, now: datetime, rng: random.Random) -> datetime:
    """跳过本轮之后的下一个计划点，**保证严格晚于 now**。

    为什么需要它：暂停期间 `next_due` 不推进（循环在暂停分支里睡 60 秒就 continue），
    恢复时那个 due 可能已经是几天前的。原先四个「跳过」分支都是 `advance(cfg, next_due)`，
    于是会在一瞬间连续 advance 上百次（每次还写一条 info 日志），直到把陈旧时刻追到未来。
    这里一次性追平：先按原网格 advance，仍在过去就继续 advance，最后一跳保底 +1 分钟。
    纯算术、无 IO、无日志，配上调用方只写一条日志，就不会刷屏也不会空转。
    """
    nxt = advance(cfg, base, rng)
    guard = 0
    while nxt <= now and guard < 10000:
        nxt = advance(cfg, nxt, rng)
        guard += 1
    if nxt <= now:  # 间隔被配得过小 / 网格异常：保底别卡在过去
        nxt = now + timedelta(minutes=1)
    return nxt


def due_from_last(
    cfg: dict, last_at: "datetime | None", now: datetime, rng: random.Random
) -> datetime:
    """下一次计划点：以「上一张的开始时刻」为基准，而不是「现在」。

    这条是用户明确要求的语义：**每 N 小时一张 = 上一张时间 + N**。
    - 没有历史（首次安装/从未出图）→ now + 间隔；
    - 有历史 → last_at + 间隔，出图耗时不会把周期拉长（画 5 分钟不等于 6 小时 5 分）；
    - 算出来已经过期（间隔改短了、或停机很久）→ 说明现在就该画，返回 now + 几秒，
      由循环立刻执行，而不是又推后一整个间隔。
    """
    # daily 模式是「墙上的时刻」（09:30 / 20:00），跟上一张没关系：
    # 过了就等下一个时刻，不补画。interval 模式才按「上一张 + 间隔」。
    if last_at is None or str(cfg.get("mode") or "interval").strip() == "daily":
        return advance(cfg, now, rng)
    nxt = advance(cfg, last_at, rng)
    if nxt <= now:
        return now + timedelta(seconds=IMMEDIATE_DELAY_SECONDS)
    return nxt


def cap_reached(cap, success_today: int) -> bool:
    """每日上限判定（纯函数）：cap<=0 = 不限；只数成功的（失败轮不占名额）。"""
    try:
        cap = int(cap or 0)
    except (TypeError, ValueError):
        cap = 0
    return cap > 0 and int(success_today or 0) >= cap


def queue_too_busy(ahead: int, threshold: int) -> bool:
    """远程队列拥塞判定（纯函数）：ahead=-1（查询失败）按不忙处理——宁试不误停；
    threshold<=0 = 关闭避让。ahead 与 threshold 同口径时用 >=（前方已有 threshold
    个任务就不再插队）。"""
    try:
        ahead = int(ahead)
    except (TypeError, ValueError):
        return False
    try:
        threshold = int(threshold or 0)
    except (TypeError, ValueError):
        threshold = 0
    return threshold > 0 and ahead >= threshold


# --------------------------------------------------------------------------- #
# 循环壳
# --------------------------------------------------------------------------- #

async def _sweep_stale(plugin, last_ts: float) -> float:
    """每隔一段时间收尾「假生成中」记录，返回本次记账时刻。

    为什么循环里也要做：启动时那次收尾（main.initialize）只处理「已经超过 15 分钟」
    的记录——崩在开画前几分钟的那条会一直挂着；被 `asyncio.wait_for` 取消的轮次
    同样会留下 running 行。这里做周期性兜底，阈值高于单轮硬超时（GEN_TIMEOUT），
    且**只在没有真实生成时**动手，绝不误伤正在画的记录。
    """
    now_ts = time.time()
    if now_ts - last_ts < SWEEP_INTERVAL_SECONDS:
        return last_ts
    if plugin._gen_lock.locked():
        return now_ts
    store = getattr(plugin, "store", None)
    if store is None:
        return now_ts
    try:
        n = await store.fail_stale_running(STALE_SWEEP_SECONDS)
        if n:
            logger.info(
                f"[萌萌绘卷] 兜底收尾 {n} 条卡住的「生成中」记录"
                f"（超过 {STALE_SWEEP_SECONDS // 60} 分钟没有任何结果）"
            )
            plugin.debug_log("store", f"兜底收尾 {n} 条中断记录")
    except Exception as e:
        logger.debug(f"[萌萌绘卷] 陈旧记录兜底失败（忽略本次）: {e}")
    return now_ts


async def run_loop(plugin) -> None:
    """插件自管调度循环。配置每轮现读（管理页改完即时生效）。"""
    state: ScheduleState = plugin.sched
    rng = random.Random()
    last_sweep = 0.0
    while True:
        try:
            root = plugin._cfg()
            cfg = plugin._schedule_cfg()
            paused_manual = _truthy(cfg.get("paused_manual", False), False)
            # 兜底收尾放最前：暂停期间也要清，否则那行会一直「生成中」
            last_sweep = await _sweep_stale(plugin, last_sweep)
            if not _truthy(root.get("enabled", True), True) or paused_manual or state.auto_paused:
                await asyncio.sleep(60)
                continue
            now = datetime.now()
            if state.next_due is None:
                if state.last_started_at is not None:
                    # 重启后：按「上一张 + 间隔」续上，而不是重新从 now 起算
                    state.next_due = due_from_last(cfg, state.last_started_at, now, rng)
                else:
                    state.next_due = initial_due(cfg, now, rng)
            if now >= state.next_due:
                quiet = quiet_span_of(cfg)
                if quiet and in_quiet_hours(now.time(), quiet):
                    # 安静时段到点：跳过本次，顺延到下个计划点（一次性追到未来）
                    state.next_due = skip_next(cfg, state.next_due, now, rng)
                    state.last_run_note = f"安静时段跳过（{cfg.get('quiet_hours')}）"
                    logger.info(
                        f"[萌萌绘卷] 安静时段跳过本次生成，下次计划 "
                        f"{state.next_due.strftime('%m-%d %H:%M')}"
                    )
                    continue
                if plugin._gen_lock.locked():
                    # 上一张还没画完（手动触发/WebUI 触发占着）：顺延，不排队
                    state.next_due = skip_next(cfg, state.next_due, now, rng)
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
                    state.next_due = skip_next(cfg, state.next_due, now, rng)
                    state.last_run_note = (
                        f"已达每日上限（{today_n}/{cfg.get('max_per_day')}），"
                        f"今日不再定时生成，下次计划 "
                        f"{state.next_due.strftime('%m-%d %H:%M')}"
                    )
                    logger.info(f"[萌萌绘卷] {state.last_run_note}")
                    continue
                # 远程队列避让：与萌绘共用同一台 ComfyUI 时，提交前先看远程
                # /queue——前方（排队+执行中）≥ 阈值就顺延 5 分钟再试，别把
                # 交互出图顶到后面。查询失败按不忙处理（宁试不误停）。
                defer_threshold = plugin._int_of(
                    plugin._sub("comfyui"), "queue_defer_threshold", 2, lo=0, hi=100
                )
                if defer_threshold > 0:
                    ahead = await plugin.remote_queue_ahead()
                    if queue_too_busy(ahead, defer_threshold):
                        state.next_due = now + timedelta(seconds=DEFER_WAIT_SECONDS)
                        state.last_run_note = (
                            f"远程队列繁忙（前方约 {ahead} 个任务），"
                            f"{DEFER_WAIT_SECONDS // 60} 分钟后再试"
                        )
                        logger.info(f"[萌萌绘卷] {state.last_run_note}")
                        continue
                # 立刻把「正在生成」摆到状态里：出图可能要几分钟，期间 next_due
                # 还是旧的（已过期），不写这句话 UI 就会一直显示「下次生成 15:40」。
                state.last_started_at = now
                state.last_run_note = f"正在生成（{now.strftime('%H:%M')} 开始）"
                logger.info(f"[萌萌绘卷] 到点开始生成（计划 {now.strftime('%m-%d %H:%M')}）")
                try:
                    summary = await asyncio.wait_for(
                        plugin.generate_one(reason="schedule"), timeout=GEN_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # 内部各环节都有预算，走到这里说明整个管线被卡死了（网络/中转站）
                    summary = {
                        "ok": False,
                        "error": f"生成超时：超过 {GEN_TIMEOUT_SECONDS // 60} 分钟未完成，已放弃本轮",
                    }
                    logger.error(f"[萌萌绘卷] {summary['error']}")
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
                # 从「这一张的开始时刻」起算下一轮：周期 = 间隔本身，
                # 出图耗时不再额外叠加（v0.7.2 用户要求的口径）。
                state.next_due = due_from_last(
                    cfg, state.last_started_at or now, datetime.now(), rng
                )
                continue
            wait = (state.next_due - now).total_seconds()
            await asyncio.sleep(min(max(wait, 1.0), 1800))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[萌萌绘卷] 调度循环异常（300 秒后自愈重试）")
            state.last_run_note = "循环异常，300 秒后重试"
            await asyncio.sleep(300)
