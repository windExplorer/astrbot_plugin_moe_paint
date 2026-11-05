"""萌萌绘卷 — 定时 LLM 提示词 + ComfyUI 自动绘图 + 图库 WebUI。

指令：/绘卷 [帮助 | 状态 | 暂停 | 继续 | 画一张 [主题词] | 投递 开启|关闭]
流程：调度到点 → 分层抽签出创作简报 → LLM 按 JSON 契约写提示词 → 尺寸白名单落宽高
→ 选工作流模板注入 → 提交 ComfyUI → 轮询取图 → sha256 归档入库 → 投递白名单会话。

架构要点（详见工作区 docs/astrbot_plugin_moe_paint_PRD.md）：
- 直连 ComfyUI（客户端改编自萌绘 comfyui_client.py，AGPL 同源，头注署名）；
- LLM 不报数字宽高，只按构图朝向从模板尺寸白名单落值；
- 投递走 StarTools.send_message（不走事件管线，天然静默）；
- 指令回执直发（event.send），绕开核心「回复时 @ 发送者」装饰（萌萌表情包同款教训）；
- 数据全落 plugin_data（安装目录更新即清零）。
"""

import asyncio
import importlib
import json
import random
import shutil
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from . import comfy_client, delivery, gallery_store, prompter, scheduler, webui_api, workflow_mgr

PLUGIN_NAME = "astrbot_plugin_moe_paint"
PLUGIN_TAG = "[萌萌绘卷]"

HELP_TEXT = (
    "萌萌绘卷：定时用 LLM 生成提示词自动绘图，投递白名单会话，生成记录进图库 WebUI。\n"
    "/绘卷              看状态\n"
    "/绘卷 暂停|继续     管理员：暂停/恢复调度\n"
    "/绘卷 画一张 [主题]  立即生成一张发到本会话（主题词可选）\n"
    "/绘卷 投递 开启|关闭 管理员：本会话是否接收定时投递\n"
    "/绘卷 帮助          本帮助"
)

_PICK_RANDOM = random.Random()


@register(
    "astrbot_plugin_moe_paint",
    "windExplorer",
    "萌萌绘卷：定时 LLM 提示词 + ComfyUI 自动绘图 + 图库 WebUI",
    "v0.1.0",
)
class MoePaintPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        # AstrBot 热更新只重载 main.py，依赖子模块会残留在 sys.modules 里跑旧代码；
        # 这里按依赖序（被依赖者在前）强制重载并重新绑定全局名（box/anima/moe_meme 同款）。
        global comfy_client, delivery, gallery_store, prompter, scheduler, webui_api, workflow_mgr
        try:
            for _dep in (
                "comfy_client", "prompter", "workflow_mgr", "gallery_store",
                "delivery", "scheduler", "webui_api",
            ):
                try:
                    _mod = importlib.import_module(f"{__package__}.{_dep}")
                    importlib.reload(_mod)
                except Exception as _err:
                    logger.warning(f"{PLUGIN_TAG} {_dep} 强制重载失败（沿用已加载模块）: {_err}")
            from . import (
                comfy_client as _cc, delivery as _dl, gallery_store as _gs,
                prompter as _pm, scheduler as _sch, webui_api as _wa,
                workflow_mgr as _wm,
            )
            comfy_client, prompter, workflow_mgr = _cc, _pm, _wm
            gallery_store, delivery, scheduler, webui_api = _gs, _dl, _sch, _wa
        except Exception as _reload_err:
            logger.warning(f"{PLUGIN_TAG} 依赖模块重载流程异常（沿用已加载模块）: {_reload_err}")

        self.config = config
        self.store: "gallery_store.Store | None" = None
        self.gallery_dir: "Path | None" = None
        self.workflows_dir: "Path | None" = None
        self.sched = scheduler.ScheduleState()
        self._sched_task: "asyncio.Task | None" = None
        self._gen_lock = asyncio.Lock()
        self._client: "comfy_client.ComfyUIClient | None" = None
        self._client_key: "tuple | None" = None
        self._templates: "list[workflow_mgr.Template]" = []

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """生命周期钩子：异常会导致整个插件加载失败，这里逐段兜底，绝不上抛。"""
        data_dir = None
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} StarTools 数据目录不可用（退回 data/plugin_data）: {e}")
            data_dir = Path("data") / "plugin_data" / PLUGIN_NAME
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            self.gallery_dir = data_dir / "gallery"
            self.gallery_dir.mkdir(parents=True, exist_ok=True)
            self.workflows_dir = data_dir / "workflows"
            self.workflows_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_builtin_workflow()
        except Exception as e:
            logger.error(f"{PLUGIN_TAG} 数据目录初始化失败: {e}")
        try:
            self.store = gallery_store.Store(str(data_dir / "moe_paint.db"))
            await self.store.init()
        except Exception as e:
            self.store = None
            logger.error(f"{PLUGIN_TAG} 记录库初始化失败（图库功能不可用）: {e}")
        try:
            self.reload_templates()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 模板加载失败: {e}")
        try:
            webui_api.register(self)
        except Exception:
            logger.exception(f"{PLUGIN_TAG} WebUI 路由注册失败")
        try:
            self._sched_task = asyncio.create_task(scheduler.run_loop(self))
        except Exception as e:
            logger.error(f"{PLUGIN_TAG} 调度循环启动失败: {e}")
        logger.info(f"{PLUGIN_TAG} 初始化完成，数据目录：{data_dir}")

    async def terminate(self) -> None:
        task = self._sched_task
        if task is not None:
            task.cancel()
            self._sched_task = None
        try:
            if self._client is not None:
                await self._client.close()
                self._client = None
                self._client_key = None
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 配置读取（每次现读，管理页改完即时生效）
    # ------------------------------------------------------------------ #

    def _cfg(self) -> dict:
        return self.config if isinstance(self.config, dict) else {}

    def _sub(self, key: str) -> dict:
        v = self._cfg().get(key)
        return v if isinstance(v, dict) else {}

    def _schedule_cfg(self) -> dict:
        return self._sub("schedule")

    def _delivery_cfg(self) -> dict:
        return self._sub("delivery")

    def _delivery_sessions(self) -> list:
        lst = self._delivery_cfg().get("sessions")
        return [str(u) for u in lst if str(u or "").strip()] if isinstance(lst, list) else []

    def _save(self) -> None:
        try:
            self.config.save_config()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 配置保存失败: {e}")

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def set_paused_manual(self, paused: bool) -> None:
        scfg = self._schedule_cfg()
        if isinstance(scfg, dict):
            scfg["paused_manual"] = bool(paused)
            self._save()

    # ------------------------------------------------------------------ #
    # ComfyUI 客户端（按配置懒建；url/timeout 变了才重建）
    # ------------------------------------------------------------------ #

    def _get_client(self) -> "comfy_client.ComfyUIClient | None":
        sub = self._sub("comfyui")
        url = str(sub.get("url") or "http://127.0.0.1:8188").strip()
        try:
            timeout = max(30, int(sub.get("draw_timeout") or 180))
        except (TypeError, ValueError):
            timeout = 180
        key = (url, timeout)
        if self._client is None or self._client_key != key:
            old = self._client
            self._client = comfy_client.ComfyUIClient(url, timeout=timeout)
            self._client_key = key
            if old is not None:
                asyncio.ensure_future(self._close_quietly(old))
        return self._client

    @staticmethod
    async def _close_quietly(client) -> None:
        try:
            await client.close()
        except Exception:
            pass

    async def _comfy_status(self) -> dict:
        """WebUI 状态页用：探活（6s 封顶，不阻塞页面）。"""
        client = self._get_client()
        if client is None:
            return {"ok": False, "error": "客户端未初始化", "url": ""}
        try:
            return await asyncio.wait_for(client.probe(), timeout=6)
        except asyncio.TimeoutError:
            return {"ok": False, "elapsed_ms": 6000, "error": "探活超时（6s）"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def remote_queue_ahead(self) -> int:
        """读远程 /queue 估算前方任务数（排队 + 执行中，与萌绘同口径）。

        供调度循环做提交前避让；查询失败返回 -1（调用方按「不忙」处理，
        宁试不误停）。手动触发不走避让，只用它算等待预算。
        """
        try:
            client = self._get_client()
            if client is None:
                return -1
            return comfy_client.queue_pending(await client.get_queue())
        except Exception as e:
            logger.debug(f"{PLUGIN_TAG} 远程队列查询失败（按不忙处理）: {e}")
            return -1

    # ------------------------------------------------------------------ #
    # LLM
    # ------------------------------------------------------------------ #

    def _provider(self):
        pid = str(self._sub("llm").get("provider_id") or "").strip()
        if pid:
            try:
                p = self.context.get_provider_by_id(pid)
                if p is not None and hasattr(p, "text_chat"):
                    return p
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 按 provider_id 取模型失败（退回全局默认）: {e}")
        try:
            return self.context.get_using_provider(None)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 取全局默认提供商失败: {e}")
            return None

    async def _llm_text(self, provider, system_prompt: str, user_prompt: str) -> str:
        """调一次 LLM 并取回文本（completion_text 优先、result_chain 兜底）。"""
        timeout = 45
        try:
            timeout = max(10, int(self._sub("llm").get("timeout") or 45))
        except (TypeError, ValueError):
            timeout = 45
        resp = await asyncio.wait_for(
            provider.text_chat(prompt=user_prompt, system_prompt=system_prompt),
            timeout=timeout,
        )
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text and getattr(resp, "result_chain", None) is not None:
            parts = []
            for seg in resp.result_chain.chain:
                t = getattr(seg, "text", None)
                if t:
                    parts.append(str(t))
            text = "".join(parts).strip()
        return text

    async def _llm_prompt(self, brief: dict, tpl: "workflow_mgr.Template") -> tuple[dict, str, float]:
        """按简报生成 JSON 契约载荷；解析失败重试 1 次（更严指令）。返回 (载荷, provider标签, 耗时ms)。"""
        provider = self._provider()
        if provider is None:
            raise RuntimeError("没有可用 LLM 提供商（先在 WebUI 配置好对话模型，或在本插件配置里指定 provider_id）")
        pid = str(self._sub("llm").get("provider_id") or "").strip()
        label = pid or "全局默认"
        sys_prompt = prompter.build_system_prompt(tpl.tag_style)
        user_prompt = prompter.build_user_prompt(brief)
        t0 = time.monotonic()
        try:
            text = await self._llm_text(provider, sys_prompt, user_prompt)
            payload = prompter.parse_llm_payload(text)
        except ValueError as e:
            # 只对「契约不合规」重试一次；超时/网络异常直接抛（下一轮调度自然重试）
            logger.warning(f"{PLUGIN_TAG} LLM 输出不合契约，重试一次: {e}")
            text = await self._llm_text(
                provider,
                sys_prompt,
                user_prompt + f"\n\n你上一次的输出不合规（{e}）。重新输出，只输出 JSON 对象本身。",
            )
            payload = prompter.parse_llm_payload(text)
        return payload, label, (time.monotonic() - t0) * 1000

    # ------------------------------------------------------------------ #
    # 模板
    # ------------------------------------------------------------------ #

    def _ensure_builtin_workflow(self) -> None:
        if self.workflows_dir is None:
            return
        target = self.workflows_dir / workflow_mgr.BUILTIN_WORKFLOW_FILE
        if target.is_file():
            return
        src = Path(__file__).parent / "builtin" / workflow_mgr.BUILTIN_WORKFLOW_FILE
        if src.is_file():
            try:
                shutil.copyfile(src, target)
                logger.info(f"{PLUGIN_TAG} 已复制内置工作流到 {target}")
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 内置工作流复制失败: {e}")

    def reload_templates(self) -> None:
        entries = self._cfg().get("workflow_templates")
        if not isinstance(entries, list):
            entries = []
        builtin_dir = Path(__file__).parent / "builtin"
        self._templates = workflow_mgr.load_templates(self.workflows_dir or Path("."), entries, builtin_dir)
        bad = [t for t in self._templates if not t.ok]
        if bad:
            logger.warning(f"{PLUGIN_TAG} 模板问题：{'; '.join(f'{t.name}: {t.problem}' for t in bad)}")
        if not workflow_mgr.enabled_pool(self._templates):
            logger.warning(f"{PLUGIN_TAG} 没有可用的工作流模板，调度会空转（检查配置 workflow_templates）")

    # ------------------------------------------------------------------ #
    # 生成管线
    # ------------------------------------------------------------------ #

    def gallery_abs(self, relpath: str) -> str:
        """库内相对路径 → 绝对路径（拒绝越界）。"""
        rel = str(relpath or "").replace("\\", "/").strip("/")
        if not rel or ".." in rel.split("/") or self.gallery_dir is None:
            return ""
        return str(self.gallery_dir / rel)

    async def notify_admin(self, text: str) -> None:
        umo = str(self._cfg().get("admin_notify_umo") or "").strip()
        if not umo:
            return
        try:
            await StarTools.send_message(umo, MessageChain(chain=[Plain(str(text))]))
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 管理员通知发送失败: {e}")

    def trigger_manual(self, theme: str = "", source: str = "webui") -> bool:
        """异步触发一张（WebUI/跨插件用，立即返回；结果进图库并按名单投递）。"""
        if self._gen_lock.locked():
            return False
        asyncio.ensure_future(self.generate_one(reason=f"manual:{source}", theme=theme))
        return True

    async def generate_one(self, reason: str = "schedule", theme: str = "", event: "AstrMessageEvent | None" = None) -> dict:
        """完整跑一轮：抽签 → LLM → 注入 → 出图 → 归档 → 投递。

        返回 {"ok": bool, "id": int, "error": str, "skipped": bool, "image_path": str}。
        任何一步失败都落 failed 记录（图库可查死因），不往会话刷错误。
        """
        if self._gen_lock.locked():
            return {"ok": False, "skipped": True, "error": "已有生成在进行", "id": 0}
        async with self._gen_lock:
            return await self._generate(reason=reason, theme=theme, event=event)

    async def _generate(self, reason: str, theme: str, event) -> dict:
        t0 = time.monotonic()
        summary: dict = {"ok": False, "id": 0, "error": "", "skipped": False, "image_path": ""}
        store = self.store
        try:
            self.reload_templates()
            if store is None:
                raise RuntimeError("记录库不可用（看初始化日志）")
            mode = str(self._cfg().get("workflow_selection") or "random")
            try:
                cursor = int(await store.get_meta("rr_cursor", "0") or 0)
            except ValueError:
                cursor = 0
            tpl, cursor = workflow_mgr.pick_template(self._templates, mode, _PICK_RANDOM, cursor)
            if mode == "round_robin":
                await store.set_meta("rr_cursor", str(cursor))

            brief = prompter.draw_brief(
                _PICK_RANDOM,
                disabled_categories=self._disabled_categories(),
                extra=self._theme_extra(),
            )
            if theme.strip():
                brief["category"] = "自定义"
                brief["subject"] = theme.strip()[:50]

            rec_id = await store.insert_running(
                ts=int(time.time()), day=self._today(), reason=str(reason)[:40],
                category=brief["category"],
                brief=json.dumps(brief, ensure_ascii=False),
                workflow=tpl.name if tpl else "",
                tag_style=tpl.tag_style if tpl else "",
            )
            summary["id"] = rec_id
            try:
                if tpl is None:
                    raise RuntimeError("没有可用的工作流模板（检查配置 workflow_templates 与 data 目录）")

                payload, prov_label, llm_ms = await self._llm_prompt(brief, tpl)
                width, height = prompter.pick_size(tpl.sizes, brief["orientation"], brief["composition"], _PICK_RANDOM)
                negative = payload["negative"] or tpl.default_negative
                caption = payload["caption"] or f"{brief['category']}·{brief['subject']}"
                seed = _PICK_RANDOM.getrandbits(48)
                await store.update_generation(
                    rec_id, prompt=payload["prompt"], negative=negative, caption=caption,
                    width=width, height=height, seed=seed,
                    llm_provider=prov_label, llm_latency_ms=round(llm_ms, 1),
                )

                wf = workflow_mgr.inject(tpl.wf, tpl.info, payload["prompt"], negative, width, height, seed)
                client = self._get_client()
                if client is None:
                    raise RuntimeError("ComfyUI 客户端不可用")
                queued = await client.queue_prompt(wf)
                prompt_id = str(queued.get("prompt_id") or "")
                if not prompt_id:
                    raise RuntimeError(f"提交失败：{json.dumps(queued, ensure_ascii=False)[:200]}")
                ahead = queued.get("_queue_position")
                if ahead is None:
                    try:
                        ahead = comfy_client.queue_pending(await client.get_queue())
                    except Exception:
                        ahead = 0
                timeout = self._wait_budget(ahead)
                history = await client.wait_for_result(
                    prompt_id, timeout,
                    interval=self._int_of(self._sub("comfyui"), "poll_interval", 2, lo=1, hi=30),
                )
                if history is None:
                    raise RuntimeError(f"等待超时（预算 {timeout}s，队列前方约 {ahead} 个任务）")
                err = comfy_client.task_error(history)
                if err:
                    raise RuntimeError(f"ComfyUI 执行失败：{err}")
                imgs = comfy_client.extract_images(history)
                if not imgs:
                    raise RuntimeError("任务完成但没有输出图片")
                entry = imgs[0]
                data = await client.get_image(
                    entry["filename"], entry.get("subfolder", ""), entry.get("type", "output")
                )
                ext = gallery_store.ext_for(entry["filename"])
                abs_path, rel, sha16, _dedup = gallery_store.archive_image(self.gallery_dir, data, ext)
                gen_ms = (time.monotonic() - t0) * 1000
                await store.update_generation(
                    rec_id, status="success", comfy_prompt_id=prompt_id,
                    image_relpath=rel, sha256=sha16, gen_latency_ms=round(gen_ms, 1), error="",
                )
                summary.update(ok=True, id=rec_id, image_path=abs_path, relpath=rel)
                logger.info(
                    f"{PLUGIN_TAG} 生成成功 #{rec_id}：{brief['category']}·{brief['subject']} "
                    f"（{tpl.name} {width}x{height} seed={seed}，{gen_ms / 1000:.1f}s）"
                )

                # 投递：指令触发 → 当前会话直发；其余 → 白名单会话
                if event is not None:
                    dcfg = self._delivery_cfg()
                    cap = delivery.build_caption(
                        dcfg.get("caption_template"), caption, brief["category"],
                        brief["subject"], tpl.name,
                        caption_on=bool(dcfg.get("caption_on", True)),
                    )
                    comps = ([Plain(cap)] if cap else []) + [Image.fromFileSystem(abs_path)]
                    await event.send(MessageChain(chain=comps))
                    await store.merge_delivered(
                        rec_id,
                        [{"umo": event.unified_msg_origin, "ok": True,
                          "ts": int(time.time()), "via": "command"}],
                    )
                else:
                    rec = await store.get_record(rec_id)
                    await delivery.deliver_record(self, rec)
            except Exception as e:
                summary["error"] = f"{type(e).__name__}: {e}"[:300]
                try:
                    await store.update_generation(
                        rec_id, status="failed", error=summary["error"],
                        gen_latency_ms=round((time.monotonic() - t0) * 1000, 1),
                    )
                except Exception:
                    pass
                logger.error(f"{PLUGIN_TAG} 生成失败 #{rec_id}: {summary['error']}")
            # 收尾：LRU 淘汰（低频，失败也做）
            try:
                max_mb = self._float_of(self._sub("gallery"), "max_total_mb", 1024.0)
                if summary["ok"]:
                    await gallery_store.prune_gallery(self.gallery_dir, store, max_mb)
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 图库淘汰失败: {e}")
        except Exception as e:
            # 走到这里说明连记录行都没建成功——只能日志留痕
            summary["error"] = f"{type(e).__name__}: {e}"[:300]
            logger.exception(f"{PLUGIN_TAG} 生成管线异常")
        return summary

    def _wait_budget(self, ahead: int) -> int:
        """等待预算：draw_timeout + 队列前方数 × queue_extra_timeout，封顶 hard_cap。"""
        sub = self._sub("comfyui")
        base = self._int_of(sub, "draw_timeout", 180, lo=30, hi=3600)
        extra = self._int_of(sub, "queue_extra_timeout", 60, lo=0, hi=3600)
        cap = self._int_of(sub, "hard_cap", 600, lo=30, hi=14400)
        try:
            ahead = max(0, int(ahead or 0))
        except (TypeError, ValueError):
            ahead = 0
        return min(base + ahead * extra, cap)

    def _disabled_categories(self) -> set:
        raw = str(self._sub("theme").get("disabled_categories") or "")
        return {w.strip() for w in raw.replace("，", ",").split(",") if w.strip()}

    def _theme_extra(self) -> dict:
        raw = self._sub("theme").get("extra_words")
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _int_of(sub: dict, key: str, default: int, lo: int = 0, hi: int = 10**9) -> int:
        try:
            return max(lo, min(hi, int(sub.get(key, default))))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_of(sub: dict, key: str, default: float) -> float:
        try:
            return float(sub.get(key, default))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    # 指令回执：直发（绕开核心「回复时 @ 发送者」装饰，萌萌表情包同款教训）
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _send_text(event: AstrMessageEvent, text: str) -> None:
        try:
            await event.send(MessageChain(chain=[Plain(str(text))]))
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 文本直发失败（忽略）: {e}")

    # ------------------------------------------------------------------ #
    # 指令
    # ------------------------------------------------------------------ #

    @filter.command("绘卷")
    async def moe_paint(self, event: AstrMessageEvent, query: GreedyStr = GreedyStr):
        """/绘卷 状态 | 暂停 | 继续 | 画一张 [主题] | 投递 开启|关闭 | 帮助"""
        text = "" if query is GreedyStr else str(query or "")
        parts = text.strip().split()
        head = parts[0] if parts else ""

        if head in ("暂停", "继续"):
            await self._toggle_schedule(event, head)
            return
        if head == "投递":
            await self._manage_delivery(event, parts[1] if len(parts) > 1 else "")
            return
        if head in ("帮助", "help"):
            await self._send_text(event, HELP_TEXT)
            return
        if head in ("画一张", "画"):
            theme = " ".join(parts[1:]) if len(parts) > 1 else ""
            await self._manual_draw(event, theme)
            return
        await self._send_status(event)

    async def _toggle_schedule(self, event: AstrMessageEvent, action: str) -> None:
        if not event.is_admin():
            await self._send_text(event, "该操作仅 AstrBot 管理员可用。")
            return
        if action == "暂停":
            self.set_paused_manual(True)
            await self._send_text(event, "萌萌绘卷调度已暂停（/绘卷 继续 恢复，WebUI 亦可）。")
        else:
            self.set_paused_manual(False)
            self.sched.auto_paused = False
            self.sched.consecutive_failures = 0
            await self._send_text(event, "萌萌绘卷调度已恢复。")

    async def _manage_delivery(self, event: AstrMessageEvent, action: str) -> None:
        if not event.is_admin():
            await self._send_text(event, "该操作仅 AstrBot 管理员可用。")
            return
        umo = event.unified_msg_origin
        dcfg = self._delivery_cfg()
        lst = dcfg.get("sessions")
        lst = [str(u) for u in lst] if isinstance(lst, list) else []
        action = (action or "").strip().lower()
        if action in ("开启", "打开", "加入", "on", "enable"):
            if umo not in lst:
                lst.append(umo)
                dcfg["sessions"] = lst
                self._save()
            await self._send_text(event, f"已开启本会话的定时投递（当前 {len(lst)} 个会话）。")
            return
        if action in ("关闭", "移除", "off", "disable"):
            if umo in lst:
                lst.remove(umo)
                dcfg["sessions"] = lst
                self._save()
            await self._send_text(event, f"已关闭本会话的定时投递（当前 {len(lst)} 个会话）。")
            return
        state = "已开启" if umo in lst else "未开启"
        await self._send_text(
            event,
            f"本会话定时投递：{state}（共 {len(lst)} 个会话）。"
            "用「/绘卷 投递 开启|关闭」修改。",
        )

    async def _manual_draw(self, event: AstrMessageEvent, theme: str) -> None:
        if self._gen_lock.locked():
            await self._send_text(event, "上一张还在生成中，稍等它画完再试。")
            return
        await self._send_text(event, "开始生成，画好直接发到这里（一般 20~90 秒）…")
        summary = await self.generate_one(reason="manual", theme=theme, event=event)
        if summary.get("skipped"):
            await self._send_text(event, "生成被跳过（已有任务占用）。")
            return
        if not summary.get("ok"):
            await self._send_text(event, f"生成失败：{summary.get('error') or '未知错误'}")
            return
        # 成功的图在 _generate 内部已直发（含投递文案），这里不再补发

    async def _send_status(self, event: AstrMessageEvent) -> None:
        lines = [PLUGIN_TAG.strip("[]") + " 状态"]
        root = self._cfg()
        scfg = self._schedule_cfg()
        if not root.get("enabled", True):
            lines.append("调度：插件总开关已关闭")
        elif self.sched.auto_paused:
            lines.append(f"调度：已自动暂停（连续失败 {self.sched.consecutive_failures} 次，/绘卷 继续 恢复）")
        elif scfg.get("paused_manual"):
            lines.append("调度：已手动暂停（/绘卷 继续 恢复）")
        else:
            lines.append("调度：运行中")
        if self.sched.next_due:
            lines.append(f"下次生成：{self.sched.next_due.strftime('%m-%d %H:%M')}（{self._schedule_desc()}）")
        if self.sched.last_run_note:
            lines.append(f"最近一轮：{self.sched.last_run_note}")
        if self.store is not None:
            try:
                counts = await self.store.counts()
                today_n = await self.store.count_day_success(self._today())
                cap = self._int_of(scfg, "max_per_day", 4, lo=0, hi=1000)
                cap_desc = f"上限 {cap}" if cap > 0 else "不限"
                lines.append(
                    f"今日 {today_n} 张（{cap_desc}）；累计 成功 {counts['success']} / "
                    f"失败 {counts['failed']} / 进行中 {counts['running']}"
                )
            except Exception:
                pass
        pool = workflow_mgr.enabled_pool(self._templates)
        names = "、".join(t.name for t in pool) if pool else "（无可用模板！）"
        lines.append(f"工作流 {len(pool)} 个：{names}")
        sessions = self._delivery_sessions()
        lines.append(f"投递会话：{len(sessions)} 个" + ("" if sessions else "（空 = 只入库不投递）"))
        pid = str(self._sub("llm").get("provider_id") or "").strip()
        lines.append(f"LLM：{pid or '全局默认提供商'}（{'已找到' if self._provider() is not None else '未找到！'}）")
        await self._send_text(event, "\n".join(lines))

    def _schedule_desc(self) -> str:
        scfg = self._schedule_cfg()
        if str(scfg.get("mode") or "interval") == "daily":
            return f"每日 {scfg.get('daily_times')}"
        try:
            minutes = int(scfg.get("interval_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0 and scfg.get("interval_hours") is not None:
            # 旧配置键兼容（v0.1.1 及更早的 interval_hours，小时）
            try:
                minutes = int(float(scfg.get("interval_hours")) * 60)
            except (TypeError, ValueError):
                minutes = 0
        desc = f"每 {minutes or 360} 分钟"
        if minutes and minutes % 60 == 0:
            desc = f"每 {minutes // 60} 小时"
        return f"{desc} ±{scfg.get('jitter_minutes')} 分钟"
