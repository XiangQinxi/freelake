# bench/ · FreeLake 性能基准

测量 FreeLake 数据层（`api.py`）的查询热路径，用于**重构前后对比**。

| 文件 | 作用 |
| --- | --- |
| `_bootstrap.py` | 共同前置：建临时数据目录、写最小 `config.toml`、`chdir` 后导入 `api`（绕开 `api.py` 的 import 副作用） |
| `seed.py` | 生成可复现的合成数据集 + 夹具（幂等，重跑会删除旧 `data.db` 重建） |
| `bench_queries.py` | 只用 `api.py` 公共函数测量 9 项热路径，输出 JSON + 控制台报告 |
| `baseline-current.json` | 重构前基线（`schema_version: "before"`） |
| `after-refactor.json` | 重构后结果（`schema_version: "after"`） |

## 一键复现

```bash
python bench/seed.py --scale 1 --data-dir bench/.benchdata
python bench/bench_queries.py --data-dir bench/.benchdata --schema-version before --out bench/baseline-current.json
```

重构后：

```bash
python bench/bench_queries.py --data-dir bench/.benchdata --schema-version after --out bench/after-refactor.json
```

## 数据集（`--scale 1`，v2 结构）

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `_user` | 500 | `u0001`…`u0500`，`u0001` 为高产作者（约 1/25 的文章） |
| `_post` | 3000 | 发布时间覆盖 2026-01-01 ~ 2026-10-25；`like_count` / `bookmark_count` / `comment_count` 由一条 SQL 回填 |
| `_tag` / `_post_tag` | 18 / 6006 | 每篇 1~3 个 `const.tags` 标签，走关联表（v2 无 JSON 列） |
| `_comment` | 20000 | 其中文章 #1501 固定 100 条（详情页夹具） |
| `_like` | 15000 | 复合主键 (post_id, user_id) |
| `_bookmark` | 10000 | `u0001` 固定 120 条（收藏列表夹具 > 100） |
| `_report` | 2000 | 状态 pending/handled/dismissed 混合；pending 受 partial unique index 约束，seed 已去重 |

夹具与实测规模写在数据目录的 `bench_manifest.json` 里，`bench_queries.py` 直接读取，**不硬编码**。

## 测量方法

- 每项 **warmup 1 次 + 重复 5 次**，报告 `median_ms` / `p95_ms`（nearest-rank）与返回行数。
- **只走 `api.py` 公共接口**；唯一例外是第 9 项 `EXPLAIN QUERY PLAN`（必须 raw SQL）。
- peewee 的 `.dicts()` / `.paginate()` 返回的是**未执行**的查询对象，`bench_queries.py`
  在计时窗口内 `_consume()` 物化，避免把「构造查询」误当成「执行查询」（否则会出现 0.0x ms 的假数据）。
- 数据集放在 `bench/.benchdata`（可用 `--data-dir` 改），**不会碰项目根的 `data.db`**。

## 9 项必测项对照表

| # | 结果名 | 接口 |
| --- | --- | --- |
| 1 | `home_first_page` / `home_first_screen_total` | `Post.get_filtered_paginate(page=1, page_size=10)`，无筛选、日期倒序 |
| 2 | `sort_date` / `sort_views` / `sort_likes` / `sort_bookmarks` | 四类排序各一次 |
| 3 | `tag_filter_page1` / `tag_filter_count` / `tag_filter_screen_total` | 标签筛选分页（重点） |
| 4 | `date_range_list_page1` / `date_range_count` + `consistency` | 日期区间筛选 + `count_filtered` 一致性核对 |
| 5 | `detail_hot_post` | `Post.get(hot_post_id)`，带 100 条评论 |
| 6 | `author_posts` / `author_stats` | `Post.get_by_author` / `Post.get_author_stats` |
| 7 | `bookmarks_ids` / `bookmarks_posts_100` / `bookmarks_page_total` | 收藏列表（100+ 条） |
| 8 | `admin_user_list` / `admin_report_list` / `admin_report_counts` | `User.get_all` / `Report.get_all` / `Report.count` |
| 9 | `explain_query_plan` | 8 个场景的 `EXPLAIN QUERY PLAN`（首页默认、标签筛选、四类排序、日期区间、作者页、收藏 IN） |

## 基线关键结论（2026-10-25，peewee 4.3.0 / SQLite 3.49.1）

- 标签筛选的 SQL 实际是 `WHERE _pw_json_contains("t1"."tags", ?) = 1` ——
  peewee 在 SQLite 上把 JSON 包含判断实现成 **Python 标量函数**，逐行回调，
  **索引完全用不上**；执行计划为 `SCAN t1` + `USE TEMP B-TREE FOR ORDER BY`。
- 模型没有任何索引定义，`ORDER BY created_at DESC` 永远走临时 B 树。
- 最慢：按点赞/收藏排序（`sort_likes` / `sort_bookmarks`，约 70 ms）——
  `_like_or_bookmark_counts()` 先全表聚合计数，再 `list(query.dicts())` 全量拉回
  Python 侧排序后切片（`api.py:611-617`）。
- 次慢：标签筛选（约 19~26 ms），即上面的 JSON 函数逐行扫描 3000 行。

## 重构后对比（2026-10-25，`after-refactor.json`，同数据集同口径）

同一份 `bench/.benchdata`（seed 重跑后夹具与行数完全一致：热点 #1501 / 100 条评论、
收藏 143 条、标签 367 篇、日期区间 306 篇；日期区间逐页累计 306 == `count_filtered` 306）。

| 场景 | before median | after median | 变化（正=变快） | rows |
| --- | ---: | ---: | ---: | --- |
| `home_first_page` | 1.39 | 1.86 | -34.0% | 10 = 10 |
| `sort_date` | 1.24 | 1.41 | -13.7% | 10 = 10 |
| `sort_views` | 1.77 | 1.67 | +5.7% | 10 = 10 |
| `sort_likes` | 69.64 | 1.80 | **+97.4%** | 10 = 10 |
| `sort_bookmarks` | 71.28 | 1.49 | **+97.9%** | 10 = 10 |
| `tag_filter_page1` | 18.75 | 2.77 | **+85.2%** | 10 = 10 |
| `tag_filter_count` | 19.01 | 1.06 | **+94.4%** | 367 = 367 |
| `date_range_list_page1` | 2.60 | 1.57 | +39.4% | 10 = 10 |
| `date_range_count` | 1.42 | 0.48 | +66.5% | 306 = 306 |
| `detail_hot_post` | 2.20 | 2.10 | +4.7% | 100 = 100 |
| `author_posts` | 3.76 | 4.65 | -23.6% | 120 = 120 |
| `author_stats` | 12.09 | 0.74 | **+93.8%** | 120 = 120 |
| `bookmarks_ids` | 1.75 | 0.78 | +55.5% | 143 = 143 |
| `bookmarks_posts_100` | 3.76 | 5.42 | -44.2% | 100 = 100 |
| `bookmarks_page_total` | 8.41 | 8.35 | +0.8% | 100 = 100 |
| `admin_user_list` | 3.97 | 3.38 | +14.7% | 500 = 500 |
| `admin_report_list` | 16.91 | 17.88 | -5.7% | 2000 = 2000 |
| `admin_report_counts` | 1.56 | 0.92 | +41.3% | 2000 = 2000 |
| `home_first_screen_total` | 8.53 | 2.89 | +66.2% | 10 = 10 |
| `tag_filter_screen_total` | 26.14 | 4.98 | **+81.0%** | 10 = 10 |

全部 20 项 **rows 与 before 完全一致**（无需调整测量项）。

### EXPLAIN QUERY PLAN 差异

| 场景 | before | after |
| --- | --- | --- |
| 首页默认（日期倒序） | `SCAN t1` + `USE TEMP B-TREE FOR ORDER BY` | `SCAN t1 USING INDEX _post_created_at_id`（无临时排序） |
| 标签筛选 | `SCAN t1` + `_pw_json_contains()` 逐行回调 + 临时 B 树 | `SEARCH t1 USING INTEGER PRIMARY KEY (rowid=?)` + `LIST SUBQUERY`（`_tag_name` / `_posttag_tag_id_post_id` 覆盖索引，367 行）+ 临时 B 树（仅排 367 行） |
| `sort_views` | 全表扫描 + 临时 B 树 | `SCAN t1 USING INDEX _post_views_id` |
| `sort_likes` | 全表聚合计数 + Python 排序 | `SCAN t1 USING INDEX _post_like_count_id` |
| `sort_bookmarks` | 全表聚合计数 + Python 排序 | `SCAN t1 USING INDEX _post_bookmark_count_id` |
| 日期区间 | `SCAN t1` + 临时 B 树 | `SEARCH t1 USING INDEX _post_created_at_id (created_at>? AND created_at<?)` |
| 作者页 | `SCAN t1` + 临时 B 树 | `SEARCH t1 USING INDEX _post_author_id_id (author_id=?)` + 临时 B 树 |
| 收藏 `post_ids IN` | `SCAN t1` + 临时 B 树 | `SEARCH t1 USING INTEGER PRIMARY KEY (rowid=?)` + 临时 B 树 |

### 已知的轻微回退（可继续优化）

- `home_first_page` / `sort_date`（同一条查询测了两次，差异属 ±0.4 ms 噪声）比 before 慢约 0.5 ms：
  列表页现在固定发 **3 条 SQL**（文章 + `_tag_map` + `_attachment_map`，见 `_post_dicts`），
  比 v1 的 1 条查询多两个往返。`bookmarks_posts_100`（-44.2%，+1.7 ms）同理，100 篇时更明显。
- `author_posts`（-23.6%）同样是每页多两条关联查询；`admin_report_list`（-5.7%，2000 行）属噪声级。
- 若继续优化：`_post_dicts` 可只在「页面确实需要标签/附件」时查关联表（列表页目前也总会查），
  或把两个 map 合成一次 UNION 查询；`author_page` 可加 `(author_id, created_at, id)` 索引消掉临时 B 树。
