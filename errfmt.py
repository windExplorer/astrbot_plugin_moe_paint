"""错误文本兜底（纯函数，无依赖，tests 可直跑）。

为什么单独成文件：`f"{type(e).__name__}: {e}"` 在异常 message 为空时会长成
「RuntimeError: 」，用户在图库里看到的就是**空白失败理由**。这类文本到处都要拼，
集中一处保证「永远有一句人话」，并让单测能覆盖。
"""

FALLBACK_REASON = "未知错误（未记录原因）"


def format_exc(exc) -> str:
    """异常 → 「类型: 说明」；说明为空时给一句通用文案，绝不返回空串。

    不退回 repr：异常类的 repr 往往就是 `Blah()`，对用户没有信息量。
    """
    name = type(exc).__name__ if exc is not None else "Error"
    try:
        msg = str(exc or "").strip()
    except Exception:
        msg = ""
    if not msg:
        msg = "未知错误（异常没有附带任何信息）"
    return f"{name}: {msg}"


def ensure_reason(text, fallback: str = FALLBACK_REASON) -> str:
    """已有文本为空/空白 → 用兜底文案。"""
    s = str(text or "").strip()
    return s or fallback


def truncate(text, limit: int = 300) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"
