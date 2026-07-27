# P1b — 路径长度收敛 + 飞书导出单篇降级

## 问题

`build_doc_paths()` 在导出循环开始**之前**就把整棵目录树递归 `mkdir` 出来
（`ensure_container()` 里的 `directory.mkdir(parents=True, exist_ok=True)`）。
`sanitize_filename()` 每层允许 90 字符，深层级中文飞书 Wiki 的目录名拼起来轻易
超过 Windows 未启用 `LongPathsEnabled` 时的 260 字符 `MAX_PATH`。
后果不是"这一篇导不出来"，而是 `OSError` 在**规划阶段**抛出——整个任务启动即失败，
`exportedDocs == 0`，前面已经抓好的目录树全部作废。

## 收敛算法

`wandao_core/browser.py` 新增 `PATH_LENGTH_LIMIT` 与
`shorten_path_for_platform(target, *, limit, keep=20)`，紧接 `sanitize_filename()` /
`pad()` 之后，与既有的文件名工具同处一地。

```
PATH_LENGTH_LIMIT = 240 if os.name == "nt" else 3800
```

三级处理，逐级才升级：

1. **不超限 → 原样返回。** 唯一的快速出口，见下节。
2. **逐段压缩。** 每个超过 `keep=20` 字符的路径段截断到 20 字符，
   `rstrip(' .-')` 去掉尾部的空格/点（Windows 不允许目录名以点或空格结尾），
   再追加 `~` + 该段**原始名字**的 SHA-1 前 8 位十六进制。叶子节点先拆出
   `.md` 后缀，压缩 stem 后再拼回去，后缀始终保留。
3. **抬升叶子。** 逐段压缩后每段仍约 29 字符，深度 10 时总长仍会超 240。
   于是从最深处开始逐层丢弃中间目录，把叶子挂到还放得下的最深祖先上；
   被丢弃的那串祖先以 `/` 连接后取 SHA-1 前 8 位，作为 `-<hash>` 追加进叶子 stem。
   最坏情况回退到 `<anchor>/<叶子>`。

### 长度按什么计

**按字符数，不是字节数。** Windows `MAX_PATH` = 260 数的是 UTF-16 code unit；
本仓库涉及的中文标题都是 BMP 内的字符，一个汉字 = 1 个 UTF-16 code unit，
恰好等于 Python `len(str(path))` 数的 code point 数。所以一个 90 汉字的目录名
对 `MAX_PATH` 的消耗是 90，而不是它在磁盘上占的 270 UTF-8 字节。
（BMP 外的字符如 emoji 每个占 2 个 UTF-16 unit，240 相对 260 留出的 20 字符余量
同时也覆盖了这种偏差，以及写盘侧派生出来的 `assets/<12位hash>.<ext>` 兄弟路径。）

## 唯一性保证

哈希只在**确实发生了信息丢失**的地方注入，且注入的是被丢掉的那部分内容的摘要：

| 两条超长路径的差异位置 | 靠什么区分 |
| --- | --- |
| 保留下来的段（短到没被压缩） | 段本身逐字保留 |
| 被截断的段（目录或叶子） | `~<sha1(原始段名)[:8]>` |
| 被抬升丢弃的中间目录 | 叶子上的 `-<sha1("/".join(被丢弃段))[:8]>` |
| 抬升深度不同 | 结果的路径段数不同，字符串必然不等 |

目录段的哈希只取**该段自己的名字**，不掺入完整路径——否则同一个目录在不同
子文件下会被算成不同名字，目录树会散架。同理，函数是纯函数、完全确定性的
（`test_shortening_is_deterministic` 锁定），同一篇文档跨次导出恒定落到同一路径。

最难的一例是"两条路径只在**被丢弃的**祖先目录上不同、连叶子名都一样"：
不带被丢弃祖先的摘要就会互相覆盖。`test_paths_differing_only_in_a_dropped_ancestor_stay_distinct`
专门造了这一对来锁死它。

## 正常路径原样不变（最重要的一条）

这个函数作用在**每一个**导出文件名上。一旦它对短路径也动手，用户的增量导出
会全线失效——文件名变了 = 认不出已经导出过的文档 = 全量重导。

保证来自函数的第一句：

```python
if len(str(resolved)) <= limit:
    return resolved
```

在 `limit` 以内的输入直接原对象返回，**不进入任何压缩分支**。
反向断言在 `tests/test_path_length.py::test_ordinary_paths_are_returned_byte_for_byte_unchanged`
里逐条覆盖：Windows 绝对路径、多层中文目录、POSIX 绝对路径、相对路径、
裸文件名、**长文件名但短路径**（单段 86 汉字，说明触发条件是整条路径长度而非单段长度）、
以及**恰好压线 240 字符**的路径；每条都断言 `str(result) == str(path)` 且类型不变。
`test_boundary_paths_are_unchanged_at_the_limit` 再从 238/239/240 逐字符逼近边界，
并确认"超出 1 个字符"才是第一个可能被改写的输入。
飞书侧另有 `test_short_tree_keeps_its_natural_paths`，断言正常深度的树规划出来
仍然是 `output/01-一级目录/01-产品需求文档.md`，与收敛前完全一致。

## 飞书侧的降级改动

`plugins/feishu/backend/export_feishu.py`，三处：

1. `from wandao_core.browser import (...)` 块里按字母序加入 `shorten_path_for_platform`。
2. `build_doc_paths()` → `ensure_container()`：**删掉** `directory.mkdir(parents=True, exist_ok=True)`，
   换成一行注释说明目录改由写盘侧按需创建。规划阶段从此不碰文件系统。
3. `build_doc_paths()` 末尾：`doc_paths[token] = parent_dir / f"..."` 外面包一层
   `shorten_path_for_platform(...)`。

"整任务失败 → 单篇失败"由此成立：真正的
`md_path.parent.mkdir(parents=True, exist_ok=True)` 位于每篇文档的 `try` 内部
（`for index, doc in enumerate(docs, ...)` 循环里，`try:` 之后、`md_path.write_text()` 之前），
它下面的 `except Exception` 会把这一篇记进 `failures`、发出
`event="document.export.failed"`，然后继续下一篇。所以就算收敛后仍然写不进去
（比如用户把输出目录设在一个本身就快满 260 的位置），损失也只有那一篇，
报告里 `exportedDocs > 0`。

`test_deep_tree_plans_paths_without_creating_directories` 用深度 10、每层 90 汉字的
假树跑 `build_doc_paths()`，断言输出目录跑完之后**仍然是空的**——预建目录一旦
回归就会被这条抓住。

## 未纳入本次改动

P1b 原方案还点名了 `plugins/yuque/backend/export_yuque.py` 和
`plugins/aliyun_thoughts/backend/export_aliyun_thoughts.py` 两处同模式代码
（语雀同样是 `ensure_container()` 里预建目录；阿里云笔记的
`ensure_document_parent_dirs()` 更进一步，且它的 `md_path.parent.mkdir()` 目前在
单篇 `try` **之外**，需要一并移进去）。这两个文件本次不动。
`shorten_path_for_platform()` 是平台无关的公共函数，改那两处时直接复用即可。

## 验证

```
python3 -m unittest discover -s tests -q   → Ran 556 tests, OK (skipped=2)
python3 scripts/quality_check.py           → Quality check passed.
```

基线 547 条，本次新增 9 条（`tests/test_path_length.py`），无回归。

## 改动文件

- `wandao_core/browser.py` — 新增 `PATH_LENGTH_LIMIT`、`shorten_path_for_platform()`
- `plugins/feishu/backend/export_feishu.py` — import、去掉预建目录、叶子路径收敛
- `tests/test_path_length.py` — 新增，9 条用例

commit: `3ee02dafec54b2f8722e603644fdefb8a9d4512a`（tag `sol/P1b`；REPORT.md 按仓库惯例不入该 commit）
