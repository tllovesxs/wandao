import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.feishu.backend import export_feishu as feishu
from plugins.feishu.backend import import_feishu


ENTRY_URL = "https://example.feishu.cn/wiki/wiki-token"
HEALTHY_STATE = {
    "href": ENTRY_URL,
    "title": "Document - Feishu",
    "readyState": "interactive",
    "hasBody": True,
    "textLength": 1447,
    "hasLoginForm": False,
    "loginRequired": False,
    "permissionDenied": False,
}


class FakeSessionCdp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict | None, int | float]] = []
        self.navigated: list[str] = []
        self.closed = False

    def send(self, method: str, params: dict | None = None, timeout: int | float = 30) -> dict:
        self.sent.append((method, params, timeout))
        return {}

    def navigate(self, url: str) -> None:
        self.navigated.append(url)

    def close(self) -> None:
        self.closed = True


def auth_args(auth_file: Path) -> argparse.Namespace:
    return argparse.Namespace(
        auth_file=str(auth_file),
        skip_auth_load=False,
        stop_event=None,
        log_callback=None,
    )


def write_auth_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cookies": [
                    {
                        "name": "session",
                        "value": "saved-cookie",
                        "domain": ".feishu.cn",
                        "path": "/",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class FeishuSessionPreparationTests(unittest.TestCase):
    def test_expected_entry_url_distinguishes_drive_folder_tokens(self) -> None:
        expected = "https://example.feishu.cn/drive/folder/folder-a"

        self.assertTrue(feishu.is_expected_entry_url(expected, expected))
        self.assertFalse(
            feishu.is_expected_entry_url(
                "https://example.feishu.cn/drive/folder/folder-b",
                expected,
            )
        )

    def test_wait_ignores_stale_login_route_until_target_page_frame_arrives(self) -> None:
        stale_login_state = {
            **HEALTHY_STATE,
            "href": "https://accounts.feishu.cn/accounts/page/login",
            "title": "Login",
            "hasLoginForm": True,
            "loginRequired": True,
        }
        cdp = FakeSessionCdp()

        with (
            mock.patch.object(
                feishu,
                "inspect_entry_session",
                side_effect=[stale_login_state, dict(HEALTHY_STATE)],
            ) as inspect_session,
            mock.patch.object(feishu.time, "sleep"),
        ):
            result = feishu.wait_for_wiki_ready(cdp, timeout=1, expected_url=ENTRY_URL)

        self.assertEqual(result["href"], ENTRY_URL)
        self.assertFalse(result["loginRequired"])
        self.assertEqual(inspect_session.call_count, 2)

    def test_healthy_target_page_does_not_restore_auth_or_navigate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            cdp = FakeSessionCdp()

            with (
                mock.patch.object(feishu, "inspect_entry_session", return_value=dict(HEALTHY_STATE)),
                mock.patch.object(feishu, "wait_for_wiki_ready", return_value=dict(HEALTHY_STATE)) as wait_ready,
            ):
                result = feishu.prepare_entry_session(cdp, auth_args(auth_file), ENTRY_URL)

        self.assertEqual(cdp.sent, [], "a healthy active session must not be overwritten by saved cookies")
        self.assertEqual(cdp.navigated, [])
        self.assertEqual(result["restoredCookies"], 0)
        self.assertFalse(result["navigated"])
        wait_ready.assert_called_once_with(
            cdp,
            timeout=35,
            args=mock.ANY,
            expected_url=ENTRY_URL,
        )

    def test_explicit_login_page_restores_auth_and_navigates_exactly_once(self) -> None:
        login_state = {
            **HEALTHY_STATE,
            "href": "https://accounts.feishu.cn/accounts/page/login",
            "title": "Login",
            "hasLoginForm": True,
            "loginRequired": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            cdp = FakeSessionCdp()

            with (
                mock.patch.object(feishu, "inspect_entry_session", return_value=login_state),
                mock.patch.object(feishu, "wait_for_wiki_ready", return_value=dict(HEALTHY_STATE)),
                mock.patch.object(feishu, "emit"),
            ):
                result = feishu.prepare_entry_session(cdp, auth_args(auth_file), ENTRY_URL)

        self.assertEqual([method for method, _params, _timeout in cdp.sent], ["Network.enable", "Network.setCookies"])
        set_cookie_calls = [call for call in cdp.sent if call[0] == "Network.setCookies"]
        self.assertEqual(len(set_cookie_calls), 1)
        self.assertEqual(cdp.navigated, [ENTRY_URL])
        self.assertEqual(result["restoredCookies"], 1)
        self.assertTrue(result["navigated"])

    def test_loading_target_restores_auth_once_when_wait_detects_login(self) -> None:
        loading_state = {
            **HEALTHY_STATE,
            "readyState": "loading",
            "hasBody": False,
            "textLength": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            cdp = FakeSessionCdp()

            with (
                mock.patch.object(feishu, "inspect_entry_session", return_value=loading_state),
                mock.patch.object(
                    feishu,
                    "wait_for_wiki_ready",
                    side_effect=[
                        feishu.FeishuLoginRequired("stale login frame"),
                        dict(HEALTHY_STATE),
                    ],
                ) as wait_ready,
                mock.patch.object(feishu, "emit"),
            ):
                result = feishu.prepare_entry_session(cdp, auth_args(auth_file), ENTRY_URL)

        self.assertEqual(wait_ready.call_count, 2)
        self.assertEqual([method for method, _params, _timeout in cdp.sent].count("Network.setCookies"), 1)
        self.assertEqual(cdp.navigated, [ENTRY_URL])
        self.assertEqual(result["restoredCookies"], 1)
        self.assertTrue(result["navigated"])

    def test_permission_state_from_other_wiki_node_navigates_to_requested_target(self) -> None:
        stale_permission_state = {
            **HEALTHY_STATE,
            "href": "https://example.feishu.cn/wiki/old-token",
            "title": "Access denied",
            "permissionDenied": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            cdp = FakeSessionCdp()

            with (
                mock.patch.object(feishu, "inspect_entry_session", return_value=stale_permission_state),
                mock.patch.object(feishu, "wait_for_wiki_ready", return_value=dict(HEALTHY_STATE)),
            ):
                result = feishu.prepare_entry_session(cdp, auth_args(auth_file), ENTRY_URL)

        self.assertEqual(cdp.sent, [])
        self.assertEqual(cdp.navigated, [ENTRY_URL])
        self.assertEqual(result["restoredCookies"], 0)
        self.assertTrue(result["navigated"])

    def test_normal_long_document_text_does_not_imply_permission_denied(self) -> None:
        document_state = {
            **HEALTHY_STATE,
            "bodyText": "Troubleshooting: access denied and 暂无权限 are error messages explained in this document. " * 20,
            "textLength": 1800,
            "permissionDenied": False,
        }
        cdp = FakeSessionCdp()

        with mock.patch.object(feishu, "inspect_entry_session", return_value=document_state):
            result = feishu.wait_for_wiki_ready(cdp, timeout=1, expected_url=ENTRY_URL)

        self.assertFalse(result["permissionDenied"])
        self.assertGreater(result["textLength"], 20)

    def test_permission_denied_page_neither_restores_auth_nor_navigates(self) -> None:
        permission_state = {
            **HEALTHY_STATE,
            "title": "Access denied",
            "permissionDenied": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            cdp = FakeSessionCdp()

            with (
                mock.patch.object(feishu, "inspect_entry_session", return_value=permission_state),
                mock.patch.object(feishu, "wait_for_wiki_ready") as wait_ready,
                self.assertRaisesRegex(feishu.ExportError, "没有权限"),
            ):
                feishu.prepare_entry_session(cdp, auth_args(auth_file), ENTRY_URL)

        self.assertEqual(cdp.sent, [])
        self.assertEqual(cdp.navigated, [])
        wait_ready.assert_not_called()


class FeishuAuthRetryTests(unittest.TestCase):
    def test_first_auth_failure_restores_once_then_operation_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            args = auth_args(auth_file)
            cdp = FakeSessionCdp()
            operation = mock.Mock(
                side_effect=[
                    feishu.FeishuLoginRequired("API returned 401"),
                    {"ok": True},
                ]
            )

            with (
                mock.patch.object(feishu, "wait_for_wiki_ready", return_value=dict(HEALTHY_STATE)) as wait_ready,
                mock.patch.object(feishu, "emit"),
            ):
                result = feishu.run_with_auth_retry(cdp, args, ENTRY_URL, operation)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(operation.call_count, 2)
        self.assertEqual([method for method, _params, _timeout in cdp.sent].count("Network.setCookies"), 1)
        self.assertEqual(cdp.navigated, [ENTRY_URL])
        wait_ready.assert_called_once_with(cdp, timeout=35, args=args, expected_url=ENTRY_URL)
        self.assertTrue(args._feishu_auth_retry_used)

    def test_second_auth_failure_is_returned_without_a_third_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            args = auth_args(auth_file)
            cdp = FakeSessionCdp()
            operation = mock.Mock(
                side_effect=[
                    feishu.FeishuLoginRequired("first 401"),
                    feishu.FeishuLoginRequired("second 401"),
                ]
            )

            with (
                mock.patch.object(feishu, "wait_for_wiki_ready", return_value=dict(HEALTHY_STATE)),
                mock.patch.object(feishu, "emit"),
                self.assertRaisesRegex(feishu.FeishuLoginRequired, "second 401"),
            ):
                feishu.run_with_auth_retry(cdp, args, ENTRY_URL, operation)

        self.assertEqual(operation.call_count, 2)
        self.assertEqual([method for method, _params, _timeout in cdp.sent].count("Network.setCookies"), 1)
        self.assertEqual(cdp.navigated, [ENTRY_URL])

    def test_retry_is_disabled_after_restore_when_skipped_or_without_credentials(self) -> None:
        cases = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_auth = root / "existing.json"
            write_auth_file(existing_auth)

            already_restored_args = auth_args(existing_auth)
            cases.append(("already-restored", already_restored_args, True))

            skip_auth_args = auth_args(existing_auth)
            skip_auth_args.skip_auth_load = True
            cases.append(("skip-auth", skip_auth_args, False))

            missing_auth_args = auth_args(root / "missing.json")
            cases.append(("missing-auth", missing_auth_args, False))

            for label, args, already_restored in cases:
                with self.subTest(label=label):
                    cdp = FakeSessionCdp()
                    operation = mock.Mock(side_effect=feishu.FeishuLoginRequired("401"))

                    with self.assertRaises(feishu.FeishuLoginRequired):
                        feishu.run_with_auth_retry(
                            cdp,
                            args,
                            ENTRY_URL,
                            operation,
                            already_restored=already_restored,
                        )

                    self.assertEqual(operation.call_count, 1)
                    self.assertEqual(cdp.sent, [])
                    self.assertEqual(cdp.navigated, [])

    def test_permission_denied_is_not_treated_as_an_auth_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / ".feishu_auth.json"
            write_auth_file(auth_file)
            args = auth_args(auth_file)
            cdp = FakeSessionCdp()
            operation = mock.Mock(side_effect=feishu.FeishuPermissionDenied("HTTP 403"))

            with self.assertRaisesRegex(feishu.FeishuPermissionDenied, "403"):
                feishu.run_with_auth_retry(cdp, args, ENTRY_URL, operation)

        self.assertEqual(operation.call_count, 1)
        self.assertEqual(cdp.sent, [])
        self.assertEqual(cdp.navigated, [])

    def test_wiki_loader_preserves_bootstrap_auth_and_permission_failures(self) -> None:
        loader = feishu.FEISHU_TREE_LOADER_JS

        self.assertIn("let bootstrapError = null", loader)
        self.assertIn("bootstrapError = error", loader)
        self.assertIn("FEISHU_AUTH_REQUIRED|FEISHU_PERMISSION_DENIED", loader)
        self.assertIn("throw bootstrapError", loader)
        self.assertIn("res.status === 401", loader)
        self.assertIn("FEISHU_AUTH_REQUIRED", loader)
        self.assertIn("res.status === 403", loader)
        self.assertIn("FEISHU_PERMISSION_DENIED", loader)
        self.assertIn("res.redirected", loader)
        self.assertIn("/\\/accounts\\/page\\/login/i", loader)
        self.assertIn("new URL(res.url, location.origin).pathname", loader)


class FeishuImportProbeSessionTests(unittest.TestCase):
    def test_probe_target_wiki_reuses_prepared_healthy_session(self) -> None:
        args = argparse.Namespace(
            wiki_url=ENTRY_URL,
            close_started_chrome=False,
        )
        cdp = FakeSessionCdp()
        tree = {
            "spaceId": "space-id",
            "space": {"space_name": "Knowledge base"},
            "rootList": ["wiki-token"],
            "childMap": {},
            "nodes": {
                "wiki-token": {
                    "wiki_token": "wiki-token",
                    "parent_wiki_token": "",
                    "title": "Root document",
                    "obj_token": "doc-token",
                    "obj_type": 22,
                    "url": ENTRY_URL,
                    "sort_id": 1,
                }
            },
        }

        with (
            mock.patch.object(import_feishu, "connect_wiki_browser", return_value=(cdp, None)),
            mock.patch.object(import_feishu, "prepare_entry_session", return_value={"state": dict(HEALTHY_STATE)}) as prepare,
            mock.patch.object(import_feishu, "load_wiki_tree", return_value=tree) as load_tree,
            mock.patch.object(import_feishu, "load_auth_state", create=True) as direct_auth_load,
        ):
            result = import_feishu.probe_target_wiki(args)

        prepare.assert_called_once_with(cdp, args, ENTRY_URL)
        load_tree.assert_called_once_with(cdp, ENTRY_URL, "wiki-token", args)
        direct_auth_load.assert_not_called()
        self.assertFalse(any(method == "Network.setCookies" for method, _params, _timeout in cdp.sent))
        self.assertTrue(cdp.closed)
        self.assertEqual(result["provider"], "feishu-import-probe")
        self.assertEqual(result["spaceId"], "space-id")
        self.assertTrue(result["readOnly"])


class FakeExtractorCdp:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.expressions: list[tuple[str, int | float]] = []

    def evaluate(self, expression: str, timeout: int | float = 60) -> dict:
        self.expressions.append((expression, timeout))
        return self.result


class SequencedExtractorCdp:
    def __init__(self, results: list[dict | Exception]) -> None:
        self.results = list(results)
        self.expressions: list[tuple[str, int | float]] = []

    def evaluate(self, expression: str, timeout: int | float = 60) -> dict:
        self.expressions.append((expression, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FeishuMarkdownExtractionTests(unittest.TestCase):
    def test_markdown_detection_accepts_serialized_icon_info_and_markdown_extension(self) -> None:
        serialized_icon_node = {
            "wiki_token": "serialized-icon",
            "obj_type": 12,
            "icon_info": json.dumps({"file_type": "md"}),
            "title": "Document",
            "url": "https://example.feishu.cn/wiki/serialized-icon",
        }
        markdown_extension_node = {
            "wiki_token": "markdown-extension",
            "obj_type": 12,
            "title": "README.markdown",
            "url": "https://example.feishu.cn/wiki/markdown-extension",
        }

        self.assertEqual(feishu.feishu_node_file_type(serialized_icon_node), "md")
        self.assertTrue(feishu.is_markdown_file_node(serialized_icon_node))
        self.assertTrue(feishu.is_exportable_feishu_node(serialized_icon_node))
        self.assertEqual(feishu.feishu_node_file_type(markdown_extension_node), "markdown")
        self.assertTrue(feishu.is_markdown_file_node(markdown_extension_node))
        self.assertTrue(feishu.is_exportable_feishu_node(markdown_extension_node))

    def test_markdown_original_file_success_uses_loader_without_waiting_for_dom(self) -> None:
        cdp = FakeExtractorCdp(
            {
                "title": "README.md",
                "markdown": "# README\n",
                "renderer": "markdown_file",
                "blockCount": 1,
                "textLength": 9,
            }
        )
        node = {
            "wiki_token": "wiki-token",
            "obj_token": "file-token",
            "obj_type": 12,
            "file_type": "md",
            "mount_point": "wiki",
            "title": "README.md",
            "url": ENTRY_URL,
        }

        with (
            mock.patch.object(feishu, "wait_for_doc_ready") as wait_ready,
            mock.patch.object(feishu, "FEISHU_MARKDOWN_FILE_LOADER_JS", "MARKDOWN_FILE_LOADER"),
            mock.patch.object(feishu, "FEISHU_CONVERTER_JS", "DOCX_CONVERTER"),
        ):
            result = feishu.extract_doc_markdown_current(cdp, node)

        wait_ready.assert_not_called()
        self.assertEqual(len(cdp.expressions), 1)
        expression, timeout = cdp.expressions[0]
        self.assertIn("(MARKDOWN_FILE_LOADER)", expression)
        self.assertNotIn("DOCX_CONVERTER", expression)
        self.assertIn("file-token", expression)
        self.assertEqual(timeout, 60)
        self.assertEqual(result["renderer"], "markdown_file")

    def test_markdown_original_file_failure_waits_for_dom_before_retry(self) -> None:
        recovered = {
            "title": "README.md",
            "markdown": "# README\n",
            "renderer": "markdown_file",
            "blockCount": 1,
            "textLength": 9,
        }
        cdp = SequencedExtractorCdp([feishu.ExportError("source download failed"), recovered])
        node = {
            "wiki_token": "wiki-token",
            "obj_token": "file-token",
            "obj_type": 12,
            "file_type": "md",
            "title": "README.md",
            "url": ENTRY_URL,
        }

        with (
            mock.patch.object(feishu, "wait_for_doc_ready") as wait_ready,
            mock.patch.object(feishu, "FEISHU_MARKDOWN_FILE_LOADER_JS", "MARKDOWN_FILE_LOADER"),
        ):
            result = feishu.extract_doc_markdown_current(cdp, node)

        wait_ready.assert_called_once_with(cdp, timeout=35, args=None, node=node)
        self.assertEqual(len(cdp.expressions), 2)
        self.assertTrue(all("(MARKDOWN_FILE_LOADER)" in expression for expression, _timeout in cdp.expressions))
        self.assertEqual(result["renderer"], "markdown_file")
        self.assertNotIn("incomplete", result)

    def test_markdown_dom_fallback_is_marked_incomplete(self) -> None:
        fallback = {
            "title": "README.md",
            "markdown": "# Preview only\n",
            "renderer": "markdown_preview_fallback",
            "sourceError": "source download failed",
            "blockCount": 1,
            "textLength": 15,
        }
        cdp = SequencedExtractorCdp([feishu.ExportError("preview not ready"), fallback])
        node = {
            "wiki_token": "wiki-token",
            "obj_token": "file-token",
            "obj_type": 12,
            "file_type": "md",
            "title": "README.md",
            "url": ENTRY_URL,
        }

        with (
            mock.patch.object(feishu, "wait_for_doc_ready") as wait_ready,
            mock.patch.object(feishu, "emit"),
        ):
            result = feishu.extract_doc_markdown_current(cdp, node)

        wait_ready.assert_called_once_with(cdp, timeout=35, args=None, node=node)
        self.assertEqual(result["renderer"], "markdown_preview_fallback")
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["sourceError"], "source download failed")

    def test_build_doc_paths_only_includes_docx_and_markdown_nodes(self) -> None:
        ordered = [
            {
                "wiki_token": "folder",
                "parent_wiki_token": "",
                "title": "Folder",
                "obj_type": 0,
                "has_child": True,
                "sort_id": 1,
                "url": "https://example.feishu.cn/drive/folder/folder",
            },
            {
                "wiki_token": "docx",
                "parent_wiki_token": "folder",
                "title": "Native document",
                "obj_type": 22,
                "sort_id": 1,
                "url": "https://example.feishu.cn/wiki/docx",
            },
            {
                "wiki_token": "markdown",
                "parent_wiki_token": "folder",
                "title": "README.md",
                "obj_type": 12,
                "file_type": "md",
                "sort_id": 2,
                "url": "https://example.feishu.cn/wiki/markdown",
            },
            {
                "wiki_token": "markdown-icon",
                "parent_wiki_token": "folder",
                "title": "CHANGELOG.md",
                "obj_type": 12,
                "icon_info": {"file_type": "md"},
                "sort_id": 3,
                "url": "https://example.feishu.cn/wiki/markdown-icon",
            },
            {
                "wiki_token": "pdf",
                "parent_wiki_token": "folder",
                "title": "Guide.pdf",
                "obj_type": 12,
                "file_type": "pdf",
                "sort_id": 4,
                "url": "https://example.feishu.cn/wiki/pdf",
            },
            {
                "wiki_token": "sheet",
                "parent_wiki_token": "folder",
                "title": "Sheet",
                "obj_type": 2,
                "sort_id": 5,
                "url": "https://example.feishu.cn/wiki/sheet",
            },
            {
                "wiki_token": "no-url",
                "parent_wiki_token": "folder",
                "title": "No URL",
                "obj_type": 22,
                "sort_id": 6,
                "url": "",
            },
        ]
        tree = {
            "rootList": ["folder"],
            "nodes": {item["wiki_token"]: item for item in ordered},
        }

        with tempfile.TemporaryDirectory() as directory:
            paths = feishu.build_doc_paths(ordered, tree, Path(directory))

        self.assertEqual(set(paths), {"docx", "markdown", "markdown-icon"})
        self.assertTrue(all(path.suffix == ".md" for path in paths.values()))
        self.assertTrue(paths["markdown"].name.endswith("README.md"))
        self.assertNotIn(".md.md", paths["markdown"].name.lower())
        self.assertTrue(paths["docx"].name.endswith("Native document.md"))


class FakeCheckpoint:
    def __init__(self) -> None:
        self.failed_items: list[tuple[str, str]] = []
        self.completed_items: list[str] = []
        self.failed_tasks: list[tuple[str, str]] = []
        self.completed_tasks: list[dict] = []
        self.closed = False

    def start_task(self, _metadata: dict) -> None:
        return None

    def upsert_item(self, *_args, **_kwargs) -> None:
        return None

    def item_status(self, _key: str) -> str:
        return ""

    def start_item(self, _key: str, _stage: str) -> None:
        return None

    def fail_item(self, key: str, error: str) -> None:
        self.failed_items.append((key, error))

    def complete_item(self, key: str, **_kwargs) -> None:
        self.completed_items.append(key)

    def stats(self) -> dict:
        return {"failed": len(self.failed_items), "completed": len(self.completed_items)}

    def fail_task(self, error: str, *, status: str) -> None:
        self.failed_tasks.append((error, status))

    def complete_task(self, report: dict) -> None:
        self.completed_tasks.append(report)

    def close(self) -> None:
        self.closed = True


class FeishuRelativeResourceReportingTests(unittest.TestCase):
    def test_relative_markdown_resources_make_report_partial_and_checkpoint_failed(self) -> None:
        node = {
            "wiki_token": "wiki-token",
            "parent_wiki_token": "",
            "title": "README.md",
            "obj_token": "file-token",
            "obj_type": 12,
            "file_type": "md",
            "has_child": False,
            "sort_id": 1,
            "url": ENTRY_URL,
        }
        tree = {
            "spaceId": "space-id",
            "space": {"space_name": "Knowledge base"},
            "rootList": ["wiki-token"],
            "childMap": {},
            "nodes": {"wiki-token": node},
        }
        extracted = {
            "title": "README.md",
            "markdown": "# README\n\n![Local diagram](assets/diagram.png)\n",
            "images": [],
            "relativeResources": ["assets/diagram.png"],
            "renderer": "markdown_file",
            "blockCount": 3,
            "textLength": 48,
        }
        checkpoint = FakeCheckpoint()
        cdp = FakeSessionCdp()

        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                wiki_url=ENTRY_URL,
                output=directory,
                wait_login=False,
                selected_doc_ids=None,
                incremental=False,
                update_existing=False,
                resume=False,
                retry_failed=False,
                download_timeout=5,
                keep_remote_images=True,
                progress_every=1,
                request_delay=0,
                request_jitter=0,
                close_started_chrome=False,
                stop_event=None,
                log_callback=None,
            )
            with (
                mock.patch.object(feishu, "connect_wiki_browser", return_value=(cdp, None)),
                mock.patch.object(feishu, "prepare_entry_session"),
                mock.patch.object(feishu, "load_wiki_tree", return_value=tree),
                mock.patch.object(feishu, "fetch_doc_markdown", return_value=extracted),
                mock.patch.object(
                    feishu,
                    "localize_images",
                    return_value=(extracted["markdown"], 0, []),
                ),
                mock.patch.object(feishu, "scan_exported_docs", return_value={}),
                mock.patch.object(feishu, "open_checkpoint_from_args", return_value=checkpoint),
                mock.patch.object(feishu, "emit"),
            ):
                report = feishu.export_wiki(args)

        self.assertEqual(report["outcome"], "partial")
        self.assertEqual(report["imageFailureCount"], 1)
        self.assertEqual(len(report["resourceFailures"]), 1)
        self.assertIn("assets/diagram.png", json.dumps(report["resourceFailures"], ensure_ascii=False))
        self.assertEqual(len(checkpoint.failed_items), 1)
        self.assertEqual(checkpoint.completed_items, [])
        self.assertEqual(len(checkpoint.failed_tasks), 1)
        self.assertEqual(checkpoint.failed_tasks[0][1], "failed")
        self.assertEqual(checkpoint.completed_tasks, [])
        self.assertTrue(checkpoint.closed)


if __name__ == "__main__":
    unittest.main()
