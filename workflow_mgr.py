"""工作流模板：加载 / 节点定位 / 参数注入 / 轮换选择。

模板 = ComfyUI API 格式 JSON（data/plugin_data/<名>/workflows/ 下）+ 配置里的元信息
（tag_style / 尺寸预设 / 默认负面词 / 节点覆盖）。节点定位**自动扫描优先、配置覆盖兜底**：
- 采样器：inputs 同时带 positive/negative 引用的节点（class_type 含 sampler/guider 优先）；
- 正/负向文本节点：采样器 inputs.positive/negative 引用指向的节点；
- latent：采样器 inputs.latent_image 引用 → 回退第一个 EmptyLatentImage（或带 width+height 的节点）；
- seed：所有 inputs 带 seed / noise_seed 数值的节点。
这覆盖标准 KSampler 链路；CFGGuider/SamplerCustomAdvanced 等异形链路靠配置里的
节点 ID 覆盖（positive_node/negative_node/latent_node/positive_field/negative_field）。
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
    info: dict = field(default_factory=dict)  # locate_nodes 结果
    ok: bool = False
    problem: str = ""
    sizes: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        """给 WebUI / 指令展示的公开形状（不含工作流 JSON 本体）。"""
        return {
            "name": self.name,
            "file": self.file,
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
            },
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
        info = locate_nodes(wf, override)
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
