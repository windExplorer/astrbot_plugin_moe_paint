"""ComfyUI HTTP 客户端：提交工作流、查询队列与历史、下载图片。

改编自 astrbot-comfyui-anima（萌绘）v7.7.x 的 comfyui_client.py（作者 windExplorer，
AGPL-3.0；本仓库同为 AGPL-3.0，文件头保留出处署名）。只保留文生图所需最小面：
探活 / 队列 / 提交 / 轮询历史 / 取图 / 错误提取，等待与轮询行为与原实现一致
（真实时间判超时、单次轮询短超时、超时临界点兜底再查一次）。
"""

import asyncio
import json
import logging
import time
import uuid

import aiohttp

logger = logging.getLogger("astrbot")


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        client_id: str | None = None,
        timeout: int = 180,
        probe_timeout: int = 15,
    ) -> None:
        self.probe_timeout = probe_timeout
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or f"astrbot-moe-paint-{uuid.uuid4().hex[:8]}"
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, *, timeout: float | None = None) -> dict:
        session = await self._session_get()
        _to = aiohttp.ClientTimeout(total=timeout) if timeout else None
        async with session.get(self.base_url + path, timeout=_to) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def probe(self) -> dict:
        """探测服务器连通性与延迟，返回 {"ok": bool, "elapsed_ms": int, "error": str}。

        与萌绘同口径：先预热一次（建连/DNS/TLS 不计时），再测同连接第二次请求的
        往返耗时；失败返回 ok=False，不抛异常。
        """
        start = time.monotonic()
        try:
            session = await self._session_get()
            async with session.get(
                self.base_url + "/", timeout=aiohttp.ClientTimeout(total=self.probe_timeout)
            ) as resp:
                resp.raise_for_status()
            s2 = time.monotonic()
            async with session.get(
                self.base_url + "/", timeout=aiohttp.ClientTimeout(total=self.probe_timeout)
            ) as resp2:
                resp2.raise_for_status()
            return {"ok": True, "elapsed_ms": int((time.monotonic() - s2) * 1000), "error": ""}
        except Exception as e:
            return {
                "ok": False,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "error": f"{type(e).__name__}: {e}",
            }

    async def get_queue(self) -> dict:
        """透传 /queue：{"queue_running": [...], "queue_pending": [...]}。"""
        return await self._get("/queue")

    async def queue_prompt(self, prompt: dict) -> dict:
        """提交工作流，返回 {"prompt_id": ..., "_queue_position": int|None}。

        _queue_position 来自中转站响应头 X-Queue-Position（直连时为 None，
        调用方回退用 /queue 待处理数估算等待预算）。

        4xx 时不直接 raise_for_status（那样只会得到「400, message='Bad Request'」）：
        ComfyUI 的校验失败原因全在响应体里（error / node_errors），必须读出来带给用户。
        """
        payload = {"prompt": prompt, "client_id": self.client_id}
        session = await self._session_get()
        async with session.post(self.base_url + "/prompt", json=payload) as resp:
            if resp.status >= 400:
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                raise RuntimeError(
                    f"ComfyUI 拒绝任务（HTTP {resp.status}）：{summarize_error_body(body)}"
                )
            data = await resp.json()
            if isinstance(data, dict):
                pos = resp.headers.get("X-Queue-Position")
                try:
                    data["_queue_position"] = (
                        int(str(pos).strip()) if pos and str(pos).strip() not in ("", "-1") else None
                    )
                except ValueError:
                    data["_queue_position"] = None
            return data

    async def get_history(self, prompt_id: str | None = None, *,
                          timeout: float | None = None) -> dict:
        """查询历史；有 id 走 /history/{id}（体积小、快）。"""
        if prompt_id:
            return await self._get(f"/history/{prompt_id}", timeout=timeout)
        return await self._get("/history", timeout=timeout)

    async def upload_image(
        self, data: bytes, filename: str, subfolder: str = "", overwrite: bool = True
    ) -> dict:
        """把图片上传到 ComfyUI 的 input 目录，返回 {"name", "subfolder", "type"}。

        用途：精修 pass 要把刚出的图喂回 ComfyUI（LoadImage 只能读 input 里的文件）。
        """
        session = await self._session_get()
        form = aiohttp.FormData()
        form.add_field(
            "image", data, filename=filename or "moe_paint.png", content_type="image/png"
        )
        form.add_field("overwrite", "true" if overwrite else "false")
        if subfolder:
            form.add_field("subfolder", subfolder)
        async with session.post(self.base_url + "/upload/image", data=form) as resp:
            if resp.status >= 400:
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                raise RuntimeError(
                    f"上传图片失败（HTTP {resp.status}）：{summarize_error_body(body)}"
                )
            payload = await resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("上传图片返回结构异常")
        return payload

    async def get_image(self, filename: str, subfolder: str, img_type: str) -> bytes:
        url = (
            f"{self.base_url}/view"
            f"?filename={filename}&subfolder={subfolder}&type={img_type}"
        )
        session = await self._session_get()
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def wait_for_result(
        self, prompt_id: str, timeout: int, interval: int = 2
    ) -> "dict | None":
        """轮询历史直到任务完成或超时；返回该 prompt_id 的历史条目，超时返回 None。

        真实时间判超时（只数 sleep 会把「请求本身很慢」漏掉）；单次轮询请求给
        短超时防挂死；超时临界点兜底再查一次（历史已持久化，可能刚写入）。
        """
        period = max(1, int(interval or 1))
        poll_to = float(min(30, max(5, period * 3)))
        start = time.monotonic()
        deadline = start + max(1, int(timeout))
        fails = 0
        while True:
            try:
                history = await self.get_history(prompt_id, timeout=poll_to)
                fails = 0
            except Exception as e:
                fails += 1
                if fails in (1, 5, 20, 50) or fails % 100 == 0:
                    logger.warning(
                        f"[萌萌绘卷] 轮询历史失败第 {fails} 次"
                        f"（已等 {time.monotonic() - start:.0f}s）: {type(e).__name__}: {e}"
                    )
                history = {}
            if prompt_id in history:
                return history[prompt_id]
            if time.monotonic() >= deadline:
                try:
                    final = await self.get_history(prompt_id, timeout=poll_to)
                except Exception:
                    final = {}
                if prompt_id in final:
                    return final[prompt_id]
                return None
            await asyncio.sleep(min(period, max(0.2, deadline - time.monotonic())))


def extract_images(history_entry: dict, output_node: str | None = None) -> list[dict]:
    """从历史条目提取输出图片列表（兼容 images/gifs/saved_images 等字段，
    兜底扫描任意含 filename 的列表——与萌绘 extract_images 同口径）。"""

    def _looks_like_images(lst):
        return bool(lst) and all(isinstance(x, dict) and "filename" in x for x in lst)

    def _imgs(out):
        if not isinstance(out, dict):
            return None
        for key in ("images", "gifs", "saved_images", "image", "imgs", "output"):
            v = out.get(key)
            if isinstance(v, list) and v:
                return v
        for v in out.values():
            if isinstance(v, list) and _looks_like_images(v):
                return v
        return None

    if not history_entry:
        return []
    outputs = history_entry.get("outputs", {})
    if output_node and output_node in outputs:
        imgs = _imgs(outputs[output_node])
        if imgs:
            return imgs
    for _nid, out in outputs.items():
        imgs = _imgs(out)
        if imgs:
            return imgs
    return []


def summarize_error_body(text: str, limit: int = 400) -> str:
    """从 ComfyUI 的错误响应体里抽出人话（error / node_errors）；抽不出就给原文摘要。

    ComfyUI 校验失败时返回形如
    {"error": {"type": "...", "message": "...", "details": "..."},
     "node_errors": {"12": {"errors": [{"message": "...", "details": "..."}]}}}
    直接 raise_for_status 会把这些全丢掉，用户只看到「400 Bad Request」。
    """
    raw = str(text or "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if isinstance(data, dict):
        parts: list[str] = []
        err = data.get("error")
        if isinstance(err, dict):
            for key in ("message", "details", "type"):
                val = str(err.get(key) or "").strip()
                if val:
                    parts.append(val)
        elif err:
            parts.append(str(err).strip())
        ne = data.get("node_errors")
        if isinstance(ne, dict):
            for nid, item in list(ne.items())[:3]:
                if not isinstance(item, dict):
                    continue
                class_type = str(item.get("class_type") or "").strip()
                for e in list(item.get("errors") or [])[:2]:
                    if not isinstance(e, dict):
                        continue
                    msg = str(e.get("message") or "").strip()
                    det = str(e.get("details") or "").strip()
                    head = f"节点 {nid}" + (f"（{class_type}）" if class_type else "")
                    line = f"{head}: {msg}{('，' + det) if det else ''}".strip()
                    if line:
                        parts.append(line)
        out = "；".join(p for p in parts if p).strip()
        if out:
            return out[:limit]
    return (raw[:limit] or "未知错误（响应体为空）")


def task_error(history_entry: dict) -> "str | None":
    """提取任务执行报错（history.status.messages 里的 execution_error），无错返回 None。

    取不到 exception_message 时也要给出可读理由（否则图库里就是空白失败原因）；
    status_str=error 但没有任何 message 时同样给一句带排查方向的话。
    """
    if not isinstance(history_entry, dict):
        return None
    status = history_entry.get("status") or {}
    msgs = status.get("messages") or []
    for m in msgs:
        if isinstance(m, (list, tuple)) and len(m) >= 2 and m[0] == "execution_error":
            detail = m[1]
            if isinstance(detail, dict):
                msg = str(detail.get("exception_message") or "").strip() or "ComfyUI 未返回异常信息"
                node = detail.get("node_id", "?")
                ntype = detail.get("node_type", "?")
                extra = ""
                node_errors = detail.get("node_errors")
                if isinstance(node_errors, dict):
                    for item in node_errors.values():
                        if not isinstance(item, dict):
                            continue
                        for e in list(item.get("errors") or [])[:1]:
                            if isinstance(e, dict):
                                extra = str(e.get("message") or e.get("details") or "").strip()
                        if extra:
                            break
                tail = f"｜{extra}" if extra else ""
                return f"{msg}（节点 {node}/{ntype}）{tail}"
    if str(status.get("status_str") or "").lower() == "error":
        return "ComfyUI 报告执行失败，但没有返回具体原因（先看 ComfyUI 控制台日志）"
    return None


def queue_pending(queue: dict) -> int:
    """从 /queue 返回估算待处理数（running + pending 长度）。"""
    if not isinstance(queue, dict):
        return 0
    n = 0
    for key in ("queue_running", "queue_pending"):
        v = queue.get(key)
        if isinstance(v, list):
            n += len(v)
    return n
