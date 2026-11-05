"""生成记录库（sqlite/aiosqlite 短连接）+ 图片归档（sha256 内容寻址）+ LRU 淘汰。

- 库文件放 plugin_data（StarTools 落位），绝不放插件安装目录（更新即清零）。
- 图片按 sha256 前 16 位内容寻址：同一张图重复生成不重占空间（萌绘 image_store 同思路）。
- LRU 淘汰按 mtime 从旧到新删「非收藏」文件，收藏（starred）永不淘汰。
- failed 行也入库（保留 brief/prompt/error）——图库能看「死因」，排 LLM 契约与
  模板问题的第一手材料。
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    day             TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'running',
    reason          TEXT    NOT NULL DEFAULT '',
    category        TEXT    NOT NULL DEFAULT '',
    brief           TEXT    NOT NULL DEFAULT '',
    prompt          TEXT    NOT NULL DEFAULT '',
    negative        TEXT    NOT NULL DEFAULT '',
    tag_style       TEXT    NOT NULL DEFAULT '',
    workflow        TEXT    NOT NULL DEFAULT '',
    width           INTEGER,
    height          INTEGER,
    seed            INTEGER,
    comfy_prompt_id TEXT    NOT NULL DEFAULT '',
    image_relpath   TEXT    NOT NULL DEFAULT '',
    sha256          TEXT    NOT NULL DEFAULT '',
    caption         TEXT    NOT NULL DEFAULT '',
    llm_provider    TEXT    NOT NULL DEFAULT '',
    llm_latency_ms  REAL,
    gen_latency_ms  REAL,
    error           TEXT    NOT NULL DEFAULT '',
    delivered_json  TEXT    NOT NULL DEFAULT '[]',
    starred         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gen_ts      ON generations(ts);
CREATE INDEX IF NOT EXISTS idx_gen_status  ON generations(status, ts);
CREATE INDEX IF NOT EXISTS idx_gen_wf      ON generations(workflow, ts);
CREATE INDEX IF NOT EXISTS idx_gen_day     ON generations(day);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL DEFAULT ''
);
"""

# 允许 update 的列（白名单，防注入拼列名）
_UPDATE_COLS = {
    "status", "reason", "category", "brief", "prompt", "negative", "tag_style",
    "workflow", "width", "height", "seed", "comfy_prompt_id", "image_relpath",
    "sha256", "caption", "llm_provider", "llm_latency_ms", "gen_latency_ms",
    "error", "delivered_json", "starred",
}

_MIME = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

_EXT_OK = re.compile(r"^[a-z0-9]{1,5}$")
_EXT_IMAGE = {"png", "webp", "jpg", "jpeg", "gif", "bmp", "avif"}


def ext_for(filename: str) -> str:
    """从 ComfyUI 输出文件名取安全扩展名（只认图片扩展名，其余一律 .png）。"""
    name = str(filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not (_EXT_OK.fullmatch(ext) and ext in _EXT_IMAGE):
        ext = "png"
    return f".{ext}"


def mime_for(ext: str) -> str:
    return _MIME.get(str(ext or "").lower(), "image/png")


def archive_image(gallery_dir: Path, data: bytes, ext: str) -> tuple[str, str, str, bool]:
    """落盘归档：返回 (绝对路径, 库内相对路径, sha256前16位, 是否重复内容)。

    相对路径形如 2026-11/ab12cd34ef56ab12.png（WebUI 用它拼 URL，投递用它拼绝对路径）。
    """
    ext = ext if _EXT_OK.fullmatch(ext.lstrip(".")) else ".png"
    sha = hashlib.sha256(data).hexdigest()
    short = sha[:16]
    month = time.strftime("%Y-%m")
    rel = f"{month}/{short}{ext}"
    target = gallery_dir / month
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{short}{ext}"
    dedup = path.is_file()
    if not dedup:
        tmp = target / f".tmp{os.getpid()}-{short}{ext}"
        tmp.write_bytes(data)
        os.replace(tmp, path)  # 原子替换，断电不留半个文件
    return str(path), rel, short, dedup


class Store:
    """generations 记录库。每次操作短连接（aiosqlite），无长连接状态。"""

    def __init__(self, db_path: str):
        self.path = str(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    # ---------------- 基础读写 ----------------

    async def insert_running(
        self, ts: int, day: str, reason: str, category: str, brief: str,
        workflow: str, tag_style: str,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO generations (ts, day, status, reason, category, brief,"
                " workflow, tag_style) VALUES (?,?,?,?,?,?,?,?)",
                (ts, day, "running", reason, category, brief, workflow, tag_style),
            )
            await db.commit()
            return int(cur.lastrowid or 0)

    async def update_generation(self, rec_id: int, **fields) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            if k in _UPDATE_COLS:
                cols.append(f"{k}=?")
                vals.append(v)
        if not cols:
            return
        vals.append(int(rec_id))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE generations SET {', '.join(cols)} WHERE id=?", vals)
            await db.commit()

    async def get_record(self, rec_id: int) -> "dict | None":
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM generations WHERE id=?", (int(rec_id),))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_record(self, rec_id: int) -> "dict | None":
        """删除记录并返回被删行（调用方负责删图片文件）。"""
        rec = await self.get_record(rec_id)
        if rec is None:
            return None
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM generations WHERE id=?", (int(rec_id),))
            await db.commit()
        return rec

    async def set_starred(self, rec_id: int, starred: bool) -> None:
        await self.update_generation(rec_id, starred=1 if starred else 0)

    async def count_relpath(self, relpath: str, exclude_id: int = 0) -> int:
        """同一内容寻址图片还被多少条**其他**记录引用（删除时决定能否 unlink 文件）。"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM generations WHERE image_relpath=? AND id<>?",
                (str(relpath), int(exclude_id)),
            )
            return int((await cur.fetchone())["n"])

    async def merge_delivered(self, rec_id: int, results: list) -> list:
        """把本次投递结果按 umo 合并进 delivered_json（重投覆盖同 umo 的旧结果）。"""
        rec = await self.get_record(rec_id)
        try:
            current = list(json.loads((rec or {}).get("delivered_json") or "[]"))
        except Exception:
            current = []
        by_umo = {item.get("umo"): item for item in current if isinstance(item, dict)}
        for item in results or []:
            if isinstance(item, dict) and item.get("umo"):
                by_umo[item["umo"]] = item
        merged = list(by_umo.values())
        await self.update_generation(
            rec_id, delivered_json=json.dumps(merged, ensure_ascii=False)
        )
        return merged

    # ---------------- 列表 / 统计 ----------------

    async def list_records(
        self, status: str = "", workflow: str = "", category: str = "",
        day: str = "", keyword: str = "", starred: int = -1,
        offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict], int]:
        where, vals = [], []
        if status:
            where.append("status=?")
            vals.append(status)
        if workflow:
            where.append("workflow=?")
            vals.append(workflow)
        if category:
            where.append("category=?")
            vals.append(category)
        if day:
            where.append("day=?")
            vals.append(day)
        if starred in (0, 1):
            where.append("starred=?")
            vals.append(starred)
        if keyword:
            where.append("(prompt LIKE ? OR caption LIKE ? OR brief LIKE ?)")
            like = f"%{keyword}%"
            vals.extend([like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT COUNT(*) AS n FROM generations{clause}", vals
            )
            total = int((await cur.fetchone())["n"])
            cur = await db.execute(
                f"SELECT * FROM generations{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                vals + [max(1, min(200, int(limit))), max(0, int(offset))],
            )
            rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    async def count_day_success(self, day: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM generations WHERE day=? AND status='success'",
                (day,),
            )
            return int((await cur.fetchone())["n"])

    async def counts(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT status, COUNT(*) AS n FROM generations GROUP BY status"
            )
            by_status = {r["status"]: int(r["n"]) for r in await cur.fetchall()}
        total = sum(by_status.values())
        return {
            "total": total,
            "success": by_status.get("success", 0),
            "failed": by_status.get("failed", 0),
            "running": by_status.get("running", 0),
        }

    async def stats(self) -> dict:
        """图库统计页：总数/成功率 + 类别与工作流分布 + 近 14 天产量。

        口径：成功率 = success / (success+failed)（running 不进分母——还没结论
        的样本不算失败也不算成功，model_panel stats_reader 的同款原则）。
        """
        base = await self.counts()
        denom = base["success"] + base["failed"]
        out = {
            **base,
            "success_rate": round(base["success"] / denom, 4) if denom else None,
            "by_category": [],
            "by_workflow": [],
            "days": [],
        }
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT category, "
                "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok_n, "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS fail_n "
                "FROM generations WHERE category<>'' GROUP BY category ORDER BY ok_n DESC"
            )
            out["by_category"] = [
                {"category": r["category"], "success": int(r["ok_n"]), "failed": int(r["fail_n"])}
                for r in await cur.fetchall()
            ]
            cur = await db.execute(
                "SELECT workflow, "
                "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok_n, "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS fail_n "
                "FROM generations WHERE workflow<>'' GROUP BY workflow ORDER BY ok_n DESC"
            )
            out["by_workflow"] = [
                {"workflow": r["workflow"], "success": int(r["ok_n"]), "failed": int(r["fail_n"])}
                for r in await cur.fetchall()
            ]
            cur = await db.execute(
                "SELECT day, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS n "
                "FROM generations WHERE day >= date('now', '-13 days') "
                "GROUP BY day ORDER BY day"
            )
            out["days"] = [{"day": r["day"], "success": int(r["n"])} for r in await cur.fetchall()]
        return out

    async def starred_relpaths(self) -> set:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT image_relpath FROM generations WHERE starred=1 AND image_relpath<>''"
            )
            return {r[0] for r in await cur.fetchall()}

    # ---------------- meta（游标等小状态，重启不丢） ----------------

    async def get_meta(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT v FROM meta WHERE k=?", (str(key),))
            row = await cur.fetchone()
            return str(row[0]) if row else default

    async def set_meta(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO meta (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (str(key), str(value)),
            )
            await db.commit()


async def prune_gallery(gallery_dir: Path, db: "Store", max_total_mb: float) -> int:
    """按总大小淘汰：非收藏、mtime 最旧的先删。返回删除的文件数（async，低频调用）。"""
    cap = max(0.0, float(max_total_mb or 0)) * 1024 * 1024
    if cap <= 0 or not gallery_dir.is_dir():
        return 0
    files: list[tuple[float, Path]] = []
    total = 0
    for p in gallery_dir.rglob("*"):
        if p.is_file() and not p.name.startswith(".tmp"):
            try:
                st = p.stat()
                files.append((st.st_mtime, p))
                total += st.st_size
            except OSError:
                continue
    if total <= cap:
        return 0
    try:
        starred = await db.starred_relpaths()
    except Exception:
        starred = set()  # 查不到收藏名单时不删（宁可不淘汰也不误删收藏）
        if files:
            return 0
    freed = 0
    for _mtime, p in sorted(files, key=lambda t: t[0]):
        if total <= cap:
            break
        rel = str(p.relative_to(gallery_dir)).replace("\\", "/")
        if rel in starred:
            continue
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            freed += 1
        except OSError:
            continue
    # 清掉空月份目录
    for d in gallery_dir.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    return freed
