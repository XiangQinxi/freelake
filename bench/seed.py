"""FreeLake 性能基准 · 合成数据集生成器（bench/seed.py）
=====================================================

在**受控目录**里生成一份可复现的合成数据集（默认系统临时目录），供
``bench/bench_queries.py`` 测量查询热路径。幂等：重复运行会删除旧
``data.db`` 后按同一随机种子重建，不会累加数据。

规模（``--scale 1``，``--scale 2`` 全部翻倍）：

| 表 | 数量 | 说明 |
| --- | --- | --- |
| _user | 500 | userid = u0001..；共用同一个 PBKDF2 密码哈希（省去 500 次 200k 迭代） |
| _post | 3000 | 日期覆盖 2026-01-01 ~ 2026-10-25，作者列 ``author_id`` |
| _tag / _post_tag | 18 / ~6000 | 每篇 1~3 个 ``const.tags`` 标签，走关联表（v2 无 JSON 列） |
| _comment | 20000 | 其中「热点文章」固定 100 条，``post_id`` 外键 |
| _like | 15000 | 复合主键 (post_id, user_id) |
| _bookmark | 10000 | 其中 u0001 固定 120 条收藏，供「收藏列表 100+ 条」使用 |
| _report | 2000 | pending/handled/dismissed ≈ 6:2:2；pending 受 partial unique index 约束 |

写入完成后用**一条 SQL** 回填 ``_post.like_count / bookmark_count / comment_count``
（等价于逐篇 ``Post.recount``，但快几个数量级）。

写法要点：
- 必须 chdir 到数据目录后再 import api（见 ``_bootstrap.prepare_env``）；
- 全部写入包在 ``db.atomic()`` 里，使用 peewee 的 ``insert_many`` 批量插入；
- 外键已开启（``PRAGMA foreign_keys=1``）：评论/点赞/收藏/标签关联必须在文章之后写；
- 只写数据目录下的文件，**不碰**仓库根目录的真实 ``data.db``。

用法::

    python bench/seed.py --scale 1                        # 写入临时目录并记录路径
    python bench/seed.py --scale 1 --data-dir bench/.benchdata   # 指定目录（推荐，可复用）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import (  # noqa: E402
    PROJECT_ROOT,
    configure_stdout,
    prepare_env,
    write_manifest,
)

# ---------------------------------------------------------------- 规模与常量

BASE_COUNTS = {
    "users": 500,
    "posts": 3000,
    "comments": 20000,
    "likes": 15000,
    "bookmarks": 10000,
    "reports": 2000,
}

DATE_START = dt.datetime(2026, 1, 1, 0, 0, 0)
DATE_END = dt.datetime(2026, 10, 25, 23, 59, 59)

# 固定夹具（bench_queries.py 通过 bench_manifest.json 读取，不靠 SQL 猜）
HOT_POST_INDEX_DIVISOR = 2  # 中间那篇文章作为「详情页」夹具
HOT_POST_COMMENT_COUNT = 100
HEAVY_AUTHOR = "u0001"  # 高产作者（同时作为收藏用户）
HEAVY_AUTHOR_EVERY = 25  # 每 25 篇有 1 篇由高产作者发布
BOOKMARK_USER = "u0001"
BOOKMARK_USER_TARGET = 120
BENCH_TAG = "技术分享"  # 标签筛选基准用（必须存在于 const.tags）
BENCH_DATE_RANGE = ("2026-03-01", "2026-03-31")

PRINT_PREFIX = "[seed]"

TOPICS = [
    "图书馆四楼自习位实测",
    "食堂三楼新窗口测评",
    "期末复习资料互换",
    "校园网提速后的体验",
    "校门口煎饼摊排队实录",
    "体育馆羽毛球场预约攻略",
    "社团招新面试复盘",
    "宿舍楼热水供应问题",
    "春季运动会报名通知",
    "二手自行车交易避坑",
    "考研自习室占座观察",
    "实习秋招投递时间线",
    "Python 小工具分享",
    "SQLite 索引踩坑记录",
    "校园卡补办流程",
    "选课系统崩溃现场",
    "雨伞丢失寻物启事",
    "组队参加黑客松",
    "深夜食堂推荐",
    "假期出行拼车信息",
]

CONTENT_SENTENCES = [
    "这一周把该跑的地方都跑了一遍，把结论先写在最前面，省得大家翻到最后。",
    "先说背景：我住在南区，平时上课在教学楼 A，路上大约十五分钟。",
    "整体体验比预期好一些，但有几个细节还是想吐槽一下。",
    "如果只是偶尔来一次，其实问题不大；长期使用的话，建议按下面几条避坑。",
    "数据是我连续记录了七天得到的，样本不大，仅供参考。",
    "有同学私信问我具体位置，补一张手绘路线在脑海里描述：进门右转第二排。",
    "欢迎在评论区补充，我会把有用的信息更新到正文里。",
    "最后提醒一句：高峰时段真的别去，队伍能排到门外。",
    "另外附上我整理的清单，需要的自取，不用谢。",
    "如果有更省时间的做法，非常欢迎交流。",
]

COMMENT_TEXTS = [
    "说得太对了，我上周也遇到过一模一样的情况。",
    "收藏了，感谢分享！",
    "请问具体是哪个位置？想去试试。",
    "这个价格感觉还可以，比外面便宜。",
    "补充一点：晚上八点以后人少很多。",
    "楼主整理得好细，辛苦了。",
    "我持不同意见，实测下来没那么夸张。",
    "求后续更新，蹲一个。",
    "已经按你说的去做了，效果不错。",
    "这条建议对新生很有用。",
    "请问有没有联系方式？想详细问问。",
    "顶一下，让更多同学看到。",
    "同问，我也在纠结这个问题。",
    "哈哈哈太真实了。",
    "谢谢提醒，差点踩坑。",
]

REPORT_REASONS = [
    "疑似广告推广",
    "内容含有不友善言论",
    "与版块主题无关",
    "疑似搬运未注明出处",
    "重复刷屏",
    "涉及个人隐私",
]


# ---------------------------------------------------------------- 小工具


def rand_dt(rng: random.Random, start: dt.datetime, end: dt.datetime) -> dt.datetime:
    span = int((end - start).total_seconds())
    return start + dt.timedelta(seconds=rng.randrange(0, span + 1))


def fmt(value: dt.datetime) -> str:
    """数据库时间统一为 ``YYYY-MM-DD HH:MM:SS`` 字符串。"""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def chunked_insert(model, rows: list[dict], chunk_size: int = 800) -> None:
    for i in range(0, len(rows), chunk_size):
        model.insert_many(rows[i : i + chunk_size]).execute()


def build_content(rng: random.Random, index: int) -> str:
    picked = rng.sample(CONTENT_SENTENCES, k=rng.randint(3, 5))
    return "\n\n".join(picked) + f"\n\n（基准数据 #{index + 1}）"


# ---------------------------------------------------------------- 各表生成


def seed_users(api, rng: random.Random, count: int, shared_hash: str) -> list[str]:
    user_ids = [f"u{i:04d}" for i in range(1, count + 1)]
    rows = []
    for i, userid in enumerate(user_ids, start=1):
        rows.append(
            {
                "userid": userid,
                "username": f"用户{i:04d}",
                "password": shared_hash,
                "created_at": fmt(rand_dt(rng, DATE_START, DATE_END)),
                "description": "这个用户有点懒，什么也没留下~"
                if i % 7 == 0
                else f"基准用户 #{i}，看看就好。",
                "avatar": "default_avatar.jpeg",
                "role": "USER",  # v2 有 CHECK (role IN ('ADMIN','USER'))，必须是 const.user 的大写值
                "secret_key": api.generate_sk_key(16),
            }
        )
    with api.db.atomic():
        chunked_insert(api._User, rows)
    return user_ids


def seed_posts(
    api, rng: random.Random, count: int, user_ids: list[str], tags: list[str]
):
    """返回 (post 行列表, 创建时间列表, 作者列表, 每篇的标签列表)。

    v2：``_post`` 只有 ``author_id``，标签/附件/评论都在各自的关联表里。
    这里只写文章行，标签在拿到 post id 之后由 ``seed_tags`` 批量写 ``_post_tag``。
    """
    rest_users = user_ids[1:] or user_ids
    rows: list[dict] = []
    created_list: list[dt.datetime] = []
    authors: list[str] = []
    tags_per_post: list[list[str]] = []
    for i in range(count):
        author = HEAVY_AUTHOR if i % HEAVY_AUTHOR_EVERY == 0 else rng.choice(rest_users)
        # 极少量作者不在 user 表里也无所谓；这里保证都在
        created = rand_dt(rng, DATE_START, DATE_END)
        chosen_tags = rng.sample(tags, k=rng.randint(1, 3))
        topic = TOPICS[i % len(TOPICS)]
        views = min(99999, int(rng.paretovariate(1.2) * 20))
        rows.append(
            {
                "author_id": author,
                "title": f"{topic}（第 {i + 1} 篇）",
                "content": build_content(rng, i),
                "created_at": fmt(created),
                "attpassword": "",
                "views": views,
            }
        )
        created_list.append(created)
        authors.append(author)
        tags_per_post.append(chosen_tags)
    with api.db.atomic():
        chunked_insert(api._Post, rows)
    return rows, created_list, authors, tags_per_post


def seed_tags(api, tags_per_post: list[list[str]], post_ids: list[int]) -> dict[str, int]:
    """写 ``_tag`` 维度与 ``_post_tag`` 关联（v2 标签筛选走这里，无 JSON 列）。"""
    names = sorted({name for chosen in tags_per_post for name in chosen})
    tag_ids: dict[str, int] = {}
    rows: list[dict] = []
    with api.db.atomic():
        for name in names:
            tag, _ = api._Tag.get_or_create(name=name)
            tag_ids[name] = tag.id
        for pid, chosen in zip(post_ids, tags_per_post):
            for position, name in enumerate(chosen):
                rows.append({"post": pid, "tag": tag_ids[name], "position": position})
        chunked_insert(api._PostTag, rows)
    return {"tags": len(tag_ids), "post_tags": len(rows)}


def backfill_counters(api) -> None:
    """一条 SQL 回填三张明细表对应的冗余计数器（等价于逐篇 recount，但快得多）。"""
    api.db.execute_sql(
        """
        UPDATE _post SET
            like_count = (SELECT COUNT(*) FROM _like WHERE _like.post_id = _post.id),
            bookmark_count = (SELECT COUNT(*) FROM _bookmark WHERE _bookmark.post_id = _post.id),
            comment_count = (SELECT COUNT(*) FROM _comment WHERE _comment.post_id = _post.id)
        """
    )


def seed_comments(
    api,
    rng: random.Random,
    post_ids: list[int],
    post_dts: list[dt.datetime],
    user_ids: list[str],
    total: int,
) -> tuple[list[list[int]], dict[int, int], dict[int, str]]:
    """插入评论行（v2：``_comment.post`` 外键 + ``user_id`` 列）。

    返回 ``(每篇文章的评论 ID 列表, comment_id -> postid, comment_id -> userid)``；
    计数器 ``_post.comment_count`` 最后统一由 ``backfill_counters`` 回填。
    """
    n_posts = len(post_ids)
    hot_index = n_posts // HOT_POST_INDEX_DIVISOR

    counts = [0] * n_posts
    counts[hot_index] = HOT_POST_COMMENT_COUNT
    remaining = max(0, total - HOT_POST_COMMENT_COUNT)
    # 热点文章固定 100 条，不参与随机分配（否则评论数会漂移，详情页夹具失真）
    pool = [i for i in range(n_posts) if i != hot_index]
    weights = [1.0 + (6.0 if i % 97 == 0 else 0.0) for i in pool]
    for picked in rng.choices(pool, weights=weights, k=remaining):
        counts[picked] += 1

    per_post_ids: list[list[int]] = [[] for _ in range(n_posts)]
    comment_postid: dict[int, int] = {}
    comment_userid: dict[int, str] = {}
    batch_rows: list[dict] = []
    batch_groups: list[tuple[int, int]] = []  # (post 下标, 该批内属于它的行数)
    chunk = 1000

    def flush() -> None:
        if not batch_rows:
            return
        # peewee 4.x: insert_many(...).execute() 返回最后一行 rowid（int），
        # 多行 insert 在 SQLite 上是连续自增，可反推本批的 ID 区间。
        last_id = int(api._Comment.insert_many(batch_rows).execute())
        ids = list(range(last_id - len(batch_rows) + 1, last_id + 1))
        pos = 0
        for post_index, n_rows in batch_groups:
            slice_ids = ids[pos : pos + n_rows]
            per_post_ids[post_index].extend(slice_ids)
            for cid in slice_ids:
                comment_postid[cid] = post_ids[post_index]
            pos += n_rows
        for cid, row in zip(ids, batch_rows):
            comment_userid[cid] = row["user_id"]
        batch_rows.clear()
        batch_groups.clear()

    with api.db.atomic():
        for index in range(n_posts):
            n_rows = counts[index]
            if not n_rows:
                continue
            base = post_dts[index]
            for _ in range(n_rows):
                created = min(
                    DATE_END,
                    base + dt.timedelta(minutes=rng.randint(1, 60 * 24 * 20)),
                )
                batch_rows.append(
                    {
                        "post": post_ids[index],
                        "user_id": rng.choice(user_ids),
                        "content": rng.choice(COMMENT_TEXTS),
                        "created_at": fmt(created),
                    }
                )
            batch_groups.append((index, n_rows))
            if len(batch_rows) >= chunk:
                flush()
        flush()

    return per_post_ids, comment_postid, comment_userid


def seed_likes_and_bookmarks(
    api,
    rng: random.Random,
    post_ids: list[int],
    user_ids: list[str],
    n_likes: int,
    n_bookmarks: int,
) -> dict[str, int]:
    like_pairs: set[tuple[int, str]] = set()
    while len(like_pairs) < n_likes:
        like_pairs.add((rng.choice(post_ids), rng.choice(user_ids)))

    bookmark_pairs: set[tuple[int, str]] = set()
    # 先把「收藏夹具用户」的 120 条固定下来，保证收藏列表 100+ 条
    for postid in rng.sample(post_ids, min(BOOKMARK_USER_TARGET, len(post_ids))):
        bookmark_pairs.add((postid, BOOKMARK_USER))
    while len(bookmark_pairs) < n_bookmarks:
        pair = (rng.choice(post_ids), rng.choice(user_ids))
        bookmark_pairs.add(pair)

    with api.db.atomic():
        chunked_insert(
            api._Like,
            [
                {"post": p, "user_id": u, "created_at": fmt(rand_dt(rng, DATE_START, DATE_END))}
                for p, u in sorted(like_pairs)
            ],
        )
        chunked_insert(
            api._Bookmark,
            [
                {"post": p, "user_id": u, "created_at": fmt(rand_dt(rng, DATE_START, DATE_END))}
                for p, u in sorted(bookmark_pairs)
            ],
        )

    return {
        "likes": len(like_pairs),
        "bookmarks": len(bookmark_pairs),
        "bookmarks_for_bench_user": sum(
            1 for _, uid in bookmark_pairs if uid == BOOKMARK_USER
        ),
    }


def seed_reports(
    api,
    rng: random.Random,
    post_ids: list[int],
    post_authors: list[str],
    comment_postid: dict[int, int],
    comment_userid: dict[int, str],
    user_ids: list[str],
    count: int,
) -> dict[str, int]:
    comment_ids = list(comment_postid)
    rows = []
    statuses = ["pending", "handled", "dismissed"]
    # v2 有两个 partial unique index（仅对 status='pending' 生效）：
    #   (reporter_id, post_id) WHERE comment_id IS NULL
    #   (reporter_id, comment_id) WHERE comment_id IS NOT NULL
    # 所以 pending 行必须按 (举报人, 目标) 去重，否则 seed 会撞 IntegrityError。
    pending_keys: set[tuple[str, str, int]] = set()
    for _ in range(count):
        status = rng.choices(statuses, weights=[6, 2, 2], k=1)[0]
        handled_by = ""
        handled_at = ""
        note = ""
        if status != "pending":
            handled_by = HEAVY_AUTHOR
            handled_at = fmt(rand_dt(rng, DATE_START, DATE_END))
            note = "已核对并处理" if status == "handled" else "未发现违规，驳回"

        key: tuple[str, int] = ("c", 0)
        reporter = user_ids[0]
        for _attempt in range(40):
            if comment_ids and rng.random() < 0.25:
                commentid = rng.choice(comment_ids)
                postid = comment_postid[commentid]
                target = comment_userid.get(commentid, "")
                key = ("c", commentid)
            else:
                index = rng.randrange(len(post_ids))
                commentid = None
                postid = post_ids[index]
                target = post_authors[index]
                key = ("p", postid)
            reporter = rng.choice(user_ids)
            if status != "pending" or (reporter, key[0], key[1]) not in pending_keys:
                break
        else:
            # 连撞 40 次太背，就降级为 handled（唯一索引只约束 pending 行）
            status = "handled"
            handled_by = HEAVY_AUTHOR
            handled_at = handled_at or fmt(rand_dt(rng, DATE_START, DATE_END))
            note = note or "已核对并处理"
        if status == "pending":
            pending_keys.add((reporter, key[0], key[1]))
        rows.append(
            {
                "post_id": postid,
                "comment_id": commentid,
                "target_user_id": target,
                "reporter_id": reporter,
                "content_preview": "这是一条用于基准数据的举报内容摘要，长度不超过五百字。",
                "reason": rng.choice(REPORT_REASONS),
                "status": status,
                "handled_by": handled_by,
                "handled_at": handled_at,
                "note": note,
                "created_at": fmt(rand_dt(rng, DATE_START, DATE_END)),
            }
        )
    with api.db.atomic():
        chunked_insert(api._Report, rows)
    pending = sum(1 for r in rows if r["status"] == "pending")
    return {"reports": len(rows), "reports_pending": pending}


# ---------------------------------------------------------------- 入口


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成 FreeLake 性能基准合成数据集（幂等，可重复运行）"
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="规模倍数，默认 1（3000 文章 / 20000 评论 / 500 用户 / 10000 收藏 / 2000 举报）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="数据集目录；省略时自动创建系统临时目录（路径会记录到 bench/.last_data_dir.json）",
    )
    parser.add_argument(
        "--seed", type=int, default=20260101, help="随机种子（保证可复现，默认 20260101）"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="只输出汇总信息"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = parse_args(argv)
    scale = max(0.01, args.scale)

    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser()
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        data_dir = data_dir.resolve()
    else:
        data_dir = Path(
            tempfile.mkdtemp(prefix=f"freelake_bench_x{scale:g}_")
        ).resolve()

    started = time.perf_counter()
    print(f"{PRINT_PREFIX} 数据集目录：{data_dir}")
    print(f"{PRINT_PREFIX} 规模倍数：{scale:g}（随机种子 {args.seed}）")

    # reset=True：删掉旧 data.db，保证幂等重建（只删数据目录内的文件）
    api = prepare_env(data_dir, reset=True)
    from const import tags as ALL_TAGS  # noqa: PLC0415  chdir 之后再导入

    if BENCH_TAG not in ALL_TAGS:
        raise SystemExit(f"基准标签 {BENCH_TAG!r} 不在 const.tags 中，请更新 seed.py")

    counts = {name: int(value * scale) for name, value in BASE_COUNTS.items()}
    print(
        f"{PRINT_PREFIX} 目标规模：用户 {counts['users']} / 文章 {counts['posts']} / "
        f"评论 {counts['comments']} / 点赞 {counts['likes']} / "
        f"收藏 {counts['bookmarks']} / 举报 {counts['reports']}"
    )

    rng = random.Random(args.seed)
    # 500 次 PBKDF2(200k) 太慢，共用一个哈希（仅基准数据，不涉及真实账号）
    shared_hash = api.hash_password("bench-only-password")

    user_ids = seed_users(api, rng, counts["users"], shared_hash)
    print(f"{PRINT_PREFIX} 用户写入完成：{len(user_ids)}")

    _, post_dts, post_authors, tags_per_post = seed_posts(
        api, rng, counts["posts"], user_ids, ALL_TAGS
    )
    post_ids = [p.id for p in api._Post.select(api._Post.id).order_by(api._Post.id)]
    if len(post_ids) != len(tags_per_post):
        raise SystemExit(f"{PRINT_PREFIX} 文章数量与标签分配数量不一致")
    print(f"{PRINT_PREFIX} 文章写入完成：{len(post_ids)}")

    tag_stats = seed_tags(api, tags_per_post, post_ids)
    print(
        f"{PRINT_PREFIX} 标签写入完成：{tag_stats['tags']} 个标签 / "
        f"{tag_stats['post_tags']} 条文章标签关联"
    )

    per_post_ids, comment_postid, comment_userid = seed_comments(
        api, rng, post_ids, post_dts, user_ids, counts["comments"]
    )
    comment_total = sum(len(c) for c in per_post_ids)
    print(f"{PRINT_PREFIX} 评论写入完成：{comment_total}")

    like_bookmark_stats = seed_likes_and_bookmarks(
        api,
        rng,
        post_ids,
        user_ids,
        counts["likes"],
        counts["bookmarks"],
    )
    print(
        f"{PRINT_PREFIX} 点赞 {like_bookmark_stats['likes']} / "
        f"收藏 {like_bookmark_stats['bookmarks']}"
    )

    report_stats = seed_reports(
        api,
        rng,
        post_ids,
        post_authors,
        comment_postid,
        comment_userid,
        user_ids,
        counts["reports"],
    )
    print(
        f"{PRINT_PREFIX} 举报写入完成：{report_stats['reports']} "
        f"（pending {report_stats['reports_pending']}）"
    )

    # 冗余计数器一条 SQL 回填（like_count / bookmark_count / comment_count）
    backfill_counters(api)
    print(f"{PRINT_PREFIX} 文章计数器已回填（like/bookmark/comment_count）")

    # ---------------- 汇总 + 夹具（通过 api 公共接口核对，确保非空）
    hot_index = len(post_ids) // HOT_POST_INDEX_DIVISOR
    hot_post_id = post_ids[hot_index]
    hot_payload = api.Post.get(hot_post_id)
    hot_comment_count = len(hot_payload["comments"]) if hot_payload else 0

    tag_count = api.Post.count_filtered(tag=BENCH_TAG)
    range_start = dt.date.fromisoformat(BENCH_DATE_RANGE[0])
    range_end = dt.date.fromisoformat(BENCH_DATE_RANGE[1])
    range_count = api.Post.count_filtered(
        start_date=range_start, end_date=range_end
    )
    heavy_posts = api.Post.get_by_author(HEAVY_AUTHOR)
    bookmark_ids = api.Bookmark.get_bookmarked_post_ids(BOOKMARK_USER)

    actual = {
        "users": api.User.count(),
        "posts": api.Post.count(),
        "tags": api._Tag.select().count(),
        "post_tags": api._PostTag.select().count(),
        "comments": api._Comment.select().count(),
        "likes": api._Like.select().count(),
        "bookmarks": api._Bookmark.select().count(),
        "reports": api._Report.select().count(),
    }

    fixtures = {
        "hot_post_id": hot_post_id,
        "hot_post_comment_count": hot_comment_count,
        "heavy_author": HEAVY_AUTHOR,
        "heavy_author_post_count": len(heavy_posts),
        "bookmark_user": BOOKMARK_USER,
        "bookmark_user_count": len(bookmark_ids),
        "tag": BENCH_TAG,
        "tag_post_count": tag_count,
        "date_range": {"start": BENCH_DATE_RANGE[0], "end": BENCH_DATE_RANGE[1]},
        "date_range_post_count": range_count,
    }

    problems = []
    if hot_comment_count != HOT_POST_COMMENT_COUNT:
        problems.append(f"热点文章评论数 {hot_comment_count} != {HOT_POST_COMMENT_COUNT}")
    if tag_count <= 0:
        problems.append(f"标签 {BENCH_TAG} 命中 0 篇")
    if range_count <= 0:
        problems.append(f"日期区间 {BENCH_DATE_RANGE} 命中 0 篇")
    if len(bookmark_ids) < 100:
        problems.append(f"收藏夹具只有 {len(bookmark_ids)} 条（要求 ≥100）")
    if problems:
        raise SystemExit(f"{PRINT_PREFIX} 数据集自检失败：" + "；".join(problems))

    elapsed = time.perf_counter() - started
    manifest = {
        "schema": 2,
        "api_schema_version": getattr(api, "SCHEMA_VERSION", None),
        "scale": scale,
        "seed": args.seed,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "seed_seconds": round(elapsed, 2),
        "counts": actual,
        "fixtures": fixtures,
        "notes": [
            "所有用户共用同一个 PBKDF2 密码哈希（仅基准数据）",
            "config.toml 无 password → import api 时不创建管理员、不发公告",
            "v2 结构：标签走 _tag/_post_tag，评论/点赞/收藏走明细表，_post 计数器由一条 SQL 回填",
        ],
    }
    manifest_path = write_manifest(data_dir, manifest)

    pointer = PROJECT_ROOT / "bench" / ".last_data_dir.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {"data_dir": str(data_dir), "scale": scale, "manifest": str(manifest_path)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"{PRINT_PREFIX} 实际规模：用户 {actual['users']} / 文章 {actual['posts']} / "
        f"评论 {actual['comments']} / 点赞 {actual['likes']} / "
        f"收藏 {actual['bookmarks']} / 举报 {actual['reports']}"
    )
    print(
        f"{PRINT_PREFIX} 夹具：热点文章 #{hot_post_id}（{hot_comment_count} 条评论）· "
        f"收藏用户 {BOOKMARK_USER}（{len(bookmark_ids)} 条）· "
        f"标签「{BENCH_TAG}」{tag_count} 篇 · "
        f"日期 {BENCH_DATE_RANGE[0]}~{BENCH_DATE_RANGE[1]} {range_count} 篇"
    )
    print(f"{PRINT_PREFIX} 数据集描述：{manifest_path}")
    print(f"{PRINT_PREFIX} 耗时 {elapsed:.1f}s · 下一步：python bench/bench_queries.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
