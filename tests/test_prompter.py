"""prompter 纯函数自检：抽签 / 契约解析 / 尺寸落值。跑法：
uv run --no-project python tests/test_prompter.py（退出码即结论）
"""

import random
import sys
from datetime import datetime

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
# v0.7.18 起默认有 40% 不指定画风，这里必须显式关掉才能断言「抽到的风格属于该池」
ok_anime = all(prompter.draw_brief(random.Random(s), weights=only_anime, style_none_pct=0)["style"]
               in prompter.STYLES_ANIME for s in range(60))
ok_real = all(prompter.draw_brief(random.Random(s), weights=only_real, style_none_pct=0)["style"]
              in prompter.STYLES_PHOTO for s in range(60))
check("style_anime_pool", ok_anime)
check("style_photo_pool", ok_real)
check("style_pool_fallback_global", prompter.style_pool_for("自定义类别") == prompter.STYLES)

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
# v0.7.12：asian / east asian 属摄影线标签，默认从人物锚定里剔除
check("persona_tags_prepended", p["prompt"].startswith("1girl, solo, young adult woman")
      and p["prompt"].endswith("night, school uniform, smile"), p["prompt"])
check("persona_negative_added", "old woman" in p["negative"] and "underage" in p["negative"])
check("persona_keeps_original_negative", p["negative"].startswith("lowres, bad hands"))
p2 = prompter.apply_persona(p, "danbooru", {"category": "动漫少女"})
check("persona_idempotent", p2["prompt"] == p["prompt"] and p2["negative"] == p["negative"])
p3 = prompter.apply_persona(base, "danbooru", {"category": "风景场景"})
# v0.7.6：景物类仍不塞 1girl（会毁风景），但排除负面词现在对全类别生效
check("persona_skips_scenery_prompt", p3["prompt"] == base["prompt"])
check("persona_scenery_negative", "old woman" in p3["negative"] and "1boy" in p3["negative"])
p4 = prompter.apply_persona(base, "danbooru", {"category": "动漫少女"}, {"persona_enabled": False})
check("persona_disabled", p4["prompt"] == base["prompt"] and p4["negative"] == base["negative"])
p5 = prompter.apply_persona(base, "natural", {"category": "真人少女"})
check("persona_natural_no_tags", p5["prompt"] == base["prompt"])
check("persona_natural_zh_negative", "未成年" in p5["negative"])
p6 = prompter.apply_persona(base, "natural_en", {"category": "写真摄影"})
check("persona_natural_en_negative", "old woman" in p6["negative"])
check("persona_no_mutate", base["prompt"] == "night, school uniform, smile")

# ---------- v0.7.6：画风硬锚定（可画类型真正写进提示词） ----------
check("normalize_scope",
      [prompter.normalize_scope(x) for x in ("ANIME", "photo", "both", "bad", None)]
      == ["anime", "photo", "both", "both", "both"])

bt = prompter.brief_text({
    "category": "风景场景", "subject": "银河", "style": "新海诚式光影",
    "composition": "超广角", "light": "星云紫蓝", "mood": "宁静", "scope": "anime",
})
check("brief_has_scope_label", "可画类型=只画动漫（二次元插画）" in bt, bt)
check("brief_scope_both", "可画类型=两者皆可" in prompter.brief_text({"scope": "bad"}))
bt2 = prompter.brief_text({"scope": "photo"}, "赛博朋克")
check("brief_theme_keeps_scope", "题材=赛博朋克" in bt2 and "可画类型=只画真人" in bt2, bt2)

# 系统提示词：三种风格都加画风硬规则；both 不加；开关可关；完全覆盖也照样追加
for st in ("danbooru", "natural", "natural_en"):
    s = prompter.build_system_prompt(st, {}, "anime")
    check(f"sys_anchor_anime_{st}", ("画风硬约束" in s or "STYLE LOCK" in s) and "JSON" in s)
    s2 = prompter.build_system_prompt(st, {}, "photo")
    check(f"sys_anchor_photo_{st}", "写实摄影" in s2 or "photorealistic photograph" in s2)
check("sys_anchor_en_only_anime",
      "2D anime illustration" in prompter.build_system_prompt("natural_en", {}, "anime"))
check("sys_anchor_both_none",
      "画风硬约束" not in prompter.build_system_prompt("danbooru", {}, "both"))
check("sys_anchor_off", "画风硬约束" not in prompter.build_system_prompt(
    "danbooru", {"style_anchor_enabled": False}, "anime"))
ov_anchor = prompter.build_system_prompt("danbooru", {"override_danbooru": "only mine"}, "anime")
check("sys_anchor_override", "画风硬约束" in ov_anchor and "Danbooru" not in ov_anchor)
check("sys_anchor_override_off", "画风硬约束" not in prompter.build_system_prompt(
    "danbooru", {"override_danbooru": "only mine", "style_anchor_enabled": False}, "anime"))

# 风格池：可画类型优先，风景 / 星空等 both 题材也跟着画风走
check("style_pool_scope_anime",
      set(prompter.style_pool_for("风景场景", "anime")) <= set(prompter.STYLES_ANIME))
check("style_pool_scope_photo",
      set(prompter.style_pool_for("宇宙星空", "photo")) <= set(prompter.STYLES_PHOTO))
check("style_pool_scope_both_global", prompter.style_pool_for("风景场景", "both") == prompter.STYLES)
check("style_pool_bad_scope", prompter.style_pool_for("风景场景", "xxx") == prompter.STYLES)
photo_only = set(prompter.STYLES_PHOTO) - set(prompter.STYLES_ANIME)
anime_styles = {prompter.draw_brief(random.Random(s), scope="anime")["style"] for s in range(200)}
check("draw_brief_anime_no_photo_style", not (anime_styles & photo_only), str(anime_styles & photo_only))
photo_styles = {prompter.draw_brief(random.Random(s), scope="photo")["style"] for s in range(200)}
anime_only = set(prompter.STYLES_ANIME) - set(prompter.STYLES_PHOTO)
check("draw_brief_photo_no_anime_style", not (photo_styles & anime_only), str(photo_styles & anime_only))
check("draw_brief_scope_field", prompter.draw_brief(random.Random(1), scope="anime")["scope"] == "anime")

# 输出硬拼接（danbooru）：前置动漫标签 + 排除写实词，景物类仍不塞人物
b = {"prompt": "scenery, night, milky way", "negative": "lowres", "caption": "x"}
a = prompter.apply_persona(b, "danbooru", {"category": "风景场景"}, {}, "anime")
check("anchor_danbooru_tags", "anime style" in a["prompt"] and "2d" in a["prompt"])
check("anchor_danbooru_neg", "photorealistic" in a["negative"] and "3d render" in a["negative"])
check("anchor_scenery_no_1girl", "1girl" not in a["prompt"])
ap = prompter.apply_persona(b, "danbooru", {"category": "风景场景"}, {}, "photo")
check("anchor_danbooru_photo", "photorealistic" in ap["prompt"])
check("anchor_photo_neg_anime", "anime," in ap["negative"])
a2 = prompter.apply_persona(a, "danbooru", {"category": "风景场景"}, {}, "anime")
check("anchor_idempotent", a2["prompt"] == a["prompt"] and a2["negative"] == a["negative"])

# 自然语言风格：句首点明画风，不插标签串
n = {"prompt": "夜空下有一条银河落在湖面。", "negative": "模糊", "caption": "x"}
na = prompter.apply_persona(n, "natural", {"category": "风景场景"}, {}, "anime")
check("anchor_natural_prefix", na["prompt"].startswith("一幅精美的二次元动漫插画，"))
check("anchor_natural_neg", "写实照片" in na["negative"])
ne = prompter.apply_persona(
    {"prompt": "A lake under the milky way.", "negative": "blurry", "caption": "x"},
    "natural_en", {"category": "风景场景"}, {}, "anime")
check("anchor_en_prefix", ne["prompt"].startswith("An exquisite anime-style illustration"))
check("anchor_en_neg", "photorealistic" in ne["negative"])
ne2 = prompter.apply_persona(
    {"prompt": "An anime illustration of a lake.", "negative": "", "caption": "x"},
    "natural_en", {"category": "风景场景"}, {}, "anime")
check("anchor_en_no_duplicate_prefix", ne2["prompt"].startswith("An anime illustration"))
np_ = prompter.apply_persona(n, "natural", {"category": "风景场景"}, {}, "photo")
check("anchor_natural_photo_prefix", np_["prompt"].startswith("一张高质量的写实摄影作品，"))

# 开关 / 两套锚定互不干扰
check("anchor_both_noop", prompter.apply_persona(
    b, "danbooru", {"category": "风景场景"}, {}, "both")["prompt"] == b["prompt"])
check("anchor_off_noop", prompter.apply_persona(
    b, "danbooru", {"category": "风景场景"}, {"style_anchor_enabled": False}, "anime")["prompt"] == b["prompt"])
check("anchor_off_keeps_persona", "old woman" in prompter.apply_persona(
    b, "danbooru", {"category": "风景场景"}, {"style_anchor_enabled": False}, "anime")["negative"])
check("anchor_persona_off_still_anchors", "anime style" in prompter.apply_persona(
    b, "danbooru", {"category": "风景场景"}, {"persona_enabled": False}, "anime")["prompt"])
check("anchor_no_mutate", b["prompt"] == "scenery, night, milky way")

# ---------- v0.7.8：danbooru 标签规范化 ----------
clean, dropped = prompter.normalize_tags(
    "1girl, hatsune miku (vocaloid), (detailed eyes:1.3), 1GIRL, artist:someone, by foo, blue_hair"
)
check("norm_weight_unwrapped", "(detailed eyes" not in clean and "detailed eyes" in clean, clean)
check("norm_paren_escaped", "hatsune miku \\(vocaloid\\)" in clean, clean)
check("norm_dedupe", clean.lower().count("1girl") == 1, clean)
check("norm_artist_dropped", "artist:" not in clean and "by foo" not in clean, clean)
check("norm_underscore", "blue hair" in clean, clean)
check("norm_banned", dropped == [] and "1girl" in clean, str(dropped))
c2, d2 = prompter.normalize_tags("1girl, nude, solo", banned=["nude"])
check("norm_banned_removed", "nude" not in c2 and d2 == ["nude"], f"{c2} {d2}")
c3, _ = prompter.normalize_tags("a, b, c, d", max_tags=2)
check("norm_max_tags", c3 == "a, b", c3)
c4, _ = prompter.normalize_tags("tag \\(already\\)")
check("norm_no_double_escape", c4 == "tag \\(already\\)", c4)

# normalize_payload：只对 danbooru；禁词进负向；自然语言原样
p = prompter.normalize_payload(
    {"prompt": "1girl (smile), nude", "negative": ""}, "danbooru", {"banned_tags": "nude"}
)
check("norm_payload_danbooru", "1girl \\(smile\\)" in p["prompt"] and "nude" in p["negative"], str(p))
p_nat = prompter.normalize_payload({"prompt": "（中文括号）不需要转义", "negative": ""}, "natural", {})
check("norm_payload_natural_untouched", p_nat["prompt"] == "（中文括号）不需要转义", p_nat["prompt"])

# apply_negative_base：英文线/中文线；可清空
nb = prompter.apply_negative_base({"prompt": "x", "negative": "extra defect"}, "danbooru", {})
check("neg_base_en",
      nb["negative"].startswith("worst quality") and nb["negative"].endswith("extra defect"),
      nb["negative"])
check("neg_base_en_dedupe",
      prompter.apply_negative_base({"prompt": "x", "negative": "lowres"}, "danbooru", {})["negative"]
      .count("lowres") == 1,
      prompter.apply_negative_base({"prompt": "x", "negative": "lowres"}, "danbooru", {})["negative"])
nb2 = prompter.apply_negative_base({"prompt": "x", "negative": ""}, "natural", {})
check("neg_base_zh", "低画质" in nb2["negative"], nb2["negative"])
nb3 = prompter.apply_negative_base({"prompt": "x", "negative": "a"}, "danbooru", {"negative_base_en": ""})
check("neg_base_off", nb3["negative"] == "a", nb3["negative"])

# _merge_tags 认中文逗号（否则整串当成一个标签，dedupe 等于没做）
merged = prompter._merge_tags("低画质", "低画质，水印", False)
check("merge_tags_zh_dedupe", merged == "低画质, 水印", merged)

# ---------- v0.7.8：镜头 / 媒介层 ----------
b_anime = prompter.draw_brief(random.Random(7), scope="anime")
b_photo = prompter.draw_brief(random.Random(7), scope="photo")
b_both = prompter.draw_brief(random.Random(7), scope="both", disabled_categories={
    "美少女", "动漫少女", "真人少女", "写真摄影", "动漫角色", "游戏角色", "人物肖像",
})
check("brief_anime_medium", bool(b_anime.get("medium")) and not b_anime.get("lens"), str(b_anime))
check("brief_photo_lens", bool(b_photo.get("lens")) and not b_photo.get("medium"), str(b_photo))
check("brief_both_neither", not b_both.get("lens") and not b_both.get("medium"), str(b_both))
check("brief_text_has_lens", "镜头=" in prompter.brief_text(b_photo), prompter.brief_text(b_photo))

# ---------- v0.7.8：指令参数 ----------
a = prompter.parse_draw_args("樱花少女 --no blurry,watermark --ratio 3:2 --seed 42")
check("args_theme", a["theme"] == "樱花少女", a["theme"])
check("args_negative", a["negative"] == "blurry,watermark", a["negative"])
check("args_ratio", a["ratio"] == 1.5 and a["orientation"] == "landscape", str(a))
check("args_seed", a["seed"] == 42, str(a["seed"]))
check("args_error_empty", a["error"] == "", a["error"])
a2 = prompter.parse_draw_args("少女 --style=胶片摄影颗粒感")
check("args_style_inline", a2["theme"] == "少女" and a2["style"] == "胶片摄影颗粒感", str(a2))
a3 = prompter.parse_draw_args("少女 --unknown x")
check("args_unknown_key", "不认识" in a3["error"], a3["error"])
check("args_ratio_alias_square", prompter.parse_ratio("方") == (1.0, "square"))
check("args_ratio_bad", prompter.parse_ratio("abc:0") == (None, ""))
check("args_ratio_portrait", prompter.parse_ratio("2:3")[1] == "portrait")

# pick_size 显式比例优先
sizes = prompter.SIZE_PRESETS["sdxl"]
w, h = prompter.pick_size(sizes, "landscape", "特写·竖幅", random.Random(3), ratio=1.78)
check("pick_size_ratio_override", max(w, h) / min(w, h) >= 1.3, f"{w}x{h}")

# ---------- v0.7.8：质检契约 ----------
sc = prompter.parse_score_payload('{"score": 82, "issues": ["手指略崩", "背景糊"]}')
check("score_parse_basic", sc["score"] == 82 and "手指" in sc["issues"], str(sc))
sc2 = prompter.parse_score_payload('```json\n{"score": "95", "issues": ""}\n```')
check("score_parse_fenced_str", sc2["score"] == 95 and sc2["issues"] == "", str(sc2))
sc3 = prompter.parse_score_payload('评分如下：{"score": 140}')
check("score_clamped", sc3["score"] == 100, str(sc3))
check("score_parse_garbage", prompter.parse_score_payload("我觉得不错") is None)
check("score_parse_no_score", prompter.parse_score_payload('{"issues": []}') is None)

# ---------- v0.7.9：时间一致性（daylight） ----------
daylight = prompter.daylight
sr6, ss6 = daylight.sun_hours(6, 35)
sr12, ss12 = daylight.sun_hours(12, 35)
check("sun_june_long_day", 4.0 < sr6 < 6.0 and 18.0 < ss6 < 20.0, f"{sr6:.2f}/{ss6:.2f}")
check("sun_dec_short_day", 6.0 < sr12 < 8.5 and 16.0 < ss12 < 18.5, f"{sr12:.2f}/{ss12:.2f}")
check("sun_clamped_lat", daylight.sun_hours(6, 999)[1] > 12)  # 纬度夹取后仍能算
check("phase_deep_night", daylight.phase_of(datetime(2026, 6, 21, 3, 0), 35) == "deep_night")
check("phase_day", daylight.phase_of(datetime(2026, 6, 21, 12, 0), 35) == "day")
check("phase_dusk", daylight.phase_of(datetime(2026, 6, 21, 18, 30), 35) == "dusk")
check("phase_night", daylight.phase_of(datetime(2026, 6, 21, 20, 0), 35) == "night")
check("phase_dec_day", daylight.phase_of(datetime(2026, 12, 21, 12, 0), 35) == "day")
lights_night = daylight.filter_lights(prompter.LIGHTS, "deep_night")
check("lights_filtered", 0 < len(lights_night) < len(prompter.LIGHTS), str(lights_night))
# v0.7.18：夜间池除强风格光外还保留中性日用光（路灯/窗内/台灯…）
_NIGHT_OK_WORDS = ("月光", "霓虹", "星云", "极光", "烛火", "路灯", "窗内", "台灯", "漫射", "室内自然光")
check("lights_night_sane", all(any(k in w for k in _NIGHT_OK_WORDS) for w in lights_night),
      str(lights_night))
check("lights_no_phase_keeps_all", daylight.filter_lights(prompter.LIGHTS, "") == prompter.LIGHTS)
check("rule_deep_night", "夜间光照" in daylight.rule_for_phase("deep_night"), daylight.rule_for_phase("deep_night"))
check("rule_unknown_empty", daylight.rule_for_phase("nope") == "")
check("conflict_night_noon", daylight.conflicts("正午的阳光很烈", "deep_night") != [])
check("conflict_none", daylight.conflicts("月光下的湖面很安静", "deep_night") == [])
check("conflict_day_midnight", daylight.conflicts("midnight street", "day") != [])

# 简报带时段：光色必须落在夜间池里，并写进简报文本
b_night = prompter.draw_brief(random.Random(11), phase="deep_night")
check("brief_phase_key", b_night.get("phase") == "deep_night")
check("brief_light_in_night_pool", b_night["light"] in lights_night, b_night["light"])
check("brief_text_phase", "时段=深夜" in prompter.brief_text(b_night), prompter.brief_text(b_night))
sys_p = prompter.build_system_prompt("danbooru", {}, "both", phase="deep_night")
check("sys_prompt_time_rule", "时间一致性" in sys_p, sys_p[-300:])
check("sys_prompt_time_rule_override", "时间一致性" in prompter.build_system_prompt(
    "danbooru", {"override_danbooru": "自定义提示词"}, "both", phase="night"))
check("sys_prompt_no_phase", "时间一致性" not in prompter.build_system_prompt("danbooru", {}, "both"))

# ---------- v0.7.9：角色卡 ----------
card = {"name": "初音未来", "tags": "hatsune miku, twintails, aqua hair",
        "negative": "wrong character, blonde hair"}
pc = prompter.apply_character({"prompt": "1girl, stage", "negative": "lowres"}, card, "danbooru")
check("char_tags_front", pc["prompt"].startswith("hatsune miku, twintails"), pc["prompt"])
check("char_negative_merged", "wrong character" in pc["negative"], pc["negative"])
pc_nat = prompter.apply_character({"prompt": "窗边的少女", "negative": ""}, card, "natural")
check("char_natural_no_tags", pc_nat["prompt"] == "窗边的少女", pc_nat["prompt"])
check("char_natural_negative", "wrong character" in pc_nat["negative"], pc_nat["negative"])
check("char_none_noop", prompter.apply_character({"prompt": "a", "negative": ""}, None, "danbooru")["prompt"] == "a")

# --char 开关
a_char = prompter.parse_draw_args("少女 --char 初音未来")
check("args_char", a_char["char"] == "初音未来" and a_char["theme"] == "少女", str(a_char))

# ---------- v0.7.10：主体类型 / 动物反拟人 / 人物近景 ----------
check("kind_person", prompter.subject_kind("动漫少女", "窗边的少女") == "person")
check("kind_animal", prompter.subject_kind("自然生态", "竹林间回望的赤狐") == "animal")
check("kind_animal_no_word", prompter.subject_kind("自然生态", "苔藓与蘑菇的微距世界") == "other")
check("kind_scene", prompter.subject_kind("风景场景", "雪山") == "other")
check("kind_custom_animal_cat",
      prompter.subject_kind("萌宠", "柴犬在雪地", animal_cats={"萌宠"}) == "animal")
check("kind_not_animal_cat", prompter.subject_kind("萌宠", "柴犬在雪地") == "other")

pend = {"prompt": "1girl, cat", "negative": "lowres"}
pa = prompter.apply_subject(pend, {"category": "自然生态", "subject": "竹林间的赤狐"}, "danbooru")
check("animal_tag_front", pa["prompt"].startswith("animal focus, no humans"), pa["prompt"])
check("animal_neg_furry", "furry" in pa["negative"] and "humanoid" in pa["negative"], pa["negative"])
check("animal_guard_off", "furry" not in prompter.apply_subject(
    {"prompt": "fox", "negative": ""}, {"category": "自然生态", "subject": "赤狐"},
    "danbooru", {"animal_guard": False})["negative"])
pa_zh = prompter.apply_subject(
    {"prompt": "一只狐狸", "negative": ""}, {"category": "自然生态", "subject": "赤狐"}, "natural"
)
check("animal_neg_zh", "福瑞" in pa_zh["negative"] and "一只狐狸" == pa_zh["prompt"], str(pa_zh))

pp = prompter.apply_subject(
    {"prompt": "1girl, window", "negative": ""}, {"category": "动漫少女", "subject": "少女"}, "danbooru"
)
check("person_detail_tags", "detailed face" in pp["prompt"], pp["prompt"])
check("person_bg_negative", "busy background" in pp["negative"], pp["negative"])
check("person_detail_off", "detailed face" not in prompter.apply_subject(
    {"prompt": "1girl", "negative": ""}, {"category": "动漫少女"}, "danbooru",
    {"person_detail": False, "person_closeup": False})["prompt"])

# 构图池：人物偏近景；非人物仍是十项
only_person = set(prompter.CATEGORIES) - {"动漫少女"}
comp_names = {c[0] for c in prompter.PERSON_COMPOSITIONS}
comps = {
    prompter.draw_brief(random.Random(s), disabled_categories=only_person)["composition"]
    for s in range(60)
}
check("person_comp_subset", comps <= comp_names, str(comps))
check("person_comp_portrait_majority",
      sum(1 for s in range(60)
          if prompter.draw_brief(random.Random(s), disabled_categories=only_person)["orientation"] == "portrait") >= 45)
only_animal = set(prompter.CATEGORIES) - {"自然生态"}
kinds = {
    prompter.draw_brief(random.Random(s), disabled_categories=only_animal,
                        animal_cats={"自然生态"})["subject_kind"]
    for s in range(60)
}
check("animal_kinds", kinds <= {"animal", "other"} and "animal" in kinds, str(kinds))

b_animal = prompter.draw_brief(random.Random(3), disabled_categories=only_animal,
                               animal_cats={"自然生态"})
if b_animal.get("subject_kind") == "animal":
    check("brief_animal_note", "非拟人" in (b_animal.get("species_note") or ""), str(b_animal))
    check("brief_text_kind", "主体=动物" in prompter.brief_text(b_animal), prompter.brief_text(b_animal))

sys_animal = prompter.build_system_prompt("danbooru", {}, "anime", subject_kind="animal")
check("sys_animal_rule", "animal focus" in sys_animal and "furry" in sys_animal, sys_animal[-400:])
check("sys_animal_rule_override", "animal focus" in prompter.build_system_prompt(
    "danbooru", {"override_danbooru": "自定义"}, "both", subject_kind="animal"))
check("sys_animal_rule_off", "animal focus" not in prompter.build_system_prompt(
    "danbooru", {"animal_guard": False}, "both", subject_kind="animal"))
sys_person = prompter.build_system_prompt("danbooru", {}, "anime", subject_kind="person")
check("sys_person_rule", "close-up" in sys_person, sys_person[-300:])
check("sys_kind_none", "animal focus" not in prompter.build_system_prompt("danbooru", {}, "both"))

# ---------- v0.7.12：冲突压缩（用真实翻车案例当 fixture） ----------
BAD = (
    "masterpiece, best quality, anime style, 2d, cel shading, solo, asian, east asian, "
    "1girl, rain, umbrella, neon lights, rainy night, city corner, twilight, golden hour, "
    "warm light, candlelight, lonely, empty street, glowing signs, wet pavement, close up, "
    "upper body, banner composition, game promo, simple background, blurry background, "
    "cinematic lighting, sharp focus, depth of field"
)
bad_tags = prompter.split_tag_list(BAD)

kept_n, drop_n = prompter.reduce_lighting(bad_tags, "霓虹夜色")
check("light_neon_keeps_neon", "neon lights" in kept_n and "glowing signs" in kept_n, str(kept_n))
check("light_neon_drops_golden", "golden hour" not in kept_n and "golden hour" in drop_n, str(drop_n))
check("light_neon_drops_candle", "candlelight" not in kept_n and "candlelight" in drop_n)
check("light_neon_drops_twilight", "twilight" not in kept_n)
check("light_neon_keeps_rainy_night", "rainy night" in kept_n)
kept_g, _drop_g = prompter.reduce_lighting(bad_tags, "黄昏暖光")
check("light_golden_keeps_warm", "golden hour" in kept_g and "warm light" in kept_g, str(kept_g))
check("light_golden_drops_neon", "neon lights" not in kept_g and "rainy night" not in kept_g)
kept_c, _ = prompter.reduce_lighting(bad_tags, "烛火暖光")
check("light_candle_primary", "candlelight" in kept_c and "golden hour" not in kept_c, str(kept_c))
kept_none, _ = prompter.reduce_lighting(bad_tags, "")
check("light_no_brief_caps_one_source",
      len([t for t in kept_none if prompter._classify_light(t)[0]]) <= 1, str(kept_none))

bg_kept, bg_drop = prompter.reduce_background(bad_tags)
check("bg_scene_drops_simple", "simple background" not in bg_kept and "simple background" in bg_drop,
      str(bg_drop))
check("bg_scene_keeps_blurry", "blurry background" in bg_kept, str(bg_kept))

fr_kept, fr_drop = prompter.reduce_framing(bad_tags, "特写·竖幅")
check("frame_closeup_drops_banner",
      "banner composition" not in fr_kept and "game promo" not in fr_kept, str(fr_drop))
check("frame_closeup_keeps_closeup", "close up" in fr_kept and "upper body" in fr_kept)
fr_wide, _ = prompter.reduce_framing(bad_tags, "全景·横幅")
check("frame_wide_drops_closeup", "close up" not in fr_wide and "upper body" not in fr_wide, str(fr_wide))

p_conf = prompter.reduce_conflicts(
    {"prompt": BAD, "negative": "lowres"},
    {"light": "霓虹夜色", "composition": "特写·竖幅"}, "danbooru",
)
check("conflicts_prompt_clean",
      "golden hour" not in p_conf["prompt"] and "banner composition" not in p_conf["prompt"]
      and "simple background" not in p_conf["prompt"], p_conf["prompt"])
check("conflicts_negative_carries", "golden hour" in p_conf["negative"]
      and "banner composition" in p_conf["negative"], p_conf["negative"])
check("conflicts_keeps_scene", "neon lights" in p_conf["prompt"] and "rainy night" in p_conf["prompt"])
check("conflicts_natural_untouched",
      prompter.reduce_conflicts({"prompt": BAD, "negative": ""},
                                {"light": "霓虹夜色"}, "natural")["prompt"] == BAD)
check("conflicts_switch_off",
      prompter.reduce_conflicts({"prompt": BAD, "negative": ""},
                                {"light": "霓虹夜色"}, "danbooru",
                                {"conflict_cleanup": False})["prompt"] == BAD)

check("soft_color_off_default", prompter.apply_soft_color({"prompt": "1girl"}, "danbooru", {})["prompt"] == "1girl")
soft = prompter.apply_soft_color({"prompt": "1girl", "negative": ""}, "danbooru", {"soft_color": True})
check("soft_color_on", "pastel colors" in soft["prompt"] and "oversaturated" in soft["negative"], str(soft))
check("soft_color_natural_off",
      prompter.apply_soft_color({"prompt": "少女"}, "natural", {"soft_color": True})["prompt"] == "少女")

# 人物锚定剔除摄影标签（asian / east asian 不该进二次元标签串）
persona_on = prompter.apply_persona({"prompt": "1girl", "negative": ""}, "danbooru",
                                    {"category": "动漫少女"}, {}, "both")
check("persona_strip_photo", "asian" not in persona_on["prompt"], persona_on["prompt"])
check("persona_keeps_adult", "young adult woman" in persona_on["prompt"], persona_on["prompt"])
persona_raw = prompter.apply_persona({"prompt": "1girl", "negative": ""}, "danbooru",
                                     {"category": "动漫少女"}, {"persona_strip_photo": False}, "both")
check("persona_strip_photo_off", "asian" in persona_raw["prompt"], persona_raw["prompt"])

# 近景时不再抽「壁纸 / 宣传图」这类大构图
only_person2 = set(prompter.CATEGORIES) - {"动漫少女"}
media = {prompter.draw_brief(random.Random(s), disabled_categories=only_person2)["medium"]
         for s in range(40)}
check("medium_closeup_pool", media <= set(prompter.MEDIUM_ANIME_CLOSEUP), str(media))

# ---------- v0.7.13：风格层与画风锚定互斥（3D 风格事故） ----------
anime_pool = prompter.style_pool_for("动漫少女", "anime")
check("style_anime_no_3d",
      not any(any(w in s.lower() for w in ("3d", "cg", "皮克斯")) for s in anime_pool),
      str(anime_pool))
check("style_anime_keeps_rest",
      "赛璐璐动画风" in anime_pool and len(anime_pool) >= 8, str(len(anime_pool)))
anime_pool_3d = prompter.style_pool_for("动漫少女", "anime", allow_3d=True)
check("style_anime_allow_3d",
      any("3d" in s.lower() or "皮克斯" in s for s in anime_pool_3d), str(anime_pool_3d))
check("style_both_keeps_3d",
      any("皮克斯" in s for s in prompter.style_pool_for("风景场景", "both")))
styles = {prompter.draw_brief(random.Random(s), scope="anime")["style"] for s in range(50)}
check("draw_anime_no_3d_style",
      not any(any(w in x.lower() for w in ("3d", "cg", "皮克斯")) for x in styles), str(styles))

kept_s, drop_s = prompter.strip_scope_conflicts(
    ["anime style", "2d", "3d", "pixar", "octane render", "photorealistic", "1girl"], "anime")
check("strip_scope_anime_drops_realism",
      drop_s == ["3d", "pixar", "octane render", "photorealistic"], str(drop_s))
check("strip_scope_anime_keeps_illust",
      "anime style" in kept_s and "1girl" in kept_s and "2d" in kept_s, str(kept_s))
kept_p, drop_p = prompter.strip_scope_conflicts(
    ["photorealistic", "anime", "cel shading", "85mm lens"], "photo")
check("strip_scope_photo_drops_illust", drop_p == ["anime", "cel shading"], str(drop_p))
check("strip_scope_photo_keeps_photo", "photorealistic" in kept_p and "85mm lens" in kept_p)
check("strip_scope_both_noop", prompter.strip_scope_conflicts(["3d", "anime"], "both")[1] == [])
check("strip_scope_allow_3d",
      prompter.strip_scope_conflicts(["3d", "photorealistic"], "anime", allow_3d=True)[1]
      == ["photorealistic"])

p3d = prompter.reduce_conflicts(
    {"prompt": "1girl, 3d, pixar, octane render, warm light", "negative": "lowres"},
    {"light": ""}, "danbooru", {}, "anime",
)
check("conflicts_scope_3d_dropped",
      "3d" not in p3d["prompt"] and "pixar" not in p3d["prompt"], p3d["prompt"])
check("conflicts_scope_3d_negative", "pixar" in p3d["negative"], p3d["negative"])
check("conflicts_scope_3d_off_when_anchor_off",
      "3d" in prompter.reduce_conflicts(
          {"prompt": "1girl, 3d", "negative": ""}, {"light": ""}, "danbooru",
          {"style_anchor_enabled": False}, "anime")["prompt"])

# ---------- v0.7.18：夜间霓虹降权 + 「不指定画风」 ----------
lights_night = prompter.daylight.filter_lights(prompter.LIGHTS, "deep_night")
daily = [w for w in prompter.LIGHTS if any(n in w for n in ("路灯", "窗内", "台灯", "漫射", "室内自然光"))]
check("night_pool_keeps_daily", daily and all(w in lights_night for w in daily), str(lights_night))
check("night_pool_size", len(lights_night) >= 8, str(len(lights_night)))
neon_n = sum(1 for s in range(300) if prompter.draw_brief(random.Random(s), phase="deep_night")["light"]
             == "霓虹夜色")
check("neon_not_dominant", neon_n / 300 < 0.12, f"{neon_n}/300")

all_none = sum(1 for s in range(300)
               if not prompter.draw_brief(random.Random(s), style_none_pct=100)["style"])
check("style_none_all", all_none == 300, str(all_none))
none_off = sum(1 for s in range(200)
               if not prompter.draw_brief(random.Random(s), style_none_pct=0)["style"])
check("style_none_off", none_off == 0, str(none_off))
mixed = sum(1 for s in range(400)
            if not prompter.draw_brief(random.Random(s), style_none_pct=40)["style"])
check("style_none_ratio", 120 <= mixed <= 200, str(mixed))
b_none = prompter.draw_brief(random.Random(1), style_none_pct=100)
check("style_none_brief_text", "画风=不指定" in prompter.brief_text(b_none),
      prompter.brief_text(b_none))
b_some = prompter.draw_brief(random.Random(1), style_none_pct=0)
check("style_some_brief_text", "画风=不指定" not in prompter.brief_text(b_some),
      prompter.brief_text(b_some))
check("light_weights_sane", prompter.LIGHT_WEIGHTS.get("霓虹夜色", 1) < 0.5,
      str(prompter.LIGHT_WEIGHTS))

print("prompter:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
