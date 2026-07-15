import tempfile
import unittest
from pathlib import Path
from unittest import mock

import export_zsxq
from export_zsxq import (
    compare_zsxq_create_time,
    export_sequence_from_name,
    group_topic_reaches_watermark,
    scan_max_export_sequence,
)


class ZsxqIssue43Tests(unittest.TestCase):
    def test_sequence_scan_continues_from_existing_immediate_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "00-知识星球入口.md").write_text("index", encoding="utf-8")
            (root / "01-第一篇.md").write_text("one", encoding="utf-8")
            (root / "07-第七篇.md").write_text("seven", encoding="utf-8")
            (root / "12-分层目录").mkdir()
            (root / "88-用户压缩包.zip").write_bytes(b"zip")
            (root / "普通说明.md").write_text("notes", encoding="utf-8")
            nested = root / "无编号目录"
            nested.mkdir()
            (nested / "99-不应影响父目录.md").write_text("nested", encoding="utf-8")

            self.assertEqual(scan_max_export_sequence(root), 12)
            self.assertEqual(scan_max_export_sequence(nested), 99)

    def test_reserved_and_malformed_names_do_not_allocate_sequences(self) -> None:
        self.assertIsNone(export_sequence_from_name("00-导出报告.json"))
        self.assertIsNone(export_sequence_from_name("标题-01.md"))
        self.assertIsNone(export_sequence_from_name("-01.md"))
        self.assertEqual(export_sequence_from_name("003-标题.md"), 3)

    def test_timestamp_comparison_accepts_zsxq_timezone_formats(self) -> None:
        self.assertEqual(
            compare_zsxq_create_time("2026-07-15T10:00:00.000+0800", "2026-07-15T10:00:00.000+08:00"),
            0,
        )
        self.assertGreater(
            compare_zsxq_create_time("2026-07-15T10:00:00.001+0800", "2026-07-15T10:00:00.000+0800"),
            0,
        )

    def test_new_posts_are_kept_until_previous_watermark_is_reached(self) -> None:
        watermark_time = "2026-07-15T10:00:00.000+0800"
        watermark_id = "100"

        self.assertFalse(
            group_topic_reaches_watermark(
                {"topic_id": "102", "create_time": "2026-07-15T10:05:00.000+0800"},
                watermark_time,
                watermark_id,
            )
        )
        # A distinct topic with the same timestamp must not be skipped.
        self.assertFalse(
            group_topic_reaches_watermark(
                {"topic_id": "101", "create_time": watermark_time},
                watermark_time,
                watermark_id,
            )
        )
        self.assertTrue(
            group_topic_reaches_watermark(
                {"topic_id": "100", "create_time": watermark_time},
                watermark_time,
                watermark_id,
            )
        )
        self.assertTrue(
            group_topic_reaches_watermark(
                {"topic_id": "99", "create_time": "2026-07-15T09:59:59.999+0800"},
                watermark_time,
                watermark_id,
            )
        )

    def test_resumed_group_exports_new_post_with_next_filesystem_sequence(self) -> None:
        class FakeCdp:
            def close(self) -> None:
                return None

        old_topic = {
            "topic_id": "100",
            "create_time": "2026-07-15T10:00:00.000+0800",
            "talk": {"text": "旧帖子正文"},
        }
        newer_topic = {
            "topic_id": "102",
            "create_time": "2026-07-15T12:00:00.000+0800",
            "talk": {"text": "更新的帖子正文"},
        }
        new_topic = {
            "topic_id": "101",
            "create_time": "2026-07-15T11:00:00.000+0800",
            "talk": {"text": "新增帖子正文"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            checkpoint = root / "checkpoint.sqlite"

            def run(topics: list[dict[str, object]], *, resume: bool, task_id: str) -> dict[str, object]:
                argv = [
                    "--entry-url",
                    "https://wx.zsxq.com/group/123456789",
                    "--output",
                    str(output),
                    "--toc-mode",
                    "toc",
                    "--limit",
                    "1",
                    "--skip-auth-load",
                    "--checkpoint-file",
                    str(checkpoint),
                    "--checkpoint-task-id",
                    task_id,
                    "--incremental",
                ]
                if resume:
                    argv.append("--resume")
                args = export_zsxq.parse_args(argv)

                def fetch_page(*_args, **_kwargs):
                    return {"ok": True, "topics": topics}

                with (
                    mock.patch.object(export_zsxq, "connect_browser", return_value=(FakeCdp(), None)),
                    mock.patch.object(export_zsxq, "navigate_with_retry"),
                    mock.patch.object(export_zsxq, "fetch_group_topics_page", side_effect=fetch_page),
                ):
                    return export_zsxq.export_entry(args)

            first_report = run([old_topic], resume=False, task_id="job-1")
            second_report = run([newer_topic, new_topic, old_topic], resume=True, task_id="job-2")
            third_report = run([newer_topic, new_topic, old_topic], resume=True, task_id="job-3")

            self.assertEqual(first_report["exportedDocs"], 1)
            self.assertEqual(second_report["exportedDocs"], 1)
            self.assertEqual(second_report["newestDiscoveredDocs"], 2)
            self.assertEqual(second_report["exportedItems"][0]["sourceId"], "102")
            self.assertEqual(second_report["exportedItems"][0]["sequence"], 2)
            self.assertTrue((output / second_report["exportedItems"][0]["localPath"]).exists())
            self.assertEqual(len(second_report["pendingItems"]), 1)
            self.assertEqual(third_report["exportedItems"][0]["sourceId"], "101")
            self.assertEqual(third_report["exportedItems"][0]["sequence"], 3)
            self.assertEqual(third_report["pendingItems"], [])

    def test_stopped_group_report_lists_exported_and_pending_files(self) -> None:
        class FakeCdp:
            def close(self) -> None:
                return None

        topics = [
            {
                "topic_id": str(topic_id),
                "create_time": f"2026-07-15T{hour:02d}:00:00.000+0800",
                "talk": {"text": f"帖子 {topic_id}"},
            }
            for topic_id, hour in ((102, 12), (101, 11))
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            args = export_zsxq.parse_args(
                [
                    "--entry-url",
                    "https://wx.zsxq.com/group/123456789",
                    "--output",
                    str(output),
                    "--toc-mode",
                    "toc",
                    "--limit",
                    "2",
                    "--skip-auth-load",
                    "--checkpoint-file",
                    str(root / "checkpoint.sqlite"),
                    "--incremental",
                ]
            )

            with (
                mock.patch.object(export_zsxq, "connect_browser", return_value=(FakeCdp(), None)),
                mock.patch.object(export_zsxq, "navigate_with_retry"),
                mock.patch.object(
                    export_zsxq,
                    "fetch_group_topics_page",
                    return_value={"ok": True, "topics": topics},
                ),
                mock.patch.object(export_zsxq, "stop_requested", side_effect=[False, True]),
            ):
                report = export_zsxq.export_entry(args)

            self.assertTrue(report["stopped"])
            self.assertEqual(len(report["exportedItems"]), 1)
            self.assertEqual(len(report["pendingItems"]), 1)
            self.assertEqual(report["exportedItems"][0]["sourceId"], "102")
            self.assertEqual(report["pendingItems"][0]["sourceId"], "101")
            self.assertTrue((output / "00-导出报告.json").exists())
            self.assertTrue((output / ".wandao" / "reports" / f"zsxq-{report['runId']}.json").exists())

    def test_resource_retry_reuses_original_markdown_path(self) -> None:
        class FakeCdp:
            def close(self) -> None:
                return None

        topic = {
            "topic_id": "100",
            "create_time": "2026-07-15T10:00:00.000+0800",
            "talk": {"text": "需要重试的帖子"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            checkpoint = root / "checkpoint.sqlite"

            def run(*, resume: bool, task_id: str, image_errors: list[dict[str, str]]) -> dict[str, object]:
                argv = [
                    "--entry-url",
                    "https://wx.zsxq.com/group/123456789",
                    "--output",
                    str(output),
                    "--toc-mode",
                    "toc",
                    "--limit",
                    "1",
                    "--skip-auth-load",
                    "--checkpoint-file",
                    str(checkpoint),
                    "--checkpoint-task-id",
                    task_id,
                    "--incremental",
                ]
                if resume:
                    argv.append("--resume")
                args = export_zsxq.parse_args(argv)
                with (
                    mock.patch.object(export_zsxq, "connect_browser", return_value=(FakeCdp(), None)),
                    mock.patch.object(export_zsxq, "navigate_with_retry"),
                    mock.patch.object(
                        export_zsxq,
                        "fetch_group_topics_page",
                        return_value={"ok": True, "topics": [topic]},
                    ),
                    mock.patch.object(
                        export_zsxq,
                        "resolve_toc_item",
                        return_value={
                            "title": "需要重试的帖子",
                            "topicId": "100",
                            "topicUid": "100",
                            "topicUrl": "https://wx.zsxq.com/topic/100",
                            "markdown": "# 需要重试的帖子\n",
                            "images": [],
                            "files": [],
                            "zsxqLinks": [],
                        },
                    ),
                    mock.patch.object(
                        export_zsxq,
                        "localize_images",
                        side_effect=lambda markdown, *_args, **_kwargs: (markdown, 0 if image_errors else 1, image_errors),
                    ),
                ):
                    return export_zsxq.export_entry(args)

            first_report = run(
                resume=False,
                task_id="job-1",
                image_errors=[{"url": "https://example.com/image.png", "error": "temporary"}],
            )
            second_report = run(resume=True, task_id="job-2", image_errors=[])

            self.assertEqual(first_report["exportedItems"][0]["sequence"], 1)
            self.assertEqual(len(first_report["failedItems"]), 1)
            self.assertEqual(second_report["exportedItems"][0]["sequence"], 1)
            self.assertEqual(first_report["exportedItems"][0]["localPath"], second_report["exportedItems"][0]["localPath"])
            self.assertEqual(len([path for path in output.glob("*.md") if export_sequence_from_name(path.name)]), 1)


if __name__ == "__main__":
    unittest.main()
