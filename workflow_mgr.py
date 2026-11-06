"""工作流模板：上传解析 / 加载 / 节点定位 / 参数注入 / 轮换选择。

模板 = ComfyUI API 格式 JSON（data/plugin_data/<名>/workflows/ 下）+ 配置里的元信息
（tag_style / 尺寸预设 / 默认负面词 / 节点覆盖）。节点定位**自动扫描优先、配置覆盖兜底**：
- 采样器：inputs 同时带 positive/negative 引用的节点（class_type 含 sampler/guider 优先）；
- 正/负向文本节点：采样器 inputs.positive/negative 引用指向的节点；
- latent：采样器 inputs.latent_image 引用 → 回退第一个 EmptyLatentImage（或带 width+height 的节点）；
- seed：所有 inputs 带 seed / noise_seed 数值的节点。
这覆盖标准 KSampler 链路；CFGGuider/SamplerCustomAdvanced 等异形链路靠配置里的
节点 ID 覆盖（positive_node/negative_node/latent_node/positive_field/negative_field）。

v0.4.0 起支持 WebUI 上传：`parse_upload` 判断格式并给出人话错误，`analyze` 在
`locate_nodes` 之上补出「底模名 / 保存节点 / latent 默认宽高 / 软警告」，
供上传预览与清单展示使用（出图管线只消费 locate_nodes 的那几个键）。
"""

import copy
import json
import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import prompter

BUILTIN_WORKFLOW_FILE = "anima_t2i.json"

_POS_FIELD_CANDIDATES = ("text", "prompt", "positive", "positive_prompt", "caption")
_NEG_FIELD_CANDIDATES = ("text", "prompt", "negative", "negative_prompt")


def _ref_target(ref) -> str:
    """ComfyUI 节点引用（如 ["3", 0]）→ 目标节点 id 字符串；非引用返回 ""。"""
    if isinstance(ref, (list, tuple)) and ref:
        return str(ref[0])
    return ""


def _probe_field(node: dict, candidates: tuple) -> str:
    inputs = (node or {}).get("inputs") or {}
    for key in candidates:
        if key in inputs:
            return key
    return "text"


@dataclass
class Template:
    name: str
    file: str
    tag_style: str = "danbooru"
    size_preset: str = "sdxl"
    default_negative: str = ""
    enabled: bool = True
    wf: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)  # locate_nodes / analyze 结果
    ok: bool = False
    problem: str = ""
    sizes: dict = field(default_factory=dict)
    source: str = "local"  # local = 本插件库；anima = 跟随萌绘工作流

    def to_public(self) -> dict:
        """给 WebUI / 指令展示的公开形状（不含工作流 JSON 本体）。"""
        return {
            "name": self.name,
            "file": self.file,
            "source": self.source,
            "tag_style": self.tag_style,
            "size_preset": self.size_preset,
            "default_negative": self.default_negative,
            "enabled": self.enabled,
            "ok": self.ok,
            "problem": self.problem,
            "nodes": {
                "positive": self.info.get("positive", ""),
                "negative": self.info.get("negative", ""),
                "latent": self.info.get("latent", ""),
                "positive_field": self.info.get("positive_field", "text"),
                "negative_field": self.info.get("negative_field", "text"),
                "seed_nodes": len(self.info.get("seed_nodes", [])),
                "save": self.info.get("save", ""),
                "model_file": self.info.get("model_file", ""),
                "latent_class": self.info.get("latent_class", ""),
                "default_width": self.info.get("default_width") or 0,
                "default_height": self.info.get("default_height") or 0,
                "kind": self.info.get("kind", "t2i"),
            },
            "warnings": self.info.get("warnings") or [],
        }


def locate_nodes(wf: dict, override: dict | None = None) -> dict:
    """自动定位注入点；override 里的显式节点 ID / 字段名优先。"""
    override = override or {}
    info: dict = {
        "positive": "",
        "negative": "",
        "latent": "",
        "seed_nodes": [],
        "positive_field": "text",
        "negative_field": "text",
    }

    # seed 节点
    for nid, node in wf.items():
        inputs = (node or {}).get("inputs") or {}
        for key in ("seed", "noise_seed"):
            v = inputs.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                info["seed_nodes"].append([str(nid), key])
                break

    # 采样器（同带 positive/negative 引用；class_type 含 sampler/guider 优先）
    samplers: list[tuple[int, str, dict]] = []
    for nid, node in wf.items():
        inputs = (node or {}).get("inputs") or {}
        if "positive" in inputs and "negative" in inputs:
            ct = str((node or {}).get("class_type", "")).lower()
            samplers.append((0 if ("sampler" in ct or "guider" in ct) else 1, str(nid), node))
    samplers.sort(key=lambda t: (t[0], t[1]))
    if samplers:
        _, _sid, snode = samplers[0]
        s_in = snode.get("inputs") or {}
        info["positive"] = _ref_target(s_in.get("positive"))
        info["negative"] = _ref_target(s_in.get("negative"))
        info["latent"] = _ref_target(s_in.get("latent_image"))

    # 显式覆盖优先
    info["positive"] = str(override.get("positive_node") or info["positive"] or "")
    info["negative"] = str(override.get("negative_node") or info["negative"] or "")
    info["latent"] = str(override.get("latent_node") or info["latent"] or "")

    # latent 回退：第一个 EmptyLatentImage / 任何带 width+height 输入的节点
    if not info["latent"]:
        for nid, node in wf.items():
            inputs = (node or {}).get("inputs") or {}
            if (node or {}).get("class_type") == "EmptyLatentImage" or (
                "width" in inputs and "height" in inputs
            ):
                info["latent"] = str(nid)
                break

    # 字段名
    if info["positive"] and info["positive"] in wf:
        info["positive_field"] = str(
            override.get("positive_field")
            or _probe_field(wf[info["positive"]], _POS_FIELD_CANDIDATES)
        )
    if info["negative"] and info["negative"] in wf:
        info["negative_field"] = str(
            override.get("negative_field")
            or _probe_field(wf[info["negative"]], _NEG_FIELD_CANDIDATES)
        )

    # 校验
    problems = []
    if not info["positive"] or info["positive"] not in wf:
        problems.append("找不到正向文本节点（可在模板配置里填 positive_node）")
    if not info["negative"] or info["negative"] not in wf:
        problems.append("找不到负向文本节点（可在模板配置里填 negative_node）")
    if not info["latent"] or info["latent"] not in wf:
        problems.append("找不到 latent 节点（可在模板配置里填 latent_node）")
    if not info["seed_nodes"]:
        problems.append("找不到 seed 节点（含 seed/noise_seed 输入）")
    info["problems"] = problems
    return info


_MODEL_INPUT_KEYS = ("unet_name", "ckpt_name", "model_name", "gguf_name")
_MODEL_CLASS_HINTS = ("loader", "checkpoint", "unet", "gguf", "diffusion")


def analyze(wf: dict, override: dict | None = None) -> dict:
    """locate_nodes + 展示/校验用的附加信息（底模、保存节点、latent 默认宽高、软警告）。"""
    info = locate_nodes(wf, override)

    # 底模：优先「类名像加载器」的节点，ckpt_name 加分（LoRA 的 lora_name 不在此列）
    best: tuple[int, str, str, str] | None = None
    for nid, node in wf.items():
        inputs = (node or {}).get("inputs") or {}
        ct = str((node or {}).get("class_type") or "")
        for key in _MODEL_INPUT_KEYS:
            v = inputs.get(key)
            if not (isinstance(v, str) and v.strip()):
                continue
            score = 2 if any(h in ct.lower() for h in _MODEL_CLASS_HINTS) else 1
            if key == "ckpt_name":
                score += 1
            if best is None or score > best[0]:
                best = (score, str(nid), ct, v.strip())
            break
    info["model_file"] = best[3] if best else ""
    info["model_class"] = best[2] if best else ""
    info["model_node"] = best[1] if best else ""

    # 保存节点：SaveImageExtended 优先
    save_id, save_ct = "", ""
    for nid, node in wf.items():
        ct = str((node or {}).get("class_type") or "")
        if "saveimage" in ct.lower():
            if not save_id or "extended" in ct.lower():
                save_id, save_ct = str(nid), ct
            if "extended" in ct.lower():
                break
    info["save"] = save_id
    info["save_class"] = save_ct

    # latent：类名 + 默认宽高 + 文生图/图生图
    lat = wf.get(info.get("latent") or "") or {}
    lin = lat.get("inputs") or {}
    info["latent_class"] = str(lat.get("class_type") or "")
    try:
        info["default_width"] = int(lin.get("width") or 0)
        info["default_height"] = int(lin.get("height") or 0)
    except (TypeError, ValueError):
        info["default_width"] = 0
        info["default_height"] = 0
    has_wh = "width" in lin and "height" in lin
    info["kind"] = "t2i" if has_wh else "img2img"

    # 硬问题（拒绝入库）：在 locate_nodes 的基础上补「尺寸节点能否注入」
    problems = list(info.get("problems") or [])
    if info["latent"] and not has_wh:
        problems.append(
            f"尺寸节点 {info['latent']}（{info['latent_class'] or '?'}）没有 width/height 输入，"
            "像是图生图或自定义尺寸链路，本插件暂不支持"
        )
    info["problems"] = problems

    # 软警告（可入库，只是提醒）
    warnings: list[str] = []
    samplers = [
        nid for nid, n in wf.items()
        if "positive" in ((n or {}).get("inputs") or {}) and "negative" in ((n or {}).get("inputs") or {})
    ]
    if len(samplers) > 1:
        warnings.append(f"检测到 {len(samplers)} 个采样器，只会往第一个注入提示词与种子")
    if not info["save"]:
        warnings.append("没找到保存节点（SaveImage 系列）；ComfyUI 仍会返回输出图，但检索可能不稳")
    if info["model_file"]:
        warnings.append(f"底模：{info['model_file']}（这台服务器上必须存在同名文件）")
    info["warnings"] = warnings
    return info


def parse_upload(text: str) -> "tuple[dict | None, list[str]]":
    """校验上传的 JSON 是否为 ComfyUI API 格式；返回 (工作流, 错误列表)。"""
    try:
        data = json.loads(text)
    except Exception as e:
        return None, [f"JSON 解析失败：{e}"]
    # 兼容 /history 导出或 {prompt: {...}} 包了一层的写法
    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        maybe = data["prompt"]
        if maybe and all(isinstance(v, dict) and "class_type" in v for v in maybe.values()):
            data = maybe
    if not isinstance(data, dict) or not data:
        return None, ["JSON 不是对象（应为 {节点ID: {class_type, inputs}}）"]
    if isinstance(data.get("nodes"), list) and isinstance(data.get("links"), list):
        return None, [
            "这是 ComfyUI **界面格式**（含 nodes/links）。请在 ComfyUI 里用「导出 (API) / Export (API)」"
            "导出后重新上传。"
        ]
    bad = [
        str(k) for k, v in data.items()
        if not isinstance(v, dict) or "class_type" not in v
    ]
    if bad:
        shown = "、".join(bad[:3]) + ("…" if len(bad) > 3 else "")
        return None, [f"节点 {shown} 缺少 class_type，不像是 API 格式工作流"]
    return data, []


def split_names(raw) -> list:
    """把「a,b，c;d 换行」这类手填/多选值拆成去重名称列表；空 → []。"""
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[,，;；\n\r]+", str(raw or ""))
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = it.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def select_workflows(cfgs: list, names, require_base: bool = True) -> list:
    """按名称/ID 列表挑工作流；名单为空 = 全部可用。

    可用 = 自身 enabled 且（require_base 时）关联了基础工作流（`base_id`）。
    用于「跟随萌绘」模式下的多工作流轮换：留空即全部参与。
    """
    usable = [
        w for w in (cfgs or [])
        if isinstance(w, dict)
        and bool(w.get("enabled", True))
        and (not require_base or str(w.get("base_id") or "").strip())
    ]
    want = split_names(names)
    if not want:
        return usable
    picked: list[dict] = []
    for w in usable:
        name = str(w.get("name") or "").strip()
        wid = str(w.get("workflow_id") or "").strip()
        if (name and name in want) or (wid and wid in want):
            picked.append(w)
    return picked


def save_workflow_file(workflows_dir: Path, name: str, wf: dict) -> str:
    """把工作流写到工作流目录，返回文件名（同名覆盖；文件名做安全清洗）。"""
    workflows_dir = Path(workflows_dir)
    workflows_dir.mkdir(parents=True, exist_ok=True)
    fname = (sanitize_filename(name) or "workflow") + ".json"
    (workflows_dir / fname).write_text(
        json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return fname


def upsert_entry(entries: list, entry: dict) -> "tuple[list, bool]":
    """按 name 覆盖或追加清单条目；返回 (新清单, 是否覆盖)。"""
    out: list[dict] = []
    replaced = False
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("name") or "").strip() == str(entry.get("name") or "").strip():
            out.append({**e, **entry})
            replaced = True
        else:
            out.append(e)
    if not replaced:
        out.append(entry)
    return out, replaced


def follow_sizes(width, height) -> dict:
    """按工作流 latent 的默认宽高派生三朝向白名单（跟随萌绘工作流时用）。"""
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return prompter.SIZE_PRESETS["sdxl"]
    if w < 64 or h < 64:
        return prompter.SIZE_PRESETS["sdxl"]
    long_side, short = (w, h) if w >= h else (h, w)
    long_side = max(64, (long_side // 16) * 16)
    short = max(64, (short // 16) * 16)
    return {
        "landscape": [[long_side, short]],
        "portrait": [[short, long_side]],
        "square": [[short, short]],
    }


def load_templates(
    workflows_dir: Path, entries: list, builtin_dir: Path
) -> list[Template]:
    """按配置清单加载模板；内置模板缺文件时自动从包内 builtin/ 复制。"""
    workflows_dir.mkdir(parents=True, exist_ok=True)
    out: list[Template] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        file = str(entry.get("file") or "").strip()
        if not file:
            file = f"{name}.json" if name else ""
        if not name or not file:
            continue
        fname = file if file.endswith(".json") else f"{file}.json"
        path = workflows_dir / fname
        try:
            if not path.is_file() and fname == BUILTIN_WORKFLOW_FILE:
                src = Path(builtin_dir) / BUILTIN_WORKFLOW_FILE
                if src.is_file():
                    shutil.copyfile(src, path)
            wf = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            out.append(
                Template(
                    name=name, file=fname, ok=False,
                    problem=f"工作流文件不存在：{fname}（把 API 格式 JSON 丢进 {workflows_dir}）",
                )
            )
            continue
        except Exception as e:
            out.append(Template(name=name, file=fname, ok=False, problem=f"JSON 解析失败: {e}"))
            continue
        if not isinstance(wf, dict) or not wf:
            out.append(Template(name=name, file=fname, ok=False, problem="JSON 不是 API 格式工作流"))
            continue
        override = {
            "positive_node": entry.get("positive_node"),
            "negative_node": entry.get("negative_node"),
            "latent_node": entry.get("latent_node"),
            "positive_field": entry.get("positive_field"),
            "negative_field": entry.get("negative_field"),
        }
        override = {k: str(v).strip() for k, v in override.items() if v}
        info = analyze(wf, override)
        preset = str(entry.get("size_preset") or "sdxl").strip() or "sdxl"
        tpl = Template(
            name=name,
            file=fname,
            tag_style=str(entry.get("tag_style") or "danbooru").strip() or "danbooru",
            size_preset=preset,
            default_negative=str(entry.get("default_negative") or "").strip(),
            enabled=bool(entry.get("enabled", True)),
            wf=wf,
            info=info,
            ok=not info.get("problems"),
            problem="；".join(info.get("problems") or []),
            sizes=prompter.SIZE_PRESETS.get(preset, prompter.SIZE_PRESETS["sdxl"]),
        )
        out.append(tpl)
    return out


def enabled_pool(templates: list[Template]) -> list[Template]:
    return [t for t in templates or [] if t.enabled and t.ok]


def pick_template(
    templates: list[Template], mode: str, rng: random.Random, cursor: int
) -> tuple["Template | None", int]:
    """按 random / round_robin 选模板；返回 (模板, 新游标)。无可用的返回 (None, cursor)。"""
    pool = enabled_pool(templates)
    if not pool:
        return None, cursor
    if str(mode or "random").strip() == "round_robin":
        idx = int(cursor) % len(pool)
        return pool[idx], idx + 1
    return rng.choice(pool), cursor


def inject(
    wf: dict,
    info: dict,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
) -> dict:
    """在深拷贝上注入：正/负向文本、latent 宽高、全部 seed 节点。"""
    wf = copy.deepcopy(wf)
    pos_id, neg_id = info["positive"], info["negative"]
    if pos_id in wf:
        wf[pos_id]["inputs"][info.get("positive_field", "text")] = prompt
    if neg_id in wf and negative:
        wf[neg_id]["inputs"][info.get("negative_field", "text")] = negative
    lat_id = info["latent"]
    if lat_id in wf:
        inputs = wf[lat_id]["inputs"]
        inputs["width"] = int(width)
        inputs["height"] = int(height)
    for nid, key in info.get("seed_nodes") or []:
        if nid in wf:
            wf[nid]["inputs"][key] = int(seed) % (2**64)
    return wf


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(name)).strip("_")[:80]
