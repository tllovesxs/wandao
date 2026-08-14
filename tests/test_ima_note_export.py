import argparse
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from plugins.ima.backend import ima_knowledge as ima
from wandao_core.checkpoint import WandaoCheckpoint


class FakeClient:
    def __init__(self, note_responses):
        self.note_responses = iter(note_responses)
        self.note_calls = []
        self.args = None

    def note(self, action, payload):
        self.note_calls.append((action, payload))
        response = next(self.note_responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeImageResponse:
    def __init__(
        self,
        body,
        content_type,
        url="https://cdn.example.test/a.png",
        content_length="",
    ):
        self.body = body
        self.headers = {"Content-Type": content_type}
        if content_length:
            self.headers["Content-Length"] = content_length
        self.url = url

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class ImaNoteExportTests(unittest.TestCase):
    def export_args(self, output):
        return argparse.Namespace(
            output=str(output),
            checkpoint_file="",
            doc_id=[],
            knowledge_base_id="",
            resume=False,
            retry_failed=False,
            progress_every=1,
        )

    def test_get_note_export_text_requests_markdown_first(self):
        markdown = "# Note\n\n**bold**\n\n![image](https://cdn.example.test/a.png)"
        client = FakeClient([{"content": markdown}])

        text, downgraded = ima.get_note_export_text(client, "note-id", "Note")

        self.assertEqual(text, markdown)
        self.assertFalse(downgraded)
        self.assertEqual(
            client.note_calls,
            [("get_doc_content", {"note_id": "note-id", "target_content_format": 1})],
        )

    def test_get_note_export_text_preserves_markdown_verbatim_without_synthesized_title(self):
        markdown = "    indented code\n\nparagraph with trailing spaces  \n"
        client = FakeClient([{"content": markdown}])

        text, downgraded = ima.get_note_export_text(client, "note-id", "Note")

        self.assertEqual(text, markdown)
        self.assertFalse(downgraded)

    def test_get_note_export_text_propagates_markdown_failure_without_plain_text_retry(self):
        client = FakeClient([ima.ImaError("markdown failed"), {"content": "plain body"}])

        with self.assertRaisesRegex(ima.ImaError, "markdown failed"):
            ima.get_note_export_text(client, "note-id", "Note")

        self.assertEqual(len(client.note_calls), 1)
        self.assertEqual(client.note_calls[0][1]["target_content_format"], 1)

    def test_localize_note_images_writes_assets_and_rewrites_links(self):
        markdown = (
            "# Note\n\n![first](https://cdn.example.test/a)\n\n"
            "![second](https://cdn.example.test/b.jpg)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            responses = [
                (b"png", "image/png", "https://cdn.example.test/a"),
                (b"jpg", "image/jpeg", "https://cdn.example.test/b.jpg"),
            ]
            with mock.patch.object(ima, "download_note_image", side_effect=responses):
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            self.assertEqual(saved, 2)
            self.assertEqual(failures, [])
            self.assertIn("![first](Note_assets/image-001.png)", rewritten)
            self.assertIn("![second](Note_assets/image-002.jpg)", rewritten)
            self.assertEqual((Path(directory) / "Note_assets" / "image-001.png").read_bytes(), b"png")

    def test_localize_note_images_without_images_does_not_create_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"

            rewritten, saved, failures = ima.localize_note_images("# Note\n\nBody", md_path)

            self.assertEqual(rewritten, "# Note\n\nBody")
            self.assertEqual(saved, 0)
            self.assertEqual(failures, [])
            self.assertFalse((Path(directory) / "Note_assets").exists())

    def test_localize_note_images_deduplicates_urls(self):
        markdown = "![one](https://cdn.example.test/a.png) ![two](https://cdn.example.test/a.png)"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", "https://cdn.example.test/a.png"),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            self.assertEqual(download.call_count, 1)
            self.assertEqual(saved, 1)
            self.assertEqual(rewritten.count("Note_assets/image-001.png"), 2)
            self.assertEqual(failures, [])

    def test_localize_note_images_rewrites_reference_style_image_definition(self):
        url = "https://cdn.example.test/reference.png?key=one&signature=two"
        markdown = f"![diagram][Asset ID]\n\n[asset id]: <{url}> \"caption\"\n"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", url),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(url)
            self.assertIn("![diagram][Asset ID]", rewritten)
            self.assertIn('[asset id]: <Note_assets/image-001.png> "caption"', rewritten)
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_rewrites_html_img_src(self):
        markdown_url = "https://cdn.example.test/image.webp?key=one&amp;signature=two"
        download_url = "https://cdn.example.test/image.webp?key=one&signature=two"
        markdown = f'<figure><img alt="diagram" src="{markdown_url}"></figure>'
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"webp", "image/webp", download_url),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(download_url)
            self.assertEqual(
                rewritten,
                '<figure><img alt="diagram" src="Note_assets/image-001.webp"></figure>',
            )
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_resumes_after_unclosed_blockquote_fence(self):
        code_url = "https://cdn.example.test/code.png"
        image_url = "https://cdn.example.test/outside.png"
        markdown = (
            f"> ```md\n> ![code]({code_url})\n\n"
            f"![outside]({image_url})\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", image_url),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(image_url)
            self.assertIn(f"![code]({code_url})", rewritten)
            self.assertIn("![outside](Note_assets/image-001.png)", rewritten)
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_unescapes_ima_signed_url_query(self):
        markdown = "![one](https://cdn.example.test/a.webp?key=one\\&signature=two)"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(
                    b"webp",
                    "image/webp",
                    "https://cdn.example.test/a.webp?key=one&signature=two",
                ),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(
                "https://cdn.example.test/a.webp?key=one&signature=two"
            )
            self.assertEqual(rewritten, "![one](Note_assets/image-001.webp)")
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_supports_balanced_parentheses_in_url(self):
        url = "https://cdn.example.test/a_(b).png"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", url),
            ):
                rewritten, saved, failures = ima.localize_note_images(
                    f"before ![one]({url}) after", md_path
                )

            self.assertEqual(rewritten, "before ![one](Note_assets/image-001.png) after")
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_ignores_code_and_escaped_images(self):
        real_url = "https://cdn.example.test/real.png"
        markdown = (
            "\\![escaped](https://cdn.example.test/escaped.png)\n"
            "`![inline](https://cdn.example.test/inline.png)`\n"
            "```markdown\n![fenced](https://cdn.example.test/fenced.png)\n```\n"
            f"![real]({real_url})\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", real_url),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(real_url)
            self.assertIn("\\![escaped](https://cdn.example.test/escaped.png)", rewritten)
            self.assertIn("`![inline](https://cdn.example.test/inline.png)`", rewritten)
            self.assertIn("![fenced](https://cdn.example.test/fenced.png)", rewritten)
            self.assertIn("![real](Note_assets/image-001.png)", rewritten)
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_ignores_indented_code_and_html_comments(self):
        real_markdown_url = "https://cdn.example.test/a_\\(b\\).png"
        real_download_url = "https://cdn.example.test/a_(b).png"
        markdown = (
            "    ![indented](https://cdn.example.test/indented.png)\n"
            ">     ![quoted-code](https://cdn.example.test/quoted-code.png)\n"
            "<!-- ![comment](https://cdn.example.test/comment.png) -->\n"
            f"\\`literal ![real]({real_markdown_url}) backtick\\`\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", real_download_url),
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            download.assert_called_once_with(real_download_url)
            self.assertIn("![indented](https://cdn.example.test/indented.png)", rewritten)
            self.assertIn(
                "![quoted-code](https://cdn.example.test/quoted-code.png)",
                rewritten,
            )
            self.assertIn("![comment](https://cdn.example.test/comment.png)", rewritten)
            self.assertIn("![real](Note_assets/image-001.png)", rewritten)
            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])

    def test_localize_note_images_respects_list_and_block_context(self):
        list_url = "https://cdn.example.test/list.png"
        after_fence_url = "https://cdn.example.test/after.png"
        markdown = (
            "- item\n\n"
            f"    ![list]({list_url})\n\n"
            "```markdown\n<!-- unclosed inside code\n```\n"
            f"![after]({after_fence_url})\n"
            "    ![code](https://cdn.example.test/code.png)\n"
            " \t![mixed](https://cdn.example.test/mixed.png)\n"
        )

        def image_response(url):
            return b"png", "image/png", url

        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                side_effect=image_response,
            ) as download:
                rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            self.assertEqual(
                [call.args[0] for call in download.call_args_list],
                [list_url, after_fence_url],
            )
            self.assertIn("![list](Note_assets/image-001.png)", rewritten)
            self.assertIn("![after](Note_assets/image-002.png)", rewritten)
            self.assertIn("![code](https://cdn.example.test/code.png)", rewritten)
            self.assertIn("![mixed](https://cdn.example.test/mixed.png)", rewritten)
            self.assertEqual(saved, 2)
            self.assertEqual(failures, [])

    def test_localize_note_images_keeps_remote_url_on_failure(self):
        url = "https://cdn.example.test/a.png"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(ima, "download_note_image", side_effect=ima.ImaError("HTTP 500")):
                rewritten, saved, failures = ima.localize_note_images(f"![one]({url})", md_path)

            self.assertEqual(rewritten, f"![one]({url})")
            self.assertEqual(saved, 0)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["kind"], "image")
            self.assertFalse((Path(directory) / "Note_assets").exists())

    def test_localize_note_images_records_malformed_url_as_resource_failure(self):
        markdown = "![one](https://[bad.example/a.png)"
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"

            rewritten, saved, failures = ima.localize_note_images(markdown, md_path)

            self.assertEqual(rewritten, markdown)
            self.assertEqual(saved, 0)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["url"], "[invalid image URL]")
            self.assertFalse((Path(directory) / "Note_assets").exists())

    def test_localize_note_images_does_not_swallow_stop_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                side_effect=ima.ImaStopped("stopped"),
            ):
                with self.assertRaisesRegex(ima.ImaStopped, "stopped"):
                    ima.localize_note_images("![one](https://cdn.example.test/a.png)", md_path)

    def test_localize_note_images_uses_final_markdown_stem(self):
        with tempfile.TemporaryDirectory() as directory:
            md_path = Path(directory) / "Note_2.md"
            with mock.patch.object(
                ima,
                "download_note_image",
                return_value=(b"png", "image/png", "https://cdn.example.test/a.png"),
            ):
                rewritten, saved, failures = ima.localize_note_images(
                    "![one](https://cdn.example.test/a.png)", md_path
                )

            self.assertEqual(saved, 1)
            self.assertEqual(failures, [])
            self.assertEqual(rewritten, "![one](Note_2_assets/image-001.png)")
            self.assertTrue((Path(directory) / "Note_2_assets" / "image-001.png").is_file())

    def test_validate_remote_image_url_rejects_private_destinations(self):
        urls = (
            "http://127.0.0.1/a.png",
            "http://[::1]/a.png",
            "http://user:pass@example.com/a.png",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ima.ImaError):
                ima.validate_remote_image_url(url)

    def test_validate_remote_image_url_rejects_non_global_destination(self):
        resolution = [
            (ima.socket.AF_INET, ima.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 443))
        ]
        with mock.patch.object(ima.socket, "getaddrinfo", return_value=resolution):
            with self.assertRaisesRegex(ima.ImaError, "公网"):
                ima.validate_remote_image_url("https://cdn.example.test/a.png")

    def test_pinned_http_connection_connects_to_validated_ip(self):
        resolution = [
            (ima.socket.AF_INET, ima.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))
        ]
        connection = ima.PinnedHTTPConnection("cdn.example.test", 80, timeout=5)
        fake_socket = mock.Mock()
        connection._create_connection = mock.Mock(return_value=fake_socket)

        with mock.patch.object(ima.socket, "getaddrinfo", return_value=resolution):
            connection.connect()

        connection._create_connection.assert_called_once_with(
            ("8.8.8.8", 80), 5, None
        )

    def test_pinned_https_connection_preserves_hostname_for_tls(self):
        connection = ima.PinnedHTTPSConnection("cdn.example.test", 443, timeout=5)
        raw_socket = mock.Mock()
        wrapped_socket = mock.Mock()
        connection._context = mock.Mock()
        connection._context.wrap_socket.return_value = wrapped_socket

        def connect_pinned(target):
            target.sock = raw_socket

        with mock.patch.object(
            ima,
            "connect_to_public_image_address",
            side_effect=connect_pinned,
        ):
            connection.connect()

        connection._context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="cdn.example.test",
        )
        self.assertIs(connection.sock, wrapped_socket)

    def test_read_note_image_response_enforces_size_limit(self):
        response = FakeImageResponse(b"12345", "image/png")

        with self.assertRaisesRegex(ima.ImaError, "大小限制"):
            ima.read_note_image_response(response, max_bytes=4)

    def test_download_note_image_rejects_non_image_response(self):
        opener = mock.Mock()
        opener.open.return_value = FakeImageResponse(b"<html>error</html>", "text/html")
        with (
            mock.patch.object(ima, "validate_remote_image_url", side_effect=lambda url: url),
            mock.patch.object(ima.urllib.request, "build_opener", return_value=opener),
        ):
            with self.assertRaisesRegex(ima.ImaError, "不是图片"):
                ima.download_note_image("https://cdn.example.test/a.png")

    def test_download_note_image_does_not_resolve_again_after_pinned_response(self):
        opener = mock.Mock()
        opener.open.return_value = FakeImageResponse(b"png", "image/png")
        with (
            mock.patch.object(ima, "validate_remote_image_url") as validate,
            mock.patch.object(ima.urllib.request, "build_opener", return_value=opener),
        ):
            body, content_type, final_url = ima.download_note_image(
                "https://cdn.example.test/a.png"
            )

        validate.assert_called_once_with("https://cdn.example.test/a.png")
        self.assertEqual((body, content_type, final_url), (
            b"png",
            "image/png",
            "https://cdn.example.test/a.png",
        ))

    def test_safe_note_image_url_removes_credentials_and_query(self):
        sanitized = ima.safe_note_image_url(
            "https://user:secret@example.com/image.png?token=private#fragment"
        )

        self.assertEqual(sanitized, "https://example.com/image.png")

    def test_save_media_entry_keeps_non_note_export_contract(self):
        client = mock.Mock()
        client.wiki.return_value = {
            "media_type": 7,
            "url_info": {"url": "https://cdn.example.test/note.md"},
        }
        entry = ima.KnowledgeEntry("kb", "KB", "media", "Doc.md", "", [], False, 7)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                ima,
                "download_url",
                return_value=(b"# Doc", "text/markdown"),
            ):
                status, path, reason, resource_failures = ima.save_media_entry(
                    client,
                    entry,
                    Path(directory),
                )

            self.assertEqual(status, "exported")
            self.assertEqual(path.read_bytes(), b"# Doc")
            self.assertEqual(reason, "")
            self.assertEqual(resource_failures, [])

    def test_save_note_entry_reuses_checkpoint_path(self):
        client = mock.Mock()
        client.wiki.return_value = {"notebook_ext_info": {"notebook_id": "note-id"}}
        entry = ima.KnowledgeEntry("kb", "KB", "note", "Note", "", [], False, 11)
        with tempfile.TemporaryDirectory() as directory:
            target_dir = Path(directory) / "KB"
            target_dir.mkdir()
            existing_path = target_dir / "Note.md"
            existing_path.write_text("old", encoding="utf-8")
            with (
                mock.patch.object(
                    ima,
                    "get_note_export_text",
                    return_value=("# Note\n\nnew", False),
                ),
                mock.patch.object(
                    ima,
                    "localize_note_images",
                    return_value=("# Note\n\nnew", 0, []),
                ) as localize,
            ):
                path, resource_failures = ima.save_note_entry(
                    client,
                    entry,
                    target_dir,
                    existing_path=existing_path,
                )

            self.assertEqual(path, existing_path)
            self.assertEqual(path.read_text("utf-8"), "# Note\n\nnew")
            localize.assert_called_once_with("# Note\n\nnew", existing_path)
            self.assertEqual(resource_failures, [])
            self.assertFalse((target_dir / "Note_2.md").exists())

    def test_export_selected_reports_note_image_failures_as_partial(self):
        kb = ima.KnowledgeBase("kb", "KB")
        entry = ima.KnowledgeEntry("kb", "KB", "note", "Note", "", [], False, 11)
        failure = {"kind": "image", "url": "https://cdn.example.test/a.png", "error": "HTTP 500", "index": "1"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "KB" / "Note.md"
            with (
                mock.patch.object(ima, "scan_remote_tree", return_value=([kb], [entry])),
                mock.patch.object(
                    ima,
                    "save_media_entry",
                    return_value=("exported_note", path, "", [failure]),
                ),
                mock.patch.object(ima, "emit"),
            ):
                report = ima.export_selected(object(), self.export_args(directory))

        self.assertEqual(report["exportedDocs"], 1)
        self.assertEqual(report["failureCount"], 0)
        self.assertEqual(report["resourceFailureCount"], 1)
        self.assertEqual(report["resourceFailures"][0]["document"], "Note")
        self.assertEqual(report["outcome"], "partial")

    def test_export_selected_reports_clean_resource_outcome(self):
        kb = ima.KnowledgeBase("kb", "KB")
        entry = ima.KnowledgeEntry("kb", "KB", "note", "Note", "", [], False, 11)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "KB" / "Note.md"
            with (
                mock.patch.object(ima, "scan_remote_tree", return_value=([kb], [entry])),
                mock.patch.object(
                    ima,
                    "save_media_entry",
                    return_value=("exported_note", path, "", []),
                ),
                mock.patch.object(ima, "emit"),
            ):
                report = ima.export_selected(object(), self.export_args(directory))

        self.assertEqual(report["resourceFailureCount"], 0)
        self.assertEqual(report["resourceFailures"], [])
        self.assertEqual(report["outcome"], "completed")

    def test_export_selected_marks_resource_failure_item_retryable(self):
        kb = ima.KnowledgeBase("kb", "KB")
        entry = ima.KnowledgeEntry("kb", "KB", "note", "Note", "", [], False, 11)
        failure = {
            "kind": "image",
            "url": "https://cdn.example.test/a.png",
            "error": "HTTP 500",
            "index": "1",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "KB" / "Note.md"
            path.parent.mkdir(parents=True)
            path.write_text("![remote](https://cdn.example.test/a.png)", encoding="utf-8")
            checkpoint_file = Path(directory) / "checkpoint.sqlite"
            args = self.export_args(directory)
            args.checkpoint_file = str(checkpoint_file)
            args.checkpoint_task_id = "ima-resource-retry"
            save_entry = mock.Mock(
                return_value=("exported_note", path, "", [failure])
            )

            original_fail_item = WandaoCheckpoint.fail_item

            def legacy_fail_item(checkpoint, item_key, error, retryable=True):
                return original_fail_item(checkpoint, item_key, error, retryable)

            with (
                mock.patch.object(ima, "scan_remote_tree", return_value=([kb], [entry])),
                mock.patch.object(ima, "save_media_entry", save_entry),
                mock.patch.object(ima, "emit"),
                mock.patch.object(WandaoCheckpoint, "fail_item", legacy_fail_item),
            ):
                ima.export_selected(object(), args)

            with closing(sqlite3.connect(checkpoint_file)) as connection:
                failed_row = connection.execute(
                    "SELECT status, local_path, metadata_json "
                    "FROM items WHERE item_key = ?",
                    (f"ima:entry:{entry.export_id}",),
                ).fetchone()

            def retry_save(_client, _entry, _output, *, existing_note_path=None):
                self.assertEqual(existing_note_path, path)
                path.write_text("![local](Note_assets/image-001.png)", encoding="utf-8")
                return "exported_note", path, "", []

            args.retry_failed = True
            save_entry.side_effect = retry_save
            with (
                mock.patch.object(ima, "scan_remote_tree", return_value=([kb], [entry])),
                mock.patch.object(ima, "save_media_entry", save_entry),
                mock.patch.object(ima, "emit"),
            ):
                retry_report = ima.export_selected(object(), args)

            with closing(sqlite3.connect(checkpoint_file)) as connection:
                completed_row = connection.execute(
                    "SELECT status, local_path FROM items WHERE item_key = ?",
                    (f"ima:entry:{entry.export_id}",),
                ).fetchone()

            self.assertEqual(failed_row[0], "failed")
            self.assertEqual(failed_row[1], str(path))
            metadata = json.loads(failed_row[2])
            self.assertEqual(len(metadata["resourceFailures"]), 1)
            self.assertEqual(retry_report["outcome"], "completed")
            self.assertEqual(completed_row, ("completed", str(path)))
            self.assertFalse((path.parent / "Note_2.md").exists())

    def test_ima_plugin_version_is_1_0_4(self):
        manifest = Path(ima.__file__).resolve().parents[1] / "plugin.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(payload["version"], "1.0.4")


if __name__ == "__main__":
    unittest.main()
