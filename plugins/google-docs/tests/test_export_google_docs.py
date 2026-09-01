import base64
import importlib.util
import json
import tempfile
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = PLUGIN_ROOT / "backend" / "export_google_docs.py"
FIXTURES = PLUGIN_ROOT / "fixtures"


def load_backend():
    spec = importlib.util.spec_from_file_location("google_docs_backend", BACKEND_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backend = load_backend()


class GoogleDocsPluginTests(unittest.TestCase):
    def test_relative_output_is_resolved_inside_plugin_data_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict("os.environ", {"WANDAO_PLUGIN_DATA_DIR": temp}, clear=False):
                args = backend.parse_args(["--output", "exports/google-docs"])
        self.assertEqual(
            Path(args.output),
            Path(temp).resolve() / "exports" / "google-docs",
        )

    def test_manifest_and_provider_files_are_consistent(self):
        manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
        provider_path = PLUGIN_ROOT / manifest["entrypoints"]["providers"][0]
        provider = json.loads(provider_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "google-docs")
        self.assertEqual(provider["id"], "google-docs-export")
        self.assertEqual(provider_path.parent.name, provider["id"])
        self.assertEqual({action["id"] for action in provider["actions"]}, {"login", "scan", "export"})
        self.assertTrue(provider["capabilities"]["images"])
        self.assertTrue(provider["capabilities"]["attachments"])
        self.assertFalse(provider["capabilities"]["batch"])
        self.assertFalse(provider["capabilities"]["retryFailures"])
        export_action = next(action for action in provider["actions"] if action["id"] == "export")
        self.assertNotEqual(export_action.get("includeSelection"), False)

    def test_accepts_only_google_docs_document_urls(self):
        source = backend.parse_google_docs_url(
            "https://docs.google.com/document/d/abc123/edit?usp=sharing#heading=h.test"
        )
        self.assertEqual(source.document_id, "abc123")
        self.assertEqual(source.canonical_url, "https://docs.google.com/document/d/abc123/edit")
        for url in (
            "http://docs.google.com/document/d/abc123/edit",
            "https://docs.google.com.evil.example/document/d/abc123/edit",
            "https://docs.google.com/spreadsheets/d/abc123/edit",
            "https://drive.google.com/drive/folders/abc123",
            "https://user:password@docs.google.com/document/d/abc123/edit",
            "https://docs.google.com:8443/document/d/abc123/edit",
        ):
            with self.subTest(url=url):
                with self.assertRaises(backend.ExportError):
                    backend.parse_google_docs_url(url)

    def test_builds_minimal_root_and_document_nodes_without_headings(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html("<p>没有标题的正文</p>", "项目资源演示")
        nodes = backend.build_document_nodes(source, document)
        self.assertEqual(nodes[0]["nodeId"], "root")
        self.assertEqual(nodes[0]["selectable"], False)
        self.assertEqual(nodes[1]["nodeId"], "fixture-document-id")
        self.assertEqual(nodes[1]["parentNodeId"], "root")
        self.assertEqual(nodes[1]["selectable"], True)

    def test_builds_selectable_heading_outline_with_hierarchy(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        nodes = backend.build_document_nodes(source, document)
        headings = nodes[1:]
        self.assertEqual([node["title"] for node in headings], [
            "第一部分", "环境部署", "Windows", "使用说明", "第二部分", "跳级小节", "使用说明",
        ])
        self.assertTrue(all(node["selectable"] for node in headings))
        self.assertEqual(headings[0]["parentNodeId"], "root")
        self.assertEqual(headings[1]["parentNodeId"], headings[0]["nodeId"])
        self.assertEqual(headings[2]["parentNodeId"], headings[1]["nodeId"])
        self.assertEqual(headings[3]["parentNodeId"], headings[0]["nodeId"])
        self.assertEqual(headings[4]["parentNodeId"], "root")
        self.assertEqual(headings[5]["parentNodeId"], headings[4]["nodeId"])
        self.assertEqual(headings[6]["parentNodeId"], headings[4]["nodeId"])
        self.assertEqual(len({node["nodeId"] for node in headings}), len(headings))
        self.assertEqual(
            [node["nodeId"] for node in headings],
            [node["nodeId"] for node in backend.build_document_nodes(source, document)[1:]],
        )

    def test_selected_heading_exports_its_subtree_with_ancestor_context(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        nodes = backend.build_document_nodes(source, document)
        environment = next(node for node in nodes if node["title"] == "环境部署")
        markdown, images, attachments = backend.render_selected_markdown(document, source, [environment["exportId"]])
        self.assertIn("# 第一部分", markdown)
        self.assertNotIn("第一部分直属正文", markdown)
        self.assertIn("## 环境部署", markdown)
        self.assertIn("### Windows", markdown)
        self.assertNotIn("第一处使用说明", markdown)
        self.assertNotIn("第二部分", markdown)
        self.assertNotIn("文档开头的脱敏说明", markdown)
        self.assertEqual(len(images), 1)
        self.assertEqual(attachments, [])

    def test_parent_and_child_selection_does_not_duplicate_sections(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        nodes = backend.build_document_nodes(source, document)
        first_part = next(node for node in nodes if node["title"] == "第一部分")
        environment = next(node for node in nodes if node["title"] == "环境部署")
        markdown, _, _ = backend.render_selected_markdown(
            document,
            source,
            [first_part["exportId"], environment["exportId"]],
        )
        self.assertEqual(markdown.count("## 环境部署"), 1)
        self.assertEqual(markdown.count("### Windows"), 1)
        self.assertIn("第一部分直属正文", markdown)
        self.assertNotIn("第二部分", markdown)

    def test_all_heading_selection_preserves_preamble_and_full_document(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        nodes = backend.build_document_nodes(source, document)
        selected = [node["exportId"] for node in nodes if node["selectable"]]
        markdown, images, attachments = backend.render_selected_markdown(document, source, selected)
        expected, expected_images, expected_attachments = backend.render_markdown(document)
        self.assertEqual(markdown, expected)
        self.assertEqual(images, expected_images)
        self.assertEqual(attachments, expected_attachments)

    def test_unknown_heading_selection_requires_rescan(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(backend.GoogleDocsError, "重新读取"):
            backend.render_selected_markdown(document, source, ["missing-heading"])

    def test_nested_heading_containers_keep_outline_and_selection_in_sync(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html(
            "<html><head><title>嵌套示例</title></head><body><div><h1>父标题</h1><p>父正文</p><h2>子标题</h2><p>子正文</p></div></body></html>"
        )
        child = next(node for node in backend.build_document_nodes(source, document) if node["title"] == "子标题")
        markdown, _, _ = backend.render_selected_markdown(document, source, [child["exportId"]])
        self.assertIn("# 父标题", markdown)
        self.assertNotIn("父正文", markdown)
        self.assertIn("## 子标题", markdown)
        self.assertIn("子正文", markdown)

    def test_list_before_heading_does_not_break_heading_selection(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html(
            "<html><head><title>列表示例</title></head><body><h1>父标题</h1><ul><li>列表项</li></ul><h2>子标题</h2><p>子正文</p></body></html>"
        )
        child = next(node for node in backend.build_document_nodes(source, document) if node["title"] == "子标题")
        markdown, _, _ = backend.render_selected_markdown(document, source, [child["exportId"]])
        self.assertIn("# 父标题", markdown)
        self.assertNotIn("列表项", markdown)
        self.assertIn("## 子标题", markdown)
        self.assertIn("子正文", markdown)

    def test_repeated_node_id_arguments_are_collected(self):
        args = backend.parse_args(["--node-id", "heading-1", "--node-id", "heading-2"])
        self.assertEqual(args.node_id, ["heading-1", "heading-2"])

    def test_parses_html_and_converts_supported_markdown(self):
        html = (FIXTURES / "document-with-resources.html").read_text(encoding="utf-8")
        document = backend.parse_exported_html(html)
        markdown, image_refs, attachment_refs = backend.render_markdown(document)
        expected = (FIXTURES / "expected" / "document.md").read_text(encoding="utf-8")
        self.assertEqual(markdown, expected)
        self.assertEqual(len(image_refs), 1)
        self.assertEqual(len(attachment_refs), 1)
        self.assertNotIn("data:image", markdown)

    def test_reads_plain_html_download(self):
        source = FIXTURES / "plain-export.html"
        self.assertEqual(
            backend.read_downloaded_html(source),
            source.read_text(encoding="utf-8"),
        )

    def test_reads_html_from_valid_zip_download(self):
        expected = (FIXTURES / "plain-export.html").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "export.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("index.html", expected)
            self.assertEqual(backend.read_downloaded_html(archive_path), expected)

    def test_rejects_google_login_page_download(self):
        with self.assertRaisesRegex(backend.GoogleDocsError, "登录"):
            backend.read_downloaded_html(FIXTURES / "login-page.html")

    def test_rejects_google_access_denied_page_download(self):
        with self.assertRaisesRegex(backend.GoogleDocsError, "权限"):
            backend.read_downloaded_html(FIXTURES / "access-denied.html")

    def test_rejects_page_titled_you_need_access(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "access.html"
            path.write_text(
                "<html><head><title>You need access</title></head><body></body></html>",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(backend.GoogleDocsError, "权限"):
                backend.read_downloaded_html(path)

    def test_document_text_is_not_mistaken_for_google_error_page(self):
        html = """<!doctype html><html><head><title>操作说明</title></head>
        <body><p>Sign in 后如果看到 You need access，请联系文档所有者。</p></body></html>"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "instructions.html"
            path.write_bytes(html.encode("utf-8"))
            self.assertEqual(backend.read_downloaded_html(path), html)

    def test_rejects_empty_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.html"
            path.write_bytes(b"")
            with self.assertRaisesRegex(backend.GoogleDocsError, "为空"):
                backend.read_downloaded_html(path)

    def test_rejects_truncated_zip_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "truncated.zip"
            path.write_bytes(b"PK\x03\x04incomplete")
            with self.assertRaisesRegex(backend.GoogleDocsError, "不完整|损坏"):
                backend.read_downloaded_html(path)

    def test_download_candidate_must_be_stable_across_two_observations(self):
        with tempfile.TemporaryDirectory() as temp:
            download_dir = Path(temp)
            before = backend.snapshot_downloads(download_dir)
            downloaded = download_dir / "export.html"
            downloaded.write_text("<html><body>first</body></html>", encoding="utf-8")
            candidate, observed = backend.find_stable_download(download_dir, before, {})
            self.assertIsNone(candidate)
            downloaded.write_text("<html><body>finished</body></html>", encoding="utf-8")
            candidate, observed = backend.find_stable_download(download_dir, before, observed)
            self.assertIsNone(candidate)
            candidate, _ = backend.find_stable_download(download_dir, before, observed)
            self.assertEqual(candidate, downloaded)

    def test_download_candidate_ignores_old_and_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            download_dir = Path(temp)
            old = download_dir / "old.zip"
            old.write_bytes(b"old")
            before = backend.snapshot_downloads(download_dir)
            partial = download_dir / "export.zip.crdownload"
            partial.write_bytes(b"partial")
            candidate, observed = backend.find_stable_download(download_dir, before, {})
            self.assertIsNone(candidate)
            candidate, _ = backend.find_stable_download(download_dir, before, observed)
            self.assertIsNone(candidate)

    def test_saves_fixture_resources_to_assets_and_attachments(self):
        html = (FIXTURES / "document-with-resources.html").read_text(encoding="utf-8")
        document = backend.parse_exported_html(html)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            result = backend.export_document_model(
                document,
                output,
                "项目资源演示",
                resource_files={
                    "assets/source-image.png": (FIXTURES / "assets" / "source-image.png").read_bytes(),
                    "attachments/source-attachment.txt": (FIXTURES / "attachments" / "source-attachment.txt").read_bytes(),
                },
            )
            markdown_path = output / "项目资源演示" / "项目资源演示.md"
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), (FIXTURES / "expected" / "document.md").read_text(encoding="utf-8"))
            self.assertTrue((output / "项目资源演示" / "assets" / "image-001.png").is_file())
            self.assertTrue((output / "项目资源演示" / "attachments" / "attachment-001.txt").is_file())
            self.assertEqual(result["imageCount"], 1)
            self.assertEqual(result["imageSuccessCount"], 1)
            self.assertEqual(result["attachmentCount"], 1)
            self.assertEqual(result["attachmentSuccessCount"], 1)
            self.assertEqual(result["resourceFailures"], [])

    def test_selected_child_export_omits_parent_body_and_parent_resources(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        document = backend.parse_exported_html((FIXTURES / "heading-outline.html").read_text(encoding="utf-8"))
        windows = next(node for node in backend.build_document_nodes(source, document) if node["title"] == "Windows")
        with tempfile.TemporaryDirectory() as temp:
            result = backend.export_document_model(
                document,
                Path(temp),
                document.title,
                source=source,
                selected_node_ids=[windows["exportId"]],
                resource_files={"assets/source-image.png": (FIXTURES / "assets" / "source-image.png").read_bytes()},
            )
            markdown = Path(result["output"]).read_text(encoding="utf-8")
            self.assertIn("# 第一部分", markdown)
            self.assertIn("## 环境部署", markdown)
            self.assertIn("### Windows", markdown)
            self.assertIn("Windows 正文", markdown)
            self.assertNotIn("环境部署正文", markdown)
            self.assertNotIn("assets/image-001", markdown)
            self.assertEqual(result["imageCount"], 0)
            self.assertFalse((Path(result["documentDir"]) / "assets").exists())

    def test_decodes_data_image_without_leaving_base64_in_markdown(self):
        encoded = base64.b64encode((FIXTURES / "assets" / "source-image.png").read_bytes()).decode("ascii")
        html = f'<h1>内嵌图片</h1><p><img src="data:image/png;base64,{encoded}" alt="内嵌"></p>'
        document = backend.parse_exported_html(html)
        with tempfile.TemporaryDirectory() as temp:
            result = backend.export_document_model(document, Path(temp), "内嵌图片")
            markdown = (Path(temp) / "内嵌图片" / "内嵌图片.md").read_text(encoding="utf-8")
            self.assertNotIn("data:image", markdown)
            self.assertIn("assets/image-001.png", markdown)
            self.assertEqual(result["imageSuccessCount"], 1)

    def test_rejects_untrusted_attachment_url(self):
        html = '<p><a data-wandao-attachment="true" href="https://example.com/file.txt">附件</a></p>'
        document = backend.parse_exported_html(html)
        with tempfile.TemporaryDirectory() as temp:
            result = backend.export_document_model(document, Path(temp), "不可信附件")
            self.assertEqual(result["attachmentCount"], 1)
            self.assertEqual(len(result["resourceFailures"]), 1)
            self.assertFalse((Path(temp) / "不可信附件" / "attachments" / "attachment-001.txt").exists())

    def test_progress_payload_contains_resource_statistics(self):
        payload = backend.make_progress_payload(1, 1, 1, 1, 1, 1, "正在完成")
        self.assertEqual(payload["event"], "task.progress")
        self.assertEqual(payload["progress"], {"current": 1, "total": 1})
        self.assertEqual(payload["stats"]["imagesFound"], 1)
        self.assertEqual(payload["stats"]["attachmentsSaved"], 1)

    def test_task_result_has_v1_required_fields(self):
        result = backend.finalize_report(
            {"totalDocs": 1, "exportedDocs": 1, "failures": [], "resourceFailures": []},
            provider="google-docs-export",
            mode="export",
        )
        expected = json.loads((FIXTURES / "expected" / "task-result.json").read_text(encoding="utf-8"))
        for key, value in expected.items():
            self.assertEqual(result[key], value)
        self.assertEqual(result["kind"], "wandao.result")
        self.assertEqual(result["schemaVersion"], 1)

    def test_login_state_requires_live_google_page(self):
        self.assertFalse(backend.login_state_is_complete({"url": "", "readyState": "complete", "bodyText": ""}))
        self.assertFalse(backend.login_state_is_complete({"url": "https://accounts.google.com/", "readyState": "complete", "bodyText": "Sign in"}))
        self.assertFalse(backend.login_state_is_complete({"url": "https://docs.google.com/", "readyState": "complete", "bodyText": ""}))
        self.assertTrue(backend.login_state_is_complete({"url": "https://docs.google.com/", "readyState": "complete", "title": "Google Docs", "bodyText": "Google Docs Home"}))
        self.assertTrue(backend.login_state_is_complete({"url": "https://myaccount.google.com/", "readyState": "complete", "title": "Google Account", "bodyText": "Google Account"}))

    def test_login_state_rejects_closed_or_wrong_page(self):
        for state in (
            None,
            {"url": "chrome://newtab/", "readyState": "complete", "bodyText": ""},
            {"url": "https://docs.google.com/document/d/example/edit", "readyState": "loading", "title": "Google Docs", "bodyText": "Google Docs"},
            {"url": "https://docs.google.com/document/d/example/edit", "readyState": "complete", "title": "Google Docs", "bodyText": "You need access"},
        ):
            with self.subTest(state=state):
                self.assertFalse(backend.login_state_is_complete(state))

    def test_login_waits_for_explicit_user_confirmation(self):
        class FakeCdp:
            def evaluate(self, expression, timeout=10):
                return {"url": "https://docs.google.com/", "readyState": "complete", "title": "Google Docs", "bodyText": "Google Docs Home"}

        args = mock.Mock(wait_seconds=1, stop_event=None)
        with mock.patch.object(backend.time, "time", side_effect=[0, 0, 31]):
            with self.assertRaises(backend.GoogleDocsError):
                backend.wait_for_login_confirmation(FakeCdp(), args, confirmation_reader=lambda: None)
        confirmed = backend.wait_for_login_confirmation(FakeCdp(), args, confirmation_reader=lambda: "\n")
        self.assertTrue(confirmed)

    def test_login_requires_confirmation_and_verifies_session_before_success(self):
        cdp = mock.Mock()
        process = mock.Mock()
        args = mock.Mock(wait_seconds=30, close_started_chrome=False, stop_event=None)
        result = {"kind": "wandao.result", "schemaVersion": 1, "provider": "google-docs-export", "mode": "login", "failures": []}
        with mock.patch.object(backend, "connect_google_docs_browser", return_value=(cdp, process)) as connect:
            with mock.patch.object(backend, "wait_for_login_confirmation", return_value=True) as wait:
                with mock.patch.object(backend, "verify_login_session", return_value={"url": "https://docs.google.com/", "title": "Google Docs"}) as verify:
                    with mock.patch.object(backend, "finalize_report", return_value=result):
                        with mock.patch.object(backend, "emit"):
                            actual = backend.run_login(args)
        self.assertEqual(actual, result)
        connect.assert_called_once_with(args, backend.LOGIN_URL)
        wait.assert_called_once_with(cdp, args)
        verify.assert_called_once_with(cdp, args)
        cdp.navigate.assert_called_once_with(backend.LOGIN_URL)
        process.terminate.assert_not_called()
        cdp.close.assert_called_once_with()

    def test_browser_helper_supports_breakaway_from_task_job(self):
        import inspect

        self.assertIn("breakaway", inspect.signature(backend.start_chrome).parameters)

    def test_google_docs_connection_requests_breakaway_browser(self):
        args = mock.Mock(port=9265, profile_dir="C:/plugin-profile", browser_path="", wait_seconds=30)
        fake_page = {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"}
        fake_cdp = mock.Mock()
        with mock.patch.object(backend, "chrome_debug_available", return_value=False):
            with mock.patch.object(backend, "page_for_google_docs", side_effect=[None, fake_page]):
                with mock.patch.object(backend, "start_chrome", return_value=mock.Mock()) as start:
                    with mock.patch.object(backend, "wait_for_debug_port"):
                        with mock.patch.object(backend, "open_tab"):
                            with mock.patch.object(backend, "CDPClient", return_value=fake_cdp):
                                backend.connect_google_docs_browser(args, backend.LOGIN_URL)
        self.assertTrue(start.call_args.kwargs["breakaway"])

    def test_login_page_selection_prefers_accounts_google_com(self):
        pages = [
            {"type": "page", "url": "https://docs.google.com/document/d/old/edit"},
            {"type": "page", "url": "https://accounts.google.com/signin/v2/identifier"},
        ]
        with mock.patch.object(backend, "http_json", return_value=pages):
            selected = backend.page_for_google_docs(9265, backend.LOGIN_URL)
        self.assertEqual(selected["url"], "https://accounts.google.com/signin/v2/identifier")

    def test_profile_path_is_plugin_specific_and_not_chrome_default(self):
        with mock.patch.dict("os.environ", {"WANDAO_PLUGIN_DATA_DIR": "C:/wandao/plugin-data/google-docs"}, clear=False):
            profile = backend.default_profile_path()
        self.assertIn("google-docs-chrome-profile", str(profile))
        self.assertNotIn("Google/Chrome/User Data", str(profile).replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
