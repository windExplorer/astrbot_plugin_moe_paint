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
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import file_response, request

from . import config_coerce

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


# 配置布尔容错统一走 config_coerce.truthy（此前每个模块各抄一份，容易漏用）
_bool_of = config_coerce.truthy


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
        # v0.7.7：状态接口不再每次重载模板——总览页每 10 秒轮询一次，
        # 重载要读 JSON/SQLite，跟随萌绘时还会刷「【底模】…」日志。
        tpl_public = [t.to_public() for t in plugin.templates_for_ui()]
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 模板加载失败: {e}")
    # 探活在 _comfy_status 内部缓存 20 秒，避免轮询把远端 ComfyUI 当靶子
    comfy = await plugin._comfy_status()
    prov = await plugin._provider()
    pid_cfg = str(plugin._sub("llm").get("provider_id") or "").strip()
    scfg = plugin._schedule_cfg()
    anima = plugin.anima_info()
    acfg = plugin._sub("anima")
    # 生成期间 next_due 仍是「已过期」的旧值：必须靠 running 区分「正在画」和「卡住」
    running = sched.running_since is not None
    overdue = 0
    if sched.next_due is not None and not running:
        overdue = max(0, int((datetime.now() - sched.next_due).total_seconds()))
    last_note = sched.last_run_note
    if running and sched.running_since is not None:
        last_note = f"正在生成（{sched.running_since.strftime('%H:%M')} 开始，已 {sched.running_for_sec() // 60} 分钟）"
    return ok({
        "enabled": _bool_of(plugin._cfg().get("enabled", True), True),
        "paused_manual": _bool_of(scfg.get("paused_manual", False), False),
        "auto_paused": bool(sched.auto_paused),
        "consecutive_failures": int(sched.consecutive_failures),
        "next_due": sched.next_due.strftime("%Y-%m-%d %H:%M:%S") if sched.next_due else None,
        "running": running,
        "running_since": sched.running_since.strftime("%Y-%m-%d %H:%M:%S") if running else None,
        "running_for_sec": sched.running_for_sec(),
        "running_note": sched.running_note if running else "",
        "next_due_overdue_sec": overdue,
        "last_run_note": last_note,
        "last_run_ts": sched.last_run_ts,
        "mode": str(scfg.get("mode") or "interval"),
        "interval_minutes": scfg.get("interval_minutes"),
        "jitter_minutes": scfg.get("jitter_minutes"),
        "daily_times": str(scfg.get("daily_times") or ""),
        "quiet_enabled": _bool_of(scfg.get("quiet_enabled", True), True),
        "quiet_hours": str(scfg.get("quiet_hours") or ""),
        "max_per_day": scfg.get("max_per_day"),
        "counts": counts,
        "today_success": today_n,
        "templates": tpl_public,
        "workflow_source": str(plugin._cfg().get("workflow_source") or "local"),
        "selection_mode": str(plugin._cfg().get("workflow_selection") or "random"),
        "comfy": comfy,
        "llm": {
            "provider_id": pid_cfg,
            "found": prov is not None,
            "label": pid_cfg or "全局默认提供商",
        },
        "delivery_sessions": len(plugin._delivery_sessions()),
        "anima": {
            "installed": bool(anima.get("installed")),
            "version": str(anima.get("version") or ""),
            "follow_server": _bool_of(acfg.get("follow_server", False), False),
            "server_key": str(acfg.get("server_key") or ""),
            "active_url": str(anima.get("active_url") or ""),
            "active_name": str(anima.get("active_name") or ""),
            "workflow": str(acfg.get("workflow") or ""),
            "size_preset": str(acfg.get("size_preset") or "follow"),
        },
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
    # starred: -1 = 不筛、0 = 只看未收藏、1 = 只看收藏
    starred = _qi("starred", -1, -1, 1)
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
    # 回退时别把 MIME 写死 png：webp/jpg 标错类型，严格 CSP 下会拒绝渲染
    from . import gallery_store

    mime = gallery_store.mime_for(path.suffix.lower())
    raw = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


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


# --------------------------------------------------------------------------- #
# 重画 / 提示词收藏（v0.7.8）
# --------------------------------------------------------------------------- #

async def h_redraw(plugin) -> dict:
    """POST {id, mode: same|variant}：按已有记录重画（同 seed / 换 seed）。"""
    body = await _payload()
    try:
        rec_id = int(body.get("id"))
    except (TypeError, ValueError):
        return err("id 不合法")
    res = await plugin.redraw_record(rec_id, str(body.get("mode") or "variant"))
    return ok(res) if res.get("ok") else err(res.get("error") or "触发失败")


async def h_prompt_presets(plugin) -> dict:
    """GET：提示词收藏列表（不含负向词，避免响应过大）。"""
    return ok({"items": await plugin.list_prompt_presets()})


async def h_prompt_preset(plugin) -> dict:
    """POST {action: save|delete|draw, id?, name?}。"""
    body = await _payload()
    action = str(body.get("action") or "").strip().lower()
    name = str(body.get("name") or "").strip()
    if action == "save":
        try:
            rec_id = int(body.get("id"))
        except (TypeError, ValueError):
            return err("id 不合法")
        res = await plugin.save_prompt_preset(rec_id, name)
    elif action == "delete":
        res = await plugin.delete_prompt_preset(name)
    elif action == "draw":
        res = await plugin.draw_from_preset(name)
    else:
        return err("action 必须是 save / delete / draw")
    return ok(res) if res.get("ok") else err(res.get("error") or "操作失败")


async def h_characters(plugin) -> dict:
    """GET：角色卡列表 + 当前模式（off / random / fixed）+ 当前时段。"""
    cfg = plugin._sub("character")
    return ok({
        "items": await plugin.list_characters(),
        "mode": str(cfg.get("mode") or "off"),
        "name": str(cfg.get("name") or ""),
        "phase": plugin.current_phase(),
    })


async def h_character_edit(plugin) -> dict:
    """POST {action: save|delete, card?|name?}。"""
    body = await _payload()
    action = str(body.get("action") or "save").strip().lower()
    if action == "delete":
        res = await plugin.delete_character(str(body.get("name") or ""))
    elif action == "save":
        res = await plugin.save_character(body.get("card") if isinstance(body.get("card"), dict) else body)
    else:
        return err("action 必须是 save / delete")
    return ok(res) if res.get("ok") else err(res.get("error") or "操作失败")


async def h_debug_score(plugin) -> dict:
    """POST {id}：对已有记录跑一次成图自检（不改状态，只返回分数，用于校准阈值）。"""
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
    path = plugin.gallery_abs(rec.get("image_relpath") or "")
    if not path:
        return err("这条记录没有图片（可能已被容量清理）")
    brief = plugin._reuse_brief(rec)
    score, why = await plugin._score_image(path, brief, rec.get("prompt") or "")
    if score is None:
        return err(why or "质检不可用")
    return ok({"score": score, "issues": why})


async def h_workflows(plugin) -> dict:
    try:
        plugin.reload_templates()
    except Exception as e:
        return err(f"模板加载失败: {e}")
    source = str(plugin._cfg().get("workflow_source") or "local")
    return ok({
        "templates": [t.to_public() for t in plugin._templates],
        "selection_mode": str(plugin._cfg().get("workflow_selection") or "random"),
        "workflows_dir": str(plugin.workflows_dir or ""),
        "source": source,
        "anima": plugin.anima_workflows() if source == "anima" else {
            "installed": plugin.anima_info().get("installed"),
            "workflows": [],
            "error": "",
        },
        # 轮换视图：有序可用名单 + 下一张是谁（只读，不推进游标）
        "rotation": await plugin.rotation_state(),
    })


async def h_workflow_parse(plugin) -> dict:
    """POST {json_text}：只解析不入库（上传预览）。"""
    body = await _payload()
    text = body.get("json_text")
    if not isinstance(text, str) or not text.strip():
        return err("请提供 json_text（工作流 JSON 文本）")
    if len(text) > 8 * 1024 * 1024:
        return err("JSON 太大（上限 8MB）")
    return ok(plugin.preview_workflow(text))


async def h_workflow_upload(plugin) -> dict:
    """POST {name, json_text, tag_style?, size_preset?, default_negative?, enabled?}"""
    body = await _payload()
    text = body.get("json_text")
    if not isinstance(text, str) or not text.strip():
        return err("请提供 json_text（工作流 JSON 文本）")
    if len(text) > 8 * 1024 * 1024:
        return err("JSON 太大（上限 8MB）")
    res = plugin.import_workflow(
        name=str(body.get("name") or ""),
        text=text,
        tag_style=str(body.get("tag_style") or "danbooru"),
        size_preset=str(body.get("size_preset") or "sdxl"),
        default_negative=str(body.get("default_negative") or ""),
        enabled=_bool_of(body.get("enabled", True), True),
        prompt_profile=str(body.get("prompt_profile") or "auto"),
        subject_scope=str(body.get("subject_scope") or "auto"),
    )
    return ok(res) if res.get("ok") else err(res.get("error") or "入库失败")


async def h_rotation_reset(plugin) -> dict:
    """POST：把顺序轮换重置到 #1（清空名字游标与老整数游标）。"""
    res = await plugin.reset_rotation()
    return ok(res) if res.get("ok") else err(res.get("error") or "重置失败")


async def h_anima_workflows(plugin) -> dict:
    """GET /anima/workflows：萌绘的出图工作流 + 基础工作流解析状态。"""
    return ok(plugin.anima_workflows())


# --------------------------------------------------------------------------- #
# 配置读写（user_gateway 模式：GET 回 items+schema；POST 扁平点号键白名单回写）
# 硬约束（box v0.12.7 踩过）：先整体校验再统一写入，未知键直接报错而非静默跳过，
# 绝不把读不到的值回写 null——一次坏保存不能清掉用户的真实配置。
# --------------------------------------------------------------------------- #

def _schema() -> dict:
    try:
        p = Path(__file__).parent / "_conf_schema.json"
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 读取 _conf_schema.json 失败: {e}")
        return {}


# 校验/转换逻辑拆到 config_coerce（纯函数、可测），这里只负责「读写 + 落盘 + 副作用」。
_cast_value = config_coerce.cast_value
_coerce_template_list = config_coerce.coerce_template_list


async def h_get_config(plugin) -> dict:
    """读当前生效配置：嵌套对象展平成点号键 + 原始 schema。"""
    schema = _schema()
    cfg = plugin._cfg()
    items: dict = {}
    for key, spec in schema.items():
        t = str(spec.get("type") or "")
        if t == "object":
            sub = cfg.get(key)
            sub = sub if isinstance(sub, dict) else {}
            for f, fs in (spec.get("items") or {}).items():
                items[f"{key}.{f}"] = sub.get(f, fs.get("default"))
        elif t == "template_list":
            val = cfg.get(key)
            items[key] = val if isinstance(val, list) else (spec.get("default") or [])
        else:
            items[key] = cfg.get(key, spec.get("default"))
    return ok({"items": items, "schema": schema})


async def h_set_config(plugin) -> dict:
    """写配置：只收 schema 里存在的键；先整体校验（类型/选项/未知键），再统一写入。"""
    body = await _payload()
    raw = body.get("items")
    if not isinstance(raw, dict):
        return err("items 必须是对象 {键: 值}")
    schema = _schema()
    cfg = plugin.config
    if cfg is None or not hasattr(cfg, "__setitem__"):
        return err("插件配置不可写")
    try:
        writes, problem = config_coerce.build_writes(raw, schema)
    except ValueError as e:
        return err(str(e))
    if problem:
        return err(problem)
    changed = config_coerce.apply_writes(cfg, writes)
    try:
        cfg.save_config()
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} save_config 失败: {e}")
        return err(f"配置已改内存但落盘失败: {e}")
    if "workflow_templates" in changed:
        try:
            plugin.reload_templates()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 模板重载失败: {e}")
    if any(k.startswith("schedule.") for k in changed):
        # 改了间隔/时刻/安静时段就立刻按新配置重排下次计划，别让旧时刻继续生效
        try:
            plugin.reschedule()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 重算下次计划失败: {e}")
    logger.info(f"{PLUGIN_TAG} WebUI 配置已保存: {', '.join(changed)}")
    plugin.debug_log("config", f"WebUI 保存配置：{', '.join(changed)}")
    return ok({"changed": changed})


async def h_providers(plugin) -> dict:
    """可用的对话模型提供商（model_panel 约定：一提供商 = 一绑定模型，按 id 选）。"""
    items: list[dict] = []
    try:
        provs = plugin.context.get_all_providers() or []
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 读取提供商列表失败: {e}")
        provs = []
    for p in provs:
        try:
            m = p.meta()
            pid = str(getattr(m, "id", "") or "")
            if not pid:
                continue
            model = str(getattr(m, "model", "") or "")
            items.append({"id": pid, "model": model, "label": f"{pid} · {model}" if model else pid})
        except Exception:
            continue
    default_id = ""
    try:
        # 4.28 起同步版已被标废弃，优先 async；老版本再退回同步
        getter = getattr(plugin.context, "get_using_provider_async", None)
        cur = await getter(None) if getter is not None else plugin.context.get_using_provider(None)
        if cur is not None:
            default_id = str((getattr(cur, "provider_config", {}) or {}).get("id") or "")
    except Exception:
        default_id = ""
    for it in items:
        it["is_default"] = bool(default_id and it["id"] == default_id)
    items.sort(key=lambda x: (not x["is_default"], str(x["label"])))
    return ok({"items": items, "default_id": default_id})


# --------------------------------------------------------------------------- #
# 关联萌绘（astrbot_plugin_comfyui_anima）
# --------------------------------------------------------------------------- #

async def h_anima(plugin) -> dict:
    """GET /anima：萌绘是否安装 / 版本 / 服务器列表 / 本插件实际会用哪个地址。"""
    info = plugin.anima_info()
    url, src = plugin._comfy_url()
    acfg = plugin._sub("anima")
    info.update({
        "follow_server": _bool_of(acfg.get("follow_server", False), False),
        "server_key": str(acfg.get("server_key") or ""),
        "effective_url": url,
        "url_source": src,
        "manual_url": str(plugin._sub("comfyui").get("url") or ""),
        "anima_plugin_name": "astrbot_plugin_comfyui_anima",
    })
    return ok(info)


def _cfg_sub(plugin, key: str) -> dict:
    cfg = plugin.config
    sub = cfg.get(key) if isinstance(cfg, dict) else None
    if not isinstance(sub, dict):
        sub = {}
        try:
            cfg[key] = sub
        except Exception:
            return {}
    return sub


async def h_anima_sync(plugin) -> dict:
    """POST {action: follow|copy|off, server_key?}

    - follow：开启「跟随萌绘」（server_key 可指定某台，空 = 萌绘当前启用的那台）
    - copy  ：把萌绘当前地址**复制**进本插件 comfyui.url（不开启跟随，之后各配各的）
    - off   ：关闭跟随（回到本插件自己的 comfyui.url）
    """
    body = await _payload()
    action = str(body.get("action") or "").strip().lower()
    key = str(body.get("server_key") or "").strip()
    if plugin.config is None:
        return err("插件配置不可写")
    info = plugin.anima_info()
    if not info.get("installed"):
        return err("没有找到萌绘插件（astrbot_plugin_comfyui_anima），请先安装并启用它")

    acfg = _cfg_sub(plugin, "anima")
    if action == "follow":
        if key and not any(key in (s["key"], s["name"]) for s in info.get("servers") or []):
            return err(f"萌绘里找不到服务器「{key}」，请刷新后重选")
        if not info.get("servers"):
            return err("萌绘里还没有可用的 ComfyUI 服务器（先去萌绘控制台配置）")
        acfg["follow_server"] = True
        acfg["server_key"] = key
        desc = "已开启跟随萌绘的服务器地址"
    elif action == "copy":
        url = str(info.get("active_url") or "").strip()
        if not url:
            return err("没读到萌绘的服务器地址，请先在萌绘里配置")
        cfg = plugin.config
        comfy = _cfg_sub(plugin, "comfyui")
        comfy["url"] = url
        acfg["follow_server"] = False
        desc = f"已把萌绘地址复制到本插件：{url}"
    elif action == "off":
        acfg["follow_server"] = False
        desc = "已关闭跟随，改用本插件自己的服务器地址"
    else:
        return err("action 必须是 follow / copy / off")
    try:
        plugin.config.save_config()
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 萌绘联动配置落盘失败: {e}")
        return err(f"配置已改内存但落盘失败: {e}")
    url, src = plugin._comfy_url()
    logger.info(f"{PLUGIN_TAG} {desc}（生效地址 {url}，来源 {src}）")
    plugin.debug_log("anima", f"{desc} → {url}")
    return ok({"message": desc, "follow_server": bool(acfg.get("follow_server")),
               "server_key": str(acfg.get("server_key") or ""),
               "effective_url": url, "url_source": src})


# --------------------------------------------------------------------------- #
# 词池 / 提示词（只读）
# --------------------------------------------------------------------------- #

async def h_theme_pool(plugin) -> dict:
    """GET /theme/pool：内置词池全貌，给「主题词池」页画题材清单。"""
    from . import prompter
    return ok(prompter.theme_pool(plugin._category_scopes()))


async def h_prompt_preview(plugin) -> dict:
    """GET /prompt/preview：三种风格下「当前真正生效」的系统提示词。

    追加规则 / 完全覆盖都算进去，用户改完能在页面上直接看到 LLM 会收到什么。
    """
    from . import prompter
    opts = plugin._prompt_opts()
    labels = {
        "danbooru": "动漫标签系（Danbooru）",
        "natural": "中文自然语言（Qwen）",
        "natural_en": "英文自然语言（Flux / 写实）",
    }
    return ok({
        "styles": [
            {"key": k, "label": v, "prompt": prompter.build_system_prompt(k, opts)}
            for k, v in labels.items()
        ],
        "persona_enabled": prompter.opt_bool(opts.get("persona_enabled"), True),
    })


# --------------------------------------------------------------------------- #
# 调试
# --------------------------------------------------------------------------- #

async def h_debug_env(plugin) -> dict:
    """GET /debug/env：一次看清楚各目录、生效地址、投递名单与模板状态。"""
    url, src = plugin._comfy_url()
    return ok({
        "comfy_url": url,
        "comfy_url_source": src,
        "manual_url": str(plugin._sub("comfyui").get("url") or ""),
        "gallery_dir": str(plugin.gallery_dir or ""),
        "workflows_dir": str(plugin.workflows_dir or ""),
        "db_path": str(getattr(plugin.store, "path", "") or ""),
        "delivery_sessions": plugin._delivery_sessions(),
        "templates": [t.to_public() for t in plugin._templates],
        "workflow_source": str(plugin._cfg().get("workflow_source") or "local"),
        "selection_mode": str(plugin._cfg().get("workflow_selection") or "random"),
        "queue_defer_threshold": plugin._int_of(
            plugin._sub("comfyui"), "queue_defer_threshold", 2, lo=0, hi=100
        ),
        "log_capacity": len(plugin._logs),
    })


async def h_debug_comfy(plugin) -> dict:
    """POST {url?}：探活 + 读队列（空地址 = 测当前生效地址，不写配置）。"""
    body = await _payload()
    res = await plugin.debug_comfy(str(body.get("url") or ""))
    return ok(res)


async def h_debug_llm(plugin) -> dict:
    """POST {theme?}：真跑一次「抽签 → LLM → 尺寸落值」，不提交 ComfyUI、不写库。"""
    body = await _payload()
    theme = str(body.get("theme") or "").strip()[:50]
    return ok(await plugin.debug_llm(theme))


async def h_debug_delivery(plugin) -> dict:
    """POST {umo, text?}：往指定会话发一条测试消息（不进投递名单、不写记录）。"""
    body = await _payload()
    res = await plugin.debug_delivery(
        str(body.get("umo") or ""), str(body.get("text") or "")[:300]
    )
    return ok(res)


async def h_debug_logs(plugin) -> dict:
    """GET /debug/logs?limit=120：内存环形日志（倒序：最新在前）。"""
    return ok({"rows": plugin.debug_logs(_qi("limit", 120, 1, 400))})


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
        ("/record/redraw", h_redraw, ["POST"]),
        ("/prompt/presets", h_prompt_presets, ["GET"]),
        ("/prompt/preset", h_prompt_preset, ["POST"]),
        ("/characters", h_characters, ["GET"]),
        ("/characters/edit", h_character_edit, ["POST"]),
        ("/debug/score", h_debug_score, ["POST"]),
        ("/stats", h_stats, ["GET"]),
        ("/workflows", h_workflows, ["GET"]),
        ("/workflows/parse", h_workflow_parse, ["POST"]),
        ("/workflows/upload", h_workflow_upload, ["POST"]),
        ("/workflows/rotation/reset", h_rotation_reset, ["POST"]),
        ("/anima/workflows", h_anima_workflows, ["GET"]),
        ("/config", h_get_config, ["GET"]),
        ("/config", h_set_config, ["POST"]),
        ("/theme/pool", h_theme_pool, ["GET"]),
        ("/prompt/preview", h_prompt_preview, ["GET"]),
        ("/providers", h_providers, ["GET"]),
        ("/anima", h_anima, ["GET"]),
        ("/anima/sync", h_anima_sync, ["POST"]),
        ("/debug/env", h_debug_env, ["GET"]),
        ("/debug/comfy", h_debug_comfy, ["POST"]),
        ("/debug/llm", h_debug_llm, ["POST"]),
        ("/debug/delivery", h_debug_delivery, ["POST"]),
        ("/debug/logs", h_debug_logs, ["GET"]),
    ]
    for path, fn, methods in routes:
        plugin.context.register_web_api(
            f"{PREFIX}{path}", _bind(plugin, fn), methods, f"MoePaint {path}"
        )
    logger.info(f"{PLUGIN_TAG} 已注册 {len(routes)} 条图库路由（前缀 {PREFIX}）")
