"""_conf_schema.json 装载期自检（复刻 AstrBot 的解析规则，见 AGENTS.md 第八节）。

为什么要有这个文件：AstrBot 装载插件时会执行
`AstrBotConfig._config_schema_to_default_config(schema)`，它对**每个节点**直接取
`v["type"]`（astrbot/core/config/astrbot_config.py:171）。任何节点缺 `type`、
或 `object` 节点缺 `items`，都会让整个插件装不上，报错形态是
`加载插件…原因：'type'` —— 本地只测「自己代码怎么读配置」是测不出来的。

跑法：uv run --no-project python tests/test_schema.py（退出码即结论）
"""

import json
import sys
from pathlib import Path

from _loader import load

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "_conf_schema.json"

# astrbot/core/config DEFAULT_VALUE_MAP 的类型集合（值只是默认值，这里不关心）
SUPPORTED_TYPES = {
    "int", "float", "bool", "string", "text", "list", "file",
    "object", "template_list", "dict",
}

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


def walk(nodes: dict, path: str = "") -> None:
    """逐节点校验：必须有合法 type；object 必须有 items 并递归。"""
    for key, spec in (nodes or {}).items():
        here = f"{path}{key}"
        if not isinstance(spec, dict):
            check(f"{here} 是对象节点", False, f"实际是 {type(spec).__name__}")
            continue
        t = spec.get("type")
        check(f"{here} 有 type", isinstance(t, str) and bool(t), f"type={t!r}")
        if t is not None:
            check(f"{here} type 合法", t in SUPPORTED_TYPES, f"type={t!r}")
        if "default" in spec and isinstance(spec.get("options"), list):
            check(
                f"{here} default 在 options 内",
                spec["default"] in spec["options"] or spec["default"] in ("", None),
                f"default={spec['default']!r}",
            )
        if t == "object":
            items = spec.get("items")
            check(f"{here} 有 items", isinstance(items, dict), f"items={type(items).__name__}")
            if isinstance(items, dict):
                walk(items, here + ".")
        elif t == "template_list":
            # template_list 的 items 是子项描述，AstrBot 不递归解析，但字段仍需 type
            items = spec.get("items")
            if isinstance(items, dict):
                for f, fs in items.items():
                    check(
                        f"{here}.items.{f} 有 type",
                        isinstance(fs, dict) and bool(fs.get("type")),
                        f"type={(fs or {}).get('type')!r}",
                    )


def main() -> int:
    check("schema 文件存在", SCHEMA_PATH.is_file(), str(SCHEMA_PATH))
    if not SCHEMA_PATH.is_file():
        return summarize()
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FAIL  schema 不是合法 JSON: {e}")
        return 1
    check("schema 是对象", isinstance(schema, dict) and bool(schema))
    walk(schema)

    # 代码里读写的关键键必须还在（防手滑删键导致静默失效）
    required = [
        ("enabled",),
        ("comfyui", "url"),
        ("comfyui", "queue_defer_threshold"),
        ("llm", "provider_id"),
        ("prompt", "persona_enabled"),
        ("prompt", "persona_text"),
        ("prompt", "extra_rules"),
        ("prompt", "style_anchor_enabled"),
        ("prompt", "override_danbooru"),
        ("schedule", "mode"),
        ("schedule", "interval_minutes"),
        ("schedule", "daily_times"),
        ("schedule", "quiet_enabled"),
        ("schedule", "quiet_hours"),
        ("schedule", "max_per_day"),
        ("anima", "follow_server"),
        ("anima", "server_key"),
        ("anima", "workflow"),
        ("anima", "size_preset"),
        ("anima", "prompt_profile"),
        ("anima", "subject_scope"),
        ("anima", "workflow_scopes"),
        ("workflow_source",),
        ("rotation_manual_advances",),
        ("manual_draw_admin_only",),
        ("manual_draw_cooldown_sec",),
        ("prompt", "ab_enabled"),
        ("prompt", "negative_base_en"),
        ("prompt", "max_tags"),
        ("prompt", "animal_guard"),
        ("prompt", "animal_categories"),
        ("prompt", "person_closeup"),
        ("prompt", "person_detail"),
        ("prompt", "conflict_cleanup"),
        ("prompt", "soft_color"),
        ("prompt", "persona_strip_photo"),
        ("quality_guard", "enabled"),
        ("quality_guard", "shots"),
        ("refine", "enabled"),
        ("refine", "ckpt_name"),
        ("time_sense", "enabled"),
        ("time_sense", "latitude"),
        ("time_sense", "sync_light"),
        ("character", "mode"),
        ("debug", "save_workflows"),
        ("debug", "keep_workflows"),
        ("delivery", "sessions"),
        ("gallery", "max_total_mb"),
        ("theme", "category_weights"),
        ("theme", "disabled_categories"),
        ("theme", "category_scopes"),
        ("theme", "character_names"),
        ("workflow_templates",),
        ("workflow_templates", "items", "subject_scope"),
    ]
    def has_path(path: tuple) -> bool:
        """在 schema 里走 a.b.c：object 的子键挂在 items 下，两处都认。"""
        cur = schema
        for seg in path:
            if not isinstance(cur, dict):
                return False
            if seg in cur:
                cur = cur[seg]
            elif isinstance(cur.get("items"), dict) and seg in cur["items"]:
                cur = cur["items"][seg]
            else:
                return False
        return isinstance(cur, dict)

    for path in required:
        check("关键键存在 " + ".".join(path), has_path(path))

    # 复刻装载期解析：object 递归取 type，任何一步 KeyError 都算失败
    def parse(nodes: dict, out: dict) -> None:
        for k, v in nodes.items():
            t = v["type"]
            if t not in SUPPORTED_TYPES:
                raise TypeError(f"{k}: 不支持的类型 {t}")
            default = v.get("default")
            if t == "object":
                out[k] = {}
                parse(v["items"], out[k])
            else:
                out[k] = default

    try:
        conf: dict = {}
        parse(schema, conf)
        check("可被 AstrBot 解析成默认配置", True)
        check("默认配置含 anima 分组", isinstance(conf.get("anima"), dict))
        check(
            "安静时段默认开启",
            conf.get("schedule", {}).get("quiet_enabled") is True,
        )
        check("默认配置含 prompt 分组", isinstance(conf.get("prompt"), dict))
        check("人物锚定默认开启", conf.get("prompt", {}).get("persona_enabled") is True)
        # 安全默认：指令手动出图只给管理员（任何群成员都能刷 = 挤掉定时轮次）
        check("手动出图默认仅管理员", conf.get("manual_draw_admin_only") is True)
    except Exception as e:
        check("可被 AstrBot 解析成默认配置", False, f"{type(e).__name__}: {e}")

    # 老配置没有新键时，代码里的兜底常量必须与 schema 默认值一致，否则升级前后行为会漂
    prompter = load("prompter")
    ps = schema["prompt"]["items"]
    check("persona_text 默认值与代码常量一致",
          ps["persona_text"]["default"] == prompter.DEFAULT_PERSONA_TEXT)
    check("persona_tags 默认值与代码常量一致",
          ps["persona_tags"]["default"] == prompter.DEFAULT_PERSONA_TAGS)
    check("persona_negative 默认值与代码常量一致",
          ps["persona_negative"]["default"] == prompter.DEFAULT_PERSONA_NEGATIVE)
    check("character_names 默认值与代码常量一致",
          schema["theme"]["items"]["character_names"]["default"] == prompter.DEFAULT_CHARACTER_NAMES)

    return summarize()


def summarize() -> int:
    print("schema:", "PASS" if not failures else f"FAIL({len(failures)})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
