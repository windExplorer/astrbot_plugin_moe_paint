"""投递白名单会话：StarTools.send_message 直发，不走事件管线 → 天然静默
（不会触发其他插件的 LLM 回复/装饰钩子）。

语义（PRD §7）：delivery.sessions 是**显式 opt-in** 列表，空 = 只入库不投递。
每张图对每个目标会话投一次，结果按 umo 合并进 delivered_json；发送失败只记
状态与日志，绝不往群里刷错误。
"""

import os
import time

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import StarTools

from . import errfmt

PLUGIN_TAG = "[萌萌绘卷]"


def build_caption(
    template: str, caption: str, category: str, subject: str, workflow: str,
    caption_on: bool = True,
) -> "str | None":
    """按模板拼投递文案；caption_on=False 或结果为空 → None（纯图）。"""
    if not caption_on:
        return None
    text = str(template or "{caption}")
    text = (
        text.replace("{caption}", str(caption or ""))
        .replace("{category}", str(category or ""))
        .replace("{subject}", str(subject or ""))
        .replace("{workflow}", str(workflow or ""))
    )
    return text.strip() or None


async def deliver_record(plugin, rec: dict, sessions: "list | None" = None) -> list:
    """把一条成功记录投递到目标会话，返回 [{umo, ok, ts, error}] 并合并进库。

    sessions=None 时用配置列表；传显式列表用于「重投」等场景。
    """
    if rec is None or not rec.get("image_relpath"):
        return []
    targets = list(sessions) if sessions is not None else plugin._delivery_sessions()
    if not targets:
        return []
    abs_path = plugin.gallery_abs(rec.get("image_relpath"))
    results: list[dict] = []
    if not abs_path or not os.path.isfile(abs_path):
        for umo in targets:
            results.append({"umo": umo, "ok": False, "ts": int(time.time()),
                            "error": "图片文件不存在"})
        await plugin.store.merge_delivered(rec["id"], results)
        return results

    caption = None
    try:
        caption = build_caption(
            plugin._delivery_cfg().get("caption_template"),
            rec.get("caption") or "",
            rec.get("category") or "",
            _subject_of(rec),
            rec.get("workflow") or "",
            caption_on=bool(plugin._delivery_cfg().get("caption_on", True)),
        )
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 投递文案拼装失败（改发纯图）: {e}")

    comps = []
    if caption:
        comps.append(Plain(caption))
    comps.append(Image.fromFileSystem(abs_path))

    for umo in targets:
        ok = False
        err = ""
        try:
            ok = bool(await StarTools.send_message(str(umo), MessageChain(chain=comps)))
            if not ok:
                err = "未找到匹配的平台实例（检查 UMO 平台 id）"
        except Exception as e:
            err = errfmt.truncate(errfmt.format_exc(e), 200)
        results.append({"umo": str(umo), "ok": ok, "ts": int(time.time()), "error": err})
        if ok:
            logger.info(f"{PLUGIN_TAG} 已投递记录 #{rec.get('id')} → {umo}")
        else:
            logger.warning(f"{PLUGIN_TAG} 投递记录 #{rec.get('id')} → {umo} 失败: {err}")
    try:
        await plugin.store.merge_delivered(rec["id"], results)
    except Exception as e:
        logger.warning(f"{PLUGIN_TAG} 投递结果落库失败: {e}")
    return results


def _subject_of(rec: dict) -> str:
    try:
        import json as _json

        brief = _json.loads(rec.get("brief") or "{}")
        if isinstance(brief, dict):
            return str(brief.get("subject") or "")
    except Exception:
        pass
    return ""
