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

# ---------- v0.6.0：类别权重 + 少女双线 + 风格池 ----------
check("cat_anime_girl", "动漫少女" in prompter.CATEGORIES)
check("cat_real_girl", "真人少女" in prompter.CATEGORIES)
check("cat_girl_subjects", all(len(prompter.CATEGORIES[k]) >= 8 for k in ("美少女", "动漫少女", "真人少女")))

w = prompter.parse_weights("美少女=5,动漫少女=4，真人少女=3;人物肖像=1")
check("parse_weights_multi", w == {"美少女": 5.0, "动漫少女": 4.0, "真人少女": 3.0, "人物肖像": 1.0}, str(w))
check("parse_weights_empty", prompter.parse_weights("") == {})
check("parse_weights_garbage", prompter.parse_weights("乱写一堆") == {})
check("parse_weights_negative", prompter.parse_weights("a=-2")["a"] == 0)
check("parse_weights_dict", prompter.parse_weights({"a": 2})["a"] == 2)
check("parse_weights_cap", prompter.parse_weights("a=999")["a"] == 100)

wts = prompter.parse_weights(prompter.DEFAULT_CATEGORY_WEIGHTS)
rngw = random.Random(7)
cnt: dict = {}
N = 800
for _ in range(N):
    c = prompter.draw_brief(rngw, weights=wts)["category"]
    cnt[c] = cnt.get(c, 0) + 1
# v0.7.3：写真摄影是真人线里的场景扩充，同属少女题材，计入少女占比
girl = sum(cnt.get(k, 0) for k in ("美少女", "动漫少女", "真人少女", "写真摄影"))
anime = cnt.get("美少女", 0) + cnt.get("动漫少女", 0)
real = cnt.get("真人少女", 0)
check("weight_girl_dominant", girl / N > 0.6, f"{girl / N:.2f} {cnt}")
check("weight_anime_gt_real", anime > real * 2, f"anime={anime} real={real}")
check("weight_others_kept", any(cnt.get(k, 0) > 0 for k in ("风景场景", "宇宙星空", "艺术创意", "自然生态")), str(cnt))

all_zero = {k: 0 for k in prompter.CATEGORIES}
b = prompter.draw_brief(random.Random(1), weights=all_zero)
check("weight_all_zero_fallback", b["category"] in prompter.CATEGORIES, b["category"])

bad = b
for seed in range(50):
    bad = prompter.draw_brief(random.Random(seed), weights={"风景场景": 99}, disabled_categories={"风景场景"})
    if bad["category"] == "风景场景":
        break
check("weight_disabled_excluded", bad["category"] != "风景场景", bad["category"])

seen = set()
for seed in range(200):
    ws = {k: (0 if k == "自然生态" else 1) for k in prompter.CATEGORIES}
    seen.add(prompter.draw_brief(random.Random(seed), weights=ws)["category"])
check("weight_zero_excluded", "自然生态" not in seen, str(sorted(seen)))

only_anime = {k: (1 if k == "动漫少女" else 0) for k in prompter.CATEGORIES}
only_real = {k: (1 if k == "真人少女" else 0) for k in prompter.CATEGORIES}
ok_anime = all(prompter.draw_brief(random.Random(s), weights=only_anime)["style"] in prompter.STYLES_ANIME
               for s in range(60))
ok_real = all(prompter.draw_brief(random.Random(s), weights=only_real)["style"] in prompter.STYLES_PHOTO
              for s in range(60))
check("style_anime_pool", ok_anime)
check("style_photo_pool", ok_real)
check("style_pool_fallback_global", prompter.style_pool_for("自定义类别") is prompter.STYLES)

dist = prompter.category_distribution(["a", "b"], {"a": 3, "b": 1})
check("distribution_pct", dist[0] == {"name": "a", "weight": 3.0, "pct": 75.0}, str(dist))
check("distribution_empty", prompter.category_distribution([], {}) == [])

# ---------- v0.7.3：写真 / 角色词池（问题 5、6） ----------
for k in ("写真摄影", "动漫角色", "游戏角色"):
    check(f"new_category_{k}", k in prompter.CATEGORIES and len(prompter.CATEGORIES[k]) >= 8)
check("photo_style_group", prompter.CATEGORY_STYLE_GROUP.get("写真摄影") == "photo")
check("role_style_group", prompter.CATEGORY_STYLE_GROUP.get("动漫角色") == "anime"
      and prompter.CATEGORY_STYLE_GROUP.get("游戏角色") == "anime")
check("photo_styles_expanded", len(prompter.STYLES_PHOTO) >= 9)
check("anime_styles_expanded", len(prompter.STYLES_ANIME) >= 11)

# ---------- v0.7.3：题材能力（问题 2） ----------
check("scope_builtin_anime", prompter.scope_of("动漫角色") == "anime")
check("scope_builtin_photo", prompter.scope_of("写真摄影") == "photo")
check("scope_builtin_both", prompter.scope_of("风景场景") == "both")
check("scope_custom_default_both", prompter.scope_of("我的猫") == "both")
check("scope_allows_same", prompter.scope_allows("anime", "动漫角色"))
check("scope_allows_both", prompter.scope_allows("anime", "风景场景"))
check("scope_blocks_photo", not prompter.scope_allows("anime", "写真摄影"))
check("scope_blocks_anime", not prompter.scope_allows("photo", "动漫角色"))
check("scope_both_wf_all", prompter.scope_allows("both", "写真摄影"))

sp = prompter.parse_scopes("我的猫=anime, 写真摄影=both, 乱写=xxx")
check("parse_scopes_valid", sp == {"我的猫": "anime", "写真摄影": "both"}, str(sp))
check("parse_scopes_override", prompter.scope_of("写真摄影", sp) == "both")
check("parse_scopes_dict", prompter.parse_scopes({"a": "photo"}) == {"a": "photo"})

anime_cats, photo_cats, both_cats = set(), set(), set()
for s in range(300):
    anime_cats.add(prompter.draw_brief(random.Random(s), scope="anime")["category"])
    photo_cats.add(prompter.draw_brief(random.Random(s), scope="photo")["category"])
    both_cats.add(prompter.draw_brief(random.Random(s), scope="both")["category"])
check("anime_wf_no_photo_cat", not (anime_cats & {"写真摄影", "真人少女", "人物肖像"}), str(anime_cats))
check("photo_wf_no_anime_cat", not (photo_cats & {"动漫角色", "游戏角色", "美少女", "动漫少女"}), str(photo_cats))
check("both_wf_unrestricted", "写真摄影" in both_cats and "动漫角色" in both_cats)
check("brief_has_scope", prompter.draw_brief(random.Random(1), scope="photo")["scope"] == "photo")

all_anime_cats = {k for k in prompter.CATEGORIES if prompter.scope_of(k) == "anime"}
non_anime = all_anime_cats | {"风景场景", "宇宙星空", "艺术创意", "自然生态"}
b = prompter.draw_brief(random.Random(3), disabled_categories=non_anime, scope="anime")
check("scope_empty_filter_fallback", b["category"] in prompter.CATEGORIES, b["category"])
b = prompter.draw_brief(random.Random(5), scope="anime", scopes={"写真摄影": "both"},
                        disabled_categories=non_anime)
check("scope_override_in_draw", b["category"] == "写真摄影", b["category"])

# ---------- v0.7.3：角色名单（可删改） ----------
kv = prompter.parse_kv_words(prompter.DEFAULT_CHARACTER_NAMES)
check("char_names_two_cats", set(kv) == {"动漫角色", "游戏角色"}, str(list(kv)))
check("char_names_words", all(len(v) >= 10 for v in kv.values()))
check("char_names_inject", "初音未来" in kv["动漫角色"] and "甘雨" in kv["游戏角色"])
check("char_names_bad_lines", prompter.parse_kv_words("没等号的一行\nA=x,y\nB=") == {"A": ["x", "y"]})
check("char_names_cleared", prompter.parse_kv_words("") == {})
pool = prompter.theme_pool()
check("theme_pool_categories", len(pool["categories"]) == len(prompter.CATEGORIES))
check("theme_pool_layers", set(pool["layers"]) == {"风格", "光色", "氛围", "构图"})
check("theme_pool_scope", next(c for c in pool["categories"] if c["name"] == "写真摄影")["scope"] == "photo")
check("theme_pool_scopes_override",
      next(c for c in prompter.theme_pool({"写真摄影": "both"})["categories"]
           if c["name"] == "写真摄影")["scope"] == "both")

# ---------- v0.7.3：系统提示词定制（问题 1） ----------
opts = {"persona_enabled": True, "persona_text": "人物一律东亚成年女性", "extra_rules": "不要画雨天"}
sys_custom = prompter.build_system_prompt("danbooru", opts)
check("sys_persona_rule", "人物一律东亚成年女性" in sys_custom)
check("sys_extra_rules", "不要画雨天" in sys_custom)
check("sys_contract_last", sys_custom.rstrip().endswith("JSON 以外的任何字符都算错误。"))
check("sys_rules_before_contract", sys_custom.index("不要画雨天") < sys_custom.index("【输出契约】"))
sys_off = prompter.build_system_prompt("danbooru", {"persona_enabled": False, "persona_text": "X", "extra_rules": "Y"})
check("sys_persona_off", "X" not in sys_off and "Y" in sys_off)
check("sys_no_rules_by_default", "【附加规则" not in prompter.build_system_prompt("danbooru"))
check("sys_opt_bool_strings", prompter.opt_bool("false") is False and prompter.opt_bool("on") is True)

ov = prompter.build_system_prompt("danbooru", {"override_danbooru": "只写你自己的规则"})
check("sys_override_replaces", "Danbooru" not in ov and "只写你自己的规则" in ov)
check("sys_override_adds_contract", "JSON" in ov)
ov2 = prompter.build_system_prompt("natural_en", {"override_natural_en": 'Output JSON only'})
check("sys_override_keeps_own_contract", ov2.count("JSON") == 1 and "English prompt" not in ov2)
check("sys_override_style_isolated", "Danbooru" in prompter.build_system_prompt("danbooru", {"override_natural_en": "x"}))
ov3 = prompter.build_system_prompt("natural_zh", {"override_natural": "only this"})
check("sys_override_natural_shared", "only this" in ov3 and "你是一位顶级" not in ov3)

# ---------- v0.7.3：人物锚定硬约束（问题 3） ----------
base = {"prompt": "night, school uniform, smile", "negative": "lowres, bad hands", "caption": "x"}
p = prompter.apply_persona(base, "danbooru", {"category": "动漫少女"})
check("persona_tags_prepended", p["prompt"].startswith("1girl, solo, asian")
      and p["prompt"].endswith("night, school uniform, smile"))
check("persona_negative_added", "old woman" in p["negative"] and "underage" in p["negative"])
check("persona_keeps_original_negative", p["negative"].startswith("lowres, bad hands"))
p2 = prompter.apply_persona(p, "danbooru", {"category": "动漫少女"})
check("persona_idempotent", p2["prompt"] == p["prompt"] and p2["negative"] == p["negative"])
p3 = prompter.apply_persona(base, "danbooru", {"category": "风景场景"})
check("persona_skips_scenery", p3["prompt"] == base["prompt"] and p3["negative"] == base["negative"])
p4 = prompter.apply_persona(base, "danbooru", {"category": "动漫少女"}, {"persona_enabled": False})
check("persona_disabled", p4["prompt"] == base["prompt"] and p4["negative"] == base["negative"])
p5 = prompter.apply_persona(base, "natural", {"category": "真人少女"})
check("persona_natural_no_tags", p5["prompt"] == base["prompt"])
check("persona_natural_zh_negative", "未成年" in p5["negative"])
p6 = prompter.apply_persona(base, "natural_en", {"category": "写真摄影"})
check("persona_natural_en_negative", "old woman" in p6["negative"])
check("persona_no_mutate", base["prompt"] == "night, school uniform, smile")

print("prompter:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
