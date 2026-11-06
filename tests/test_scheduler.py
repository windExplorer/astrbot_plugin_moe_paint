"""scheduler 纯函数自检：安静时段 / 时刻表 / 下一个计划点。跑法：
uv run --no-project python tests/test_scheduler.py（退出码即结论）
"""

import random
import sys
from datetime import datetime, time as dtime, timedelta

from _loader import load

scheduler = load("scheduler")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


# ---------- 安静时段 ----------
check("quiet_parse", scheduler.parse_quiet_hours("01:00-08:00") == (dtime(1, 0), dtime(8, 0)))
check("quiet_parse_fullwidth", scheduler.parse_quiet_hours("01：00－08：00".replace("－", "-")) == (dtime(1), dtime(8)))
check("quiet_parse_empty", scheduler.parse_quiet_hours("") is None)
check("quiet_parse_garbage", scheduler.parse_quiet_hours("abc") is None)
check("quiet_parse_bad_range", scheduler.parse_quiet_hours("99:00-08:00") is None)
q = scheduler.parse_quiet_hours("01:00-08:00")
check("quiet_in", scheduler.in_quiet_hours(dtime(3, 30), q) is True)
check("quiet_out", scheduler.in_quiet_hours(dtime(9, 0), q) is False)
q2 = scheduler.parse_quiet_hours("23:00-06:00")
check("quiet_cross_midnight_in", scheduler.in_quiet_hours(dtime(23, 30), q2) and scheduler.in_quiet_hours(dtime(2, 0), q2))
check("quiet_cross_midnight_out", not scheduler.in_quiet_hours(dtime(12, 0), q2))
check("quiet_none_span", scheduler.in_quiet_hours(dtime(3), None) is False)

# ---------- 安静时段开关（v0.3.0：用开关停用，不靠清空时间） ----------
check(
    "quiet_span_default_on",
    scheduler.quiet_span_of({"quiet_hours": "01:00-08:00"}) == (dtime(1), dtime(8)),
)
check(
    "quiet_span_off_bool",
    scheduler.quiet_span_of({"quiet_enabled": False, "quiet_hours": "01:00-08:00"}) is None,
)
check(
    "quiet_span_off_string",
    scheduler.quiet_span_of({"quiet_enabled": "false", "quiet_hours": "01:00-08:00"}) is None,
)
check(
    "quiet_span_on_string",
    scheduler.quiet_span_of({"quiet_enabled": "true", "quiet_hours": "23:00-06:00"})
    == (dtime(23), dtime(6)),
)
check("quiet_span_no_time", scheduler.quiet_span_of({"quiet_enabled": True}) is None)
check("quiet_span_none_cfg", scheduler.quiet_span_of(None) is None)

# ---------- 每日时刻表 ----------
check("daily_parse", scheduler.parse_daily_times("09:30,20:00") == [(9, 30), (20, 0)])
check("daily_parse_dedup", scheduler.parse_daily_times("20:00,09:30,20:00") == [(9, 30), (20, 0)])
check("daily_parse_invalid", scheduler.parse_daily_times("25:00,abc") == [])
check("daily_parse_lenient", scheduler.parse_daily_times("8:5") == [(8, 5)])  # 宽容：个位分钟也认

now10 = datetime(2026, 11, 6, 10, 0)
check("daily_next_today", scheduler.next_daily_slot([(9, 30), (20, 0)], now10) == datetime(2026, 11, 6, 20, 0))
now21 = datetime(2026, 11, 6, 21, 0)
check("daily_next_tomorrow", scheduler.next_daily_slot([(9, 30), (20, 0)], now21) == datetime(2026, 11, 7, 9, 30))
now9 = datetime(2026, 11, 6, 9, 0)
check("daily_next_strict", scheduler.next_daily_slot([(9, 30), (20, 0)], now9) == datetime(2026, 11, 6, 9, 30))
check("daily_next_empty", scheduler.next_daily_slot([], now10) == datetime(2026, 11, 7, 10, 0))

# ---------- interval（分钟粒度） ----------
rng = random.Random(5)
check("interval_minutes_basic", scheduler.interval_next(now10, 30, 0, rng) == now10 + timedelta(minutes=30))
check("interval_minutes_clamp_min", scheduler.interval_next(now10, 0, 0, rng) == now10 + timedelta(minutes=1))
check("interval_none_default", scheduler.interval_next(now10, None, 0, rng) == now10 + timedelta(hours=6))
for _ in range(100):
    nxt = scheduler.interval_next(now10, 30, 5, rng)
    delta_min = (nxt - now10).total_seconds() / 60
    if not (30 <= delta_min <= 35.0001):
        check("interval_jitter_range", False, f"{delta_min}min")
        break
else:
    check("interval_jitter_range", True)

# ---------- advance ----------
cfg_i = {"mode": "interval", "interval_minutes": 360, "jitter_minutes": 0}
cfg_legacy = {"mode": "interval", "interval_hours": 2, "jitter_minutes": 0}  # 旧键兼容
cfg_d = {"mode": "daily", "daily_times": "09:30,20:00"}
check("advance_interval", scheduler.advance(cfg_i, now10, rng) == now10.replace(hour=16))
check("advance_legacy_hours", scheduler.advance(cfg_legacy, now10, rng) == now10.replace(hour=12))
check("advance_daily", scheduler.advance(cfg_d, now10, rng) == datetime(2026, 11, 6, 20, 0))

# ---------- 状态默认 ----------
st = scheduler.ScheduleState()
check("state_defaults", st.next_due is None and st.consecutive_failures == 0 and not st.auto_paused)

# ---------- 每日上限 ----------
check("cap_reached_at_limit", scheduler.cap_reached(4, 4) is True)
check("cap_below_limit", scheduler.cap_reached(4, 3) is False)
check("cap_zero_unlimited", scheduler.cap_reached(0, 999) is False)
check("cap_none_unlimited", scheduler.cap_reached(None, 999) is False)
check("cap_bad_value_unlimited", scheduler.cap_reached("abc", 5) is False)
check("cap_string_value", scheduler.cap_reached("4", 4) is True)

# ---------- 远程队列避让 ----------
check("queue_busy_at_threshold", scheduler.queue_too_busy(2, 2) is True)
check("queue_busy_above", scheduler.queue_too_busy(5, 2) is True)
check("queue_busy_below", scheduler.queue_too_busy(1, 2) is False)
check("queue_busy_off", scheduler.queue_too_busy(9, 0) is False)
check("queue_busy_unknown", scheduler.queue_too_busy(-1, 2) is False)  # 查询失败按不忙
check("queue_busy_bad_ahead", scheduler.queue_too_busy("x", 2) is False)
check("queue_busy_none_threshold", scheduler.queue_too_busy(5, None) is False)

# ---------- v0.7.1：生成中状态（UI 靠它区分「正在画」与「卡住」） ----------
from datetime import datetime, timedelta  # noqa: E402

st = scheduler.ScheduleState()
check("running_default", st.running_since is None and st.running_for_sec() == 0)
st.running_since = datetime.now() - timedelta(seconds=125)
elapsed = st.running_for_sec()
check("running_for_sec", 124 <= elapsed <= 127, str(elapsed))
check("gen_timeout_defined", scheduler.GEN_TIMEOUT_SECONDS >= 600, str(scheduler.GEN_TIMEOUT_SECONDS))

# ---------- v0.7.2：下次计划 = 上一张 + 间隔（不是「现在 + 间隔」） ----------
CFG_INT = {"mode": "interval", "interval_minutes": 360, "jitter_minutes": 0}
NOW = datetime(2026, 11, 6, 15, 50)

got = scheduler.due_from_last(CFG_INT, None, NOW, random.Random(1))
check("due_no_history", got == NOW + timedelta(hours=6), str(got))

last = datetime(2026, 11, 6, 12, 0)
got = scheduler.due_from_last(CFG_INT, last, NOW, random.Random(1))
check("due_from_last_base", got == datetime(2026, 11, 6, 18, 0), str(got))

# 上一张 08:00 + 6h = 14:00 已过 → 立刻（几秒后），不能又推 6 小时
got = scheduler.due_from_last(CFG_INT, datetime(2026, 11, 6, 8, 0), NOW, random.Random(1))
check("due_overdue_immediate",
      got == NOW + timedelta(seconds=scheduler.IMMEDIATE_DELAY_SECONDS), str(got))

# jitter 只加不减
CFG_J = {"mode": "interval", "interval_minutes": 360, "jitter_minutes": 30}
got = scheduler.due_from_last(CFG_J, last, NOW, random.Random(1))
check("due_jitter_not_early", got >= datetime(2026, 11, 6, 18, 0), str(got))

# daily：上一张 10:00 → 今天 20:00
CFG_D = {"mode": "daily", "daily_times": "09:30,20:00"}
got = scheduler.due_from_last(CFG_D, datetime(2026, 11, 6, 10, 0), NOW, random.Random(1))
check("due_daily_next_slot", got == datetime(2026, 11, 6, 20, 0), str(got))

# daily 是墙上的时刻：09:30 早过了就等 20:00，不补画（与「上一张」无关）
got = scheduler.due_from_last(CFG_D, datetime(2026, 11, 6, 8, 0), NOW, random.Random(1))
check("due_daily_no_catchup", got == datetime(2026, 11, 6, 20, 0), str(got))
got = scheduler.due_from_last(CFG_D, None, NOW, random.Random(1))
check("due_daily_no_history", got == datetime(2026, 11, 6, 20, 0), str(got))

# 改配置场景：上一张 09:00，间隔从 6h 改成 3h（12:00 已过）→ 立刻，而不是 18:50
got = scheduler.due_from_last(
    {"mode": "interval", "interval_minutes": 180, "jitter_minutes": 0},
    datetime(2026, 11, 6, 9, 0), NOW, random.Random(1),
)
check("due_shorter_interval_catches_up",
      got == NOW + timedelta(seconds=scheduler.IMMEDIATE_DELAY_SECONDS), str(got))

print("scheduler:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
