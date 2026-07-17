from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "plugins" / "xiliu" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import import_flowus


def target_list() -> list[dict]:
    return [{
        "id": "root-page",
        "name": "Root",
        "spaceId": "space-1",
        "spaceName": "Space",
        "role": "editor",
    }]


def import_args(source: Path, checkpoint: Path, *extra: str):
    return import_flowus.parse_args([
        "--source-dir", str(source),
        "--parent-id", "root-page",
        "--space-id", "space-1",
        "--checkpoint-file", str(checkpoint),
        "--checkpoint-task-id", "xiliu-idempotency-test",
        *extra,
    ])


def item_metadata(checkpoint: Path, item_key: str) -> tuple[str, dict]:
    connection = sqlite3.connect(checkpoint)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT status, metadata_json FROM items WHERE item_key = ?", (item_key,),
        ).fetchone()
        assert row is not None
        return str(row["status"]), json.loads(str(row["metadata_json"] or "{}"))
    finally:
        connection.close()


class StatefulImportServer:
    def __init__(self) -> None:
        self.args = None
        self.pages: dict[str, dict] = {
            "root-page": {"id": "root-page", "title": "Root", "parentId": ""},
        }
        self.page_request_calls = 0
        self.page_requests: list[dict[str, str]] = []
        self.page_lookup_calls = 0
        self.upload_calls = 0
        self.enqueue_calls = 0
        self.tasks_by_request: dict[str, str] = {}
        self.task_status_calls = 0
        self.create_timeout_title = ""
        self.create_timeout_fired = False
        self.create_fail_before_write_title = ""
        self.create_fail_before_write_fired = False
        self.enqueue_timeout_once = False
        self.enqueue_timeout_fired = False
        self.processing_once = False

    def get_json(self, url: str, **_kwargs):
        self.page_lookup_calls += 1
        page_id = url.rstrip("/").rsplit("/", 1)[-1]
        if page_id in self.pages:
            return {"code": 200, "data": {"blocks": {page_id: self.pages[page_id]}}}
        return {"code": 404, "msg": "not found"}

    def request(self, method: str, url: str, data=None, **_kwargs) -> bytes:
        del method
        if url == import_flowus.BLOCKS_TRANSACTIONS_API:
            operations = data["transactions"][0]["operations"]
            create = next((op for op in operations if op.get("command") == "set"), None)
            if create is not None:
                self.page_request_calls += 1
                args = create["args"]
                page_id = str(create["id"])
                title = str(args.get("data", {}).get("segments", [{}])[0].get("text", ""))
                self.page_requests.append({
                    "pageId": page_id,
                    "requestId": str(data.get("requestId") or ""),
                    "transactionId": str(data["transactions"][0].get("id") or ""),
                    "title": title,
                })
                if title == self.create_fail_before_write_title and not self.create_fail_before_write_fired:
                    self.create_fail_before_write_fired = True
                    raise import_flowus.FlowUsError("创建请求发送失败且服务端未写入")
                self.pages.setdefault(page_id, {
                    "id": page_id,
                    "title": title,
                    "parentId": str(args.get("parentId") or ""),
                })
                if title == self.create_timeout_title and not self.create_timeout_fired:
                    self.create_timeout_fired = True
                    raise import_flowus.FlowUsError("创建成功后的响应超时")
            return json.dumps({"code": 200, "data": {}}).encode()

        if url.startswith(import_flowus.IMPORT_TEMP_FILE_API):
            self.upload_calls += 1
            return json.dumps({
                "code": 200,
                "data": {"ossName": "oss/import-content.html"},
            }).encode()

        if url == import_flowus.ENQUEUE_TASK_API:
            self.enqueue_calls += 1
            task_id = f"task-{len(self.tasks_by_request) + 1}"
            self.tasks_by_request[task_id] = task_id
            block_id = str(data["request"]["blockId"])
            self.pages[block_id]["subNodes"] = ["imported-block"]
            if self.enqueue_timeout_once and not self.enqueue_timeout_fired:
                self.enqueue_timeout_fired = True
                raise import_flowus.FlowUsError("入队成功后的响应超时")
            return json.dumps({"code": 200, "data": {"taskId": task_id}}).encode()

        if url == import_flowus.GET_TASKS_API:
            self.task_status_calls += 1
            task_id = str(data["taskIds"][0])
            if self.processing_once and self.task_status_calls == 1:
                task = {"status": "processing", "result": {}}
            else:
                task = {"status": "success", "result": {"status": "success"}}
            return json.dumps({
                "code": 200,
                "data": {"results": {task_id: task}},
            }).encode()

        raise AssertionError(f"unexpected request: {url}")


class XiliuImportIdempotencyTests(unittest.TestCase):
    def run_import(self, server: StatefulImportServer, args):
        with (
            patch.object(import_flowus, "FlowUsClient", lambda *_: server),
            patch.object(import_flowus, "list_import_targets", lambda _client: target_list()),
        ):
            return import_flowus.import_flowus(args)

    def test_document_create_response_timeout_reuses_created_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            server = StatefulImportServer()
            server.create_timeout_title = "Doc"

            first = self.run_import(server, import_args(source, checkpoint, "--resume"))
            second = self.run_import(server, import_args(source, checkpoint, "--resume"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(second["outcome"], "completed")
            created = [page for key, page in server.pages.items() if key != "root-page"]
            self.assertEqual([page["title"] for page in created], ["Doc"])
            self.assertEqual(server.page_request_calls, 1)
            self.assertEqual(server.page_lookup_calls, 1)
            status, metadata = item_metadata(checkpoint, "xiliu:import:doc.md")
            self.assertEqual(status, "completed")
            self.assertEqual(metadata["stage"], "completed")
            self.assertTrue(metadata["pageId"])
            self.assertTrue(metadata["createRequestId"])
            self.assertTrue(metadata["createTransactionId"])

    def test_create_request_failure_reuses_ids_before_recreating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            server = StatefulImportServer()
            server.create_fail_before_write_title = "Doc"

            first = self.run_import(server, import_args(source, checkpoint, "--resume"))
            failed_status, failed_metadata = item_metadata(checkpoint, "xiliu:import:doc.md")
            second = self.run_import(server, import_args(source, checkpoint, "--resume"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(failed_status, "failed")
            self.assertEqual(failed_metadata["stage"], "creating-page")
            self.assertEqual(second["outcome"], "completed")
            self.assertEqual(server.page_request_calls, 2)
            self.assertEqual(server.page_lookup_calls, 1)
            self.assertEqual(len(server.pages) - 1, 1)
            first_request, second_request = server.page_requests
            self.assertEqual(first_request["pageId"], second_request["pageId"])
            self.assertEqual(first_request["requestId"], second_request["requestId"])
            self.assertEqual(first_request["transactionId"], second_request["transactionId"])

    def test_enqueue_response_timeout_reuses_upload_and_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            server = StatefulImportServer()
            server.enqueue_timeout_once = True

            first = self.run_import(server, import_args(source, checkpoint, "--resume"))
            _status, failed_metadata = item_metadata(checkpoint, "xiliu:import:doc.md")
            second = self.run_import(server, import_args(source, checkpoint, "--resume"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(second["outcome"], "completed")
            self.assertEqual(server.upload_calls, 1)
            self.assertEqual(server.enqueue_calls, 1)
            self.assertEqual(len(server.tasks_by_request), 1)
            self.assertTrue(failed_metadata["enqueueRequestId"])

    def test_poll_timeout_resume_keeps_task_and_does_not_reenqueue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            server = StatefulImportServer()
            server.processing_once = True

            with patch.object(import_flowus, "wait_with_stop", lambda _args, _seconds: time.sleep(1.05)):
                first = self.run_import(
                    server, import_args(source, checkpoint, "--resume", "--task-timeout", "1"),
                )
            failed_status, failed_metadata = item_metadata(checkpoint, "xiliu:import:doc.md")
            second = self.run_import(server, import_args(source, checkpoint, "--resume"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(failed_status, "failed")
            self.assertEqual(failed_metadata["stage"], "polling-import")
            self.assertTrue(failed_metadata["taskId"])
            self.assertEqual(second["outcome"], "completed")
            self.assertEqual(server.upload_calls, 1)
            self.assertEqual(server.enqueue_calls, 1)
            self.assertEqual(len(server.tasks_by_request), 1)

    def test_folder_create_response_timeout_reuses_folder_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            folder = source / "A"
            folder.mkdir(parents=True)
            (folder / "doc.md").write_text("# Doc\ncontent", encoding="utf-8")
            checkpoint = root / "checkpoint.sqlite"
            server = StatefulImportServer()
            server.create_timeout_title = "A"

            first = self.run_import(server, import_args(source, checkpoint, "--resume"))
            second = self.run_import(server, import_args(source, checkpoint, "--resume"))

            self.assertEqual(first["outcome"], "partial")
            self.assertEqual(second["outcome"], "completed")
            created = [page for key, page in server.pages.items() if key != "root-page"]
            self.assertEqual(sorted(page["title"] for page in created), ["A", "Doc"])
            self.assertEqual(server.page_request_calls, 2)
            self.assertEqual(server.page_lookup_calls, 1)
            folder_status, folder_metadata = item_metadata(checkpoint, "xiliu:folder:A")
            self.assertEqual(folder_status, "completed")
            self.assertEqual(folder_metadata["stage"], "folder-created")
            doc = next(page for page in created if page["title"] == "Doc")
            folder_page = next(page for page in created if page["title"] == "A")
            self.assertEqual(doc["parentId"], folder_page["id"])


if __name__ == "__main__":
    unittest.main()
