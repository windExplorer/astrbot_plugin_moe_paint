"""顺序轮换的运行时自检：从 main.py 原样抠出方法再 exec。跑法：
uv run --no-project python tests/test_rotation.py（退出码即结论）

为什么不用 import main：本机没有 AstrBot 运行时，但游标读写 / 手动出图占位 /
老整数游标过渡这些逻辑全在 main.py 的方法里，纯函数层测不到。用 AST 抠定义
既测磁盘上的真代码，又不必为了跑测试去装 astrbot。
"""

import ast
import asyncio
import sys
from pathlib import Path

from _loader import load, root

workflow_mgr = load("workflow_mgr")

WANT = {
    "_manual_advances",
    "should_advance_rotation",
    "_rotation_cursor",
    "rotation_state",
    "reset_rotation",
}

src = (root() / "main.py").read_text(encoding="utf-8")
picked = []
for node in ast.walk(ast.parse(src)):
    if not isinstance(node, ast.ClassDef):
        continue
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in WANT:
            picked.append(item)
missing = WANT - {f.name for f in picked}
if missing:
    print(f"FAIL  从 main.py 抠不出方法: {sorted(missing)}")
    sys.exit(1)

module = ast.Module(body=picked, type_ignores=[])
ast.fix_missing_locations(module)
ns = {"workflow_mgr": workflow_mgr}
exec(compile(module, "main.py", "exec"), ns)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


class FakeStore:
    def __init__(self, meta=None):
        self.data = dict(meta or {})

    async def get_meta(self, key, default=""):
        return self.data.get(key, default)

    async def set_meta(self, key, value):
        self.data[key] = str(value)


class Stub:
    def __init__(self, cfg=None, meta=None, templates=None):
        self.config = dict(cfg or {})
        self.store = FakeStore(meta or {})
        self._templates = list(templates or [])

    def _cfg(self):
        return self.config

    def reload_templates(self):
        pass

    def debug_log(self, *a, **k):
        pass


for name in WANT:
    setattr(Stub, name, ns[name])


def tpl(name, file, enabled=True, ok=True):
    return workflow_mgr.Template(
        name=name, file=file, enabled=enabled, ok=ok, key=f"local:{file}"
    )


TRI = [tpl("甲", "a.json"), tpl("乙", "b.json"), tpl("丙", "c.json")]

# ---------- 手动出图占位开关 ----------
s = Stub(templates=TRI)
check("schedule_advances", s.should_advance_rotation("schedule", "round_robin", True) is True)
check("manual_command_no_advance", s.should_advance_rotation("manual", "round_robin", True) is False)
check("manual_webui_no_advance", s.should_advance_rotation("manual:webui", "round_robin", True) is False)
check("random_never_advances", s.should_advance_rotation("schedule", "random", True) is False)
check("no_tpl_never_advances", s.should_advance_rotation("schedule", "round_robin", False) is False)

s2 = Stub(cfg={"rotation_manual_advances": True}, templates=TRI)
check("manual_advances_when_enabled",
      s2.should_advance_rotation("manual", "round_robin", True) is True)
s3 = Stub(cfg={"rotation_manual_advances": "yes"}, templates=TRI)
check("manual_advances_string_bool",
      s3.should_advance_rotation("manual:webui", "round_robin", True) is True)
check("manual_advances_default_false", s._manual_advances() is False)

# ---------- 游标读取与老版本过渡 ----------
s = Stub(templates=TRI)
check("cursor_fresh_install", asyncio.run(s._rotation_cursor(s.store)) == ("", None))
s = Stub(meta={"rr_last": "local:b.json"}, templates=TRI)
check("cursor_new_key", asyncio.run(s._rotation_cursor(s.store)) == ("local:b.json", None))
s = Stub(meta={"rr_cursor": "2"}, templates=TRI)
check("cursor_legacy_transition", asyncio.run(s._rotation_cursor(s.store)) == ("", 2))
s = Stub(meta={"rr_cursor": "bad"}, templates=TRI)
check("cursor_legacy_bad", asyncio.run(s._rotation_cursor(s.store)) == ("", 0))
s = Stub(meta={"rr_cursor": "2", "rr_last": "local:a.json"}, templates=TRI)
check("cursor_new_wins", asyncio.run(s._rotation_cursor(s.store)) == ("local:a.json", None))

# ---------- 轮换视图（只读，不推进） ----------
s = Stub(meta={"rr_last": "local:a.json"}, templates=TRI)
st = asyncio.run(s.rotation_state())
check("view_mode_from_config", st["mode"] == "random" and st["ordered"] is False)
check("view_next_after_first", st["next_index"] == 2 and st["next_name"] == "乙", str(st))
check("view_total", st["total"] == 3)
check("view_manual_flag", st["manual_advances"] is False)
s.config["workflow_selection"] = "round_robin"
check("view_ordered", asyncio.run(s.rotation_state())["ordered"] is True)
s.config["rotation_manual_advances"] = True
check("view_manual_flag_on", asyncio.run(s.rotation_state())["manual_advances"] is True)

s = Stub(meta={"rr_last": "local:gone.json"}, templates=TRI)
check("view_last_lost", asyncio.run(s.rotation_state())["last_found"] is False)

s = Stub(meta={"rr_cursor": "2"}, templates=TRI)
s.config["workflow_selection"] = "round_robin"
check("view_legacy_next", asyncio.run(s.rotation_state())["next_index"] == 3)

# 停用中间一条：视图里的顺序连续，且不会因池子变化错位
s = Stub(meta={"rr_last": "local:a.json"}, templates=[TRI[0], TRI[2]])
check("view_no_jump", asyncio.run(s.rotation_state())["next_name"] == "丙")

# ---------- 重置 ----------
s = Stub(meta={"rr_last": "local:c.json", "rr_cursor": "7"}, templates=TRI)
res = asyncio.run(s.reset_rotation())
check("reset_ok", res.get("ok") is True, str(res))
check("reset_clears_keys",
      s.store.data.get("rr_last") == "" and s.store.data.get("rr_cursor") == "0",
      str(s.store.data))
check("reset_next_is_first", asyncio.run(s.rotation_state())["next_index"] == 1)
s.store = None
check("reset_no_store", asyncio.run(s.reset_rotation()).get("ok") is False)

print("rotation:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
