"""gallery_store 自检：建库 / 记录 CRUD / 投递合并 / 归档去重 / LRU。跑法：
uv run --no-project --with aiosqlite python tests/test_store.py（退出码即结论）
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from _loader import load

gallery_store = load("gallery_store")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = gallery_store.Store(str(root / "t.db"))
        await store.init()

        # 插入 running → 更新成功
        rid = await store.insert_running(
            ts=1000, day="2026-11-06", reason="schedule", category="宇宙星空",
            brief='{"category":"宇宙星空"}', workflow="anima 默认", tag_style="danbooru",
        )
        check("insert_id", rid > 0)
        rec = await store.get_record(rid)
        check("insert_running_status", rec["status"] == "running")
        await store.update_generation(
            rid, status="success", prompt="1girl, night", negative="lowres",
            width=1216, height=832, seed=123, image_relpath="2026-11/aaa.png",
            sha256="aaa", caption="银河", gen_latency_ms=1234.5,
        )
        rec = await store.get_record(rid)
        check("update_success", rec["status"] == "success" and rec["width"] == 1216)
        check("update_brief_kept", rec["brief"].startswith('{"category"'))

        # 投递合并（同 umo 覆盖）
        await store.merge_delivered(rid, [{"umo": "a:GroupMessage:1", "ok": True}])
        await store.merge_delivered(rid, [{"umo": "a:GroupMessage:1", "ok": True},
                                          {"umo": "b:GroupMessage:2", "ok": False}])
        rec = await store.get_record(rid)
        delivered = rec["delivered_json"]
        check("merge_delivered", delivered.count("umo") == 2, delivered)

        # 过滤列表
        rid2 = await store.insert_running(ts=1001, day="2026-11-06", reason="manual",
                                          category="风景场景", brief="{}", workflow="wf2",
                                          tag_style="natural")
        await store.update_generation(rid2, status="failed", error="boom")
        rows, total = await store.list_records(status="success")
        check("list_filter_status", total == 1 and rows[0]["id"] == rid)
        rows, total = await store.list_records(category="宇宙星空")
        check("list_filter_category", total == 1)
        rows, total = await store.list_records(keyword="银河")
        check("list_keyword", total == 1)
        rows, total = await store.list_records(offset=1, limit=1)
        check("list_paging", total == 2 and len(rows) == 1 and rows[0]["id"] == rid,
              f"total={total} id={rows[0]['id'] if rows else '?'}")

        # 统计口径：running 不进分母
        stats = await store.stats()
        check("stats_rate", stats["success_rate"] == 0.5, str(stats["success_rate"]))
        check("stats_by_category", any(c["category"] == "宇宙星空" and c["success"] == 1 for c in stats["by_category"]))
        check("stats_days", stats["days"][0]["day"] == "2026-11-06")

        # 收藏 & LRU 保护
        await store.set_starred(rid, True)
        check("starred_relpaths", "2026-11/aaa.png" in await store.starred_relpaths())
        await store.set_starred(rid, False)
        check("starred_relpaths_off", await store.starred_relpaths() == set())

        # count_relpath
        await store.update_generation(rid2, image_relpath="2026-11/aaa.png")
        check("count_relpath", await store.count_relpath("2026-11/aaa.png", exclude_id=rid2) == 1)
        check("count_relpath_excl", await store.count_relpath("2026-11/aaa.png", exclude_id=rid) == 1)

        # meta
        await store.set_meta("rr_cursor", "3")
        check("meta_roundtrip", await store.get_meta("rr_cursor") == "3")
        check("meta_default", await store.get_meta("none", "x") == "x")

        # v0.7.2：重启后要靠它恢复「上一张是什么时候」
        last_ts = await store.last_generation_ts()
        check("last_ts_positive", last_ts >= 1000, str(last_ts))

        # 归档：内容寻址去重
        gdir = root / "gallery"
        data = b"png-bytes-0001"
        abs1, rel1, sha1, dedup1 = gallery_store.archive_image(gdir, data, ".png")
        abs2, rel2, sha2, dedup2 = gallery_store.archive_image(gdir, data, ".png")
        check("archive_path_shape", rel1.startswith("2026-") and rel1.endswith(".png"), rel1)
        check("archive_dedup", dedup1 is False and dedup2 is True and rel1 == rel2)
        check("archive_abs_exists", Path(abs1).is_file())

        # v0.7.7：跨月去重（内容寻址不能每月各存一份）
        old_dir = gdir / "2020-01"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_path = old_dir / Path(rel1).name
        Path(abs1).replace(old_path)
        _abs3, rel3, _sha3, dedup3 = gallery_store.archive_image(gdir, data, ".png")
        check("archive_dedup_across_month", dedup3 is True and rel3 == f"2020-01/{Path(rel1).name}",
              f"rel3={rel3} dedup={dedup3}")
        check("archive_no_duplicate_file", not (gdir / rel1).is_file())
        check("ext_for_safe", gallery_store.ext_for("x.exe") == ".png" and gallery_store.ext_for("a.webp") == ".webp")
        check("mime_for", gallery_store.mime_for(".webp") == "image/webp")

        # 删除（共享文件不删）
        got = await store.delete_record(rid2)
        check("delete_returns_row", got is not None and got["id"] == rid2)
        check("count_after_delete", await store.count_relpath("2026-11/aaa.png") == 1)

        # v0.7.0：失败但没给理由 → 兜底写一句（图库里不能再出现空白报错）
        rNoReason = await store.insert_running(ts=1500, day="2026-11-06", reason="t",
                                               category="c", brief="{}", workflow="w",
                                               tag_style="danbooru")
        await store.update_generation(rNoReason, status="failed")
        rec = await store.get_record(rNoReason)
        check("failed_reason_fallback", bool(str(rec["error"] or "").strip()), repr(rec["error"]))

        # v0.7.0：陈旧 running 收尾（插件重启留下的「永远生成中」）
        stale_id = await store.insert_running(ts=100, day="2026-11-06", reason="t",
                                              category="c", brief="{}", workflow="w",
                                              tag_style="danbooru")
        fresh_id = await store.insert_running(ts=int(__import__("time").time()), day="2026-11-06",
                                              reason="t", category="c", brief="{}",
                                              workflow="w", tag_style="danbooru")
        n = await store.fail_stale_running(older_than_sec=900)
        stale = await store.get_record(stale_id)
        fresh = await store.get_record(fresh_id)
        check("stale_running_marked", n >= 1 and stale["status"] == "failed", f"n={n} {stale['status']}")
        check("stale_running_reason", "中断" in str(stale["error"]), str(stale["error"]))
        check("fresh_running_kept", fresh["status"] == "running", fresh["status"])

        # LRU：三个文件、一个收藏；cap≈1B → 非收藏全删，收藏存活
        gdir2 = root / "gallery2"
        _a1, f1, _s1, _ = gallery_store.archive_image(gdir2, b"file-1", ".png")
        _a2, f2, _s2, _ = gallery_store.archive_image(gdir2, b"file-2", ".png")
        _a3, f3, _s3, _ = gallery_store.archive_image(gdir2, b"file-3", ".webp")
        rA = await store.insert_running(ts=2000, day="2026-11-06", reason="t",
                                        category="c", brief="{}", workflow="w", tag_style="danbooru")
        rB = await store.insert_running(ts=2001, day="2026-11-06", reason="t",
                                        category="c", brief="{}", workflow="w", tag_style="danbooru")
        rC = await store.insert_running(ts=2002, day="2026-11-06", reason="t",
                                        category="c", brief="{}", workflow="w", tag_style="danbooru")
        await store.update_generation(rA, status="success", image_relpath=f1)
        await store.update_generation(rB, status="success", image_relpath=f2)
        await store.update_generation(rC, status="success", image_relpath=f3)
        await store.set_starred(rA, True)  # f1 收藏
        freed = await gallery_store.prune_gallery(gdir2, store, 0.000001)
        check("prune_removed", freed == 2, f"freed={freed}")
        check("prune_starred_survives", (gdir2 / f1).is_file())
        check("prune_unstarred_gone", not (gdir2 / f2).is_file() and not (gdir2 / f3).is_file())
        # v0.7.7：淘汰后必须把记录标成「已清理」，否则图库里是永远加载不了的破图
        rec_b = await store.get_record(rB)
        rec_a = await store.get_record(rA)
        check("prune_marks_record", rec_b["pruned"] == 1 and rec_b["image_relpath"] == "",
              f"pruned={rec_b['pruned']} rel={rec_b['image_relpath']!r}")
        check("prune_keeps_starred_record", rec_a["pruned"] == 0 and rec_a["image_relpath"] == f1,
              f"pruned={rec_a['pruned']} rel={rec_a['image_relpath']!r}")
        # cap 0 = 不淘汰
        _a4, f4, _s4, _ = gallery_store.archive_image(gdir2, b"file-4", ".png")
        freed0 = await gallery_store.prune_gallery(gdir2, store, 0)
        check("prune_cap0_disabled", freed0 == 0 and (gdir2 / f4).is_file())


asyncio.run(main())
print("store:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
