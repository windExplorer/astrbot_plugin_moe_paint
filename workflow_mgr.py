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

# --------------------------------------------------------------------------- #
# 底模画像（v0.5.0）：提示词风格 / 尺寸白名单 / 质量前缀按底模家族自动适配
#
# 为什么需要：不同底模吃的提示词完全不同 —— 动漫标签系（anima/Illustrious/Pony）
# 要 danbooru 标签串 + 质量词；Flux / SD3 / 写实 SDXL 要英文自然语言且不需要
# masterpiece 这类词；Qwen-Image 这类中文模型用中文描述效果最好。
# 识别只做「文件名关键词 → 家族」，识别不到就保持模板里的手填值（不瞎猜）。
# --------------------------------------------------------------------------- #

_FAMILY_RULES: tuple = (
    ("anime", (
        "anima", "illustrious", "noob", "pony", "animagine", "anything",
        "counterfeit", "meina", "hassaku", "aam", "pastel", "flat2d",
        "anime", "waifu",
    )),
    ("flux", ("flux", "schnell", "kontext")),
    ("qwen", ("qwen",)),
    ("sd3", ("sd3", "sd_3", "stable-diffusion-3")),
    ("photoreal", (
        "realvis", "juggernaut", "epicrealis", "realistic", "absolute",
        "chillout", "majicmix", "cyberrealistic", "mrunsharp", "sdxl-refiner",
    )),
)

_PROFILE_BY_FAMILY: dict = {
    "anime": {
        "label": "动漫标签系", "tag_style": "danbooru", "size_preset": "",
        "quality_prefix": "masterpiece, best quality, very aesthetic, absurdres",
    },
    "flux": {
        "label": "Flux", "tag_style": "natural_en", "size_preset": "flux",
        "quality_prefix": "",
    },
    "qwen": {
        "label": "Qwen-Image（中文）", "tag_style": "natural_zh", "size_preset": "qwen",
        "quality_prefix": "",
    },
    "sd3": {
        "label": "SD3.x", "tag_style": "natural_en", "size_preset": "sdxl",
        "quality_prefix": "",
    },
    "photoreal": {
        "label": "写实底模", "tag_style": "natural_en", "size_preset": "sdxl",
        "quality_prefix": "",
    },
    "unknown": {
        "label": "未识别", "tag_style": "", "size_preset": "", "quality_prefix": "",
    },
}


def family_of(model_file: str) -> str:
    """底模文件名 → 家族（anime / flux / qwen / sd3 / photoreal / unknown）。"""
    name = str(model_file or "").lower()
    if not name:
        return "unknown"
    for family, keys in _FAMILY_RULES:
        if any(k in name for k in keys):
            return family
    return "unknown"


def profile_for_family(family: str) -> dict:
    """按家族取画像（外部在「文件名认不出、但其它线索知道家族」时用）。"""
    fam = str(family or "unknown")
    out = dict(_PROFILE_BY_FAMILY.get(fam, _PROFILE_BY_FAMILY["unknown"]))
    out["family"] = fam if fam in _PROFILE_BY_FAMILY else "unknown"
    out["model_file"] = ""
    out["model_class"] = ""
    return out


def guess_profile(model_file: str, model_class: str = "") -> dict:
    """按底模猜提示词画像；model_class 仅用于展示（例如 UNETLoader / CheckpointLoader）。"""
    family = family_of(model_file)
    out = profile_for_family(family)
    out["model_file"] = str(model_file or "")
    out["model_class"] = str(model_class or "")
    return out


# 可画类型：底模家族 → 它能画什么。动漫系画不出照片、写实系画不出二次元角色；
# Flux / Qwen / SD3 是通用底模，两种都行；认不出来时不设限制（宁可让用户手填）。
_SCOPE_BY_FAMILY: dict = {
    "anime": "anime",
    "photoreal": "photo",
    "flux": "both",
    "qwen": "both",
    "sd3": "both",
    "unknown": "both",
}


def scope_for_family(family: str) -> str:
    return _SCOPE_BY_FAMILY.get(str(family or "unknown"), "both")


def resolve_scope(configured: str, family: str) -> str:
    """模板的 subject_scope + 底模家族 → 生效可画类型。auto = 按底模推断。"""
    v = str(configured or "auto").strip().lower()
    if v in ("anime", "photo", "both"):
        return v
    return scope_for_family(family)


def parse_scope_overrides(raw) -> dict:
    """解析「工作流名或id=auto|anime|photo|both」为映射；非法值丢弃。

    v0.7.5：跟随萌绘时的逐条可画类型配置（anima.workflow_scopes）。
    兼容中文/英文冒号，逗号 / 分号 / 换行分隔；也接受现成的 dict。
    """
    if isinstance(raw, dict):
        items = list(raw.items())
    else:
        items = []
        for part in re.split(r"[,，;；\n\r]+", str(raw or "")):
            if not part.strip():
                continue
            for sep in ("=", "：", ":"):
                if sep in part:
                    name, _, val = part.partition(sep)
                    items.append((name, val))
                    break
    out: dict[str, str] = {}
    for name, val in items:
        key = str(name).strip()
        v = str(val).strip().lower()
        if key and v in ("auto", "anime", "photo", "both"):
            out[key] = v
    return out


def workflow_scope_for(chosen: dict, overrides: dict, fallback: str = "auto") -> str:
    """单条萌绘工作流的配置可画类型：逐条覆盖优先，未配置回落到整体开关。

    逐条名单可用工作流名或 workflow_id 作键；都没命中才用 fallback
    （anima.subject_scope，默认 auto = 交给 resolve_scope 按底模推断）。
    """
    for key in (
        str((chosen or {}).get("name") or "").strip(),
        str((chosen or {}).get("workflow_id") or "").strip(),
    ):
        if key and key in (overrides or {}):
            return overrides[key]
    v = str(fallback or "auto").strip().lower()
    return v if v in ("auto", "anime", "photo", "both") else "auto"


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
    prompt_profile: str = "auto"  # auto / danbooru / natural / natural_en
    profile: dict = field(default_factory=dict)  # guess_profile 结果
    quality_prefix: str = ""  # danbooru 系自动加的质量词（其余为空）
    subject_scope: str = "auto"  # 配置值 auto / anime / photo / both
    scope: str = "both"  # 生效能力，抽签时用它筛类别
    key: str = ""  # 轮换游标的稳定标识（local:文件名 / anima:工作流 id）

    def to_public(self) -> dict:
        """给 WebUI / 指令展示的公开形状（不含工作流 JSON 本体）。"""
        return {
            "name": self.name,
            "file": self.file,
            "source": self.source,
            "key": self.key,
            "tag_style": self.tag_style,
            "size_preset": self.size_preset,
            "prompt_profile": self.prompt_profile,
            "profile": self.profile,
            "quality_prefix": self.quality_prefix,
            "subject_scope": self.subject_scope,
            "scope": self.scope,
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
    """按**有序名单**挑工作流；名单为空 = 全部可用（按萌绘列表顺序）。

    可用 = 自身 enabled 且（require_base 时）关联了基础工作流（`base_id`）。

    v0.7.4：名单的填写顺序**就是轮换顺序**。此前只是拿它当集合做 `name in want`
    判断，顺序仍取萌绘配置列表，用户按什么顺序勾都不影响出图，是「顺序看不懂」
    的主要来源。现在按名单逐项查表，同时兼容用 workflow_id 指定。
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
    by_key: dict = {}
    for w in usable:
        for k in (str(w.get("name") or "").strip(), str(w.get("workflow_id") or "").strip()):
            if k and k not in by_key:
                by_key[k] = w
    picked: list[dict] = []
    seen: set[int] = set()
    for n in want:
        w = by_key.get(n)
        if w is None or id(w) in seen:
            continue
        seen.add(id(w))
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
                    name=name, file=fname, ok=False, key=f"local:{fname}",
                    problem=f"工作流文件不存在：{fname}（把 API 格式 JSON 丢进 {workflows_dir}）",
                )
            )
            continue
        except Exception as e:
            out.append(Template(name=name, file=fname, ok=False, key=f"local:{fname}",
                                problem=f"JSON 解析失败: {e}"))
            continue
        if not isinstance(wf, dict) or not wf:
            out.append(Template(name=name, file=fname, ok=False, key=f"local:{fname}",
                                problem="JSON 不是 API 格式工作流"))
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
        mode = str(entry.get("prompt_profile") or "auto").strip() or "auto"
        manual_style = str(entry.get("tag_style") or "danbooru").strip() or "danbooru"
        preset = str(entry.get("size_preset") or "sdxl").strip() or "sdxl"
        profile = guess_profile(info.get("model_file", ""), info.get("model_class", ""))
        scope_cfg = str(entry.get("subject_scope") or "auto").strip().lower() or "auto"
        scope = resolve_scope(scope_cfg, profile.get("family"))
        tag_style = manual_style
        if mode == "auto":
            # 识别到家族才改；识别不到保持手填值（不瞎猜）
            if profile.get("tag_style"):
                tag_style = profile["tag_style"]
            if profile.get("size_preset"):
                preset = profile["size_preset"]
            if profile.get("family") != "unknown" and (tag_style != manual_style or preset != str(
                entry.get("size_preset") or "sdxl"
            ).strip()):
                info.setdefault("warnings", []).append(
                    f"底模适配：{profile['label']} → 提示词 {tag_style} / 尺寸 {preset}"
                    f"（模板里原填 {manual_style}/{entry.get('size_preset') or 'sdxl'}）"
                )
        else:
            tag_style = mode
        quality = (
            str(profile.get("quality_prefix") or "masterpiece, best quality, very aesthetic, absurdres")
            if tag_style == "danbooru" else ""
        )
        tpl = Template(
            name=name,
            file=fname,
            tag_style=tag_style,
            size_preset=preset,
            default_negative=str(entry.get("default_negative") or "").strip(),
            enabled=bool(entry.get("enabled", True)),
            wf=wf,
            info=info,
            ok=not info.get("problems"),
            problem="；".join(info.get("problems") or []),
            sizes=prompter.SIZE_PRESETS.get(preset, prompter.SIZE_PRESETS["sdxl"]),
            prompt_profile=mode,
            profile=profile,
            quality_prefix=quality,
            subject_scope=scope_cfg,
            scope=scope,
            key=f"local:{fname}",
        )
        out.append(tpl)
    return out


def enabled_pool(templates: list[Template]) -> list[Template]:
    return [t for t in templates or [] if t.enabled and t.ok]


def template_key(t: Template) -> str:
    """模板的稳定标识：显式 key → 文件名 → 名字（兜底，老数据/手工构造）。"""
    return (
        str(getattr(t, "key", "") or "").strip()
        or str(t.file or "").strip()
        or str(t.name or "").strip()
    )


def next_index(pool: list[Template], last_key: str = "", legacy_cursor=None) -> int:
    """算出本轮该画第几条（0-based）；不推进任何状态。

    v0.7.4 起游标记「上一张的稳定标识」而不是绝对计数，在**当前可用池**里找它的
    下一条：停用 / 新增 / 校验失败导致池子变化时顺序不会错位。
    - 找不到上一张（首次启用、模板被删/停用/改名）→ 从 #1 重新、可预期地开始；
    - 老版本升级（库里只有整数 `rr_cursor`）→ 把它当「下一张索引」用于这一轮，
      调用方随后写入名字游标，之后不再走这条分支。
    """
    if not pool:
        return 0
    n = len(pool)
    lk = str(last_key or "").strip()
    if lk:
        keys = [template_key(t) for t in pool]
        try:
            return (keys.index(lk) + 1) % n
        except ValueError:
            return 0
    if legacy_cursor is not None:
        try:
            return int(legacy_cursor) % n
        except (TypeError, ValueError):
            return 0
    return 0


def pick_template(
    templates: list[Template], mode: str, rng: random.Random,
    last_key: str = "", legacy_cursor=None,
) -> "tuple[Template | None, str]":
    """按 random / round_robin 选模板；返回 (模板, 新的 last_key)。

    round_robin 靠「上一张的标识」推进；random 不推进游标，切回轮换时从上次
    轮换选中的那条继续。无可用的返回 (None, 原 last_key)。
    """
    pool = enabled_pool(templates)
    if not pool:
        return None, str(last_key or "")
    if str(mode or "random").strip() != "round_robin":
        return rng.choice(pool), str(last_key or "")
    idx = next_index(pool, last_key, legacy_cursor)
    return pool[idx], template_key(pool[idx])


def rotation_state(templates, mode, last_key: str = "", legacy_cursor=None) -> dict:
    """给控制台看的轮换视图：有序可用名单 + 下一张序号与名字。纯函数、只读。"""
    pool = enabled_pool(templates)
    idx = next_index(pool, last_key, legacy_cursor)
    m = str(mode or "random").strip()
    return {
        "mode": m,
        "ordered": m == "round_robin",
        "total": len(pool),
        "items": [
            {
                "index": i + 1,
                "key": template_key(t),
                "name": t.name,
                "file": t.file,
                "source": t.source,
                "scope": getattr(t, "scope", "both"),
            }
            for i, t in enumerate(pool)
        ],
        "next_index": (idx + 1) if pool else 0,
        "next_name": pool[idx].name if pool else "",
        "next_key": template_key(pool[idx]) if pool else "",
        "last_key": str(last_key or ""),
        "last_found": bool(pool) and bool(last_key)
        and any(template_key(t) == str(last_key) for t in pool),
    }


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
