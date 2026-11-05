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

print("workflow_mgr:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
