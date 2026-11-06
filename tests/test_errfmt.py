"""errfmt 自检：异常文本兜底（保证失败理由永不为空）。跑法：
uv run --no-project python tests/test_errfmt.py（退出码即结论）
"""

import sys

from _loader import load

errfmt = load("errfmt")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


# 常规异常：类型 + 说明
e = ValueError("prompt 为空")
out = errfmt.format_exc(e)
check("format_full", out == "ValueError: prompt 为空", out)

# 空 message 的异常（最容易在图库里留下空白理由）
out = errfmt.format_exc(RuntimeError())
check("format_empty_msg", out.startswith("RuntimeError: ") and len(out) > len("RuntimeError: "), out)
check("format_empty_has_text", "未知错误" in out or "RuntimeError()" in out, out)

# None / 非异常对象
check("format_none", "Error" in errfmt.format_exc(None))
check("format_string_input", "boom" in errfmt.format_exc("boom"))

# 空白字符串的异常
class Blank(Exception):
    def __str__(self):
        return "   "

check("format_blank", "未知错误" in errfmt.format_exc(Blank()))

# ensure_reason
check("ensure_keep", errfmt.ensure_reason("boom") == "boom")
check("ensure_blank", errfmt.ensure_reason("   ") == errfmt.FALLBACK_REASON)
check("ensure_none", errfmt.ensure_reason(None) == errfmt.FALLBACK_REASON)
check("ensure_custom_fallback", errfmt.ensure_reason("", "自定义") == "自定义")

# truncate
check("truncate_short", errfmt.truncate("abc", 10) == "abc")
long_text = "x" * 50
out = errfmt.truncate(long_text, 10)
check("truncate_long", len(out) == 10 and out.endswith("…"), out)
check("truncate_none", errfmt.truncate(None) == "")

print("errfmt:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
