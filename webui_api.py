"""图库 WebUI 端点（AstrBot 插件 Page 通道，register_web_api）。

- 信封走 AstrBot 桥接约定：成功 ``{"status":"ok","data":...}``、失败
  ``{"status":"error","message":...}``（user_gateway 同款 ok/err，Dashboard 会据此
  抛错/脱壳——自创信封语义会错乱，见 AGENTS.md 第八节）。
- 图片走官方 ``astrbot.api.web.file_response``（4.28.1 正式 API）。
- 前端（pages/moe-paint/）跑在无 allow-same-origin 的 sandbox iframe 里：
  存储/弹窗等约束在页面侧处理，这里只保证端点语义正确。
"""

import asyncio
import json
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import file_response, request

PREFIX = "/astrbot_plugin_moe_paint"
PLUGIN_TAG = "[萌萌绘卷]"


def ok(data=None) -> dict:
    return {"status": "ok", "data": data}


def err(message: str) -> dict:
    return {"status": "error", "message": str(message)}


def _q(name: str, default: str = "") -> str:
    try:
        v = request.query.get(name)
        return ("" if v is None else str(v)).strip() or default
    except Exception:
        return default


def _qi(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(_q(name, str(default)))))
    except (TypeError, ValueError):
        return default


async def _payload() -> dict:
    try:
        data = await request.json(default=None)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _bind(plugin, fn) -> callable:
    """把 fn(plugin) 包成无参协程；任何异常转 err 信封，避免前端只看到 500。"""

    async def handler():
        try:
            return await fn(plugin)
        except Exception as e:
            logger.exception(f"{PLUGIN_TAG} {getattr(fn, '__name__', '?')} 处理异常")
            return err(f"{getattr(fn, '__name__', 'handler')} 失败: {e}")

    handler.__name__ = f"moe_paint_{getattr(fn, '__name__', 'handler')}"
    return handler


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

async def h_ping(plugin) -> dict:
    return ok({"pong": True, "name": "astrbot_plugin_moe_paint"})


async def h_status(plugin) -> dict:
    """调度器状态 + 计数 + ComfyUI 探活（6s 封顶）+ 模板健康。"""
    store = plugin.store
    counts = {"total": 0, "success": 0, "failed": 0, "running": 0}
    today_n = 0
    if store is not None:
        try:
            counts = await store.counts()
            today_n = await store.count_day_success(plugin._today())
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 状态取数失败: {e}")
    sched = plugin.sched
    tpl_public = []
    try:
        plugin.reload_templates()
        tpl_public = [t.to_public() for t in plugin._templates]
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 模板加载失败: {e}")
    comfy = await plugin._comfy_status()
    prov = plugin._provider()
    pid_cfg = str(plugin._sub("llm").get("provider_id") or "").strip()
    scfg = plugin._schedule_cfg()
    return ok({
        "enabled": bool(plugin._cfg().get("enabled", True)),
        "paused_manual": bool(scfg.get("paused_manual", False)),
        "auto_paused": bool(sched.auto_paused),
        "consecutive_failures": int(sched.consecutive_failures),
        "next_due": sched.next_due.strftime("%Y-%m-%d %H:%M:%S") if sched.next_due else None,
        "last_run_note": sched.last_run_note,
        "last_run_ts": sched.last_run_ts,
        "mode": str(scfg.get("mode") or "interval"),
        "interval_minutes": scfg.get("interval_minutes"),
        "daily_times": str(scfg.get("daily_times") or ""),
        "quiet_hours": str(scfg.get("quiet_hours") or ""),
        "max_per_day": scfg.get("max_per_day"),
        "counts": counts,
        "today_success": today_n,
        "templates": tpl_public,
        "selection_mode": str(plugin._cfg().get("workflow_selection") or "random"),
        "comfy": comfy,
        "llm": {
            "provider_id": pid_cfg,
            "found": prov is not None,
            "label": pid_cfg or "全局默认提供商",
        },
        "delivery_sessions": len(plugin._delivery_sessions()),
    })


async def h_control(plugin) -> dict:
    """POST {action: pause|resume|trigger, theme?}。"""
    body = await _payload()
    action = str(body.get("action") or "").strip()
    if action == "pause":
        plugin.set_paused_manual(True)
        return ok({"paused": True})
    if action == "resume":
        plugin.set_paused_manual(False)
        plugin.sched.auto_paused = False
        plugin.sched.consecutive_failures = 0
        return ok({"paused": False})
    if action == "trigger":
        theme = str(body.get("theme") or "").strip()[:50]
        started = plugin.trigger_manual(theme, source="webui")
        return ok({"started": bool(started), "theme": theme})
    return err("action 必须是 pause / resume / trigger")


async def h_records(plugin) -> dict:
    """GET /records?offset&limit&status&workflow&category&day&keyword&starred"""
    if plugin.store is None:
        return err("数据库未就绪")
    offset = _qi("offset", 0, 0, 10_000_000)
    limit = _qi("limit", 50, 1, 200)
    starred = _qi("starred", "-1")
    try:
        starred = int(starred)
    except ValueError:
        starred = -1
    rows, total = await plugin.store.list_records(
        status=_q("status"),
        workflow=_q("workflow"),
        category=_q("category"),
        day=_q("day"),
        keyword=_q("keyword"),
        starred=starred,
        offset=offset,
        limit=limit,
    )
    for r in rows:
        try:
            r["delivered"] = json.loads(r.pop("delivered_json") or "[]")
        except Exception:
            r["delivered"] = []
            r.pop("delivered_json", None)
    return ok({"rows": rows, "total": total, "offset": offset, "limit": limit})


async def h_record(plugin) -> dict:
    if plugin.store is None:
        return err("数据库未就绪")
    try:
        rec_id = int(_q("id", "0"))
    except ValueError:
        return err("id 不合法")
    rec = await plugin.store.get_record(rec_id)
    if rec is None:
        return err("记录不存在")
    try:
        rec["delivered"] = json.loads(rec.pop("delivered_json") or "[]")
    except Exception:
        rec["delivered"] = []
        rec.pop("delivered_json", None)
    return ok(rec)


def _safe_gallery_path(plugin, relpath: str) -> "Path | None":
    """把库内 relpath 解析成 gallery 内的真实路径；防目录穿越。"""
    rel = str(relpath or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = Path(plugin.gallery_dir).resolve() if plugin.gallery_dir else None
    if root is None:
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


async def h_image(plugin):
    """GET /image?id= → file_response（路径只按库内 relpath 拼，杜绝穿越）。

    直链模式（standalone/外部工具用）；内嵌 Page 的沙箱 iframe 里直链不可靠，
    前端一律走 /thumb（data URL）。"""
    if plugin.store is None:
        return err("数据库未就绪")
    try:
        rec_id = int(_q("id", "0"))
    except ValueError:
        return err("id 不合法")
    rec = await plugin.store.get_record(rec_id)
    if rec is None or not rec.get("image_relpath"):
        return err("记录不存在或没有图片")
    path = _safe_gallery_path(plugin, rec["image_relpath"])
    if path is None or not path.is_file():
        return err("图片文件不存在")
    from . import gallery_store

    ext = path.suffix.lower()
    return file_response(str(path), content_type=gallery_store.mime_for(ext))


# --------------------------------------------------------------------------- #
# 缩略图（data URL：内嵌 Page 的 <img> 唯一可靠姿势，anima ImageViewer 同款结论）
# --------------------------------------------------------------------------- #

import base64  # noqa: E402  （局部依赖，集中放这里保持顶部干净）
from collections import OrderedDict  # noqa: E402

_THUMB_CACHE: "OrderedDict[tuple, str]" = OrderedDict()
_THUMB_CACHE_MAX = 256


def _thumb_data_url(path: Path, size: int) -> str:
    """读文件并缩到最长边 ≤ size 的 JPEG data URL；Pillow 不可用则回退原图 base64。"""
    try:
        from PIL import Image

        with Image.open(str(path)) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            import io

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            data = buf.getvalue()
        return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"
    except ImportError:
        pass  # 没有 Pillow：回退原图 base64（体积大但能用）
    except Exception as e:
        raise RuntimeError(f"缩略图生成失败: {e}") from e
    raw = path.read_bytes()
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"


async def h_thumb(plugin):
    """GET /thumb?id=&size=300 → {data_url}；服务端 LRU 缓存（键含文件 mtime）。"""
    if plugin.store is None:
        return err("数据库未就绪")
    try:
        rec_id = int(_q("id", "0"))
    except ValueError:
        return err("id 不合法")
    size = _qi("size", 320, 64, 2400)
    rec = await plugin.store.get_record(rec_id)
    if rec is None or not rec.get("image_relpath"):
        return err("记录不存在或没有图片")
    path = _safe_gallery_path(plugin, rec["image_relpath"])
    if path is None or not path.is_file():
        return err("图片文件不存在")
    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        mtime = 0
    key = (rec_id, size, mtime)
    hit = _THUMB_CACHE.get(key)
    if hit is None:
        hit = await asyncio.to_thread(_thumb_data_url, path, size)
        _THUMB_CACHE[key] = hit
        while len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
            _THUMB_CACHE.popitem(last=False)
    else:
        _THUMB_CACHE.move_to_end(key)
    return ok({"data_url": hit, "size": size})


async def h_delete(plugin) -> dict:
    """POST {id}：删记录；内容寻址文件无其他记录引用时一并删除。"""
    if plugin.store is None:
        return err("数据库未就绪")
    body = await _payload()
    try:
        rec_id = int(body.get("id"))
    except (TypeError, ValueError):
        return err("id 不合法")
    rec = await plugin.store.delete_record(rec_id)
    if rec is None:
        return err("记录不存在")
    rel = rec.get("image_relpath") or ""
    removed_file = False
    if rel:
        try:
            if await plugin.store.count_relpath(rel, exclude_id=rec_id) == 0:
                path = _safe_gallery_path(plugin, rel)
                if path is not None and path.is_file():
                    path.unlink()
                    removed_file = True
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 删除图片文件失败（记录已删）: {e}")
    logger.info(f"{PLUGIN_TAG} 已删除记录 #{rec_id}（文件已删={removed_file}）")
    return ok({"deleted": True, "file_removed": removed_file})


async def h_star(plugin) -> dict:
    if plugin.store is None:
        return err("数据库未就绪")
    body = await _payload()
    try:
        rec_id = int(body.get("id"))
    except (TypeError, ValueError):
        return err("id 不合法")
    starred = bool(body.get("starred"))
    await plugin.store.set_starred(rec_id, starred)
    return ok({"id": rec_id, "starred": starred})


async def h_resend(plugin) -> dict:
    """POST {id}：按当前投递名单重投（结果合并进 delivered_json）。"""
    if plugin.store is None:
        return err("数据库未就绪")
    body = await _payload()
    try:
        rec_id = int(body.get("id"))
    except (TypeError, ValueError):
        return err("id 不合法")
    rec = await plugin.store.get_record(rec_id)
    if rec is None:
        return err("记录不存在")
    from . import delivery

    results = await delivery.deliver_record(plugin, rec)
    return ok({"results": results})


async def h_stats(plugin) -> dict:
    if plugin.store is None:
        return err("数据库未就绪")
    return ok(await plugin.store.stats())


async def h_workflows(plugin) -> dict:
    try:
        plugin.reload_templates()
    except Exception as e:
        return err(f"模板加载失败: {e}")
    return ok({
        "templates": [t.to_public() for t in plugin._templates],
        "selection_mode": str(plugin._cfg().get("workflow_selection") or "random"),
        "workflows_dir": str(plugin.workflows_dir or ""),
    })


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

def register(plugin) -> None:
    routes: list[tuple[str, object, list[str]]] = [
        ("/ping", h_ping, ["GET"]),
        ("/status", h_status, ["GET"]),
        ("/control", h_control, ["POST"]),
        ("/records", h_records, ["GET"]),
        ("/record", h_record, ["GET"]),
        ("/image", h_image, ["GET"]),
        ("/thumb", h_thumb, ["GET"]),
        ("/record/delete", h_delete, ["POST"]),
        ("/record/star", h_star, ["POST"]),
        ("/record/resend", h_resend, ["POST"]),
        ("/stats", h_stats, ["GET"]),
        ("/workflows", h_workflows, ["GET"]),
    ]
    for path, fn, methods in routes:
        plugin.context.register_web_api(
            f"{PREFIX}{path}", _bind(plugin, fn), methods, f"MoePaint {path}"
        )
    logger.info(f"{PLUGIN_TAG} 已注册 {len(routes)} 条图库路由（前缀 {PREFIX}）")
