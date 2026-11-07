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
from collections import deque
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from . import (
    comfy_client, config_coerce, daylight, delivery, errfmt, gallery_store,
    prompter, scheduler, webui_api, workflow_mgr,
)

PLUGIN_NAME = "astrbot_plugin_moe_paint"
PLUGIN_TAG = "[萌萌绘卷]"
# 提示词收藏在 meta 表里的键前缀（preset:<名字>）——不占主表列，删改都轻
PRESET_PREFIX = "preset:"
# 角色卡同理（char:<名字>）
CHAR_PREFIX = "char:"
# 关联萌绘（ComfyUI萌绘）的插件名：只读它的配置取服务器地址，不调用其内部接口
ANIMA_PLUGIN_NAME = "astrbot_plugin_comfyui_anima"

HELP_TEXT = (
    "萌萌绘卷：定时用 LLM 生成提示词自动绘图，投递白名单会话，生成记录进图库 WebUI。\n"
    "/绘卷              看状态\n"
    "/绘卷 暂停|继续     管理员：暂停/恢复调度\n"
    "/绘卷 画一张 [主题]  立即生成一张发到本会话（主题词可选）\n"
    "  可带开关：--no 模糊,水印  --style 胶片  --ratio 3:2  --seed 42\n"
    "/绘卷 投递 开启|关闭 管理员：本会话是否接收定时投递\n"
    "/绘卷 帮助          本帮助"
)

_PICK_RANDOM = random.Random()


@register(
    "astrbot_plugin_moe_paint",
    "windExplorer",
    "萌萌绘卷：定时 LLM 提示词 + ComfyUI 自动绘图 + 图库 WebUI",
    "v0.7.18",
)
class MoePaintPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        # AstrBot 热更新只重载 main.py，依赖子模块会残留在 sys.modules 里跑旧代码；
        # 这里按依赖序（被依赖者在前）强制重载并重新绑定全局名（box/anima/moe_meme 同款）。
        global comfy_client, config_coerce, delivery, errfmt, gallery_store, prompter, scheduler, webui_api, workflow_mgr
        try:
            for _dep in (
                "comfy_client", "daylight", "prompter", "workflow_mgr", "config_coerce",
                "errfmt", "gallery_store", "delivery", "scheduler", "webui_api",
            ):
                try:
                    _mod = importlib.import_module(f"{__package__}.{_dep}")
                    importlib.reload(_mod)
                except Exception as _err:
                    logger.warning(f"{PLUGIN_TAG} {_dep} 强制重载失败（沿用已加载模块）: {_err}")
            from . import (
                comfy_client as _cc, config_coerce as _cf, delivery as _dl,
                errfmt as _ef, gallery_store as _gs, prompter as _pm,
                scheduler as _sch, webui_api as _wa, workflow_mgr as _wm,
            )
            comfy_client, prompter, workflow_mgr = _cc, _pm, _wm
            config_coerce, errfmt, gallery_store = _cf, _ef, _gs
            delivery, scheduler, webui_api = _dl, _sch, _wa
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
        # 后台任务强引用：asyncio 只持弱引用，ensure_future 的返回值不接住
        # 可能在 GC 时被回收（官方文档明说），异常也不会被检索。
        self._bg_tasks: "set[asyncio.Task]" = set()
        # ComfyUI 探活结果缓存：(时间戳, 结果)，控制台轮询不重复打服务器
        self._probe_cache: "tuple[float, dict] | None" = None
        # 手动出图冷却：会话 UMO → 上次触发时刻（秒）
        self._manual_last: "dict[str, float]" = {}
        # 调试用的内存环形日志（不落盘，重启即清空；WebUI「调试」页读它）
        self._logs: "deque[dict]" = deque(maxlen=400)

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
            # 重启后「上一张」的时间要从库里捞回来，否则下次计划会从启动时刻重新起算
            try:
                last_ts = await self.store.last_generation_ts()
                if last_ts:
                    self.sched.last_started_at = datetime.fromtimestamp(int(last_ts))
                    logger.info(
                        f"{PLUGIN_TAG} 上次生成开始于 "
                        f"{self.sched.last_started_at:%Y-%m-%d %H:%M}，下次计划按它 + 间隔推算"
                    )
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 读取上次生成时间失败（从当前时间起算）: {e}")
            # 上次进程退出时可能留下 status=running 的记录（图库里会永远「生成中」且理由为空）。
            # 这里只清「已经超过 15 分钟」的（热重载时旧实例可能真在画，别误伤）；
            # 更年轻的那批由调度循环的周期性兜底收尾（scheduler._sweep_stale）。
            stale = await self.store.fail_stale_running()
            if stale:
                logger.info(f"{PLUGIN_TAG} 已把 {stale} 条中断的旧记录收尾为失败（含中断原因）")
                self.debug_log("store", f"启动清理：{stale} 条中断记录已标记失败")
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
        for bg in list(self._bg_tasks):
            try:
                bg.cancel()
            except Exception:
                pass
        self._bg_tasks.clear()
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
        """投递会话名单（去重保序）：手改配置重复一行不该重复发。"""
        lst = self._delivery_cfg().get("sessions")
        if not isinstance(lst, list):
            return []
        out: list[str] = []
        for u in lst:
            s = str(u or "").strip()
            if s and s not in out:
                out.append(s)
        return out

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

    def reschedule(self) -> None:
        """按当前配置重算下次计划点。

        基准是**上一张的开始时刻**，不是「保存配置的时刻」——否则改一次间隔就
        等于把这一轮往后推一整个周期（v0.7.2 用户反馈）。
        """
        try:
            base = self.sched.last_started_at
            if base is None and self.sched.last_run_ts:
                base = datetime.fromtimestamp(int(self.sched.last_run_ts))
            self.sched.next_due = scheduler.due_from_last(
                self._schedule_cfg(), base, datetime.now(), _PICK_RANDOM
            )
            hint = f"以上一张 {base:%m-%d %H:%M} 为基准" if base else "无历史，从当前时间起算"
            note = f"已按新配置重算下次计划：{self.sched.next_due:%Y-%m-%d %H:%M}（{hint}）"
            logger.info(f"{PLUGIN_TAG} {note}")
            self.debug_log("schedule", note)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 重算下次计划失败: {e}")

    # ------------------------------------------------------------------ #
    # 调试日志（内存环形缓冲，不落盘）
    # ------------------------------------------------------------------ #

    def debug_log(self, kind: str, msg: str) -> None:
        """记一条调试事件（WebUI 调试页可见）。任何异常都吞掉，绝不影响主流程。"""
        try:
            self._logs.append({
                "ts": int(time.time()),
                "kind": str(kind or "info"),
                "msg": str(msg)[:500],
            })
        except Exception:
            pass

    def debug_logs(self, limit: int = 120) -> list:
        try:
            n = max(1, min(400, int(limit)))
        except (TypeError, ValueError):
            n = 120
        return list(self._logs)[-n:][::-1]

    # ------------------------------------------------------------------ #
    # 关联萌绘：只读它的配置（comfyui_servers），拿服务器地址
    # ------------------------------------------------------------------ #

    def _anima_star(self):
        try:
            return self.context.get_registered_star(ANIMA_PLUGIN_NAME)
        except Exception as e:
            logger.debug(f"{PLUGIN_TAG} 读取萌绘插件元数据失败: {e}")
            return None

    def _anima_cfg(self) -> dict:
        md = self._anima_star()
        cfg = getattr(md, "config", None) if md is not None else None
        return cfg if isinstance(cfg, dict) else {}

    def anima_info(self) -> dict:
        """萌绘联动快照：是否安装 / 版本 / 服务器列表 / 当前会用哪台。"""
        md = self._anima_star()
        info: dict = {
            "installed": md is not None,
            "activated": bool(getattr(md, "activated", False)) if md is not None else False,
            "version": str(getattr(md, "version", "") or "") if md is not None else "",
            "servers": [],
            "active_key": "",
            "active_url": "",
            "active_name": "",
            "error": "",
        }
        if md is None:
            return info
        cfg = self._anima_cfg()
        raw = cfg.get("comfyui_servers")
        servers: list[dict] = []
        if isinstance(raw, list):
            for i, s in enumerate(raw):
                if not isinstance(s, dict):
                    continue
                url = str(s.get("url") or "").strip()
                if not url:
                    continue
                servers.append({
                    "key": str(s.get("__template_key") or ""),
                    "name": str(s.get("name") or f"服务器{i + 1}"),
                    "url": url,
                    "enabled": bool(s.get("enabled")),
                    "device": str(s.get("device") or ""),
                })
        info["servers"] = servers
        if not servers:
            info["error"] = "萌绘里还没有可用的 ComfyUI 服务器（先去萌绘控制台配置）"
            return info
        want = str(self._sub("anima").get("server_key") or "").strip()
        chosen = None
        if want:
            for s in servers:
                if want in (s["key"], s["name"]):
                    chosen = s
                    break
        if chosen is None:
            chosen = next((s for s in servers if s["enabled"]), servers[0])
        info.update(
            active_key=chosen["key"],
            active_url=chosen["url"],
            active_name=chosen["name"],
        )
        return info

    def _comfy_url(self) -> "tuple[str, str]":
        """(生效地址, 来源 manual|anima)。follow_server 开启且萌绘有地址时用萌绘的。"""
        manual = str(self._sub("comfyui").get("url") or "http://127.0.0.1:8188").strip()
        manual = manual or "http://127.0.0.1:8188"
        if bool(self._sub("anima").get("follow_server")):
            info = self.anima_info()
            if info.get("active_url"):
                return str(info["active_url"]), "anima"
        return manual, "manual"

    # ------------------------------------------------------------------ #
    # 关联萌绘：用它的工作流（workflow_source = anima）
    # ------------------------------------------------------------------ #

    def _anima_instance(self):
        """萌绘插件实例（未激活时为 None）。只调用它的公开/半公开方法，失败一律降级。"""
        md = self._anima_star()
        if md is None or not getattr(md, "activated", False):
            return None
        return getattr(md, "star_cls", None)

    def _anima_workflow_cfgs(self) -> list:
        inst = self._anima_instance()
        if inst is None:
            return []
        try:
            ws = inst._workflows() or []
        except Exception as e:
            logger.debug(f"{PLUGIN_TAG} 读取萌绘工作流列表失败: {e}")
            return []
        return [w for w in ws if isinstance(w, dict)]

    def anima_workflows(self) -> dict:
        """列出萌绘的出图工作流 + 其关联基础工作流的解析状态。"""
        md = self._anima_star()
        inst = self._anima_instance()
        acfg = self._sub("anima")
        out: dict = {
            "installed": md is not None,
            "activated": inst is not None,
            "version": str(getattr(md, "version", "") or "") if md is not None else "",
            "error": "",
            "workflows": [],
            "selected": str(acfg.get("workflow") or ""),
            "source": str(self._cfg().get("workflow_source") or "local"),
        }
        if inst is None:
            out["error"] = "萌绘未安装或未激活"
            return out
        store = getattr(inst, "workflow_store", None)
        cfgs = self._anima_workflow_cfgs()
        if not cfgs:
            out["error"] = "萌绘里还没有工作流（去萌绘控制台的工作流页新建一个）"
            return out
        for i, w in enumerate(cfgs):
            bid = str(w.get("base_id") or "").strip()
            rec = None
            if store is not None and bid.isdigit():
                try:
                    rec = store.get(int(bid))
                except Exception:
                    rec = None
            roles = (rec or {}).get("roles") or {}
            parse_ok = bool(rec) and bool(rec.get("parse_ok"))
            reason = ""
            if not bid:
                reason = "旧版工作流（未关联基础工作流），暂不支持"
            elif not rec:
                reason = "关联的基础工作流已被删除"
            elif not parse_ok:
                reason = str(rec.get("parse_msg") or "基础工作流解析未通过")
            out["workflows"].append({
                "key": str(w.get("workflow_id") or ""),
                "name": str(w.get("name") or f"工作流{i + 1}"),
                "enabled": bool(w.get("enabled", True)),
                "base_id": bid,
                "base_name": str((rec or {}).get("name") or ""),
                "model_file": str(roles.get("model_file") or ""),
                "parse_ok": parse_ok,
                "supported": bool(bid) and parse_ok,
                "reason": reason,
            })
        # 多选轮换：picked = 当前参与的名字；usable = 符合条件可参与的总数
        out["usable"] = len(workflow_mgr.select_workflows(cfgs, ""))
        out["picked"] = [
            str(w.get("name") or "")
            for w in workflow_mgr.select_workflows(cfgs, acfg.get("workflow"))
        ]
        return out

    @staticmethod
    def _anima_template_failure(problem: str) -> list:
        return [workflow_mgr.Template(
            name="萌绘工作流", file="", source="anima", ok=False, problem=problem,
        )]

    def _anima_templates(self) -> list:
        """跟随萌绘：把选中的工作流各组装成一个 Template（可多选，随机/轮换）。

        `anima.workflow` 留空 = 萌绘里所有可用的新版工作流都参与。
        """
        inst = self._anima_instance()
        if inst is None:
            return self._anima_template_failure("萌绘未安装或未激活，无法使用它的工作流")
        cfgs = self._anima_workflow_cfgs()
        if not cfgs:
            return self._anima_template_failure("萌绘里还没有工作流（去萌绘控制台的工作流页新建）")
        raw = self._sub("anima").get("workflow")
        picked = workflow_mgr.select_workflows(cfgs, raw)
        if not picked:
            want = "、".join(workflow_mgr.split_names(raw))
            if want:
                why = f"萌绘里找不到可用工作流「{want}」（检查名字，或它已被停用/未关联基础工作流）"
            elif not any(str(w.get("base_id") or "").strip() for w in cfgs):
                why = "萌绘里的工作流都是旧版（未关联基础工作流），暂不支持"
            else:
                why = "萌绘里没有已启用的新版工作流（需要关联基础工作流并启用）"
            return self._anima_template_failure(why)
        out: list = []
        for w in picked:
            tpl = self._build_anima_template(inst, w)
            if tpl is not None:
                out.append(tpl)
        return out

    def _build_anima_template(self, inst, chosen: dict):
        """把萌绘的一条工作流组装成 Template（含它自己的覆盖参数）。

        优先用萌绘的运行时构造（`_load_from_base` + `_apply_base_overrides`），
        这样「固定种子 / 步数 / 采样器 / 放大 / 清理 / 保存格式」等覆盖与萌绘出图一致；
        这两个是半公开方法，改名时只降级为「只用基础工作流底图」，不会把插件带崩。
        """
        acfg = self._sub("anima")
        name = str(chosen.get("name") or "萌绘工作流").strip()
        bid = str(chosen.get("base_id") or "").strip()
        try:
            wf_ctx, prompt = inst._load_from_base(chosen)
        except Exception as e:
            return workflow_mgr.Template(
                name=name, file=f"anima:{bid or '?'}", source="anima", ok=False,
                problem=f"读取萌绘工作流失败：{e}",
            )
        try:
            inst._apply_base_overrides(wf_ctx, prompt)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 应用萌绘工作流覆盖失败（继续用底图原参数）: {e}")
        override = {
            "positive_node": wf_ctx.get("positive_node"),
            "positive_field": wf_ctx.get("positive_field"),
            "negative_node": wf_ctx.get("negative_node"),
            "negative_field": wf_ctx.get("negative_field"),
            "latent_node": wf_ctx.get("resolution_node"),
        }
        override = {k: str(v).strip() for k, v in override.items() if str(v or "").strip()}
        info = workflow_mgr.analyze(prompt, override)
        preset = str(acfg.get("size_preset") or "follow").strip() or "follow"
        if preset == "follow":
            sizes = workflow_mgr.follow_sizes(info.get("default_width"), info.get("default_height"))
        else:
            sizes = prompter.SIZE_PRESETS.get(preset, prompter.SIZE_PRESETS["sdxl"])

        # 提示词适配：优先按底模文件名识别；文件名认不出再看萌绘底模库的 is_anima 判定
        mode = str(acfg.get("prompt_profile") or "auto").strip() or "auto"
        profile = workflow_mgr.guess_profile(info.get("model_file", ""), info.get("model_class", ""))
        if profile.get("family") == "unknown":
            try:
                is_anima = wf_ctx.get("is_anima")
            except Exception:
                is_anima = None
            if is_anima is True:
                profile = workflow_mgr.profile_for_family("anime")
            elif is_anima is False:
                profile = workflow_mgr.profile_for_family("photoreal")
        if mode == "auto":
            tag_style = str(profile.get("tag_style") or "danbooru")
        else:
            tag_style = mode
        # 与本地库口径保持一致（workflow_mgr.load_templates）：danbooru 系即使认不出
        # 底模家族也要有质量词兜底，否则同样的模板换个来源就少一段质量词。
        quality = (
            str(profile.get("quality_prefix") or prompter.QUALITY_PREFIX_DANBOORU)
            if tag_style == "danbooru" else ""
        )
        # 逐条可画类型优先；没单独配的回落到整体开关 anima.subject_scope（默认 auto 按底模推断）
        overrides = workflow_mgr.parse_scope_overrides(acfg.get("workflow_scopes"))
        scope_cfg = workflow_mgr.workflow_scope_for(chosen, overrides, acfg.get("subject_scope"))
        # 萌绘多条工作流可能共用同一个 base_id，游标必须用工作流自己的 id 才不串位
        wid = str(chosen.get("workflow_id") or "").strip()
        return workflow_mgr.Template(
            name=name,
            file=f"anima:{bid or '?'}",
            tag_style=tag_style,
            size_preset=preset,
            default_negative="",
            enabled=True,
            wf=prompt,
            info=info,
            ok=not info.get("problems"),
            problem="；".join(info.get("problems") or []),
            sizes=sizes,
            source="anima",
            prompt_profile=mode,
            profile=profile,
            quality_prefix=quality,
            subject_scope=scope_cfg,
            scope=workflow_mgr.resolve_scope(scope_cfg, profile.get("family")),
            key=f"anima:{wid or bid or name}",
        )

    # ------------------------------------------------------------------ #
    # ComfyUI 客户端（按配置懒建；url/timeout 变了才重建）
    # ------------------------------------------------------------------ #

    def _get_client(self) -> "comfy_client.ComfyUIClient | None":
        sub = self._sub("comfyui")
        url, _src = self._comfy_url()
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
                self.spawn(self._close_quietly(old))
        return self._client

    def spawn(self, coro) -> "asyncio.Task | None":
        """起一个后台协程并**持有强引用**。

        asyncio 对 task 只持弱引用：`asyncio.ensure_future(...)` 的返回值不接住，
        task 可能在跑完前被 GC 掉，异常也只会变成 "Task was destroyed but it is
        pending"。这里统一记账，terminate 时一并取消。
        """
        try:
            task = asyncio.ensure_future(coro)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 后台任务创建失败: {errfmt.format_exc(e)}")
            return None
        self._bg_tasks.add(task)

        def _done(t: "asyncio.Task") -> None:
            self._bg_tasks.discard(t)
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            except Exception:
                return
            if exc is not None:
                logger.warning(f"{PLUGIN_TAG} 后台任务异常: {errfmt.format_exc(exc)}")

        task.add_done_callback(_done)
        return task

    @staticmethod
    async def _close_quietly(client) -> None:
        try:
            await client.close()
        except Exception:
            pass

    async def _comfy_status(self, max_age: float = 20.0) -> dict:
        """WebUI 状态页用：探活（6s 封顶，不阻塞页面）。

        `max_age` 内直接复用上次结果：控制台总览每 10 秒轮询一次，每次都真打两发
        HTTP 会把远端 ComfyUI 当靶子（还拖慢自己的状态接口）。调试页走
        `debug_comfy()`，那条永远真探。
        """
        url, src = self._comfy_url()
        now = time.time()
        cached = self._probe_cache
        if (
            max_age > 0
            and cached is not None
            and now - cached[0] <= max_age
            and str((cached[1] or {}).get("url") or "") == url
        ):
            return dict(cached[1])
        client = self._get_client()
        if client is None:
            return {"ok": False, "error": "客户端未初始化", "url": url, "source": src}
        try:
            res = await asyncio.wait_for(client.probe(), timeout=6)
        except asyncio.TimeoutError:
            res = {"ok": False, "elapsed_ms": 6000, "error": "探活超时（6s）"}
        except Exception as e:
            res = {"ok": False, "error": errfmt.format_exc(e)}
        res = dict(res or {})
        res["url"] = url
        res["source"] = src
        self._probe_cache = (now, res)
        return dict(res)

    async def debug_comfy(self, url: str = "", timeout_s: int = 8) -> dict:
        """调试：对指定地址（空 = 当前生效地址）探活 + 读队列，不写任何配置。"""
        url = str(url or "").strip()
        source = "manual"
        if not url:
            url, source = self._comfy_url()
        url = url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "url": url, "error": "地址需以 http:// 或 https:// 开头"}
        probe_client = comfy_client.ComfyUIClient(url, timeout=30, probe_timeout=timeout_s)
        out: dict = {"ok": False, "url": url, "source": source, "error": "", "queue": None}
        try:
            out.update(await probe_client.probe())
            try:
                q = await asyncio.wait_for(probe_client.get_queue(), timeout=timeout_s)
                out["queue"] = {
                    "running": len((q or {}).get("queue_running") or []),
                    "pending": len((q or {}).get("queue_pending") or []),
                }
            except Exception as e:
                out["queue_error"] = errfmt.format_exc(e)
        finally:
            await self._close_quietly(probe_client)
        self.debug_log("comfy", f"连通性测试 {url} → {'OK ' + str(out.get('elapsed_ms')) + 'ms' if out.get('ok') else 'FAIL ' + str(out.get('error'))}")
        return out

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

    async def _provider(self):
        """取提示词用的 LLM：配置里指定的 → 全局默认。

        v0.7.7：全局默认改走 `get_using_provider_async()`。`context.get_using_provider()`
        从 4.28 起标了 `@deprecated`，而控制台每 10 秒轮询一次 `/status`，等于持续踩
        废弃接口；老版本没有 async 版时再退回同步版。
        """
        pid = str(self._sub("llm").get("provider_id") or "").strip()
        if pid:
            try:
                p = self.context.get_provider_by_id(pid)
                if p is not None and hasattr(p, "text_chat"):
                    return p
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 按 provider_id 取模型失败（退回全局默认）: {e}")
        getter = getattr(self.context, "get_using_provider_async", None)
        if getter is not None:
            try:
                p = await getter(None)
                if p is not None:
                    return p
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 取全局默认提供商失败（async 版）: {e}")
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

    async def _llm_prompt(
        self, brief: dict, tpl: "workflow_mgr.Template",
        char: "dict | None" = None, ab_text: str = "", phase: str = "",
    ) -> tuple[dict, str, float]:
        """按简报生成 JSON 契约载荷；解析失败重试 1 次（更严指令）。

        返回 (载荷, provider标签, 耗时ms)。v0.7.9 新增：
        - `phase`：时段（写进系统提示词的硬规则 + 输出后扫矛盾）；
        - `char`：角色卡（danbooru 前置角色标签、负向合并排除词）；
        - `ab_text`：A/B 变体文本，追加在系统提示词末尾。
        """
        provider = await self._provider()
        if provider is None:
            raise RuntimeError("没有可用 LLM 提供商（先在 WebUI 配置好对话模型，或在本插件配置里指定 provider_id）")
        pid = str(self._sub("llm").get("provider_id") or "").strip()
        label = pid or "全局默认"
        opts = self._prompt_opts()
        # 工作流可画类型必须一路传到提示词：系统提示词的画风硬规则 + 出图后的
        # 硬拼接标签，两处都要用同一个值，否则又会「设了只画动漫却画出真人」。
        scope = str(getattr(tpl, "scope", "both") or "both")
        sys_prompt = prompter.build_system_prompt(
            tpl.tag_style, opts, scope, phase=phase,
            subject_kind=str(brief.get("subject_kind") or ""),
        )
        ab_text = str(ab_text or "").strip()
        if ab_text:
            sys_prompt = sys_prompt.rstrip() + "\n【本轮附加规则】\n" + ab_text + "\n"
        user_prompt = prompter.build_user_prompt(brief)

        quality = str(getattr(tpl, "quality_prefix", "") or "").strip()
        extra_q = str(opts.get("quality_extra") or "").strip()
        if extra_q:
            quality = f"{quality}, {extra_q}".strip(", ") if quality else extra_q

        def finalize(payload: dict) -> dict:
            """落地顺序（每一步只用前面已确定的字段，互不覆盖）。

            ① 按风格清洗：danbooru 解权重 / 转义括号 / 去重 / 去 artist / 裁标签，
               禁词从正向剔掉并写进负向；
            ② 人物锚定（补人物标签 + 排除负面词，画风锚定在它内部）；
            ③ 角色卡（danbooru 前置角色标签 + 负向）；
            ④ 质量词放最前（系统提示里承诺「由系统添加」，这里真正落地）；
            ⑤ 通用负面基线垫到负向最前（LLM 给空串时也有兜底）。
            """
            out = prompter.normalize_payload(payload, tpl.tag_style, opts)
            out = prompter.apply_persona(out, tpl.tag_style, brief, opts, scope)
            out = prompter.apply_character(out, char, tpl.tag_style)
            # 主体收口（v0.7.10）：动物加 animal focus + 反拟人词；人物加细节词
            out = prompter.apply_subject(out, brief, tpl.tag_style, opts)
            # 冲突压缩（v0.7.12）：七个光同时上 / simple background 与街景并存 /
            # banner 与 close up 打架 —— 按简报做减法，清掉的标签进负向；
            # v0.7.13：顺带剔除与画风锚定矛盾的词（动漫锚定下的 3d / photorealistic）
            out = prompter.reduce_conflicts(out, brief, tpl.tag_style, opts, scope=scope)
            out = prompter.apply_soft_color(out, tpl.tag_style, opts)
            out["prompt"] = prompter.apply_quality_prefix(out.get("prompt", ""), quality)
            out = prompter.apply_negative_base(out, tpl.tag_style, opts)
            out["_tag_count"] = len(prompter.split_tag_list(out.get("prompt", "")))
            return out

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
        payload["_raw"] = text[:4000]
        payload = finalize(payload)

        # 时间一致性兜底（v0.7.9）：简报已经按时段收窄了光色、系统提示词也写了硬规则，
        # 但模型仍可能写出「深夜 + 正午烈日」。命中矛盾就带着原因重写一次；再不合就
        # 保留原样（出图比卡在这一步重要），只记日志。
        if phase and self._time_guard():
            hits = daylight.conflicts(payload.get("prompt", ""), phase)
            if hits:
                logger.info(
                    f"{PLUGIN_TAG} 提示词与时段矛盾（{'、'.join(hits[:3])}），纠偏重写一次"
                )
                try:
                    fixed_text = await self._llm_text(
                        provider, sys_prompt,
                        user_prompt + "\n\n" + daylight.correct_hint(hits, phase),
                    )
                    fixed = prompter.parse_llm_payload(fixed_text)
                    fixed["_raw"] = fixed_text[:4000]
                    fixed["_time_fixed"] = 1
                    payload = finalize(fixed)
                except Exception as e:
                    logger.warning(
                        f"{PLUGIN_TAG} 时段纠偏失败（保留原提示词）: {errfmt.format_exc(e)}"
                    )
        return payload, label, (time.monotonic() - t0) * 1000

    async def debug_llm(self, theme: str = "") -> dict:
        """调试：真的跑一次「抽签 → LLM → 尺寸落值」，但不提交 ComfyUI、不写库。

        用来验证模型/提示词契约是否正常——图库里的失败记录只能看到结果，
        这里能看到简报、原始返回、耗时与落到的宽高。
        """
        t0 = time.monotonic()
        out: dict = {"ok": False, "error": "", "brief": None, "template": "", "tag_style": "",
                     "scope": "", "size": None, "prompt": "", "negative": "", "caption": "",
                     "raw": "", "llm_provider": "", "llm_ms": 0, "total_ms": 0}
        try:
            self.reload_templates()
            mode = str(self._cfg().get("workflow_selection") or "random")
            last_key, legacy = await self._rotation_cursor(self.store) if self.store else ("", None)
            tpl, _ = workflow_mgr.pick_template(
                self._templates, mode, _PICK_RANDOM, last_key, legacy
            )
            phase = self.current_phase()
            popts = self._prompt_opts()
            brief = prompter.draw_brief(
                _PICK_RANDOM,
                disabled_categories=self._disabled_categories(),
                extra=self._theme_extra(),
                weights=self._category_weights(),
                scope=getattr(tpl, "scope", "both"),
                scopes=self._category_scopes(),
                phase=phase if self._light_sync() else "",
                person_closeup=config_coerce.truthy(popts.get("person_closeup", True), True),
                animal_cats=set(prompter.split_tag_list(popts.get("animal_categories"))),
                style_allow_3d=config_coerce.truthy(popts.get("style_allow_3d", False), False),
                style_none_pct=self._int_of(
                    self._sub("theme"), "style_none_pct", prompter.DEFAULT_STYLE_NONE_PCT,
                    lo=0, hi=100,
                ),
            )
            args = prompter.parse_draw_args(theme)
            if args["theme"]:
                brief["category"] = "自定义"
                brief["subject"] = args["theme"][:50]
            if args["style"]:
                brief["style"] = args["style"]
            if args["orientation"]:
                brief["orientation"] = args["orientation"]
            char = await self._pick_character(args)
            if char:
                brief["character"] = str(char.get("name") or "")
            out["brief"] = brief
            out["phase"] = phase
            out["character"] = str((char or {}).get("name") or "")
            out["subject_kind"] = str(brief.get("subject_kind") or "")
            tag_style = tpl.tag_style if tpl else "danbooru"
            out["tag_style"] = tag_style
            out["scope"] = str(getattr(tpl, "scope", "both") or "both")
            out["template"] = tpl.name if tpl else "（无可用模板，仅测提示词）"
            sizes = tpl.sizes if tpl else prompter.SIZE_PRESETS["sdxl"]
            payload, label, ms = await self._llm_prompt(
                brief, tpl or workflow_mgr.Template(name="", file=""), char=char, phase=phase
            )
            width, height = prompter.pick_size(
                sizes, brief["orientation"], brief["composition"], _PICK_RANDOM,
                ratio=args.get("ratio"),
            )
            out.update(
                ok=True, prompt=payload.get("prompt", ""), negative=payload.get("negative", ""),
                caption=payload.get("caption", ""), raw=payload.get("_raw", ""),
                llm_provider=label, llm_ms=round(ms, 1), size={"width": width, "height": height},
                tag_count=int(payload.get("_tag_count") or 0),
                args_note=str(args.get("error") or ""),
                time_fixed=bool(payload.get("_time_fixed")),
                dropped=list(payload.get("_dropped") or []),
            )
            self.debug_log("llm", f"试跑成功（{label}，{ms / 1000:.1f}s，{width}x{height}）")
        except Exception as e:
            out["error"] = errfmt.truncate(errfmt.format_exc(e), 400)
            self.debug_log("llm", f"试跑失败：{out['error']}")
        out["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return out

    async def debug_delivery(self, umo: str, text: str = "") -> dict:
        """调试：往指定会话发一条测试消息（不写记录、不进投递名单）。"""
        umo = str(umo or "").strip()
        if not umo:
            return {"ok": False, "error": "请填目标会话 UMO（如 aiocqhttp:GroupMessage:123456）"}
        body = str(text or "").strip() or "萌萌绘卷 · 投递自检消息（收到说明投递链路正常）"
        try:
            okd = bool(await StarTools.send_message(umo, MessageChain(chain=[Plain(body)])))
        except Exception as e:
            err = errfmt.truncate(errfmt.format_exc(e), 200)
            self.debug_log("delivery", f"测试投递 {umo} 失败：{err}")
            return {"ok": False, "error": err}
        self.debug_log("delivery", f"测试投递 {umo} → {'成功' if okd else '未找到平台实例'}")
        return {"ok": okd, "error": "" if okd else "未找到匹配的平台实例（检查 UMO 的平台 id）"}

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
        """按 workflow_source 装配模板：local = 本插件工作流库；anima = 用萌绘的工作流。"""
        source = str(self._cfg().get("workflow_source") or "local").strip()
        if source == "anima":
            tpls = self._anima_templates()
            self._templates = tpls
            bad = [t for t in tpls if not t.ok]
            if bad:
                logger.warning(
                    f"{PLUGIN_TAG} 萌绘工作流问题：{'; '.join(f'{t.name}: {t.problem}' for t in bad)}"
                )
            pool = workflow_mgr.enabled_pool(tpls)
            if not pool:
                logger.warning(f"{PLUGIN_TAG} 跟随萌绘模式下没有可用工作流（检查来源与选择）")
            else:
                mode = str(self._cfg().get("workflow_selection") or "random")
                logger.info(
                    f"{PLUGIN_TAG} 模板来源：萌绘工作流 {len(pool)} 个"
                    f"（{'、'.join(t.name for t in pool)}），选择方式 {mode}"
                )
            return
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

    def templates_for_ui(self) -> list:
        """控制台展示用的模板快照：**不重载**，只在从未加载过时补一次。

        为什么不能每次重载：`/status` 被前端每 10 秒轮询一次，而重载要读工作流
        JSON；跟随萌绘时还要走 `_load_from_base()`，那个方法内部每次都写一条
        「【底模】…」日志——用户只要开着总览页，AstrBot 日志就被刷成流水。
        配置变更（保存/上传）与 `/workflows` 自己会调 `reload_templates()`。
        """
        if not self._templates:
            try:
                self.reload_templates()
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 模板首次加载失败: {e}")
        return self._templates

    # ------------------------------------------------------------------ #
    # 顺序轮换：游标（记「上一张是谁」）与控制台视图
    # ------------------------------------------------------------------ #

    def _manual_advances(self) -> bool:
        """手动出图（「立即画一张」/ 指令）是否占用一个轮换位；默认不占。"""
        v = self._cfg().get("rotation_manual_advances", False)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "on", "yes")

    def should_advance_rotation(self, reason: str, mode: str, has_tpl: bool) -> bool:
        """这一轮出图成功后是否推进顺序轮换游标（纯判定，便于自检）。

        指令用 reason="manual"，WebUI/跨插件用 "manual:xxx"，两者都算手动。
        失败轮在调用方根本不会走到「写游标」那一步，故这里只管来源与模式。
        """
        if str(mode or "") != "round_robin" or not has_tpl:
            return False
        is_manual = str(reason or "").strip().lower().startswith("manual")
        return not is_manual or self._manual_advances()

    async def _rotation_cursor(self, store) -> "tuple[str, int | None]":
        """读轮换游标：新名字游标优先；老整数游标只在升级后第一轮作过渡。"""
        last = str(await store.get_meta("rr_last", "") or "").strip()
        if last:
            return last, None
        raw = await store.get_meta("rr_cursor", "")
        if not str(raw or "").strip():
            return "", None  # 全新安装，没有老游标
        try:
            return "", int(str(raw))
        except ValueError:
            return "", 0

    async def rotation_state(self) -> dict:
        """当前轮换视图（只读、不推进游标）；调用方需先 reload_templates()。"""
        mode = str(self._cfg().get("workflow_selection") or "random")
        if self.store is None:
            last, legacy = "", None
        else:
            last, legacy = await self._rotation_cursor(self.store)
        out = workflow_mgr.rotation_state(self._templates, mode, last, legacy)
        out["manual_advances"] = self._manual_advances()
        return out

    async def reset_rotation(self) -> dict:
        """把顺序轮换重置到 #1（清空名字游标与老整数游标）。"""
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        await self.store.set_meta("rr_last", "")
        await self.store.set_meta("rr_cursor", "0")
        self.reload_templates()
        self.debug_log("workflow", "顺序轮换已重置到 #1")
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # 工作流上传 / 解析（WebUI）
    # ------------------------------------------------------------------ #

    def preview_workflow(self, text: str) -> dict:
        """只解析不入库：给上传预览用。"""
        wf, errors = workflow_mgr.parse_upload(str(text or ""))
        if wf is None:
            return {"ok": False, "errors": errors, "problems": [], "warnings": [],
                    "nodes": {}, "node_count": 0}
        info = workflow_mgr.analyze(wf)
        return {
            "ok": not errors and not info.get("problems"),
            "errors": errors,
            "problems": info.get("problems") or [],
            "warnings": info.get("warnings") or [],
            "nodes": {
                "positive": info.get("positive", ""),
                "negative": info.get("negative", ""),
                "latent": info.get("latent", ""),
                "latent_class": info.get("latent_class", ""),
                "save": info.get("save", ""),
                "save_class": info.get("save_class", ""),
                "model_file": info.get("model_file", ""),
                "default_width": info.get("default_width") or 0,
                "default_height": info.get("default_height") or 0,
                "seed_nodes": len(info.get("seed_nodes") or []),
                "kind": info.get("kind", "t2i"),
            },
            "node_count": len(wf),
        }

    def import_workflow(
        self,
        name: str,
        text: str,
        tag_style: str = "danbooru",
        size_preset: str = "sdxl",
        default_negative: str = "",
        enabled: bool = True,
        prompt_profile: str = "auto",
        subject_scope: str = "auto",
    ) -> dict:
        """上传入库：解析 → 落盘 workflows/<名>.json → 更新清单 → 重载。"""
        name = str(name or "").strip()[:60]
        if not name:
            return {"ok": False, "error": "模板名不能为空"}
        if self.workflows_dir is None:
            return {"ok": False, "error": "工作流目录未初始化（插件数据目录异常）"}
        preview = self.preview_workflow(text)
        if preview.get("problems"):
            return {"ok": False, "error": "；".join(preview["problems"]), "problems": preview["problems"]}
        if not preview.get("node_count"):
            return {"ok": False, "error": (preview.get("errors") or ["工作流为空"])[0]}
        wf, errors = workflow_mgr.parse_upload(str(text or ""))
        if wf is None:
            return {"ok": False, "error": errors[0] if errors else "解析失败"}
        try:
            fname = workflow_mgr.save_workflow_file(self.workflows_dir, name, wf)
        except Exception as e:
            return {"ok": False, "error": f"写入工作流文件失败：{e}"}
        entry = {
            "name": name,
            "file": fname,
            "tag_style": str(tag_style or "danbooru"),
            "size_preset": str(size_preset or "sdxl"),
            "prompt_profile": str(prompt_profile or "auto"),
            "subject_scope": str(subject_scope or "auto"),
            "default_negative": str(default_negative or "").strip(),
            "enabled": bool(enabled),
        }
        cfg = self.config
        if cfg is None:
            return {"ok": False, "error": "插件配置不可写"}
        entries = cfg.get("workflow_templates")
        entries = entries if isinstance(entries, list) else []
        # 记下同名条目的旧文件名：改名后旧 JSON 会变成没人引用的死文件
        old_file = ""
        for e in entries:
            if isinstance(e, dict) and str(e.get("name") or "").strip() == name:
                old_file = str(e.get("file") or "").strip()
                break
        new_entries, replaced = workflow_mgr.upsert_entry(entries, entry)
        cfg["workflow_templates"] = new_entries
        try:
            cfg.save_config()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 工作流清单落盘失败: {e}")
            return {"ok": False, "error": f"清单已改内存但落盘失败：{e}"}
        try:
            self.reload_templates()
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 模板重载失败: {e}")
        self._drop_orphan_workflow_file(old_file, fname, new_entries)
        logger.info(f"{PLUGIN_TAG} 工作流已入库「{name}」→ {fname}（{'覆盖' if replaced else '新增'}）")
        self.debug_log("workflow", f"上传工作流「{name}」（{fname}，{'覆盖' if replaced else '新增'}）")
        return {
            "ok": True,
            "name": name,
            "file": fname,
            "replaced": replaced,
            "nodes": preview.get("nodes") or {},
            "warnings": preview.get("warnings") or [],
        }

    def _drop_orphan_workflow_file(self, old_file: str, new_file: str, entries: list) -> None:
        """删掉「改名后被落下、且没有任何条目再引用」的旧工作流文件。

        只处理这一条改名留下的那个文件，绝不按「未被引用」批量清理——README 允许
        用户手动把 API JSON 丢进 workflows/ 再登记，批量清会直接删掉用户素材。
        内置起步工作流也不删（删了下次启动又会从 builtin/ 复制回来，白折腾）。
        """
        if self.workflows_dir is None:
            return
        old = str(old_file or "").strip()
        new = str(new_file or "").strip()
        if not old:
            return
        if not old.endswith(".json"):
            old = f"{old}.json"
        if new and not new.endswith(".json"):
            new = f"{new}.json"
        if old == new or old == workflow_mgr.BUILTIN_WORKFLOW_FILE:
            return
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            f = str(e.get("file") or "").strip()
            if f and not f.endswith(".json"):
                f = f"{f}.json"
            if f == old:
                return
        try:
            (self.workflows_dir / old).unlink()
        except FileNotFoundError:
            return
        except OSError as e:
            logger.warning(f"{PLUGIN_TAG} 清理旧工作流文件失败（忽略）: {e}")
            return
        logger.info(f"{PLUGIN_TAG} 已清理改名后不再引用的工作流文件: {old}")

    # ------------------------------------------------------------------ #
    # 时间一致性 / 角色卡 / A-B（v0.7.9）
    # ------------------------------------------------------------------ #

    def _time_cfg(self) -> dict:
        return self._sub("time_sense")

    def _time_enabled(self) -> bool:
        return config_coerce.truthy(self._time_cfg().get("enabled", True), True)

    def _time_guard(self) -> bool:
        """出图前扫矛盾的开关（关掉就只靠简报与系统提示词约束）。"""
        return self._time_enabled() and config_coerce.truthy(
            self._time_cfg().get("guard_retry", True), True
        )

    def _light_sync(self) -> bool:
        """光色池是否跟时段走（关掉可以随时画白天）。"""
        return self._time_enabled() and config_coerce.truthy(
            self._time_cfg().get("sync_light", True), True
        )

    def current_phase(self) -> str:
        """当前时段 key（deep_night / dawn / …）；时间一致性关掉时返回空串。"""
        if not self._time_enabled():
            return ""
        try:
            lat = float(self._time_cfg().get("latitude") or 35.0)
        except (TypeError, ValueError):
            lat = 35.0
        try:
            return daylight.phase_of(datetime.now(), lat)
        except Exception:
            return ""

    async def list_characters(self) -> list:
        if self.store is None:
            return []
        out: list[dict] = []
        try:
            rows = await self.store.list_meta(CHAR_PREFIX)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 读取角色卡失败: {e}")
            return []
        for key, val in rows:
            try:
                data = json.loads(val or "{}")
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get("name"):
                continue
            out.append({
                "name": str(data.get("name") or ""),
                "aliases": [str(a) for a in (data.get("aliases") or []) if str(a).strip()],
                "tags": str(data.get("tags") or ""),
                "negative": str(data.get("negative") or ""),
                "note": str(data.get("note") or ""),
            })
        out.sort(key=lambda x: x["name"])
        return out

    async def save_character(self, card: dict) -> dict:
        """新建/覆盖一张角色卡（按 name 存 meta，改名会另存一份）。"""
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        card = card if isinstance(card, dict) else {}
        name = str(card.get("name") or "").strip()[:40]
        if not name:
            return {"ok": False, "error": "角色名不能为空"}
        aliases = card.get("aliases")
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.replace("，", ",").split(",")]
        aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        payload = {
            "name": name,
            "aliases": aliases[:8],
            "tags": str(card.get("tags") or "").strip(),
            "negative": str(card.get("negative") or "").strip(),
            "note": str(card.get("note") or "").strip()[:200],
        }
        await self.store.set_meta(
            f"{CHAR_PREFIX}{name}", json.dumps(payload, ensure_ascii=False)
        )
        self.debug_log("character", f"已保存角色卡「{name}」")
        return {"ok": True, "name": name}

    async def delete_character(self, name: str) -> dict:
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        key = f"{CHAR_PREFIX}{str(name or '').strip()}"
        if len(key) <= len(CHAR_PREFIX):
            return {"ok": False, "error": "角色名不能为空"}
        await self.store.delete_meta(key)
        return {"ok": True}

    async def _pick_character(self, args: dict) -> "dict | None":
        """本轮用哪张角色卡：--char 优先，其次配置的固定角色，最后随机。"""
        cfg = self._sub("character")
        want = str((args or {}).get("char") or "").strip()
        mode = str(cfg.get("mode") or "off").strip().lower()
        if not want and mode == "fixed":
            want = str(cfg.get("name") or "").strip()
        cards = await self.list_characters()
        if not cards:
            return None
        if want:
            for c in cards:
                if want == c["name"] or want in (c.get("aliases") or []):
                    return c
            self.debug_log("character", f"找不到角色卡「{want}」（按本轮无角色处理）")
            return None
        if mode == "random":
            return _PICK_RANDOM.choice(cards)
        return None

    async def _ab_variant(self, store) -> "tuple[str, str]":
        """A/B 提示词变体：返回 (标签, 变体文本)。按 meta 游标轮换，两边样本量接近。"""
        opts = self._prompt_opts()
        if not config_coerce.truthy(opts.get("ab_enabled", False), False):
            return "", ""
        a = str(opts.get("ab_a") or "").strip()
        b = str(opts.get("ab_b") or "").strip()
        if not a or not b:
            return "", ""
        try:
            cur = str(await store.get_meta("ab_cursor", "A") or "A").strip().upper()
        except Exception:
            cur = "A"
        nxt = "B" if cur == "A" else "A"
        try:
            await store.set_meta("ab_cursor", nxt)
        except Exception:
            pass
        return (cur if cur in ("A", "B") else "A"), (a if cur == "A" else b)

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

    def trigger_manual(self, theme: str = "", source: str = "webui", template: str = "") -> bool:
        """异步触发一张（WebUI/跨插件用，立即返回；结果进图库并按名单投递）。

        `template` 非空时用指定工作流（控制台「立即画一张」旁边的下拉），
        留空仍按 `workflow_selection` 的随机/顺序规则挑。
        """
        if self._gen_lock.locked():
            return False
        return self.spawn(
            self.generate_one(reason=f"manual:{source}", theme=theme, template=template)
        ) is not None

    # ------------------------------------------------------------------ #
    # 重画 / 提示词收藏（v0.7.8）
    # ------------------------------------------------------------------ #

    async def redraw_record(self, rec_id: int, mode: str = "variant") -> dict:
        """按已有记录重画：same = 同 seed（几乎复现），variant = 换 seed（同一提示词变体）。

        跳过 LLM 与抽签，直接用记录里的提示词/负面/尺寸 —— 这是调质量最常用的动作：
        看到一张不错的，想「再来几张」或「同样的构图换种子」。
        """
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        if self._gen_lock.locked():
            return {"ok": False, "error": "已有生成在进行"}
        rec = await self.store.get_record(int(rec_id))
        if rec is None:
            return {"ok": False, "error": "记录不存在"}
        if not str(rec.get("prompt") or "").strip():
            return {"ok": False, "error": "这条记录没有提示词（可能失败在 LLM 之前）"}
        same = str(mode or "").strip().lower() in ("same", "same_seed", "同种子")
        reuse = {
            "prompt": rec.get("prompt") or "",
            "negative": rec.get("negative") or "",
            "caption": rec.get("caption") or "",
            "width": rec.get("width") or 0,
            "height": rec.get("height") or 0,
            "seed": rec.get("seed") if same else None,
            "workflow": rec.get("workflow") or "",
        }
        task = self.spawn(self.generate_one(
            reason=f"redraw:{'same' if same else 'variant'}", reuse=reuse
        ))
        if task is None:
            return {"ok": False, "error": "后台任务创建失败"}
        return {"ok": True, "started": True, "same_seed": same}

    async def save_prompt_preset(self, rec_id: int, name: str) -> dict:
        """把某条记录的提示词存成可复用的收藏（存 meta，不占主表）。"""
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        name = str(name or "").strip()[:40]
        if not name:
            return {"ok": False, "error": "收藏名不能为空"}
        rec = await self.store.get_record(int(rec_id))
        if rec is None or not str(rec.get("prompt") or "").strip():
            return {"ok": False, "error": "这条记录没有提示词"}
        payload = {
            "prompt": rec.get("prompt") or "",
            "negative": rec.get("negative") or "",
            "caption": rec.get("caption") or "",
            "width": rec.get("width") or 0,
            "height": rec.get("height") or 0,
            "workflow": rec.get("workflow") or "",
        }
        await self.store.set_meta(
            f"{PRESET_PREFIX}{name}", json.dumps(payload, ensure_ascii=False)
        )
        self.debug_log("preset", f"已收藏提示词「{name}」")
        return {"ok": True, "name": name}

    async def list_prompt_presets(self) -> list:
        if self.store is None:
            return []
        out: list[dict] = []
        try:
            rows = await self.store.list_meta(PRESET_PREFIX)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 读取提示词收藏失败: {e}")
            return []
        for key, val in rows:
            try:
                data = json.loads(val or "{}")
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get("prompt"):
                continue
            out.append({
                "name": key[len(PRESET_PREFIX):],
                "prompt": str(data.get("prompt") or ""),
                "width": int(data.get("width") or 0),
                "height": int(data.get("height") or 0),
                "workflow": str(data.get("workflow") or ""),
            })
        out.sort(key=lambda x: x["name"])
        return out

    async def delete_prompt_preset(self, name: str) -> dict:
        if self.store is None:
            return {"ok": False, "error": "记录库不可用"}
        key = f"{PRESET_PREFIX}{str(name or '').strip()}"
        if len(key) <= len(PRESET_PREFIX):
            return {"ok": False, "error": "收藏名不能为空"}
        await self.store.delete_meta(key)
        return {"ok": True}

    async def draw_from_preset(self, name: str) -> dict:
        """用收藏的提示词直接画一张（换 seed）。"""
        want = str(name or "").strip()
        for it in await self.list_prompt_presets():
            if it["name"] != want:
                continue
            if self._gen_lock.locked():
                return {"ok": False, "error": "已有生成在进行"}
            reuse = {
                "prompt": it["prompt"], "negative": "", "caption": "",
                "width": it["width"], "height": it["height"],
                "seed": None, "workflow": it["workflow"],
            }
            # 负向词没进列表接口（避免响应过大），这里从 meta 里补
            try:
                raw = await self.store.get_meta(f"{PRESET_PREFIX}{want}")
                data = json.loads(raw or "{}")
                if isinstance(data, dict):
                    reuse["negative"] = str(data.get("negative") or "")
                    reuse["caption"] = str(data.get("caption") or "")
            except Exception:
                pass
            task = self.spawn(self.generate_one(reason="preset", reuse=reuse))
            if task is None:
                return {"ok": False, "error": "后台任务创建失败"}
            return {"ok": True, "started": True}
        return {"ok": False, "error": "找不到这个收藏"}

    async def generate_one(
        self, reason: str = "schedule", theme: str = "",
        event: "AstrMessageEvent | None" = None, reuse: "dict | None" = None,
        template: str = "",
    ) -> dict:
        """完整跑一轮：抽签 → 连拍/重画 → 选优 → 投递最佳。

        返回 {"ok": bool, "id": int, "error": str, "skipped": bool, "image_path": str,
        "shots": int, "score": int|None}。任何一张失败都落 failed 记录（图库可查死因），
        不往会话刷错误；只有选中的那张会投递。
        """
        if self._gen_lock.locked():
            return {"ok": False, "skipped": True, "error": "已有生成在进行", "id": 0}
        async with self._gen_lock:
            # 标记「正在生成」：调度循环在出图期间不会推进 next_due，UI 靠这个区分
            # 「正在画」与「卡住了」。放在 finally 里，取消/异常都不会留下假状态。
            self.sched.running_since = datetime.now()
            self.sched.running_note = str(reason or "")[:40]
            try:
                return await self._run_round(
                    reason=reason, theme=theme, event=event, reuse=reuse,
                    template_hint=template,
                )
            finally:
                self.sched.running_since = None
                self.sched.running_note = ""

    async def _run_round(self, reason: str, theme: str, event, reuse, template_hint: str = "") -> dict:
        """一轮 = 一次抽签 + 最多 N 张（连拍/低分重画）+ 投递最好那张。"""
        summary: dict = {
            "ok": False, "id": 0, "error": "", "skipped": False,
            "image_path": "", "shots": 0, "score": None,
        }
        store = self.store
        try:
            plan = await self._plan(reason, theme, reuse, template_hint)
            brief = plan["brief"]
            tpl = plan["tpl"]
            guard = self._guard_enabled()
            budget = self._shots() if guard else 1
            if guard:
                budget += self._score_retries()
            budget = max(1, min(5, budget))

            results: list[dict] = []
            carry = reuse
            for _i in range(budget):
                r = await self._generate(plan, carry)
                results.append(r)
                if not r.get("ok"):
                    break
                if carry is not None:
                    # 连拍/重画沿用同一份提示词，但必须换种子，否则出的是同一张
                    carry = {**carry, "seed": None}
                else:
                    carry = {
                        k: r.get(k)
                        for k in ("prompt", "negative", "caption", "width", "height")
                    }
                if not guard:
                    break
                score = r.get("score")
                if score is None or int(score) >= self._min_score():
                    break
                self.debug_log(
                    "quality",
                    f"#{r.get('id')} 质检 {score} 分 < {self._min_score()}，换种子重画",
                )

            oks = [r for r in results if r.get("ok")]
            if not oks:
                last = results[-1] if results else {}
                summary.update(
                    id=int(last.get("id") or 0),
                    error=str(last.get("error") or "生成失败"),
                )
                self.debug_log("error", f"本轮失败：{summary['error']}")
            else:
                # 有分按分，没分按先后（-id 让最早的排前面）
                best = max(
                    oks,
                    key=lambda r: (
                        r.get("score") if r.get("score") is not None else -1,
                        -int(r.get("id") or 0),
                    ),
                )
                summary.update(
                    ok=True,
                    id=int(best.get("id") or 0),
                    image_path=str(best.get("image_path") or ""),
                    relpath=str(best.get("relpath") or ""),
                    score=best.get("score"),
                    shots=len(results),
                )
                if plan.get("should_advance"):
                    try:
                        await store.set_meta("rr_last", plan.get("new_last") or "")
                    except Exception as e:
                        logger.warning(f"{PLUGIN_TAG} 轮换游标写入失败: {e}")
                if len(oks) > 1:
                    self.debug_log(
                        "generate",
                        f"连拍 {len(results)} 张，投递 #{best.get('id')}"
                        f"（质检 {best.get('score')}）",
                    )
                await self._deliver_best(best, brief, tpl, event)
            # 收尾：LRU 淘汰（低频，失败也做）
            try:
                max_mb = self._float_of(self._sub("gallery"), "max_total_mb", 1024.0)
                if summary["ok"]:
                    await gallery_store.prune_gallery(self.gallery_dir, store, max_mb)
            except Exception as e:
                logger.warning(f"{PLUGIN_TAG} 图库淘汰失败: {e}")
        except Exception as e:
            summary["error"] = errfmt.truncate(errfmt.format_exc(e), 300)
            logger.exception(f"{PLUGIN_TAG} 生成轮次异常")
        return summary

    async def _plan(self, reason: str, theme: str, reuse, template_hint: str = "") -> dict:
        """选模板 + 抽签（或从记录还原）。连拍与重画共用同一份计划。

        `template_hint`（控制台手动指定工作流）优先于轮换/随机；指定的模板不存在或
        被停用时退回正常挑选并记一条调试日志 —— 手动画一张不该因为选错名字就失败。
        """
        self.reload_templates()
        store = self.store
        if store is None:
            raise RuntimeError("记录库不可用（看初始化日志）")
        mode = str(self._cfg().get("workflow_selection") or "random")
        last_key, legacy = await self._rotation_cursor(store)
        tpl = self._template_by_name((reuse or {}).get("workflow") or "") if reuse else None
        hint = str(template_hint or "").strip()
        if tpl is None and hint:
            tpl = self._template_by_name(hint)
            if tpl is None:
                self.debug_log(
                    "generate",
                    f"指定的工作流「{hint}」不可用（未启用或已删除），本轮按原规则挑选",
                )
        new_last = last_key
        if tpl is None:
            tpl, new_last = workflow_mgr.pick_template(
                self._templates, mode, _PICK_RANDOM, last_key, legacy
            )
        if reuse:
            # 重画不占轮换位：用户只是想再来一张，不是「轮到下一张」
            return {
                "tpl": tpl, "brief": self._reuse_brief(reuse), "args": {},
                "reason": reason, "should_advance": False, "new_last": new_last,
                "character": None, "phase": "", "ab": "", "ab_text": "",
            }
        # 只有真的出完一张、且该轮允许推进时才落游标：失败轮与手动试画
        # （默认）都不占位置，定时计划严格按自己的节奏走。
        should_advance = self.should_advance_rotation(reason, mode, tpl is not None)
        # 主题词可能带开关：少女 --no blurry,watermark --ratio 3:2 --seed 42
        args = prompter.parse_draw_args(theme)
        if args.get("error"):
            self.debug_log("generate", f"指令参数有误（该部分已忽略）：{args['error']}")
        # 时段（时间一致性关掉时返回空串，行为与旧版一致）
        phase = self.current_phase()
        popts = self._prompt_opts()
        brief = prompter.draw_brief(
            _PICK_RANDOM,
            disabled_categories=self._disabled_categories(),
            extra=self._theme_extra(),
            weights=self._category_weights(),
            scope=getattr(tpl, "scope", "both"),
            scopes=self._category_scopes(),
            phase=phase if self._light_sync() else "",
            # v0.7.10：人物偏近景；动物类别可扩（自然生态默认在内）
            person_closeup=config_coerce.truthy(popts.get("person_closeup", True), True),
            animal_cats=set(prompter.split_tag_list(popts.get("animal_categories"))),
            # v0.7.13：动漫锚定下不抽 3D/CG 类风格（与负向 3d render 自相矛盾）
            style_allow_3d=config_coerce.truthy(popts.get("style_allow_3d", False), False),
            # v0.7.18：有比例的「不指定画风」，让底模走默认审美
            style_none_pct=self._int_of(
                self._sub("theme"), "style_none_pct", prompter.DEFAULT_STYLE_NONE_PCT,
                lo=0, hi=100,
            ),
        )
        if args["theme"]:
            brief["category"] = "自定义"
            brief["subject"] = args["theme"][:50]
        if args["style"]:
            brief["style"] = args["style"]
        if args["orientation"]:
            brief["orientation"] = args["orientation"]
        # 角色卡：--char 优先，其次配置的固定角色，最后随机；名字写进简报让 LLM 画对人
        char = await self._pick_character(args)
        if char:
            brief["character"] = str(char.get("name") or "")
        # A/B 变体（关掉时为空）
        ab, ab_text = await self._ab_variant(store)
        return {
            "tpl": tpl, "brief": brief, "args": args, "reason": reason,
            "should_advance": should_advance, "new_last": new_last,
            "character": char, "phase": phase, "ab": ab, "ab_text": ab_text,
        }

    async def _deliver_best(self, best: dict, brief: dict, tpl, event) -> None:
        """只投递选中的那一张；同轮其余记录留在图库，可手动重投。

        投递异常只记 `delivery_error`，绝不动记录状态（v0.7.7 的教训：投递失败会把
        刚写完的 success 改成 failed，图在库里却显示失败）。
        """
        rec_id = int(best.get("id") or 0)
        abs_path = str(best.get("image_path") or "")
        if not rec_id or not abs_path:
            return
        try:
            if event is not None:
                dcfg = self._delivery_cfg()
                cap = delivery.build_caption(
                    dcfg.get("caption_template"), str(best.get("caption") or ""),
                    str(brief.get("category") or ""), str(brief.get("subject") or ""),
                    tpl.name if tpl else "",
                    caption_on=config_coerce.truthy(dcfg.get("caption_on", True), True),
                )
                comps = ([Plain(cap)] if cap else []) + [Image.fromFileSystem(abs_path)]
                await event.send(MessageChain(chain=comps))
                await self.store.merge_delivered(
                    rec_id,
                    [{"umo": event.unified_msg_origin, "ok": True,
                      "ts": int(time.time()), "via": "command"}],
                )
            else:
                rec = await self.store.get_record(rec_id)
                await delivery.deliver_record(self, rec)
        except Exception as e:
            err = errfmt.truncate(errfmt.format_exc(e), 200)
            logger.warning(f"{PLUGIN_TAG} #{rec_id} 投递失败（图已入库，状态保持成功）: {err}")
            self.debug_log("delivery", f"#{rec_id} 投递失败：{err}")

    async def _generate(self, plan: dict, reuse: "dict | None" = None) -> dict:
        """出一张图：建记录 → 取提示词（或复用）→ 注入 → 提交 → 取图 → 体检 → 存档 → 打分。

        模板与创作简报由 `generate_one()` 统一决定（连拍与重画要复用同一份），这里只管
        「画出来一张」；投递与图库淘汰也在外层。这样一张失败不会拖累整轮，投递失败更
        不会把已经成功的记录改成失败（v0.7.7 的教训）。
        """
        t0 = time.monotonic()
        summary: dict = {
            "ok": False, "id": 0, "error": "", "skipped": False,
            "image_path": "", "relpath": "", "score": None,
            "prompt": "", "negative": "", "caption": "",
            "width": 0, "height": 0, "seed": None,
        }
        store = self.store
        try:
            if store is None:
                raise RuntimeError("记录库不可用（看初始化日志）")
            tpl = plan["tpl"]
            brief = plan["brief"]
            args = plan.get("args") or {}
            reason = str(plan.get("reason") or "")
            extra_negative = str(args.get("negative") or "").strip()

            rec_id = await store.insert_running(
                ts=int(time.time()), day=self._today(), reason=reason[:40],
                category=brief["category"],
                brief=json.dumps(brief, ensure_ascii=False),
                workflow=tpl.name if tpl else "",
                tag_style=tpl.tag_style if tpl else "",
            )
            summary["id"] = rec_id
            self.debug_log(
                "generate",
                f"#{rec_id} 开始（{reason}｜{brief['category']}·{brief['subject']}"
                f"｜模板 {tpl.name if tpl else '无'}）",
            )
            try:
                if tpl is None:
                    hint = "（检查配置 workflow_templates 与 data 目录）"
                    if str(self._cfg().get("workflow_source") or "local") == "anima":
                        why = self._templates[0].problem if self._templates else "萌绘里没有可用工作流"
                        hint = f"（跟随萌绘模式：{why}）"
                    raise RuntimeError("没有可用的工作流模板" + hint)

                if reuse:
                    # 图库「同 seed / 换 seed 重画」：完全跳过 LLM，保证是同一张画的变体
                    payload = {
                        "prompt": str(reuse.get("prompt") or ""),
                        "negative": str(reuse.get("negative") or ""),
                        "caption": str(reuse.get("caption") or ""),
                    }
                    prov_label, llm_ms = "复用", 0.0
                else:
                    payload, prov_label, llm_ms = await self._llm_prompt(
                        brief, tpl,
                        char=plan.get("character"),
                        ab_text=plan.get("ab_text") or "",
                        phase=plan.get("phase") or "",
                    )
                width = int(reuse.get("width") or 0) if reuse else 0
                height = int(reuse.get("height") or 0) if reuse else 0
                if width < 64 or height < 64:
                    width, height = prompter.pick_size(
                        tpl.sizes, str(brief.get("orientation") or "square"),
                        str(brief.get("composition") or ""), _PICK_RANDOM,
                        ratio=args.get("ratio"),
                    )
                negative = payload.get("negative") or tpl.default_negative
                if extra_negative:
                    negative = f"{negative}, {extra_negative}" if negative else extra_negative
                # v0.7.15：这类工作流（ZeroOut / CFG=1）本来就不吃负向，
                # inject 会跳过写入；这里只留一条日志，避免用户以为负向没生效是 bug
                if tpl.info.get("negative_disabled"):
                    self.debug_log(
                        "generate",
                        f"#{rec_id} 该模板不使用负向（{tpl.info.get('negative_note') or '无'}），已跳过注入",
                    )
                caption = payload.get("caption") or f"{brief['category']}·{brief['subject']}"
                seed = None
                if reuse and reuse.get("seed") is not None:
                    seed = abs(int(reuse["seed"])) % (2**63)
                if seed is None:
                    seed = int(args.get("seed") or 0) or _PICK_RANDOM.getrandbits(48)
                summary.update(
                    prompt=payload.get("prompt", ""), negative=negative, caption=caption,
                    width=width, height=height, seed=seed,
                )
                await store.update_generation(
                    rec_id, prompt=payload["prompt"], negative=negative, caption=caption,
                    width=width, height=height, seed=seed,
                    llm_provider=prov_label, llm_latency_ms=round(llm_ms, 1),
                    ab=str(plan.get("ab") or ""),
                )

                data = b""
                entry: dict = {}
                prompt_id = ""
                for try_i in range(2):
                    wf = workflow_mgr.inject(
                        tpl.wf, tpl.info, payload["prompt"], negative, width, height, seed,
                        overrides=getattr(tpl, "params", None),
                    )
                    # 留档：这一版才是真正提交给 ComfyUI 的图（空图重试会覆盖成最后一版）
                    await self._save_run_workflow(rec_id, "main", wf, template=tpl.name)
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
                    # 空图/全黑体检：ComfyUI 出错时常常给一张全黑 PNG，以前会当成功存档
                    blank = gallery_store.suspect_blank(data, entry.get("filename", ""))
                    if not blank:
                        break
                    if try_i == 0:
                        logger.warning(f"{PLUGIN_TAG} #{rec_id} 输出疑似无效（{blank}），换种子重试")
                        seed = _PICK_RANDOM.getrandbits(48)
                        summary["seed"] = seed
                        await store.update_generation(rec_id, seed=seed)
                        continue
                    raise RuntimeError(f"输出疑似无效：{blank}（已重试一次）")

                # 精修 pass（可选，默认关）：失败就用原图，绝不阻断出图
                data, refine_note = await self._maybe_refine(
                    data, entry.get("filename", ""), payload["prompt"], negative, seed, rec_id
                )
                ext = gallery_store.ext_for(entry["filename"])
                if refine_note:
                    # 精修图固定是 SaveImage 产出的 PNG，别沿用原图的扩展名
                    ext = ".png"
                abs_path, rel, sha16, _dedup = gallery_store.archive_image(self.gallery_dir, data, ext)
                gen_ms = (time.monotonic() - t0) * 1000
                await store.update_generation(
                    rec_id, status="success", comfy_prompt_id=prompt_id,
                    image_relpath=rel, sha256=sha16, gen_latency_ms=round(gen_ms, 1), error="",
                )
                summary.update(ok=True, id=rec_id, image_path=abs_path, relpath=rel)
                logger.info(
                    f"{PLUGIN_TAG} 生成成功 #{rec_id}：{brief['category']}·{brief['subject']} "
                    f"（{tpl.name} {width}x{height} seed={seed}，{gen_ms / 1000:.1f}s"
                    f"{'，' + refine_note if refine_note else ''}）"
                )
                self.debug_log(
                    "generate",
                    f"#{rec_id} 成功（{tpl.name} {width}x{height}，总 {gen_ms / 1000:.1f}s，"
                    f"LLM {llm_ms / 1000:.1f}s）",
                )
                # 成图自检（可选）：这里只打分落库，「要不要重画」由 generate_one 决定
                if self._guard_enabled():
                    score, why = await self._score_image(
                        abs_path, brief, payload.get("prompt", "")
                    )
                    if score is not None:
                        summary["score"] = score
                        await store.update_generation(rec_id, score=score, score_reason=why)
                        self.debug_log("quality", f"#{rec_id} 质检 {score} 分 {why}".strip())
                    elif why:
                        self.debug_log("quality", f"#{rec_id} 质检跳过：{why}")
            except asyncio.CancelledError:
                # 被硬超时/插件卸载取消：必须落一行收尾，否则这条记录永远是「生成中」
                msg = "生成被取消（超时或插件重载），本轮没有结果"
                try:
                    await store.update_generation(
                        rec_id, status="failed", error=msg,
                        gen_latency_ms=round((time.monotonic() - t0) * 1000, 1),
                    )
                except Exception:
                    pass
                summary["error"] = msg
                logger.warning(f"{PLUGIN_TAG} 生成被取消 #{rec_id}")
                self.debug_log("error", f"#{rec_id} 已取消（超时/重载）")
                raise
            except Exception as e:
                summary["error"] = errfmt.truncate(errfmt.format_exc(e), 300)
                try:
                    await store.update_generation(
                        rec_id, status="failed", error=summary["error"],
                        gen_latency_ms=round((time.monotonic() - t0) * 1000, 1),
                    )
                except Exception:
                    pass
                logger.error(f"{PLUGIN_TAG} 生成失败 #{rec_id}: {summary['error']}")
                self.debug_log("error", f"#{rec_id} 失败：{summary['error']}")

        except Exception as e:
            # 走到这里说明连记录行都没建成功——只能日志留痕
            summary["error"] = errfmt.truncate(errfmt.format_exc(e), 300)
            logger.exception(f"{PLUGIN_TAG} 生成管线异常")
        return summary

    # ------------------------------------------------------------------ #
    # 画质闸门 / 连拍 / 精修（v0.7.8）
    # ------------------------------------------------------------------ #

    def _guard_cfg(self) -> dict:
        return self._sub("quality_guard")

    def _guard_enabled(self) -> bool:
        """成图自检开关。关掉时连拍强制为 1（不评分就没必要多拍）。"""
        return config_coerce.truthy(self._guard_cfg().get("enabled", False), False)

    def _shots(self) -> int:
        if not self._guard_enabled():
            return 1
        return self._int_of(self._guard_cfg(), "shots", 1, lo=1, hi=4)

    def _min_score(self) -> int:
        return self._int_of(self._guard_cfg(), "min_score", 60, lo=0, hi=100)

    def _score_retries(self) -> int:
        return self._int_of(self._guard_cfg(), "retry", 1, lo=0, hi=3)

    def _template_by_name(self, name: str):
        """按名字找当前可用模板（重画时优先回到原工作流，找不到再随便挑一个）。"""
        want = str(name or "").strip()
        if not want:
            return None
        for t in workflow_mgr.enabled_pool(self._templates):
            if str(t.name or "") == want:
                return t
        return None

    @staticmethod
    def _reuse_brief(rec: dict) -> dict:
        """从记录里还原创作简报；老记录缺字段时给一份最小可用结构。"""
        try:
            brief = json.loads(rec.get("brief") or "{}")
            if isinstance(brief, dict) and brief:
                # 老记录可能缺键：补齐下游会直接取值的那几个，别让重画 KeyError
                brief.setdefault("category", str(rec.get("category") or ""))
                brief.setdefault("subject", "")
                brief.setdefault("style", "")
                brief.setdefault("composition", "")
                brief.setdefault("orientation", "square")
                return brief
        except Exception:
            pass
        return {
            "category": str(rec.get("category") or ""), "subject": "", "style": "",
            "composition": "", "orientation": "square", "light": "", "mood": "",
            "scope": "both",
        }

    async def _score_image(self, abs_path: str, brief: dict, prompt: str) -> "tuple[int | None, str]":
        """用多模态模型给成品打分（0~100），返回 (分数, 理由/说明)。

        为什么值得多一次调用：定时出图没人盯着，崩手/糊图直接投进群里最伤口碑。
        评分只做粗筛（低于阈值换种子重画一次），不追求精确；模型不支持图片输入时
        会返回 (None, 原因)，调用方按「跳过质检」处理，不影响出图。
        """
        if not abs_path:
            return None, "没有图片路径"
        cfg = self._guard_cfg()
        provider = None
        pid = str(cfg.get("provider_id") or "").strip()
        try:
            if pid:
                p = self.context.get_provider_by_id(pid)
                if p is not None and hasattr(p, "text_chat"):
                    provider = p
            if provider is None:
                provider = await self._provider()
        except Exception as e:
            return None, f"取视觉模型失败：{errfmt.truncate(errfmt.format_exc(e), 120)}"
        if provider is None:
            return None, "没有可用的对话模型"
        sys_prompt = (
            "你是图像质检员。只输出一个 JSON 对象，禁止解释与代码围栏："
            '{"score": 0 到 100 的整数, "issues": ["中文短句，最多 3 条"]}。\n'
            "评分看四件事：① 结构是否崩坏（肢体、手指、五官、多手多脚、身体断裂）；"
            "② 是否清晰（糊成一团、大面积噪点、黑块）；③ 是否与描述相符（题材与主体）；"
            "④ 完成度（构图切割、水印文字、明显接缝）。"
            "90 以上=可直接发布；70~89=小瑕疵；60~69=明显缺陷但可辨认；60 以下=崩坏或与描述不符。"
        )
        user_prompt = (
            f"创作简报：{prompter.brief_text(brief)}\n"
            f"提示词：{str(prompt or '')[:900]}\n"
            "请按契约输出 JSON。"
        )
        timeout = self._int_of(cfg, "timeout", 45, lo=10, hi=180)
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=user_prompt, system_prompt=sys_prompt, image_urls=[abs_path],
                ),
                timeout=timeout,
            )
            text = str(getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            return None, f"质检调用失败：{errfmt.truncate(errfmt.format_exc(e), 160)}"
        data = prompter.parse_score_payload(text)
        if data is None:
            return None, f"质检输出不合契约：{text[:120]}"
        return int(data["score"]), str(data.get("issues") or "")

    def _debug_cfg(self) -> dict:
        return self._sub("debug")

    async def _save_run_workflow(
        self, rec_id: int, kind: str, wf: dict, template: str = ""
    ) -> None:
        """把这一轮真正提交给 ComfyUI 的工作流存下来（排障用）。

        为什么值得存：图出问题时「提示词看着没问题」是最常见的情况——真正被注入的
        正负向、尺寸、种子、采样参数都在图里，只有把提交图原样留档才看得出是模型、
        模板还是提示词的问题。保存失败一律忽略，绝不影响出图。
        """
        if self.store is None or not rec_id or not wf:
            return
        opts = self._debug_cfg()
        if not config_coerce.truthy(opts.get("save_workflows", True), True):
            return
        try:
            await self.store.save_run_workflow(
                int(rec_id), kind, wf, template=template,
                keep=self._int_of(opts, "keep_workflows", 200, lo=0, hi=100000),
            )
        except Exception as e:
            logger.debug(f"{PLUGIN_TAG} 保存工作流快照失败（忽略）: {errfmt.format_exc(e)}")

    def _refine_cfg(self) -> dict:
        return self._sub("refine")

    def _refine_enabled(self) -> bool:
        cfg = self._refine_cfg()
        return config_coerce.truthy(cfg.get("enabled", False), False) and bool(
            str(cfg.get("ckpt_name") or "").strip()
        )

    async def _maybe_refine(
        self, data: bytes, filename: str, positive: str, negative: str, seed: int,
        rec_id: int = 0,
    ) -> "tuple[bytes, str]":
        """放大 + 低 denoise 重绘（可选）。失败一律退回原图，绝不阻断出图。"""
        if not self._refine_enabled() or not data:
            return data, ""
        try:
            out = await self._run_refine(data, filename, positive, negative, seed, rec_id)
        except Exception as e:
            err = errfmt.truncate(errfmt.format_exc(e), 200)
            logger.warning(f"{PLUGIN_TAG} 精修失败（用原图）: {err}")
            self.debug_log("refine", f"精修失败（用原图）：{err}")
            return data, ""
        if not out:
            return data, ""
        self.debug_log("refine", f"精修完成（{len(out) // 1024}KB）")
        return out, "已精修"

    async def _run_refine(
        self, data: bytes, filename: str, positive: str, negative: str, seed: int,
        rec_id: int = 0,
    ) -> bytes:
        """上传成品图到 ComfyUI，按内置精修图跑一遍，返回新图字节。"""
        cfg = self._refine_cfg()
        client = self._get_client()
        if client is None:
            raise RuntimeError("ComfyUI 客户端不可用")
        name = str(filename or "moe_paint.png").strip() or "moe_paint.png"
        uploaded = await client.upload_image(data, name)
        image_name = str(uploaded.get("name") or name)
        wf = workflow_mgr.build_refine_workflow(
            image_name=image_name,
            ckpt_name=str(cfg.get("ckpt_name") or "").strip(),
            positive=positive,
            negative=negative,
            scale=float(cfg.get("scale") or 1.5),
            denoise=float(cfg.get("denoise") or 0.35),
            steps=self._int_of(cfg, "steps", 20, lo=4, hi=60),
            cfg_scale=float(cfg.get("cfg") or 5.0),
            seed=int(seed or 0),
            sampler=str(cfg.get("sampler_name") or "euler").strip() or "euler",
            scheduler=str(cfg.get("scheduler") or "normal").strip() or "normal",
        )
        await self._save_run_workflow(rec_id, "refine", wf, template="内置精修图")
        queued = await client.queue_prompt(wf)
        prompt_id = str(queued.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError(f"精修提交失败：{json.dumps(queued, ensure_ascii=False)[:160]}")
        timeout = self._int_of(cfg, "timeout", 300, lo=30, hi=1800)
        history = await client.wait_for_result(prompt_id, timeout, interval=2)
        if history is None:
            raise RuntimeError(f"精修等待超时（{timeout}s）")
        err = comfy_client.task_error(history)
        if err:
            raise RuntimeError(f"精修执行失败：{err}")
        imgs = comfy_client.extract_images(history)
        if not imgs:
            raise RuntimeError("精修没有输出图片")
        entry = imgs[0]
        return await client.get_image(
            entry["filename"], entry.get("subfolder", ""), entry.get("type", "output")
        )

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

    def _category_weights(self) -> dict:
        """类别权重（theme.category_weights，格式「类别=权重」）；没配 = 均匀。"""
        raw = self._sub("theme").get("category_weights")
        try:
            return prompter.parse_weights(raw)
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 类别权重解析失败（按均匀抽签）: {e}")
            return {}

    def _category_scopes(self) -> dict:
        """类别可画类型覆盖（theme.category_scopes，格式「类别=anime|photo|both」）。"""
        try:
            return prompter.parse_scopes(self._sub("theme").get("category_scopes"))
        except Exception as e:
            logger.warning(f"{PLUGIN_TAG} 类别能力解析失败（按内置映射）: {e}")
            return {}

    def _prompt_opts(self) -> dict:
        """提示词与人物锚定配置。老配置没有 prompt 分组时用代码默认值兜底。"""
        sub = self._cfg().get("prompt")
        if isinstance(sub, dict):
            return sub
        return {
            "persona_enabled": True,
            "persona_text": prompter.DEFAULT_PERSONA_TEXT,
            "persona_tags": prompter.DEFAULT_PERSONA_TAGS,
            "persona_negative": prompter.DEFAULT_PERSONA_NEGATIVE,
            "persona_negative_zh": prompter.DEFAULT_PERSONA_NEGATIVE_ZH,
            "extra_rules": "",
            "style_anchor_enabled": True,
        }

    def _theme_extra(self) -> dict:
        """词池扩充 + 角色名单，统一并成「类别 → 逗号分隔词」交给 draw_brief。

        角色名单放在配置里而不是代码里，用户删改某一行即刻生效；
        内置的通用角色原型仍在 CATEGORIES 里，不会被名单删掉。
        """
        cfg = self._sub("theme")
        raw = cfg.get("extra_words")
        extra = dict(raw) if isinstance(raw, dict) else {}
        names_raw = cfg.get("character_names")
        if names_raw is None:
            names_raw = prompter.DEFAULT_CHARACTER_NAMES
        for cat, words in prompter.parse_kv_words(names_raw).items():
            if not words:
                continue
            cur = str(extra.get(cat) or "").strip().strip(",")
            extra[cat] = (cur + "," if cur else "") + ",".join(words)
        return extra

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

    def manual_draw_gate(self, is_admin: bool, umo: str, now: float = 0.0) -> "tuple[bool, str]":
        """指令「画一张」的准入判定（纯判定，便于自检）：管理员开关 + 按会话冷却。

        为什么要有：这个指令会独占生成锁几十秒到几分钟，而调度器遇到锁占用是把
        整轮定时任务顺延掉一个间隔（scheduler 的「上一张仍在生成」分支）。没有闸门
        等于任何群成员都能刷图并把定时计划挤掉。
        冷却按会话 UMO 记账，不是全局：群里两个人同时点只该拦第二个。
        """
        if config_coerce.truthy(self._cfg().get("manual_draw_admin_only", True), True) and not is_admin:
            return False, "「/绘卷 画一张」仅管理员可用（想放开请到控制台「生成服务」页关掉对应开关）"
        cooldown = self._int_of(self._cfg(), "manual_draw_cooldown_sec", 60, lo=0, hi=3600)
        if cooldown <= 0:
            return True, ""
        ts = float(now or time.time())
        last = float(self._manual_last.get(str(umo)) or 0)
        if last and ts - last < cooldown:
            wait = int(cooldown - (ts - last)) + 1
            return False, f"手动出图冷却中，再等 {wait} 秒（控制台可调）"
        return True, ""

    def mark_manual_draw(self, umo: str) -> None:
        try:
            self._manual_last[str(umo)] = time.time()
        except Exception:
            pass

    async def _manual_draw(self, event: AstrMessageEvent, theme: str) -> None:
        allowed, why = self.manual_draw_gate(bool(event.is_admin()), event.unified_msg_origin)
        if not allowed:
            await self._send_text(event, why)
            return
        if self._gen_lock.locked():
            await self._send_text(event, "上一张还在生成中，稍等它画完再试。")
            return
        self.mark_manual_draw(event.unified_msg_origin)
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
        prov_ok = (await self._provider()) is not None
        lines.append(f"LLM：{pid or '全局默认提供商'}（{'已找到' if prov_ok else '未找到！'}）")
        await self._send_text(event, "\n".join(lines))

    def _schedule_desc(self) -> str:
        scfg = self._schedule_cfg()
        if str(scfg.get("mode") or "interval") == "daily":
            desc = f"每天 {scfg.get('daily_times')}"
        else:
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
            minutes = minutes or 360
            if minutes % 1440 == 0:
                span = f"每 {minutes // 1440} 天"
            elif minutes % 60 == 0:
                span = f"每 {minutes // 60} 小时"
            else:
                span = f"每 {minutes} 分钟"
            jitter = self._int_of(scfg, "jitter_minutes", 0, lo=0, hi=1440)
            # jitter 只加不减（scheduler.interval_next），文案必须跟着实现走，
            # 写「±N」会让人以为会往前浮动。
            desc = f"{span}" + (f"（+0~{jitter} 分钟）" if jitter else "")
        if scheduler.quiet_span_of(scfg) is not None:
            desc += f"，安静时段 {scfg.get('quiet_hours')} 跳过"
        return desc
