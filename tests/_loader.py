"""独立测试装载器：本机 import 不了整个 astrbot 运行时，用 spec 把插件模块
按伪包名加载（同 model_panel 的抠函数式思路，但模块级相对导入更干净）。

用法：`from _loader import load; prompter = load("prompter")`
依赖序：prompter → workflow_mgr（相对导入 prompter）→ scheduler / gallery_store。
"""

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PKG = "moe_paint_pkg"
_LOADED: dict = {}

# 模块间相对导入的依赖序（被依赖者先加载）
_DEPS = {
    "workflow_mgr": ["prompter"],
    "prompter": ["daylight"],
    "delivery": ["config_coerce"],
    "main": ["comfy_client", "daylight", "prompter", "workflow_mgr", "gallery_store",
             "delivery", "scheduler", "webui_api"],
}


def load(name: str):
    _ensure_pkg()
    if name in _LOADED:
        return _LOADED[name]
    for dep in _DEPS.get(name, []):
        load(dep)
    path = _ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    _LOADED[name] = mod
    return mod


def root() -> Path:
    return _ROOT


def _ensure_pkg():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ROOT)]  # 让「from . import x」在 finder 路径也能解析
        sys.modules[_PKG] = pkg
