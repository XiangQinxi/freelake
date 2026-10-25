"""FreeLake 性能基准 · 查询热路径测量（bench/bench_queries.py）
============================================================

对 ``bench/seed.py`` 生成的数据集，**只通过 api.py 的公共函数**测量热路径
（唯一例外是第 9 项 EXPLAIN QUERY PLAN，需要 raw SQL 才能拿到执行计划）。

测量方式：每项 warmup 1 次 + 重复 5 次，报告 median / p95（ms）与返回行数。
结果写入 ``bench/baseline-current.json``（``--schema-version`` 标记 before/after），
同一脚本在模型重构后重跑即可对比。

用法::

    python bench/seed.py --scale 1                       # 1) 生成数据集
    python bench/bench_queries.py                        # 2) 测量并写 baseline-current.json
    # 重构成后：
    python bench/bench_queries.py --schema-version after --out bench/baseline-after.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import (  # noqa: E402
    PROJECT_ROOT,
    configure_stdout,
    prepare_env,
    read_manifest,
    resolve_data_dir,
)

DEFAULT_OUT = PROJECT_ROOT / "bench" / "baseline-current.json"


# ---------------------------------------------------------------- 统计工具


def p95(samples: list[float]) -> float:
    """nearest-rank 95 分位（小样本下≈最大值，避免插值造成的乐观偏差）。"""
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil(0.95 * len(ordered))
    return ordered[min(rank, len(ordered)) - 1]


def _consume(value):
    """把 peewee 的惰性查询（``.dicts()`` 返回未执行的 Query）就地物化。

    ``get_filtered_paginate`` / ``get_by_author`` 等返回的是**未执行**的查询对象，
    页面代码在迭代时才真正跑 SQL。计时必须把「执行 + 取行」算进去，否则会得到
    0.0x ms 的假数据。
    """
    if isinstance(value, (list, tuple, dict, int, float, str)) or value is None:
        return value
    return list(value)


def measure(fn, warmup: int, repeat: int) -> tuple[list[float], object]:
    """warmup 次预热 + repeat 次计时，返回 (毫秒样本, 最后一次返回值)。

    每次调用都经过 ``_consume``，确保惰性查询被真正执行。
    """

    def timed():
        return _consume(fn())

    last = None
    for _ in range(max(0, warmup)):
        last = timed()
    samples: list[float] = []
    for _ in range(max(1, repeat)):
        started = time.perf_counter()
        last = timed()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples, last


# ---------------------------------------------------------------- 主流程


class Runner:
    def __init__(self, api, *, warmup: int, repeat: int):
        self.api = api
        self.warmup = warmup
        self.repeat = repeat
        self.results: list[dict] = []

    def run(self, name: str, fn, *, rows=None, kind: str = "rows", note: str = "") -> dict:
        samples, last = measure(fn, self.warmup, self.repeat)
        row_count = rows(last) if callable(rows) else len(last)
        entry = {
            "name": name,
            "median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(p95(samples), 3),
            "rows": row_count,
            "rows_kind": kind,
            "min_ms": round(min(samples), 3),
            "max_ms": round(max(samples), 3),
            "samples_ms": [round(s, 3) for s in samples],
        }
        if note:
            entry["note"] = note
        self.results.append(entry)
        print(
            f"  {name:<34} median={entry['median_ms']:9.2f} ms  "
            f"p95={entry['p95_ms']:9.2f} ms  rows={row_count}"
        )
        return entry


def explain_query_plan(api, query, label: str) -> dict:
    """对 peewee 查询生成的真实 SQL 取 EXPLAIN QUERY PLAN（唯一允许的 raw SQL）。"""
    sql, params = query.sql()
    rows = api.db.execute_sql("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    plan = [row[3] for row in rows]  # (id, parent, notused, detail)
    return {"name": label, "sql": sql, "params": params, "plan": plan}


def print_plan(entry: dict) -> None:
    print(f"\n[EXPLAIN QUERY PLAN] {entry['name']}")
    for line in entry["plan"]:
        print(f"    {line}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="测量 FreeLake 数据层查询热路径（基准数据集由 bench/seed.py 生成）"
    )
    parser.add_argument("--data-dir", default=None, help="数据集目录（默认读 bench/.last_data_dir.json）")
    parser.add_argument(
        "--schema-version",
        default="before",
        choices=["before", "after"],
        help="结果标记：重构前 before（默认）/ 重构后 after",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="结果 JSON 输出路径")
    parser.add_argument("--warmup", type=int, default=1, help="预热次数（默认 1）")
    parser.add_argument("--repeat", type=int, default=5, help="计时重复次数（默认 5）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = parse_args(argv)

    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = (PROJECT_ROOT / out_path).resolve()

    data_dir = resolve_data_dir(args.data_dir)
    api = prepare_env(data_dir)  # chdir + 准备 config.toml + import api
    manifest = read_manifest(data_dir)
    fixtures = manifest["fixtures"]

    print("=" * 78)
    print("FreeLake 查询性能基准")
    print(f"  数据集目录 : {data_dir}")
    print(f"  schema     : {args.schema_version}")
    print(
        "  数据规模   : 用户 {users} / 文章 {posts} / 评论 {comments} / "
        "点赞 {likes} / 收藏 {bookmarks} / 举报 {reports}".format(**manifest["counts"])
    )
    print(f"  预热/重复  : {args.warmup} / {args.repeat}")

    # ---------------- 夹具准备 + 预检（确保筛选条件返回非空）
    tag = fixtures["tag"]
    start = dt.date.fromisoformat(fixtures["date_range"]["start"])
    end = dt.date.fromisoformat(fixtures["date_range"]["end"])
    hot_id = int(fixtures["hot_post_id"])
    heavy_author = fixtures["heavy_author"]
    bookmark_user = fixtures["bookmark_user"]
    page_size = 10

    post_total = api.Post.count()
    tag_count = api.Post.count_filtered(tag=tag)
    range_count = api.Post.count_filtered(start_date=start, end_date=end)
    hot_post = api.Post.get(hot_id)
    bookmark_ids = api.Bookmark.get_bookmarked_post_ids(bookmark_user)

    problems = []
    if post_total <= 0:
        problems.append("文章表为空（请先运行 bench/seed.py）")
    if tag_count <= 0:
        problems.append(f"标签「{tag}」命中 0 篇")
    if range_count <= 0:
        problems.append(f"日期区间 {start}~{end} 命中 0 篇")
    if not hot_post or len(hot_post.get("comments") or []) != fixtures["hot_post_comment_count"]:
        problems.append("热点文章详情夹具不符合预期")
    if len(bookmark_ids) < 100:
        problems.append(f"收藏夹具只有 {len(bookmark_ids)} 条（要求 ≥100）")
    if problems:
        raise SystemExit("基准预检失败：" + "；".join(problems))

    print(
        f"  夹具       : 热点文章 #{hot_id}（{len(hot_post['comments'])} 条评论）· "
        f"标签「{tag}」{tag_count} 篇 · 日期区间 {range_count} 篇 · "
        f"收藏用户 {bookmark_user}（{len(bookmark_ids)} 条）"
    )
    print("-" * 78)

    runner = Runner(api, warmup=args.warmup, repeat=args.repeat)

    # 1) 首页首屏（无筛选，按日期倒序）
    runner.run(
        "home_first_page",
        lambda: api.Post.get_filtered_paginate(page=1, page_size=page_size),
        note="Post.get_filtered_paginate(page=1, page_size=10)，无筛选/按日期",
    )

    # 2) 四类排序各一次
    for sort_by in ("date", "views", "likes", "bookmarks"):
        runner.run(
            f"sort_{sort_by}",
            lambda sort_by=sort_by: api.Post.get_filtered_paginate(
                page=1, page_size=page_size, sort_by=sort_by
            ),
            note=f"sort_by={sort_by}",
        )

    # 3) 标签筛选分页（重点）
    runner.run(
        "tag_filter_page1",
        lambda: api.Post.get_filtered_paginate(tag=tag, page=1, page_size=page_size),
        note=f"tag={tag}",
    )
    runner.run(
        "tag_filter_count",
        lambda: api.Post.count_filtered(tag=tag),
        rows=lambda value: value,
        kind="count",
        note="与上面列表同口径计数（分页总数）",
    )

    # 4) 日期区间筛选 + count_filtered（计数与列表一致性）
    runner.run(
        "date_range_list_page1",
        lambda: api.Post.get_filtered_paginate(
            start_date=start, end_date=end, page=1, page_size=page_size
        ),
        note=f"{start} ~ {end}",
    )
    runner.run(
        "date_range_count",
        lambda: api.Post.count_filtered(start_date=start, end_date=end),
        rows=lambda value: value,
        kind="count",
        note="日期区间计数",
    )
    # 一致性核对（不计时）：逐页取完，总行数应等于 count_filtered
    paged_total = 0
    page = 1
    while page <= 1000:
        rows = api.Post.get_filtered_paginate(
            start_date=start, end_date=end, page=page, page_size=100
        )
        if not rows:
            break
        paged_total += len(rows)
        page += 1
    consistency = {
        "date_range": {"start": str(start), "end": str(end)},
        "count_filtered": range_count,
        "paged_total": paged_total,
        "consistent": range_count == paged_total,
    }
    print(
        f"  [一致性] 日期区间 count_filtered={range_count}，逐页累计={paged_total} → "
        f"{'一致 ✓' if consistency['consistent'] else '不一致 ✗'}"
    )

    # 5) 详情页（该文章带 100 条评论）
    runner.run(
        "detail_hot_post",
        lambda: api.Post.get(hot_id),
        rows=lambda post: len(post["comments"]) if post else 0,
        note=f"post_id={hot_id}",
    )

    # 6) 个人主页（高产作者全部文章 + 统计）
    runner.run(
        "author_posts",
        lambda: api.Post.get_by_author(heavy_author),
        note=f"author={heavy_author}",
    )
    runner.run(
        "author_stats",
        lambda: api.Post.get_author_stats(heavy_author),
        rows=lambda stats: stats["post_count"],
        kind="count",
        note="个人主页 4 个指标",
    )

    # 7) 收藏列表（100+ 条）
    runner.run(
        "bookmarks_ids",
        lambda: api.Bookmark.get_bookmarked_post_ids(bookmark_user),
        note=f"user={bookmark_user}",
    )
    runner.run(
        "bookmarks_posts_100",
        lambda: api.Post.get_filtered_paginate(
            post_ids=bookmark_ids, page=1, page_size=100
        ),
        note="pages/bookmarks.py 的实际取数（post_ids + 一次取 100 篇）",
    )

    def bookmarks_page_total():
        ids = api.Bookmark.get_bookmarked_post_ids(bookmark_user)
        posts = api.Post.get_filtered_paginate(post_ids=ids, page=1, page_size=100)
        api.User.get_configs(p["authorid"] for p in posts)
        return posts

    runner.run(
        "bookmarks_page_total",
        bookmarks_page_total,
        note="收藏页整页（ids + 列表 + 作者配置）",
    )

    # 8) 管理后台：用户列表、举报列表
    runner.run("admin_user_list", api.User.get_all, note="pages/admin.py:77 User.get_all()")
    runner.run(
        "admin_report_list",
        lambda: api.Report.get_all(),
        note="pages/admin.py:386 Report.get_all(status=...)",
    )

    def admin_report_counts():
        return {
            "pending": api.Report.count("pending"),
            "handled": api.Report.count("handled"),
            "dismissed": api.Report.count("dismissed"),
        }

    runner.run(
        "admin_report_counts",
        admin_report_counts,
        rows=lambda stats: sum(stats.values()),
        kind="count",
        note="举报管理顶部三个计数",
    )

    # 附加：首页首屏完整开销（列表 + 每篇最新评论 + 作者配置，对应 pages/home.py）
    def home_first_screen_total():
        posts = api.Post.get_filtered_paginate(page=1, page_size=page_size)
        api.Post.get_last_comments(posts)
        api.User.get_configs(p["authorid"] for p in posts)
        return posts

    runner.run(
        "home_first_screen_total",
        home_first_screen_total,
        note="首页首屏整屏（列表 + 最新评论 + 作者配置）",
    )

    # 标签筛选整屏（带筛选时的首屏开销）
    def tag_screen_total():
        posts = api.Post.get_filtered_paginate(tag=tag, page=1, page_size=page_size)
        api.Post.get_last_comments(posts)
        api.User.get_configs(p["authorid"] for p in posts)
        return posts

    runner.run(
        "tag_filter_screen_total",
        tag_screen_total,
        note="标签筛选首屏整屏",
    )

    # 9) EXPLAIN QUERY PLAN：覆盖 after 版本的 9 个场景，前两条 label 与 before 相同以便对照
    prints = []
    date_order = (api._Post.created_at.desc(), api._Post.id.desc())

    def plan_of(query, label: str) -> None:
        prints.append(explain_query_plan(api, query, label))

    # ① 首页首屏（无筛选，日期倒序）—— 与 before 同名
    plan_of(
        api.Post._apply_filters(api._Post.select()).order_by(*date_order),
        "home_first_page（无筛选 + 日期倒序）",
    )
    # ② 标签筛选（重点）—— 与 before 同名
    plan_of(
        api.Post._apply_filters(api._Post.select(), tag=tag).order_by(*date_order),
        "tag_filter（标签筛选 + 日期倒序）",
    )
    # ③④⑤ 另外三种排序
    plan_of(
        api.Post._apply_filters(api._Post.select()).order_by(
            api._Post.views.desc(), api._Post.id.desc()
        ),
        "sort_views（按浏览量 + id 倒序）",
    )
    plan_of(
        api.Post._apply_filters(api._Post.select()).order_by(
            api._Post.like_count.desc(), api._Post.id.desc()
        ),
        "sort_likes（按点赞数 + id 倒序）",
    )
    plan_of(
        api.Post._apply_filters(api._Post.select()).order_by(
            api._Post.bookmark_count.desc(), api._Post.id.desc()
        ),
        "sort_bookmarks（按收藏数 + id 倒序）",
    )
    # ⑥ 日期区间筛选
    plan_of(
        api.Post._apply_filters(
            api._Post.select(), start_date=start, end_date=end
        ).order_by(*date_order),
        "date_range（日期区间 + 日期倒序）",
    )
    # ⑦ 个人主页（Post.get_by_author 的真实 SQL）
    plan_of(
        api._Post.select()
        .where(api._Post.author_id == heavy_author)
        .order_by(*date_order),
        f"author_page（author_id={heavy_author} + 日期倒序）",
    )
    # ⑧ 收藏页的 post_ids IN 查询
    plan_of(
        api.Post._apply_filters(api._Post.select(), post_ids=bookmark_ids).order_by(
            *date_order
        ),
        "bookmarks_in（收藏 post_ids IN + 日期倒序）",
    )

    for item in prints:
        print_plan(item)

    # ---------------- 汇总输出
    slowest = max(runner.results, key=lambda item: item["median_ms"])
    print("-" * 78)
    print("按 median 排序（慢 → 快）：")
    for item in sorted(runner.results, key=lambda item: item["median_ms"], reverse=True):
        print(
            f"  {item['median_ms']:9.2f} ms  (p95 {item['p95_ms']:9.2f} ms, "
            f"rows {item['rows']:>5})  {item['name']}"
        )

    payload = {
        "schema_version": args.schema_version,
        "scale": _as_number(manifest["scale"]),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {
            "data_dir": str(data_dir),
            "seed": manifest.get("seed"),
            "counts": manifest["counts"],
            "fixtures": fixtures,
        },
        "environment": {
            "python": platform.python_version(),
            "peewee": _module_version("peewee"),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "method": {
            "warmup": args.warmup,
            "repeat": args.repeat,
            "statistic": "median",
            "p95": "nearest-rank",
            "page_size": page_size,
            "note": "只通过 api.py 公共函数测量；EXPLAIN QUERY PLAN 为唯一 raw SQL",
        },
        "results": runner.results,
        "consistency": consistency,
        "explain_query_plan": prints,
        "reproduce": {
            "seed": f"python bench/seed.py --scale {manifest['scale']:g} --data-dir bench/.benchdata",
            "bench": (
                "python bench/bench_queries.py "
                f"--data-dir bench/.benchdata --schema-version {args.schema_version} "
                f"--out bench/{out_path.name}"
            ),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("-" * 78)
    print(f"最慢项：{slowest['name']} → median {slowest['median_ms']:.2f} ms")
    print(f"结果已写入：{out_path}")
    print("复现：python bench/seed.py --scale 1 --data-dir bench/.benchdata")
    print("      python bench/bench_queries.py")
    return 0


def _as_number(value):
    """1.0 → 1（JSON 里保留整数的可读性），其它浮点原样返回。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
        return getattr(module, "__version__", "unknown")
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
