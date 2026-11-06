"""配置读写的纯函数层（不依赖 astrbot，tests/ 可直跑）。

为什么单独拆出来：WebUI 保存配置的语义是「扁平点号键」——
前端发的是 `{"schedule.quiet_enabled": true, "comfyui.url": "..."}`。
如果只在 schema 顶层 `schema.get(key)` 找，所有嵌套键都会被判成
「未知配置项」，表现为**每次都提示保存失败/根本没存**。
这段逻辑一旦错，用户所有配置页都是坏的，所以必须能被测试覆盖。
"""

from __future__ import annotations


def resolve_spec(schema: dict, key: str) -> "tuple[str | None, str | None, dict | None]":
    """点号键 → (父键, 子键 | None, schema 节点)。找不到返回 (None, None, None)。

    - `"enabled"`        → ("enabled", None, spec)
    - `"comfyui.url"`    → ("comfyui", "url", 子节点 spec)
    - 更深层级（object 套 object）不支持：本插件 schema 只有一层。
    """
    if not isinstance(schema, dict):
        return None, None, None
    key = str(key or "").strip()
    if not key:
        return None, None, None
    if key in schema and isinstance(schema[key], dict):
        return key, None, schema[key]
    if "." in key:
        parent, child = key.split(".", 1)
        ps = schema.get(parent)
        if isinstance(ps, dict) and str(ps.get("type") or "") == "object":
            fs = (ps.get("items") or {}).get(child)
            if isinstance(fs, dict):
                return parent, child, fs
    return None, None, None


def truthy(v, default: bool = True) -> bool:
    """配置布尔容错："false"/"0"/"off"/"no" 视为关闭；None/空串 → 默认值。

    为什么必须集中一处：AstrBotConfig、WebUI、手改 JSON、老版本迁移都会出现
    **字符串布尔**，而 `bool("false") is True`——开关会形同没有。之前只有
    scheduler / prompter / webui_api 各留了一份，delivery 和 main 里漏用了，
    于是 `delivery.caption_on="false"` 照样附图说、`enabled="false"` 照样跑。
    """
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "开启", "开")


def cast_value(spec: dict, value, key: str):
    """按 schema 的 type 转换；bool 兼容常见字符串写法，失败抛带键名的 ValueError。"""
    t = str((spec or {}).get("type") or "string")
    try:
        if t == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("true", "1", "on", "yes"):
                return True
            if s in ("false", "0", "off", "no"):
                return False
            raise ValueError(f"{key} 需要 true/false，收到 {value!r}")
        if t == "int":
            return int(float(value))
        if t == "float":
            return float(value)
        if t == "list":
            if isinstance(value, list):
                return [str(v) for v in value]
            raise ValueError(f"{key} 需要数组，收到 {type(value).__name__}")
        if t == "dict":
            if isinstance(value, dict):
                return value
            raise ValueError(f"{key} 需要对象，收到 {type(value).__name__}")
        return "" if value is None else str(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} 类型转换失败: {e}") from e


def coerce_template_list(raw, spec: dict) -> list:
    """模板清单整体替换：逐条白名单字段 + 类型转换；空清单拒绝（防一键清空）。"""
    if not isinstance(raw, list):
        raise ValueError("workflow_templates 需要数组")
    fields = (spec or {}).get("items") or {}
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"模板 #{i + 1} 不是对象")
        name = str(item.get("name") or "").strip()
        file = str(item.get("file") or "").strip()
        if not name or not file:
            raise ValueError(f"模板 #{i + 1} 缺 name 或 file")
        entry: dict = {"name": name, "file": file}
        for f, fs in fields.items():
            if f in ("name", "file") or f not in item:
                continue
            v = cast_value(fs, item.get(f), f"模板#{i + 1}.{f}")
            opts = (fs or {}).get("options")
            if opts and v and v not in opts:
                raise ValueError(f"模板 #{i + 1} 的 {f} 取值必须是 {opts} 之一（收到 {v!r}）")
            entry[f] = v
        out.append(entry)
    if not out:
        raise ValueError("workflow_templates 不能为空（至少保留一个模板；要停用请关 enabled）")
    return out


def build_writes(items, schema: dict) -> "tuple[list[tuple[tuple, object]], str]":
    """把 WebUI 提交的 items 校验成待写列表；返回 ([(路径元组, 值)], 错误信息)。

    支持三种传法：点号键（推荐）、object 整块（{comfyui: {url: ...}}）、模板清单。
    先整体校验再统一写入 —— 一次坏保存不能把其他键写成半截。
    """
    if not isinstance(items, dict):
        return [], "items 必须是对象 {键: 值}"
    writes: list[tuple[tuple, object]] = []
    for key, value in items.items():
        parent, child, spec = resolve_spec(schema, key)
        if spec is None:
            return [], f"未知配置项: {key}"
        t = str(spec.get("type") or "")
        if child is None and t == "object":
            if not isinstance(value, dict):
                return [], f"{key} 需要对象（子键请拆成点号键传，如 {key}.url）"
            sub_fields = spec.get("items") or {}
            for f, v in value.items():
                fs = sub_fields.get(f)
                if fs is None:
                    return [], f"未知配置项: {key}.{f}"
                try:
                    writes.append(((key, f), cast_value(fs, v, f"{key}.{f}")))
                except ValueError as e:
                    return [], str(e)
            continue
        if child is None and t == "template_list":
            try:
                writes.append(((key,), coerce_template_list(value, spec)))
            except ValueError as e:
                return [], str(e)
            continue
        label = f"{parent}.{child}" if child else str(parent)
        try:
            v = cast_value(spec, value, label)
        except ValueError as e:
            return [], str(e)
        opts = spec.get("options")
        if opts and v != "" and v not in opts:
            return [], f"{label} 取值必须是 {opts} 之一（收到 {v!r}）"
        writes.append(((parent, child) if child else (parent,), v))
    if not writes:
        return [], "没有可写入的配置项"
    return writes, ""


def apply_writes(cfg, writes: list[tuple[tuple, object]]) -> list[str]:
    """把待写列表落到配置对象上，返回变更的键名（不负责 save_config）。"""
    changed: list[str] = []
    for path, v in writes or []:
        if len(path) == 1:
            cfg[path[0]] = v
        else:
            sub = cfg.get(path[0])
            if not isinstance(sub, dict):
                sub = {}
                cfg[path[0]] = sub
            sub[path[1]] = v
        changed.append(".".join(str(p) for p in path))
    return changed
