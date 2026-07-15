import argparse
import inspect
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
    summarize_zsxq_api_failure,
    throttle_comment_request,
    zsxq_item_key_from_source,
    zsxq_resource_key,
)
from wandao_checkpoint import WandaoCheckpoint


class ZsxqGroupExportTests(unittest.TestCase):
    def test_group_id_is_parsed_from_group_urls_and_query(self) -> None:
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/group/123456789"), "123456789")
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/groups/123456789/topics"), "123456789")
        self.assertEqual(group_id_from_url("https://wx.zsxq.com/digests?group_id=987654321"), "987654321")
        self.assertFalse(is_group_entry_url("https://wx.zsxq.com/columns/123456789"))

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

    def test_group_directory_pagination_has_extra_safe_delay_defaults(self) -> None:
        args = parse_args(["--entry-url", "https://wx.zsxq.com/group/123456789", "--output", "out"])

        self.assertEqual(args.group_page_delay, 4.0)
        self.assertEqual(args.group_page_jitter, 4.0)
        self.assertEqual(args.group_page_size, 20)
        self.assertEqual(args.request_delay, 2.5)
        self.assertEqual(args.request_jitter, 2.5)
        self.assertFalse(args.fetch_full_comments)
        self.assertEqual(args.comment_request_delay, 3.0)
        self.assertEqual(args.comment_request_jitter, 2.0)

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
