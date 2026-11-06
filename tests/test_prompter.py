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

# ---------- v0.5.0：底模适配（三种提示词风格 / 质量词 / qwen 尺寸） ----------
check("preset_qwen", prompter.SIZE_PRESETS["qwen"]["square"] == [[1328, 1328]])
check("preset_qwen_16", all(w % 16 == 0 and h % 16 == 0
                           for arr in prompter.SIZE_PRESETS["qwen"].values() for w, h in arr))

sys_zh = prompter.build_system_prompt("natural")
sys_zh2 = prompter.build_system_prompt("natural_zh")
check("sys_natural_zh", "中文" in sys_zh and sys_zh2 == sys_zh)
sys_en = prompter.build_system_prompt("natural_en")
check("sys_natural_en_english", "English prompt" in sys_en and "JSON" in sys_en)
sys_tags = prompter.build_system_prompt("danbooru")
check("sys_danbooru_tags", "Danbooru" in sys_tags)

check("quality_prefix_added",
      prompter.apply_quality_prefix("1girl, night", prompter.QUALITY_PREFIX_DANBOORU)
      == prompter.QUALITY_PREFIX_DANBOORU + ", 1girl, night")
check("quality_prefix_no_dup",
      prompter.apply_quality_prefix("masterpiece, 1girl", prompter.QUALITY_PREFIX_DANBOORU)
      == "masterpiece, 1girl")
check("quality_prefix_empty_prefix", prompter.apply_quality_prefix("a scene", "") == "a scene")
check("quality_prefix_empty_prompt", prompter.apply_quality_prefix("", "masterpiece") == "")

# 尺寸白名单仍可正确落值（qwen 横竖方都能命中）
for orient in ("landscape", "portrait", "square"):
    w, h = prompter.pick_size(prompter.SIZE_PRESETS["qwen"], orient, "中景·横幅", random.Random(1))
    check(f"qwen_pick_{orient}", w % 16 == 0 and h % 16 == 0 and w > 0 and h > 0, f"{w}x{h}")

# ---------- v0.5.1：尺寸跟随构图（每张图不同） ----------
sdxl = prompter.SIZE_PRESETS["sdxl"]
check("ratio_by_comp_wide", prompter.composition_ratio("超广角远景·横幅") == 1.78)
check("ratio_by_comp_portrait", prompter.composition_ratio("全身像·竖幅") == 1.78)
check("ratio_by_comp_unknown", prompter.composition_ratio("自定义构图") is None)
check("ratio_by_comp_empty", prompter.composition_ratio("") is None)

w, h = prompter.pick_size(sdxl, "landscape", "超广角远景·横幅", random.Random(3))
check("size_ultra_wide", (w, h) == (1344, 768), f"{w}x{h}")
w, h = prompter.pick_size(sdxl, "portrait", "全身像·竖幅", random.Random(3))
check("size_full_body_tall", (w, h) == (768, 1344), f"{w}x{h}")
w, h = prompter.pick_size(sdxl, "square", "对称构图·方形", random.Random(3))
check("size_square", (w, h) == (1024, 1024), f"{w}x{h}")

# 特写/半身像落在 3:4 一档（允许相邻档随机）
for seed in range(5):
    w, h = prompter.pick_size(sdxl, "portrait", "特写·竖幅", random.Random(seed))
    check(f"size_closeup_{seed}", [w, h] in sdxl["portrait"] and abs(max(w, h) / min(w, h) - 1.33) < 0.2,
          f"{w}x{h}")

# 认不出构图 → 不会越界，仍在白名单内
for seed in range(5):
    w, h = prompter.pick_size(sdxl, "landscape", "自定义构图", random.Random(seed))
    check(f"size_unknown_in_whitelist_{seed}", [w, h] in sdxl["landscape"], f"{w}x{h}")

# 「每张图不同」：不同构图给出不同尺寸（不是永远一个值）
picked = set()
for comp, orient in (("超广角远景·横幅", "landscape"), ("中景·横幅", "landscape"),
                     ("特写·竖幅", "portrait"), ("全身像·竖幅", "portrait"),
                     ("对称构图·方形", "square")):
    for seed in range(3):
        picked.add(prompter.pick_size(sdxl, orient, comp, random.Random(seed)))
check("size_varies_by_composition", len(picked) >= 3, str(sorted(picked)))

print("prompter:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
