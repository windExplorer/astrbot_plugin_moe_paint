"""config_coerce 自检：WebUI 保存配置的点号键语义。跑法：
uv run --no-project python tests/test_config_coerce.py（退出码即结论）

为什么单独立这个测试：前端提交的是 `{"schedule.quiet_enabled": true}` 这种扁平键，
后端若只按 schema 顶层查（schema.get("schedule.quiet_enabled")）就会全部判成
「未知配置项」—— 表现为**每个配置页都保存不了**，而且在页面里只看到一句红字。
这个 bug 只能靠单测守住，冒烟测试（桩桥）测不出来。
"""

import json
import sys

from _loader import load, root

cc = load("config_coerce")

SCHEMA = json.loads((root() / "_conf_schema.json").read_text(encoding="utf-8"))

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


# ---------- resolve_spec ----------
p, c, spec = cc.resolve_spec(SCHEMA, "enabled")
check("resolve_top", (p, c) == ("enabled", None) and spec.get("type") == "bool")

p, c, spec = cc.resolve_spec(SCHEMA, "schedule.quiet_enabled")
check("resolve_dot", (p, c) == ("schedule", "quiet_enabled") and spec.get("type") == "bool")

p, c, spec = cc.resolve_spec(SCHEMA, "nope.key")
check("resolve_unknown", spec is None)

p, c, spec = cc.resolve_spec(SCHEMA, "enabled.nope")
check("resolve_non_object_child", spec is None)

# 前端用到的主要点号键都必须能解析（schema 改名会在这里炸）
FRONTEND_KEYS = [
    "comfyui.url", "comfyui.draw_timeout", "comfyui.queue_extra_timeout",
    "comfyui.hard_cap", "comfyui.poll_interval", "comfyui.queue_defer_threshold",
    "llm.provider_id", "llm.timeout",
    "prompt.persona_enabled", "prompt.persona_text", "prompt.persona_tags",
    "prompt.persona_negative", "prompt.persona_negative_zh", "prompt.extra_rules",
    "prompt.style_anchor_enabled",
    "prompt.override_danbooru", "prompt.override_natural", "prompt.override_natural_en",
    "schedule.mode", "schedule.interval_minutes", "schedule.jitter_minutes",
    "schedule.daily_times", "schedule.quiet_enabled", "schedule.quiet_hours",
    "schedule.max_per_day",
    "anima.follow_server", "anima.server_key", "anima.workflow", "anima.size_preset",
    "anima.prompt_profile", "anima.subject_scope", "anima.workflow_scopes",
    "rotation_manual_advances",
    "theme.disabled_categories", "theme.extra_words", "theme.category_weights",
    "theme.category_scopes", "theme.character_names",
    "delivery.sessions", "delivery.caption_on", "delivery.caption_template",
    "gallery.max_total_mb",
]
missing = [k for k in FRONTEND_KEYS if cc.resolve_spec(SCHEMA, k)[2] is None]
check("resolve_all_frontend_keys", not missing, "缺: " + ", ".join(missing))

for top in ("enabled", "workflow_source", "workflow_selection", "rotation_manual_advances",
            "workflow_templates", "admin_notify_umo"):
    check(f"resolve_top_{top}", cc.resolve_spec(SCHEMA, top)[2] is not None)

# ---------- build_writes：点号键 ----------
writes, err = cc.build_writes({
    "schedule.quiet_enabled": "false",
    "schedule.max_per_day": "6",
    "comfyui.url": "http://127.0.0.1:8188",
}, SCHEMA)
check("writes_dot_ok", not err and len(writes) == 3, err)
mapping = {(".".join(str(x) for x in path)): v for path, v in writes}
check("writes_bool_coerced", mapping.get("schedule.quiet_enabled") is False, str(mapping))
check("writes_int_coerced", mapping.get("schedule.max_per_day") == 6, str(mapping))
check("writes_str_kept", mapping.get("comfyui.url") == "http://127.0.0.1:8188", str(mapping))

# ---------- build_writes：object 整块 ----------
writes, err = cc.build_writes({"comfyui": {"url": "http://a", "draw_timeout": 200}}, SCHEMA)
check("writes_object_block", not err and len(writes) == 2, err)
check("writes_object_paths", all(len(p) == 2 for p, _ in writes), str(writes))

# ---------- 错误路径 ----------
writes, err = cc.build_writes({"nope.key": 1}, SCHEMA)
check("unknown_key_rejected", not writes and "未知配置项" in err, err)

writes, err = cc.build_writes({"schedule.mode": "hourly"}, SCHEMA)
check("options_enforced", not writes and "取值必须是" in err, err)

writes, err = cc.build_writes({"comfyui": {"nope": 1}}, SCHEMA)
check("unknown_child_rejected", not writes and "未知配置项" in err, err)

writes, err = cc.build_writes({}, SCHEMA)
check("empty_items_rejected", not writes and "没有可写入" in err, err)

# ---------- template_list ----------
tpl_spec = SCHEMA["workflow_templates"]
ok_entry = {"name": "a", "file": "a.json", "tag_style": "natural", "size_preset": "flux", "enabled": True}
writes, err = cc.build_writes({"workflow_templates": [ok_entry]}, SCHEMA)
check("tpl_ok", not err and len(writes) == 1, err)
check("tpl_path_top_level", writes[0][0] == ("workflow_templates",), str(writes))
check("tpl_fields_kept", writes[0][1][0]["size_preset"] == "flux", str(writes))

writes, err = cc.build_writes({"workflow_templates": []}, SCHEMA)
check("tpl_empty_rejected", not writes and "不能为空" in err, err)

writes, err = cc.build_writes(
    {"workflow_templates": [{"name": "a", "file": "a.json", "tag_style": "bogus"}]}, SCHEMA
)
check("tpl_bad_option", not writes and "取值必须是" in err, err)

writes, err = cc.build_writes({"workflow_templates": [{"name": "", "file": "a.json"}]}, SCHEMA)
check("tpl_missing_name", not writes and "缺 name" in err, err)

# v0.7.3：模板题材能力是 template_list 的新字段，必须能存且受 options 约束
writes, err = cc.build_writes(
    {"workflow_templates": [{"name": "a", "file": "a.json", "subject_scope": "photo"}]}, SCHEMA
)
check("tpl_subject_scope_saved", not err and writes[0][1][0].get("subject_scope") == "photo", err)
writes, err = cc.build_writes(
    {"workflow_templates": [{"name": "a", "file": "a.json", "subject_scope": "bogus"}]}, SCHEMA
)
check("tpl_subject_scope_bad_option", not writes and "取值必须是" in err, err)

# prompt 分组（text 类型）能按点号键正常写入
writes, err = cc.build_writes({"prompt.extra_rules": "不要画雨天", "prompt.persona_enabled": False}, SCHEMA)
check("prompt_group_writable", not err and len(writes) == 2, err)
pmap = {".".join(str(x) for x in path): v for path, v in writes}
check("prompt_text_kept", pmap.get("prompt.extra_rules") == "不要画雨天", str(pmap))
check("prompt_bool_coerced", pmap.get("prompt.persona_enabled") is False, str(pmap))

# ---------- apply_writes ----------
cfg: dict = {}
changed = cc.apply_writes(cfg, [(("schedule", "quiet_enabled"), False), (("enabled",), True)])
check("apply_nested", cfg["schedule"]["quiet_enabled"] is False and cfg["enabled"] is True, str(cfg))
check("apply_changed_names", changed == ["schedule.quiet_enabled", "enabled"], str(changed))
overflow: dict = {"comfyui": "坏值"}
cc.apply_writes(overflow, [(("comfyui", "url"), "http://x")])
check("apply_repairs_non_dict", isinstance(overflow["comfyui"], dict)
      and overflow["comfyui"]["url"] == "http://x", str(overflow))

print("config_coerce:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
