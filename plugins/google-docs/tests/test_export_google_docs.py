import base64
import importlib.util
import json
import tempfile
import sys
import unittest
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

    def test_builds_minimal_root_and_document_nodes(self):
        source = backend.GoogleDocsSource("fixture-document-id", "https://docs.google.com/document/d/fixture-document-id/edit")
        nodes = backend.build_document_nodes(source, "项目资源演示")
        self.assertEqual(nodes[0]["nodeId"], "root")
        self.assertEqual(nodes[0]["selectable"], False)
        self.assertEqual(nodes[1]["nodeId"], "fixture-document-id")
        self.assertEqual(nodes[1]["parentNodeId"], "root")
        self.assertEqual(nodes[1]["selectable"], True)

    def test_parses_html_and_converts_supported_markdown(self):
        html = (FIXTURES / "document-with-resources.html").read_text(encoding="utf-8")
        document = backend.parse_exported_html(html)
        markdown, image_refs, attachment_refs = backend.render_markdown(document)
        expected = (FIXTURES / "expected" / "document.md").read_text(encoding="utf-8")
        self.assertEqual(markdown, expected)
        self.assertEqual(len(image_refs), 1)
        self.assertEqual(len(attachment_refs), 1)
        self.assertNotIn("data:image", markdown)

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