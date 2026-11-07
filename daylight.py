"""时间一致性（纯标准库）：日出日落估算、时段判定、光照词池收窄、矛盾扫描。

为什么要有：定时出图不看时钟——凌晨三点抽到「黄昏暖光」，或让 LLM 写出
「正午的烈日」，画面和时间就会打架。这里不引入时区/天文库：用「纬度 + 月份」
按太阳赤纬粗略估算日出日落（十分钟级误差，选光照词完全够用），把当前时刻折成
六个时段，再据此收窄光照池、给系统提示词一条硬规则、并在出图前扫一遍矛盾。

思路与 mine_chat 的 daylight.py 同源（同一作者），这里只保留绘图要用的部分，
保持纯函数以便 tests/ 直接跑。
"""

import math

# 时段（按一天先后；deep_night 同时是「兜底」——落在所有区间之外就是深夜）
PHASES = ("dawn", "morning", "day", "dusk", "night", "deep_night")

PHASE_LABEL = {
    "dawn": "黎明",
    "morning": "上午",
    "day": "白天",
    "dusk": "黄昏",
    "night": "入夜",
    "deep_night": "深夜",
}

# 每个时段「说得通」的光照关键词（对应 prompter.LIGHTS 里的词）。
# 空集合 = 不限制（认不出时段时）。
_LIGHT_KEYS = {
    "deep_night": ("月光", "霓虹", "星云", "极光", "烛火"),
    "night": ("月光", "霓虹", "星云", "极光", "烛火"),
    "dawn": ("清晨", "薄雾", "阴天", "逆光"),
    "morning": ("清晨", "薄雾", "丁达尔", "阴天", "逆光"),
    "day": ("丁达尔", "阴天", "逆光", "清晨"),
    "dusk": ("黄昏", "逆光", "霓虹", "烛火", "阴天"),
}

# v0.7.18：中性日用光不受时段限制。
# 之前夜间池只剩 月光/霓虹/极光/星云/烛火，四个都是强风格光，导致「夜里必霓虹」；
# 路灯、窗内暖灯、台灯、室内自然光这些放进任何时段都成立，一律保留。
_LIGHT_NEUTRAL = ("自然光", "窗内", "窗边", "台灯", "路灯", "漫射")

# 与时段「明显矛盾」的写法（只用于出图前的兜底扫描：命中就纠偏一次）。
_CONFLICT_WORDS = {
    "deep_night": ("正午", "烈日", "阳光明媚", "艳阳", "midday", "noon", "bright daylight",
                   "sunny day", "sunlight"),
    "night": ("正午", "烈日", "阳光明媚", "艳阳", "midday", "noon", "bright daylight",
              "sunny day", "sunlight"),
    "dawn": ("正午", "烈日", "午夜", "深夜", "midday", "noon", "midnight"),
    "morning": ("午夜", "深夜", "midnight", "starless night"),
    "day": ("午夜", "深夜", "midnight"),
    "dusk": ("正午", "烈日", "midnight", "midday", "noon"),
}


def _declination(month: int) -> float:
    """太阳赤纬（度）：用每月 15 日近似，绘图场景够用。"""
    doy = (0, 15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349)
    m = int(month or 1)
    m = 1 if m < 1 else (12 if m > 12 else m)
    return 23.44 * math.sin(math.radians(360.0 / 365.0 * (doy[m] - 81)))


def sun_hours(month: int, latitude: float = 35.0) -> "tuple[float, float]":
    """返回 (日出小时, 日落小时)，本地时间、24 制小数（如 4.83 = 04:50）。

    纬度夹到 ±66° 以内：再往极圈走 acos 会进入极昼/极夜，本插件也不需要那种精度。
    """
    try:
        lat = float(latitude if latitude is not None else 35.0)
    except (TypeError, ValueError):
        lat = 35.0
    lat = max(-66.0, min(66.0, lat))
    decl = _declination(month)
    cos_h = -math.tan(math.radians(lat)) * math.tan(math.radians(decl))
    cos_h = max(-1.0, min(1.0, cos_h))
    half_day = math.degrees(math.acos(cos_h)) / 15.0
    return 12.0 - half_day, 12.0 + half_day


def phase_of(when, latitude: float = 35.0) -> str:
    """datetime → 时段 key。以日出/日落为锚点切段，落在区间外即深夜。"""
    try:
        month = int(when.month)
        h = float(when.hour) + float(when.minute) / 60.0
    except Exception:
        return "day"
    sunrise, sunset = sun_hours(month, latitude)
    if sunrise - 1.5 <= h < sunrise + 1.0:
        return "dawn"
    if sunrise + 1.0 <= h < sunrise + 4.0:
        return "morning"
    if sunrise + 4.0 <= h < sunset - 2.0:
        return "day"
    if sunset - 2.0 <= h < sunset + 0.5:
        return "dusk"
    if sunset + 0.5 <= h < sunset + 2.0:
        return "night"
    return "deep_night"


def phase_label(phase: str) -> str:
    return PHASE_LABEL.get(str(phase or ""), "白天")


def filter_lights(lights, phase: str) -> list:
    """按时段收窄光照词池；一个都不剩（或时段认不出）就退回原池。

    中性日用光（路灯 / 窗内 / 台灯 / 室内自然光…）任何时段都保留 —— 否则夜间
    只剩月/霓虹/极光/星云这类强风格光，出图会「只要是晚上就赛博朋克」。
    """
    pool = [str(x) for x in (lights or [])]
    keys = _LIGHT_KEYS.get(str(phase or ""))
    if not keys:
        return pool
    kept = [
        w for w in pool
        if any(k in w for k in keys) or any(n in w for n in _LIGHT_NEUTRAL)
    ]
    return kept or pool


def rule_for_phase(phase: str, clock: str = "") -> str:
    """给系统提示词的一条硬规则（中文，三套提示词共用）。"""
    key = str(phase or "")
    if key not in PHASE_LABEL:
        return ""
    label = PHASE_LABEL[key]
    when = f"当前时间 {clock}，" if clock else ""
    if key in ("deep_night", "night"):
        return (
            f"【时间一致性·硬约束】{when}时段是{label}。画面必须是夜间光照"
            "（月光、路灯、霓虹、烛火、星空等），天空是夜色；"
            "禁止出现正午烈日、明亮白天的天空、大面积日照高光。"
        )
    if key == "dusk":
        return (
            f"【时间一致性·硬约束】{when}时段是{label}。画面用黄昏光（低角度暖光、"
            "天空渐变、逆光轮廓）；可以有余晖，但不要写正午的垂直强光。"
        )
    if key == "dawn":
        return (
            f"【时间一致性·硬约束】{when}时段是{label}。画面用清晨的柔光和薄雾、"
            "低饱和冷调起步；不要写深夜的纯黑天空，也不要写正午烈日。"
        )
    return (
        f"【时间一致性·硬约束】{when}时段是{label}。光照要符合白天"
        "（自然光/窗光/顶光均可）；不要写成午夜或深夜。"
    )


def conflicts(text, phase: str) -> list:
    """扫一遍文本里与时段矛盾的词，返回命中列表（空 = 没矛盾）。"""
    words = _CONFLICT_WORDS.get(str(phase or ""))
    if not words:
        return []
    low = str(text or "").lower()
    return [w for w in words if w.lower() in low]


def correct_hint(hits, phase: str) -> str:
    """纠偏指令：告诉 LLM 它上一版跟时段打架，要求重写。"""
    label = phase_label(phase)
    got = "、".join(str(x) for x in (hits or [])[:4]) or "白天光照"
    return (
        f"你上一次写的提示词与当前时段（{label}）矛盾：出现了「{got}」。"
        f"请重写，光照与天空必须符合{label}，只输出 JSON 对象本身。"
    )
