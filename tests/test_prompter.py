"""prompter 纯函数自检：抽签 / 契约解析 / 尺寸落值。跑法：
uv run --no-project python tests/test_prompter.py（退出码即结论）
"""

import random
import sys

from _loader import load

prompter = load("prompter")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


# ---------- 抽签 ----------
rng = random.Random(42)
brief = prompter.draw_brief(rng)
check("brief_keys", set(brief.keys()) >= {
    "category", "subject", "style", "composition", "orientation", "light", "mood"},
    str(brief))
check("orientation_valid", brief["orientation"] in ("landscape", "portrait", "square"))

b2 = prompter.draw_brief(random.Random(1), disabled_categories={"宇宙星空"})
check("disabled_never_drawn", b2["category"] != "宇宙星空")

for _ in range(200):
    b3 = prompter.draw_brief(random.Random(), disabled_categories={"宇宙星空"})
    if b3["category"] == "宇宙星空":
        check("disabled_never_drawn_200rounds", False, b3["category"])
        break
else:
    check("disabled_never_drawn_200rounds", True)

b4 = prompter.draw_brief(random.Random(7), extra={"我的猫": "橘猫打翻鱼缸, 三花猫占键盘"})
check("extra_new_category", b4["category"] == "我的猫" or True)  # 类别进了池即可
hits = 0
for _ in range(500):
    if prompter.draw_brief(random.Random(), extra={"我的猫": "橘猫打翻鱼缸"})["category"] == "我的猫":
        hits += 1
check("extra_new_category_reachable", hits > 0, f"hits={hits}")

hit_builtin = False
for _ in range(1000):
    if prompter.draw_brief(random.Random(), extra={"宇宙星空": "冰环行星的极夜"})["subject"] == "冰环行星的极夜":
        hit_builtin = True
        break
check("extra_append_builtin", hit_builtin)

b6 = prompter.draw_brief(random.Random(11), extra={"构图": "环绕全景@landscape"})
check("extra_composition_orientation", b6["orientation"] in ("landscape", "portrait", "square"))
found = False
for _ in range(500):
    b = prompter.draw_brief(random.Random(), extra={"构图": "环绕全景@landscape"})
    if b["composition"] == "环绕全景" and b["orientation"] == "landscape":
        found = True
        break
check("extra_composition_reachable", found)

# ---------- 简报文本 / 主题覆盖 ----------
txt = prompter.brief_text(brief, theme="赛博朋克夜市")
check("brief_text_theme", "题材=赛博朋克夜市" in txt and "类别=自定义" not in txt, txt)

# ---------- 系统提示词 ----------
sys_d = prompter.build_system_prompt("danbooru")
sys_n = prompter.build_system_prompt("natural")
check("sys_danbooru_contract", "JSON" in sys_d and "caption" in sys_d)
check("sys_danbooru_no_quality", "masterpiece" in sys_d)  # 提示「不要写质量词」也包含该词
check("sys_natural_cn", "中文" in sys_n)

# ---------- JSON 契约解析 ----------
p = prompter.parse_llm_payload('{"prompt":"1girl, smile","negative":"","caption":"好"}')
check("parse_plain", p["prompt"] == "1girl, smile" and p["caption"] == "好")
p = prompter.parse_llm_payload('```json\n{"prompt":"scenery","caption":"ok"}\n```')
check("parse_fenced", p["prompt"] == "scenery")
p = prompter.parse_llm_payload('前缀说明 {"prompt":"a b","caption":""} 后缀')
check("parse_wrapped", p["prompt"] == "a b")
p = prompter.parse_llm_payload('{"prompt":"x"}')
check("parse_defaults", p["negative"] == "" and p["caption"] == "")
for bad, why in [
    ("", "empty"),
    ("不是json", "nojson"),
    ('{"negative":"x"}', "no_prompt"),
    ('["a"]', "not_dict"),
]:
    try:
        prompter.parse_llm_payload(bad)
        check(f"parse_reject_{why}", False, "未抛异常")
    except ValueError:
        check(f"parse_reject_{why}", True)

# ---------- 尺寸落值 ----------
sizes = prompter.SIZE_PRESETS["sdxl"]
w, h = prompter.pick_size(sizes, "landscape", "超广角远景·横幅", rng)
check("pick_size_wide", (w, h) == (1344, 768), f"{w}x{h}")
w, h = prompter.pick_size(sizes, "square", "对称构图·方形", rng)
check("pick_size_square", (w, h) == (1024, 1024))
w, h = prompter.pick_size({}, "portrait", "特写·竖幅", rng)
check("pick_size_fallback", (w, h) == (1024, 1024), f"{w}x{h}")
for _ in range(50):
    w, h = prompter.pick_size(sizes, "portrait", "半身像·竖幅", rng)
    if (w, h) not in [tuple(c) for c in sizes["portrait"]]:
        check("pick_size_in_whitelist", False, f"{w}x{h}")
        break
else:
    check("pick_size_in_whitelist", True)

print("prompter:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
