"""workflow_mgr 自检：内置模板节点定位 / 注入 / 轮换。跑法：
uv run --no-project python tests/test_workflow_mgr.py（退出码即结论）
"""

import json
import random
import sys
import tempfile
from pathlib import Path

from _loader import load, root

workflow_mgr = load("workflow_mgr")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


wf = json.loads((root() / "builtin" / "anima_t2i.json").read_text(encoding="utf-8"))

# ---------- 内置模板节点定位 ----------
info = workflow_mgr.locate_nodes(wf)
check("locate_positive", info["positive"] == "3", str(info))
check("locate_negative", info["negative"] == "4", str(info))
check("locate_latent", info["latent"] == "5", str(info))
check("locate_seed", ("6", "seed") in [tuple(x) for x in info["seed_nodes"]], str(info["seed_nodes"]))
check("locate_ok", not info["problems"], str(info["problems"]))
check("locate_field_default", info["positive_field"] == "text")

# 覆盖项优先
info2 = workflow_mgr.locate_nodes(wf, {"positive_node": "4"})
check("override_positive", info2["positive"] == "4")

# ---------- 注入 ----------
tpl = workflow_mgr.Template(
    name="t", file="anima_t2i.json", tag_style="danbooru", size_preset="sdxl",
    default_negative="默认负向", wf=wf, info=info, ok=True, sizes=workflow_mgr.prompter.SIZE_PRESETS["sdxl"],
)
out = workflow_mgr.inject(tpl.wf, info, "1girl, night", "lowres", 1216, 832, 12345)
check("inject_prompt", out["3"]["inputs"]["text"] == "1girl, night")
check("inject_negative", out["4"]["inputs"]["text"] == "lowres")
check("inject_size", out["5"]["inputs"]["width"] == 1216 and out["5"]["inputs"]["height"] == 832)
check("inject_seed", out["6"]["inputs"]["seed"] == 12345)
check("inject_no_mutate", tpl.wf["3"]["inputs"]["text"] == "1girl")  # 原件不被污染
out2 = workflow_mgr.inject(tpl.wf, info, "x", "", 832, 1216, 7)
check("inject_negative_empty_keeps", out2["4"]["inputs"]["text"] != "x")

# ---------- 轮换 ----------
ok_tpl = workflow_mgr.Template(name="a", file="a.json", ok=True, enabled=True)
ok_tpl2 = workflow_mgr.Template(name="a2", file="a2.json", ok=True, enabled=True)
bad_tpl = workflow_mgr.Template(name="b", file="b.json", ok=False, enabled=True, problem="x")
off_tpl = workflow_mgr.Template(name="c", file="c.json", ok=True, enabled=False)
pool = [ok_tpl, ok_tpl2, bad_tpl, off_tpl]
picked, last = workflow_mgr.pick_template(pool, "random", random.Random(1), "a.json")
check("pick_random_excludes_bad", picked in (ok_tpl, ok_tpl2))
check("pick_random_no_advance", last == "a.json", last)
p1, k1 = workflow_mgr.pick_template(pool, "round_robin", random.Random(1), "")
p2, k2 = workflow_mgr.pick_template(pool, "round_robin", random.Random(1), k1)
p3, k3 = workflow_mgr.pick_template(pool, "round_robin", random.Random(1), k2)
check("pick_rr_cycles", p1 is ok_tpl and p2 is ok_tpl2 and p3 is ok_tpl,
      f"{p1.name},{p2.name},{p3.name}")
check("pick_rr_keys", (k1, k2, k3) == ("a.json", "a2.json", "a.json"), f"{k1},{k2},{k3}")
none_tpl, kept = workflow_mgr.pick_template(
    [bad_tpl, off_tpl], "round_robin", random.Random(1), "a.json"
)
check("pick_empty_pool", none_tpl is None and kept == "a.json")

# ---------- 加载（内置复制 + 校验） ----------
with tempfile.TemporaryDirectory() as td:
    wf_dir = Path(td) / "workflows"
    entries = [
        {"name": "anima 默认", "file": "anima_t2i.json", "tag_style": "danbooru",
         "size_preset": "sdxl", "enabled": True},
    ]
    loaded = workflow_mgr.load_templates(wf_dir, entries, root() / "builtin")
    check("load_builtin_copied", (wf_dir / "anima_t2i.json").is_file())
    check("load_one", len(loaded) == 1)
    t = loaded[0]
    check("load_ok", t.ok, t.problem)
    check("load_sizes", t.sizes.get("landscape") == [[1216, 832], [1152, 896], [1344, 768]])
    loaded2 = workflow_mgr.load_templates(
        wf_dir, [{"name": "缺文件", "file": "nope.json", "enabled": True}], root() / "builtin"
    )
    check("load_missing_flagged", len(loaded2) == 1 and not loaded2[0].ok and "不存在" in loaded2[0].problem)

# ---------- v0.4.0：上传解析 / analyze / 跟随尺寸 ----------
api_text = json.dumps(wf, ensure_ascii=False)
parsed, perrs = workflow_mgr.parse_upload(api_text)
check("parse_api_ok", parsed is not None and not perrs, str(perrs))
check("parse_api_nodes", parsed is not None and len(parsed) == len(wf))

ui_text = json.dumps({"last_node_id": 9, "nodes": [{"id": 1}], "links": []})
_, uerr = workflow_mgr.parse_upload(ui_text)
check("parse_ui_rejected", not _ and any("界面格式" in e for e in uerr), str(uerr))

_, jerr = workflow_mgr.parse_upload("{not json")
check("parse_bad_json", any("JSON 解析失败" in e for e in jerr), str(jerr))

_, merr = workflow_mgr.parse_upload(json.dumps({"1": {"inputs": {}}}))
check("parse_missing_class_type", any("class_type" in e for e in merr), str(merr))

# /history 导出的 {prompt: {...}} 包一层也能认
wrapped, werr = workflow_mgr.parse_upload(json.dumps({"prompt": wf, "client_id": "x"}))
check("parse_unwrap_prompt", wrapped is not None and not werr, str(werr))

ana = workflow_mgr.analyze(wf)
check("analyze_model", ana["model_file"] == "anima-turbo-v1.0.safetensors", ana.get("model_file", ""))
check("analyze_save", ana["save"] == "8" and "SaveImage" in ana["save_class"], ana.get("save_class", ""))
check("analyze_latent", ana["latent_class"] == "EmptyLatentImage")
check("analyze_default_wh", (ana["default_width"], ana["default_height"]) == (1216, 832),
      f'{ana["default_width"]}x{ana["default_height"]}')
check("analyze_kind", ana["kind"] == "t2i")
check("analyze_no_problems", not ana["problems"], str(ana["problems"]))

# 图生图链路（latent 无 width/height）拒绝入库
img2img = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    "2": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["9", 0]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "y"}},
    "5": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["3", 0],
                                               "negative": ["4", 0], "latent_image": ["2", 0]}},
}
ai = workflow_mgr.analyze(img2img)
check("analyze_img2img_flagged", ai["kind"] == "img2img" and any("暂不支持" in p for p in ai["problems"]),
      str(ai["problems"]))

fs = workflow_mgr.follow_sizes(1344, 768)
check("follow_sizes_landscape", fs["landscape"] == [[1344, 768]], str(fs))
check("follow_sizes_portrait_swap", fs["portrait"] == [[768, 1344]], str(fs))
check("follow_sizes_square", fs["square"] == [[768, 768]], str(fs))
check("follow_sizes_bad", workflow_mgr.follow_sizes(0, 0).get("landscape") == [[1216, 832], [1152, 896], [1344, 768]])
fs2 = workflow_mgr.follow_sizes(832, 1216)
check("follow_sizes_vertical_input", fs2["landscape"] == [[1216, 832]], str(fs2))

# ---------- 入库：落盘 + 同名覆盖 ----------
with tempfile.TemporaryDirectory() as td:
    wf_dir = Path(td) / "workflows"
    fname = workflow_mgr.save_workflow_file(wf_dir, "anima 默认/v2", wf)
    check("save_file_sanitized", fname == "anima_默认_v2.json" and (wf_dir / fname).is_file(), fname)
    back = json.loads((wf_dir / fname).read_text(encoding="utf-8"))
    check("save_file_roundtrip", back["3"]["inputs"]["text"] == "1girl")

    entries, replaced = workflow_mgr.upsert_entry(
        [{"name": "a", "file": "a.json", "tag_style": "danbooru"}],
        {"name": "a", "file": "a2.json", "size_preset": "flux"},
    )
    check("upsert_replaced", replaced and entries[0]["file"] == "a2.json"
          and entries[0]["tag_style"] == "danbooru" and entries[0]["size_preset"] == "flux", str(entries))
    entries2, replaced2 = workflow_mgr.upsert_entry(entries, {"name": "b", "file": "b.json"})
    check("upsert_appended", (not replaced2) and len(entries2) == 2, str(entries2))

# ---------- 多工作流轮换：名单解析 / 挑选 ----------
check("split_names_none", workflow_mgr.split_names("") == [])
check("split_names_none_list", workflow_mgr.split_names(None) == [])
check("split_names_mixed", workflow_mgr.split_names("a, b，c;d\n e") == ["a", "b", "c", "d", "e"],
      str(workflow_mgr.split_names("a, b，c;d\n e")))
check("split_names_dedup", workflow_mgr.split_names("a,a，a") == ["a"])
check("split_names_list_input", workflow_mgr.split_names(["x", " y ", ""]) == ["x", "y"])

cfgs = [
    {"name": "写实", "workflow_id": "wf_1", "enabled": True, "base_id": "3"},
    {"name": "动漫", "workflow_id": "wf_2", "enabled": True, "base_id": "4"},
    {"name": "停用的", "workflow_id": "wf_3", "enabled": False, "base_id": "5"},
    {"name": "旧版", "workflow_id": "wf_4", "enabled": True, "base_id": ""},
]
picked_all = workflow_mgr.select_workflows(cfgs, "")
check("select_all_usable", [w["name"] for w in picked_all] == ["写实", "动漫"], str(picked_all))
picked_one = workflow_mgr.select_workflows(cfgs, "动漫")
check("select_by_name", [w["name"] for w in picked_one] == ["动漫"], str(picked_one))
picked_multi = workflow_mgr.select_workflows(cfgs, "写实,动漫")
check("select_multi", len(picked_multi) == 2, str(picked_multi))
# v0.7.4：名单填写顺序就是轮换顺序（此前按萌绘配置顺序，勾选顺序无效）
check("select_multi_order",
      [w["name"] for w in workflow_mgr.select_workflows(cfgs, "动漫,写实")] == ["动漫", "写实"])
check("select_by_id_order",
      [w["name"] for w in workflow_mgr.select_workflows(cfgs, "wf_2,wf_1")] == ["动漫", "写实"])
check("select_order_dedup",
      [w["name"] for w in workflow_mgr.select_workflows(cfgs, "动漫,wf_2")] == ["动漫"])
picked_by_id = workflow_mgr.select_workflows(cfgs, "wf_2")
check("select_by_id", [w["name"] for w in picked_by_id] == ["动漫"], str(picked_by_id))
picked_disabled = workflow_mgr.select_workflows(cfgs, "停用的")
check("select_skips_disabled", picked_disabled == [], str(picked_disabled))
picked_legacy = workflow_mgr.select_workflows(cfgs, "旧版")
check("select_skips_no_base", picked_legacy == [], str(picked_legacy))
check("select_unknown_empty", workflow_mgr.select_workflows(cfgs, "不存在") == [])

# 多模板轮换（跟随萌绘时同样走 random / round_robin）
multi = [workflow_mgr.Template(name=n, file=f"anima:{i}", ok=True, enabled=True, source="anima")
         for i, n in enumerate(["写实", "动漫", "国风"])]
q1, a1 = workflow_mgr.pick_template(multi, "round_robin", random.Random(1), "")
q2, a2 = workflow_mgr.pick_template(multi, "round_robin", random.Random(1), a1)
q3, a3 = workflow_mgr.pick_template(multi, "round_robin", random.Random(1), a2)
check("anima_rr_cycles", [q1.name, q2.name, q3.name] == ["写实", "动漫", "国风"]
      and a3 == "anima:2", f"{q1.name},{q2.name},{q3.name},k={a3}")

# ---------- v0.5.0：底模画像（提示词风格 / 尺寸 / 质量词） ----------
pf = workflow_mgr.guess_profile("anima-turbo-v1.0.safetensors")
check("profile_anime_style", pf["tag_style"] == "danbooru", str(pf))
check("profile_anime_quality", "masterpiece" in pf["quality_prefix"], str(pf))
check("profile_anime_keep_size", pf["size_preset"] == "", str(pf))

pf = workflow_mgr.guess_profile("flux1-dev-fp8.safetensors")
check("profile_flux_style", pf["tag_style"] == "natural_en", str(pf))
check("profile_flux_size", pf["size_preset"] == "flux", str(pf))
check("profile_flux_no_quality", pf["quality_prefix"] == "", str(pf))

pf = workflow_mgr.guess_profile("qwen_image_vae.safetensors")
check("profile_qwen_style", pf["tag_style"] == "natural_zh", str(pf))
check("profile_qwen_size", pf["size_preset"] == "qwen", str(pf))

pf = workflow_mgr.guess_profile("realvisxlV40_v40Bakedvae.safetensors")
check("profile_photoreal_style", pf["tag_style"] == "natural_en" and pf["size_preset"] == "sdxl", str(pf))

pf = workflow_mgr.guess_profile("mystery-model.safetensors")
check("profile_unknown_keeps_manual", pf["family"] == "unknown" and pf["tag_style"] == "", str(pf))
check("profile_empty_file", workflow_mgr.guess_profile("")["family"] == "unknown")

flux_wf = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "b"}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1344, "height": 768}},
    "5": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["2", 0], "negative": ["3", 0],
                                               "latent_image": ["4", 0], "model": ["1", 0]}},
    "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0]}},
}
with tempfile.TemporaryDirectory() as td:
    wf_dir = Path(td) / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "flux.json").write_text(json.dumps(flux_wf), encoding="utf-8")

    auto = workflow_mgr.load_templates(
        wf_dir,
        [{"name": "flux", "file": "flux.json", "tag_style": "danbooru",
          "size_preset": "sdxl", "prompt_profile": "auto", "enabled": True}],
        root() / "builtin",
    )[0]
    check("auto_profile_style", auto.tag_style == "natural_en", auto.tag_style)
    check("auto_profile_size", auto.size_preset == "flux", auto.size_preset)
    check("auto_profile_quality_empty", auto.quality_prefix == "", auto.quality_prefix)
    check("auto_profile_warned", any("底模适配" in w for w in auto.info.get("warnings") or []),
          str(auto.info.get("warnings")))

    forced = workflow_mgr.load_templates(
        wf_dir,
        [{"name": "flux", "file": "flux.json", "tag_style": "danbooru",
          "size_preset": "sdxl", "prompt_profile": "danbooru", "enabled": True}],
        root() / "builtin",
    )[0]
    check("forced_profile_style", forced.tag_style == "danbooru", forced.tag_style)
    check("forced_profile_size", forced.size_preset == "sdxl", forced.size_preset)
    check("forced_profile_quality", "masterpiece" in forced.quality_prefix, forced.quality_prefix)

# ---------- v0.7.3：模板题材能力（问题 2） ----------
check("scope_family_anime", workflow_mgr.scope_for_family("anime") == "anime")
check("scope_family_photoreal", workflow_mgr.scope_for_family("photoreal") == "photo")
for fam in ("flux", "qwen", "sd3", "unknown"):
    check(f"scope_family_{fam}", workflow_mgr.scope_for_family(fam) == "both")
check("resolve_scope_auto_anime", workflow_mgr.resolve_scope("auto", "anime") == "anime")
check("resolve_scope_auto_unknown", workflow_mgr.resolve_scope("auto", "unknown") == "both")
check("resolve_scope_forced", workflow_mgr.resolve_scope("photo", "anime") == "photo")
check("resolve_scope_garbage", workflow_mgr.resolve_scope("乱写", "anime") == "anime")
check("resolve_scope_empty", workflow_mgr.resolve_scope("", "photoreal") == "photo")

with tempfile.TemporaryDirectory() as td:
    wf_dir = Path(td) / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "a.json").write_text(json.dumps(wf), encoding="utf-8")
    (wf_dir / "f.json").write_text(json.dumps(flux_wf), encoding="utf-8")

    a = workflow_mgr.load_templates(
        wf_dir, [{"name": "动漫", "file": "a.json", "enabled": True}], root() / "builtin")[0]
    check("load_scope_anime_auto", a.scope == "anime", a.scope)
    f = workflow_mgr.load_templates(
        wf_dir, [{"name": "通用", "file": "f.json", "enabled": True}], root() / "builtin")[0]
    check("load_scope_flux_auto", f.scope == "both", f.scope)
    a2 = workflow_mgr.load_templates(
        wf_dir, [{"name": "动漫", "file": "a.json", "subject_scope": "photo", "enabled": True}],
        root() / "builtin")[0]
    check("load_scope_manual_override", a2.scope == "photo" and a2.subject_scope == "photo", a2.scope)
    pub = a.to_public()
    check("public_exposes_scope", pub.get("scope") == "anime" and pub.get("subject_scope") == "auto",
          str(pub.get("scope")))

# ---------- v0.7.4：顺序轮换（游标记标识、不跳位、可视） ----------
t3 = workflow_mgr.Template(name="c", file="c.json", ok=True, enabled=True)
tri = [ok_tpl, ok_tpl2, t3]  # a.json / a2.json / c.json
check("next_index_start", workflow_mgr.next_index(tri, "") == 0)
check("next_index_after_first", workflow_mgr.next_index(tri, "a.json") == 1)
check("next_index_wraps", workflow_mgr.next_index(tri, "c.json") == 0)
check("next_index_missing_key", workflow_mgr.next_index(tri, "不存在") == 0)
check("template_key_explicit",
      workflow_mgr.template_key(workflow_mgr.Template(name="x", file="x.json", key="anima:w1"))
      == "anima:w1")
check("template_key_fallback", workflow_mgr.template_key(ok_tpl) == "a.json")

# 停用中间一条：顺序不断裂，仍从「上一张」往后找现存的下一条
shrunk = [ok_tpl, t3]
check("no_jump_after_disable", workflow_mgr.next_index(shrunk, "a.json") == 1,
      str(workflow_mgr.next_index(shrunk, "a.json")))
check("no_jump_when_last_removed", workflow_mgr.next_index(shrunk, "a2.json") == 0)
# 老整数游标过渡：rr_cursor=2 表示「下一张是 #3」
check("legacy_cursor_transition", workflow_mgr.next_index(tri, "", 2) == 2)
check("legacy_cursor_modulo", workflow_mgr.next_index(tri, "", 5) == 2)
check("new_key_wins_over_legacy", workflow_mgr.next_index(tri, "a.json", 2) == 1)

st = workflow_mgr.rotation_state(tri, "round_robin", "a.json")
check("rotation_state_total", st["total"] == 3, str(st))
check("rotation_state_next", st["next_index"] == 2 and st["next_name"] == "a2", str(st))
check("rotation_state_items", [i["index"] for i in st["items"]] == [1, 2, 3])
check("rotation_state_last_found", st["last_found"] is True)
check("rotation_state_last_lost",
      workflow_mgr.rotation_state(tri, "round_robin", "gone")["last_found"] is False)
check("rotation_state_random",
      workflow_mgr.rotation_state(tri, "random", "a.json")["ordered"] is False)
check("rotation_state_empty", workflow_mgr.rotation_state([], "round_robin")["total"] == 0)

with tempfile.TemporaryDirectory() as td:
    wf_dir = Path(td) / "workflows"
    kt = workflow_mgr.load_templates(
        wf_dir, [{"name": "动漫", "file": "a.json", "enabled": True}], root() / "builtin")[0]
    check("load_template_key", kt.key == "local:a.json"
          and workflow_mgr.template_key(kt) == "local:a.json", kt.key)
    check("load_template_public_key", kt.to_public().get("key") == "local:a.json")

# ---------- v0.7.5：逐条可画类型（跟随萌绘） ----------
check("parse_scope_overrides_basic",
      workflow_mgr.parse_scope_overrides("写实人像=photo, 二次元=anime\n通用=both")
      == {"写实人像": "photo", "二次元": "anime", "通用": "both"})
check("parse_scope_overrides_cn_colon",
      workflow_mgr.parse_scope_overrides("A：anime；B：photo") == {"A": "anime", "B": "photo"})
check("parse_scope_overrides_bad_dropped",
      workflow_mgr.parse_scope_overrides("A=bogus,B=ANIME,=photo,C=") == {"B": "anime"})
check("parse_scope_overrides_dict",
      workflow_mgr.parse_scope_overrides({"A": "photo", "B": "x"}) == {"A": "photo"})
check("parse_scope_overrides_empty", workflow_mgr.parse_scope_overrides("") == {})

ch = {"name": "写实人像", "workflow_id": "wf_1"}
ov = {"写实人像": "photo"}
check("workflow_scope_by_name", workflow_mgr.workflow_scope_for(ch, ov, "auto") == "photo")
check("workflow_scope_by_id",
      workflow_mgr.workflow_scope_for({"name": "x", "workflow_id": "wf_1"},
                                     {"wf_1": "anime"}, "auto") == "anime")
check("workflow_scope_fallback",
      workflow_mgr.workflow_scope_for({"name": "其它"}, ov, "photo") == "photo")
check("workflow_scope_fallback_auto",
      workflow_mgr.workflow_scope_for({"name": "其它"}, {}, "") == "auto")
check("workflow_scope_bad_fallback",
      workflow_mgr.workflow_scope_for({"name": "其它"}, {}, "xxx") == "auto")
check("workflow_scope_empty_chosen",
      workflow_mgr.workflow_scope_for({}, ov, "both") == "both")
check("workflow_scope_resolved",
      workflow_mgr.resolve_scope(
          workflow_mgr.workflow_scope_for({"name": "其它"}, {}, "auto"), "anime") == "anime")

# ---------- v0.7.8：采样器定位 / 参数注入 / 精修图 ----------
check("locate_sampler", info.get("sampler") == "6", str(info.get("sampler")))

ov = workflow_mgr.inject(
    tpl.wf, info, "p", "n", 1024, 1024, 9, overrides={"steps": 28, "cfg": 6.0, "sampler_name": "euler"}
)
check("inject_steps", ov["6"]["inputs"]["steps"] == 28, str(ov["6"]["inputs"]))
check("inject_cfg", ov["6"]["inputs"]["cfg"] == 6.0, str(ov["6"]["inputs"]))
check("inject_sampler_name", ov["6"]["inputs"]["sampler_name"] == "euler", str(ov["6"]["inputs"]))
# 工作流里没有的字段不硬塞（异形采样链会被 ComfyUI 直接拒绝）
wf_nosched = {
    "1": {"class_type": "KSampler", "inputs": {
        "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "denoise": 1.0,
    }},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "b"}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
}
info_ns = workflow_mgr.locate_nodes(wf_nosched)
out_ns = workflow_mgr.inject(
    wf_nosched, info_ns, "p", "n", 512, 512, 5,
    overrides={"scheduler": "karras", "steps": 30},
)
check("inject_missing_field_untouched",
      "scheduler" not in out_ns["1"]["inputs"] and out_ns["1"]["inputs"]["steps"] == 30,
      str(out_ns["1"]["inputs"]))

pr = workflow_mgr.params_for_entry({"params_preset": "high"})
check("params_preset_high", pr == {"steps": 36, "cfg": 6.5}, str(pr))
pr2 = workflow_mgr.params_for_entry({"params_preset": "keep", "sampler_name": "dpmpp_2m"})
check("params_keep_only_explicit", pr2 == {"sampler_name": "dpmpp_2m"}, str(pr2))
pr3 = workflow_mgr.params_for_entry({"params_preset": "fast", "steps": 12})
check("params_explicit_overrides_preset", pr3["steps"] == 12 and pr3["cfg"] == 5.0, str(pr3))
check("params_empty", workflow_mgr.params_for_entry({}) == {})

refine = workflow_mgr.build_refine_workflow(
    image_name="a.png", ckpt_name="m.safetensors", positive="p", negative="n",
    scale=1.5, denoise=0.35, steps=20, cfg_scale=5.0, seed=7,
)
check("refine_nodes", set(refine) == {str(i) for i in range(1, 10)}, str(sorted(refine)))
check("refine_load_image", refine["2"]["inputs"]["image"] == "a.png")
check("refine_ckpt", refine["1"]["inputs"]["ckpt_name"] == "m.safetensors")
check("refine_denoise", refine["7"]["inputs"]["denoise"] == 0.35)
check("refine_scale_clamped", workflow_mgr.build_refine_workflow(
    "a.png", "m", "p", "n", scale=9.0)["3"]["inputs"]["scale_by"] == 2.0)
check("refine_negative_wired",
      refine["7"]["inputs"]["negative"] == ["6", 0] and refine["6"]["inputs"]["text"] == "n")

print("workflow_mgr:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
