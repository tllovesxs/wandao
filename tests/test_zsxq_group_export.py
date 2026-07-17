import argparse
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import export_zsxq
from export_zsxq import (
    ExportError,
    content_from_topic_api,
    filter_follow_zsxq_links,
    group_id_from_url,
    normalize_group_max_pages,
    normalize_group_page_size,
    group_scope_from_args,
    is_group_entry_url,
    localize_files,
    normalize_group_limit,
    parse_args,
    previous_zsxq_end_time,
    resolve_toc_item_api,
    should_long_sleep_after_export,
    should_refresh_newest_group_topics,
    should_scan_newest_before_group_resume,
    should_upgrade_completed_preview,
    summarize_zsxq_api_failure,
    throttle_comment_request,
    inherit_completed_group_items,
    load_compatible_group_cursor,
    zsxq_group_resume_key,
    zsxq_item_key_from_source,
    zsxq_resource_key,
)
from wandao_checkpoint import WandaoCheckpoint


class ZsxqGroupExportTests(unittest.TestCase):
    def test_group_id_is_parsed_from_group_urls_and_query(self) -> None:
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/group/123456789"), "123456789")
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/groups/123456789/topics"), "123456789")
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/digests/15288445111222"), "15288445111222")
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/digests?group_id=987654321"), "987654321")
        self.assertFalse(is_group_entry_url("https://wx.zsxq.com/columns/123456789"))

    def test_browser_selection_never_falls_back_to_another_provider_page(self) -> None:
        unrelated = {
            "type": "page",
            "url": "https://www.yuque.com/example",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/yuque",
        }
        zsxq_page = {
            "type": "page",
            "url": "https://wx.zsxq.com/digests/15288445111222",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/zsxq",
        }
        with mock.patch.object(export_zsxq, "http_json", side_effect=[[unrelated], [zsxq_page]]):
            self.assertIsNone(export_zsxq.page_for_zsxq(9222))
            self.assertEqual(export_zsxq.page_for_zsxq(9223), zsxq_page)

    def test_zsxq_starts_on_a_new_port_when_default_port_is_another_provider(self) -> None:
        args = argparse.Namespace(port=9222, profile_dir=None, browser_path=None)
        page = {
            "type": "page",
            "url": "https://wx.zsxq.com/digests/15288445111222",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/zsxq",
        }
        cdp = mock.Mock()
        with (
            mock.patch.object(export_zsxq, "chrome_debug_available", side_effect=[True, True]),
            mock.patch.object(export_zsxq, "page_for_zsxq", side_effect=[None, page]),
            mock.patch.object(export_zsxq, "find_available_debug_port", return_value=9223),
            mock.patch.object(export_zsxq, "start_chrome", return_value=mock.Mock()) as start,
            mock.patch.object(export_zsxq, "wait_for_debug_port"),
            mock.patch.object(export_zsxq, "open_tab"),
            mock.patch.object(export_zsxq, "CDPClient", return_value=cdp),
            mock.patch.object(export_zsxq.time, "sleep"),
        ):
            resolved, _browser = export_zsxq.connect_browser(args, "https://wx.zsxq.com/digests/15288445111222")

        self.assertIs(resolved, cdp)
        self.assertEqual(args.port, 9223)
        self.assertEqual(start.call_args.args[0], 9223)

    def test_group_scope_auto_detects_digest_url_and_respects_explicit_choice(self) -> None:
        auto_args = argparse.Namespace(group_scope="auto")
        owner_args = argparse.Namespace(group_scope="by_owner")

        self.assertEqual(group_scope_from_args("https://wx.zsxq.com/digests?group_id=123456789", auto_args), "digests")
        self.assertEqual(group_scope_from_args("https://wx.zsxq.com/group/123456789", auto_args), "all")
        self.assertEqual(group_scope_from_args("https://wx.zsxq.com/group/123456789", owner_args), "by_owner")

    def test_previous_end_time_moves_cursor_backwards(self) -> None:
        self.assertEqual(previous_zsxq_end_time("2026-07-08T10:20:30.123+0800"), "2026-07-08T10:20:30.122+0800")
        self.assertEqual(previous_zsxq_end_time("2026-07-08T10:20:30.000+0800"), "2026-07-08T10:20:29.999+0800")
        self.assertEqual(previous_zsxq_end_time("2026-07-08T00:00:00.000Z"), "2026-07-07T23:59:59.999Z")

    def test_group_resume_scope_uses_group_identity_and_inherits_completed_topics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_file = root / ".wandao" / "checkpoint.sqlite"
            scope_key = zsxq_group_resume_key("15288445111222", "digests", root)
            old = WandaoCheckpoint.open(checkpoint_file, "old-job", "zsxq", "export")
            try:
                old.start_task({
                    "source": "https://wx.zsxq.com/digests/15288445111222?from=old",
                    "outputDir": str(root),
                    "groupId": "15288445111222",
                    "groupScope": "digests",
                    "resumeKey": scope_key,
                })
                old.upsert_item(
                    "zsxq:topic:82255484182121460",
                    title="已完成帖子",
                    source_url="https://wx.zsxq.com/topic/82255484182121460",
                    source_id="82255484182121460",
                )
                completed_path = root / "01-已完成帖子.md"
                completed_path.write_text("# 已完成", encoding="utf-8")
                old.complete_item("zsxq:topic:82255484182121460", local_path=str(completed_path))
                old.save_cursor("zsxq-group", {
                    "group_id": "15288445111222",
                    "scope": "digests",
                    "end_time": "2026-07-11T21:15:21.881+0800",
                })
            finally:
                old.close()

            resumed = WandaoCheckpoint.open(checkpoint_file, "new-job", "zsxq", "export")
            try:
                resumed.start_task({
                    "source": "https://wx.zsxq.com/groups/15288445111222/topics",
                    "outputDir": str(root),
                    "groupId": "15288445111222",
                    "groupScope": "digests",
                    "resumeKey": scope_key,
                })
                cursor, source_task_id = load_compatible_group_cursor(
                    resumed,
                    "15288445111222",
                    "digests",
                    "https://wx.zsxq.com/groups/15288445111222/topics",
                    root,
                    scope_key,
                )
                self.assertEqual(source_task_id, "old-job")
                self.assertEqual(cursor["end_time"], "2026-07-11T21:15:21.881+0800")
                self.assertEqual(inherit_completed_group_items(resumed, source_task_id), 1)
                self.assertEqual(resumed.item_status("zsxq:topic:82255484182121460"), "completed")
            finally:
                resumed.close()

    def test_checkpoint_is_claimed_before_browser_login_can_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = parse_args([
                "--entry-url", "https://wx.zsxq.com/digests/15288445111222",
                "--output", str(root / "out"),
                "--checkpoint-file", str(root / "checkpoint.sqlite"),
                "--checkpoint-task-id", "login-failure",
            ])
            with mock.patch.object(export_zsxq, "connect_browser", side_effect=ExportError("需要重新登录")):
                with self.assertRaisesRegex(ExportError, "需要重新登录"):
                    export_zsxq.export_entry(args)
            checkpoint = WandaoCheckpoint.open(root / "checkpoint.sqlite", "login-failure", "zsxq", "export")
            try:
                row = checkpoint.conn.execute(
                    "SELECT status, error_summary FROM tasks WHERE task_id = ?",
                    ("login-failure",),
                ).fetchone()
                self.assertEqual(row["status"], "failed")
                self.assertIn("需要重新登录", row["error_summary"])
            finally:
                checkpoint.close()

    def test_login_does_not_save_invalid_session(self) -> None:
        args = parse_args([
            "--entry-url", "https://wx.zsxq.com/digests/15288445111222",
            "--login",
            "--auth-file", "auth.json",
        ])
        cdp = mock.Mock()
        browser = mock.Mock()
        invalid = {"ok": False, "text": "HTTP 401 code=1009 已在其它设备登录"}
        with (
            mock.patch.object(export_zsxq, "connect_browser", return_value=(cdp, browser)),
            mock.patch.object(export_zsxq, "validate_zsxq_auth", return_value=invalid),
            mock.patch.object(export_zsxq, "save_auth_state") as save_auth,
        ):
            with self.assertRaisesRegex(ExportError, "账号验证未通过"):
                export_zsxq.login_and_save_auth(args, wait_callback=lambda: None)
        save_auth.assert_not_called()
        cdp.close.assert_called_once()

    def test_follow_link_scope_can_keep_only_article_pages(self) -> None:
        links = [
            {"text": "短链帖子", "href": "https://t.zsxq.com/4q1k1"},
            {"text": "帖子页", "href": "https://wx.zsxq.com/topic/22255248518811121"},
            {"text": "文章页", "href": "https://articles.zsxq.com/id_a06ublo6fkvx.html"},
        ]

        articles_args = argparse.Namespace(follow_link_scope="articles")
        none_args = argparse.Namespace(follow_link_scope="none")
        all_args = argparse.Namespace(follow_link_scope="all")

        self.assertEqual(
            [item["href"] for item in filter_follow_zsxq_links(links, articles_args)],
            ["https://articles.zsxq.com/id_a06ublo6fkvx.html"],
        )
        self.assertEqual(filter_follow_zsxq_links(links, none_args), [])
        self.assertEqual(len(filter_follow_zsxq_links(links, all_args)), 3)

    def test_long_sleep_starts_after_threshold_on_every_interval(self) -> None:
        args = argparse.Namespace(long_sleep_after=25, long_sleep_every=12)

        self.assertFalse(should_long_sleep_after_export(args, 24))
        self.assertFalse(should_long_sleep_after_export(args, 25))
        self.assertFalse(should_long_sleep_after_export(args, 35))
        self.assertTrue(should_long_sleep_after_export(args, 36))
        self.assertFalse(should_long_sleep_after_export(args, 37))
        self.assertTrue(should_long_sleep_after_export(args, 48))

    def test_newest_group_refresh_only_runs_when_a_prior_group_task_exists(self) -> None:
        args = argparse.Namespace(resume=True, retry_failed=False)
        self.assertFalse(
            should_refresh_newest_group_topics(
                args, use_group_topics=True, resumed_from_task_id=""
            )
        )

    def test_group_resume_exports_pending_items_before_checking_new_posts(self) -> None:
        args = argparse.Namespace(resume=True, retry_failed=False)
        self.assertFalse(
            should_scan_newest_before_group_resume(
                args,
                use_group_topics=True,
                resumed_from_task_id="prior-job",
                restored_pending_items=1,
            )
        )
        self.assertTrue(
            should_scan_newest_before_group_resume(
                args,
                use_group_topics=True,
                resumed_from_task_id="prior-job",
                restored_pending_items=0,
            )
        )
        self.assertTrue(
            should_refresh_newest_group_topics(
                args, use_group_topics=True, resumed_from_task_id="prior-job"
            )
        )
        self.assertFalse(
            should_refresh_newest_group_topics(
                argparse.Namespace(resume=True, retry_failed=True),
                use_group_topics=True,
                resumed_from_task_id="prior-job",
            )
        )

    def test_group_directory_pagination_has_extra_safe_delay_defaults(self) -> None:
        args = parse_args(["--entry-url", "https://wx.zsxq.com/group/123456789", "--output", "out"])

        self.assertEqual(args.port, export_zsxq.DEFAULT_ZSXQ_PORT)
        self.assertEqual(args.group_page_delay, 4.0)
        self.assertEqual(args.group_page_jitter, 4.0)
        self.assertEqual(args.group_page_size, 20)
        self.assertEqual(args.request_delay, 2.5)
        self.assertEqual(args.request_jitter, 2.5)
        self.assertFalse(args.fetch_full_comments)
        self.assertEqual(args.comment_request_delay, 3.0)
        self.assertEqual(args.comment_request_jitter, 2.0)

    def test_group_provider_exposes_adjustable_long_sleep_controls(self) -> None:
        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "plugins" / "zsxq" / "providers" / "zsxq-group" / "provider.json")
            .read_text(encoding="utf-8")
        )
        fields = {field["name"]: field for field in manifest["fields"]}
        expected = {
            "long_sleep_after": ("--long-sleep-after", 25),
            "long_sleep_every": ("--long-sleep-every", 12),
            "long_sleep_min": ("--long-sleep-min", 120),
            "long_sleep_max": ("--long-sleep-max", 300),
        }
        for name, (arg, default) in expected.items():
            self.assertEqual(fields[name]["arg"], arg)
            self.assertEqual(fields[name]["default"], default)
            self.assertIn("export", fields[name]["actions"])

    def test_zsxq_timing_args_have_safe_floor(self) -> None:
        args = parse_args(
            [
                "--entry-url",
                "https://wx.zsxq.com/group/123456789",
                "--output",
                "out",
                "--request-delay",
                "0",
                "--request-jitter",
                "0",
                "--comment-request-delay",
                "0",
                "--comment-request-jitter",
                "0",
                "--group-page-size",
                "60",
            ]
        )

        self.assertEqual(args.request_delay, 1.0)
        self.assertEqual(args.request_jitter, 1.0)
        self.assertEqual(args.comment_request_delay, 3.0)
        self.assertEqual(args.comment_request_jitter, 2.0)
        self.assertEqual(args.group_page_size, 20)

    def test_checkpoint_args_are_parsed(self) -> None:
        args = parse_args(
            [
                "--entry-url",
                "https://wx.zsxq.com/group/123456789",
                "--output",
                "out",
                "--checkpoint-file",
                "out/.wandao/checkpoint.sqlite",
                "--resume",
                "--retry-failed",
            ]
        )

        self.assertEqual(args.checkpoint_file, "out/.wandao/checkpoint.sqlite")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)

    def test_full_comment_api_uses_smaller_safe_batch(self) -> None:
        self.assertEqual(export_zsxq.DEFAULT_COMMENT_BATCH_SIZE, 30)
        source = inspect.getsource(export_zsxq.fetch_topic_comments_api)
        self.assertIn("DEFAULT_COMMENT_BATCH_SIZE", source)

    def test_group_page_size_is_clamped_to_safe_api_count(self) -> None:
        self.assertEqual(normalize_group_page_size(argparse.Namespace(group_page_size=0)), 20)
        self.assertEqual(normalize_group_page_size(argparse.Namespace(group_page_size=12)), 12)
        self.assertEqual(normalize_group_page_size(argparse.Namespace(group_page_size=60)), 20)

    def test_group_limit_defaults_and_allows_large_single_runs(self) -> None:
        self.assertEqual(normalize_group_limit(argparse.Namespace(limit=0)), 50)
        self.assertEqual(normalize_group_limit(argparse.Namespace(limit=120)), 120)
        self.assertEqual(normalize_group_limit(argparse.Namespace(limit=10000)), 10000)

    def test_group_max_pages_expands_to_cover_large_limit(self) -> None:
        args = argparse.Namespace(group_max_pages=200)

        self.assertEqual(normalize_group_max_pages(args, limit=100, page_size=20), 200)
        self.assertEqual(normalize_group_max_pages(args, limit=5000, page_size=20), 250)

    def test_api_failure_summary_keeps_http_200_diagnostics(self) -> None:
        result = {
            "attempts": [
                {
                    "url": "https://api.zsxq.com/v2/groups/123/topics?count=60",
                    "status": 200,
                    "topicCount": 0,
                    "textPreview": "<html>请登录后继续</html>",
                    "authRequired": True,
                },
                {
                    "url": "https://api.zsxq.com/v1.10/groups/123/topics?count=60",
                    "status": 200,
                    "topicCount": 0,
                    "message": "",
                },
            ],
            "authRequired": True,
        }

        message = summarize_zsxq_api_failure(result, "知识星球 group 主题列表读取失败：123")

        self.assertIn("登录状态", message)
        self.assertIn("HTTP 200", message)
        self.assertIn("帖子列表接口", message)
        self.assertNotEqual(message, "200; 200")

    def test_api_failure_summary_explains_deleted_topic_code(self) -> None:
        result = {
            "attempts": [
                {
                    "url": "https://api.zsxq.com/v2/topics/22255248518811121/info",
                    "status": 200,
                    "code": "1007",
                    "message": "主题不存在或已被删除",
                }
            ],
        }

        message = summarize_zsxq_api_failure(result, "topic API 读取失败：22255248518811121")

        self.assertIn("目标帖子不存在", message)
        self.assertIn("code=1007", message)

    def test_full_comment_api_has_its_own_safe_delay_floor(self) -> None:
        args = argparse.Namespace(
            request_delay=0,
            request_jitter=0,
            comment_request_delay=3.0,
            comment_request_jitter=2.0,
        )

        with mock.patch.object(export_zsxq.random, "uniform", return_value=1.25), mock.patch.object(export_zsxq, "wait_with_stop") as wait:
            throttle_comment_request(args)

        wait.assert_called_once_with(args, 4.25)
        self.assertEqual(args._comment_request_count, 1)

    def test_group_raw_topic_exports_without_extra_detail_or_comment_api(self) -> None:
        class FailingCdp:
            def evaluate(self, *_args, **_kwargs):
                raise AssertionError("raw group topics should not need extra browser/API calls")

        args = argparse.Namespace(include_comments=True, fetch_full_comments=False, skip_video_topics=True)
        source = {
            "title": "列表里的主题",
            "topicId": "123456789",
            "topicUid": "123456789",
            "key": "group:all:0:123456789",
            "groupTitle": "全部主题",
            "rawTopic": {
                "topic_id": "123456789",
                "talk": {
                    "text": "列表接口已有正文",
                    "images": [{"original": {"url": "https://example.com/a.png"}}],
                },
                "show_comments": [
                    {"owner": {"name": "Alice"}, "create_time": "2026-07-09T10:00:00.000+0800", "text": "可见评论"}
                ],
            },
        }

        content = resolve_toc_item_api(FailingCdp(), source, args)

        self.assertIn("列表接口已有正文", content["markdown"])
        self.assertIn("可见评论", content["markdown"])
        self.assertEqual(content["commentCount"], 1)

    def test_topic_api_content_keeps_images_files_and_comments(self) -> None:
        args = argparse.Namespace(include_comments=True)
        topic = {
            "topic_id": "123456789",
            "talk": {
                "text": "正文内容",
                "images": [{"original": {"url": "https://example.com/image.png"}}],
                "files": [{"name": "资料.pdf", "download_url": "https://example.com/file.pdf"}],
            },
        }
        source = {"title": "测试主题", "key": "group:all:0:123456789", "groupTitle": "全部主题"}
        comments = [{"author": "Alice", "time": "2026-07-08T10:00:00.000+0800", "text": "评论内容"}]

        content = content_from_topic_api(topic, source, args, comments=comments)
        markdown = content["markdown"]

        self.assertIn("# 测试主题", markdown)
        self.assertIn("## 图片", markdown)
        self.assertIn("![图片 1](https://example.com/image.png)", markdown)
        self.assertIn("## 附件", markdown)
        self.assertIn("[资料.pdf](https://example.com/file.pdf)", markdown)
        self.assertEqual(content["files"][0]["name"], "资料.pdf")
        self.assertIn("## 评论区", markdown)
        self.assertEqual(content["commentCount"], 1)
        self.assertEqual(content["topicId"], "123456789")
        self.assertEqual(zsxq_item_key_from_source(content), "zsxq:topic:123456789")

    def test_api_declared_article_marks_topic_markdown_as_upgradeable_preview(self) -> None:
        topic = {
            "topic_id": "22255488584842111",
            "talk": {
                "text": "帖子摘要",
                "article": {"article_url": "https://articles.zsxq.com/full.html", "title": "完整文章"},
            },
        }
        content = content_from_topic_api(topic, {"title": "帖子摘要"}, argparse.Namespace(include_comments=False))

        self.assertEqual(content["contentCompleteness"], "preview")
        self.assertEqual(content["previewArticleUrl"], "https://articles.zsxq.com/full.html")

    def test_article_content_preserves_topic_id_for_checkpoint_key(self) -> None:
        class UnusedCdp:
            pass

        args = argparse.Namespace(include_comments=False, fetch_full_comments=False, skip_video_topics=True)
        source = {
            "title": "带文章的主题",
            "topicId": "22255488584842111",
            "topicUid": "22255488584842111",
            "key": "group:all:0:22255488584842111",
            "groupTitle": "全部主题",
            "rawTopic": {
                "topic_id": "22255488584842111",
                "talk": {
                    "text": "摘要",
                    "article": {"article_url": "https://articles.zsxq.com/example.html", "title": "完整文章"},
                },
            },
        }

        with mock.patch.object(
            export_zsxq,
            "collect_article_content",
            return_value={"title": "完整文章", "markdown": "# 完整文章\n", "images": [], "files": []},
        ):
            content = resolve_toc_item_api(UnusedCdp(), source, args)

        self.assertEqual(content["topicId"], "22255488584842111")
        self.assertEqual(content["topicUid"], "22255488584842111")
        self.assertEqual(zsxq_item_key_from_source(content), "zsxq:topic:22255488584842111")
        self.assertEqual(content["contentCompleteness"], "full")

    def test_completed_preview_is_upgraded_only_for_api_declared_article_relation(self) -> None:
        source = {
            "topicId": "22255488584842111",
            "rawTopic": {
                "topic_id": "22255488584842111",
                "talk": {"text": "摘要", "article": {"article_url": "https://articles.zsxq.com/full.html"}},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WandaoCheckpoint.open(Path(tmp) / "checkpoint.sqlite", "preview-upgrade", "zsxq", "export")
            try:
                key = "zsxq:topic:22255488584842111"
                checkpoint.start_task({"source": "https://wx.zsxq.com/group/1", "outputDir": tmp})
                checkpoint.upsert_item(key, title="摘要")
                checkpoint.complete_item(key, local_path=str(Path(tmp) / "preview.md"), metadata={"contentCompleteness": "preview"})
                self.assertTrue(should_upgrade_completed_preview(checkpoint, key, source))

                checkpoint.complete_item(key, metadata={"contentCompleteness": "full"})
                self.assertFalse(should_upgrade_completed_preview(checkpoint, key, source))
                self.assertFalse(should_upgrade_completed_preview(checkpoint, key, {"href": "https://articles.zsxq.com/related.html"}))
            finally:
                checkpoint.close()

    def test_column_overview_uses_own_checkpoint_key_before_queue_items(self) -> None:
        class FakeCdp:
            def close(self):
                pass

        toc = {
            "href": "https://wx.zsxq.com/columns/15288445111222",
            "title": "测试专栏",
            "groups": [
                {
                    "key": "group:0",
                    "groupIndex": 0,
                    "groupTitle": "目录",
                    "topics": [
                        {
                            "key": "toc:0:0",
                            "title": "第一篇",
                            "topicId": "55522515524115114",
                            "topicUid": "55522515524115114",
                            "topicUrl": "https://wx.zsxq.com/topic/55522515524115114",
                        }
                    ],
                }
            ],
            "totalTopics": 1,
        }
        entry = {
            "title": "测试专栏",
            "url": "https://wx.zsxq.com/columns/15288445111222",
            "markdown": "# 测试专栏\n![图](https://example.com/cover.png)",
            "images": ["https://example.com/cover.png"],
            "zsxqLinks": [],
        }
        topic = {
            "title": "第一篇",
            "markdown": "# 第一篇\n正文",
            "images": [],
            "files": [],
            "topicId": "55522515524115114",
            "topicUid": "55522515524115114",
            "topicUrl": "https://wx.zsxq.com/topic/55522515524115114",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            args = parse_args(
                [
                    "--entry-url",
                    "https://wx.zsxq.com/columns/15288445111222",
                    "--output",
                    str(output),
                    "--toc-mode",
                    "toc",
                    "--toc-key",
                    "toc:0:0",
                    "--max-depth",
                    "1",
                    "--skip-auth-load",
                    "--checkpoint-file",
                    str(root / "checkpoint.sqlite"),
                ]
            )

            with (
                mock.patch.object(export_zsxq, "connect_browser", return_value=(FakeCdp(), None)),
                mock.patch.object(export_zsxq, "collect_toc", return_value=toc),
                mock.patch.object(export_zsxq, "collect_entry_links", return_value=entry),
                mock.patch.object(export_zsxq, "resolve_toc_item", return_value=topic),
                mock.patch.object(export_zsxq, "download_image", side_effect=RuntimeError("blocked in test")),
            ):
                report = export_zsxq.export_entry(args)

            overview = output / "01-专栏正文.md"
            self.assertTrue(overview.exists())
            self.assertEqual(report["exportedDocs"], 1)

            checkpoint = WandaoCheckpoint.open(root / "checkpoint.sqlite", task_id="default", provider_id="zsxq", action="export")
            try:
                overview_key = zsxq_item_key_from_source({"href": "https://wx.zsxq.com/columns/15288445111222", "key": "overview"})
                resource_key = zsxq_resource_key("image", "https://example.com/cover.png")

                self.assertEqual(checkpoint.item_status(overview_key), "failed")
                resource = checkpoint.resource_record(resource_key)
                self.assertIsNotNone(resource)
                self.assertEqual(resource["item_key"], overview_key)
            finally:
                checkpoint.close()

    def test_localize_files_rewrites_attachment_links_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "doc.md"
            target = Path(tmp) / "assets" / "files" / "demo.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("demo", encoding="utf-8")

            with mock.patch.object(export_zsxq, "download_file", return_value=target):
                markdown, success, failures = localize_files(
                    "[资料](https://example.com/file.pdf)",
                    [{"name": "资料.pdf", "url": "https://example.com/file.pdf"}],
                    md_path,
                    10,
                )

        self.assertEqual(success, 1)
        self.assertEqual(failures, [])
        self.assertIn("assets/files/demo.pdf", markdown)

    def test_localize_files_reuses_completed_checkpoint_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = root / "doc.md"
            target = root / "assets" / "files" / "demo.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("demo", encoding="utf-8")
            checkpoint = WandaoCheckpoint.open(root / ".wandao" / "checkpoint.sqlite", task_id="default", provider_id="zsxq", action="export")
            try:
                checkpoint.start_task({"source": "https://wx.zsxq.com/group/test", "outputDir": str(root)})
                resource_key = zsxq_resource_key("attachment", "https://example.com/file.pdf")
                checkpoint.upsert_resource("item-1", resource_key, "attachment", "https://example.com/file.pdf")
                checkpoint.complete_resource(resource_key, local_path=str(target))

                with mock.patch.object(export_zsxq, "download_file", side_effect=AssertionError("should reuse checkpoint")):
                    markdown, success, failures = export_zsxq.localize_files(
                        "[资料](https://example.com/file.pdf)",
                        [{"name": "资料.pdf", "url": "https://example.com/file.pdf"}],
                        md_path,
                        10,
                        checkpoint=checkpoint,
                        item_key="item-1",
                    )
            finally:
                checkpoint.close()

        self.assertEqual(success, 0)
        self.assertEqual(failures, [])
        self.assertIn("assets/files/demo.pdf", markdown)


if __name__ == "__main__":
    unittest.main()
