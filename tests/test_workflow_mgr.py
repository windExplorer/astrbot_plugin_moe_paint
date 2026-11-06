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
picked, cur = workflow_mgr.pick_template(pool, "random", random.Random(1), 0)
check("pick_random_excludes_bad", picked in (ok_tpl, ok_tpl2))
p1, c1 = workflow_mgr.pick_template(pool, "round_robin", random.Random(1), 0)
p2, c2 = workflow_mgr.pick_template(pool, "round_robin", random.Random(1), c1)
check("pick_rr_cycles", p1 is ok_tpl and p2 is ok_tpl2 and c1 == 1 and c2 == 2, f"c={c1},{c2}")
none_tpl, cur = workflow_mgr.pick_template([bad_tpl, off_tpl], "random", random.Random(1), 0)
check("pick_empty_pool", none_tpl is None)

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

print("workflow_mgr:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
