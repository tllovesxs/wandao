import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.aliyun_thoughts.backend import export_aliyun_thoughts as aliyun
from plugins.feishu.backend import export_feishu as feishu
from plugins.ima.backend import ima_knowledge as ima
from plugins.onenote.backend import export_onenote as onenote
from plugins.wiz.backend import export_wiz as wiz
from plugins.yinxiang.backend import export_yinxiang as yinxiang
from plugins.youdao.backend import export_youdao as youdao
from plugins.yuque.backend import export_yuque as yuque
from plugins.zsxq.backend import export_zsxq as zsxq


REPO_ROOT = Path(__file__).resolve().parents[1]


def manifest(provider_id: str) -> dict:
    for path in (REPO_ROOT / "plugins").glob("*/providers/*/provider.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("id") == provider_id:
            return data
    raise AssertionError(f"missing provider manifest: {provider_id}")


def value_at_path(source: dict, expression: str):
    value = source
    for key in expression.split("."):
        value = value[key]
    return value


class ProviderTocBackendContractTests(unittest.TestCase):
    def assert_manifest_matches_payload(self, provider_id: str, payload: dict) -> None:
        provider = manifest(provider_id)
        toc = provider["toc"]
        items = value_at_path(payload, toc["itemsPath"])
        self.assertIsInstance(items, list, provider_id)
        self.assertTrue(items, provider_id)
        if toc.get("adapter") == "yinxiang-notebooks":
            note = items[0]["notes"][0]
            self.assertTrue(note["guid"])
            return
        if toc.get("adapter") == "zsxq-column-groups":
            topic = items[0]["topics"][0]
            self.assertTrue(topic["key"])
            return
        for key_name in ("idKey", "exportIdKey", "titleKey", "parentIdKey"):
            self.assertTrue(all(toc[key_name] in item for item in items), f"{provider_id}: {key_name}")
        selectable = [
            item for item in items
            if item.get(toc.get("selectableKey")) is True
            or item.get(toc.get("typeKey")) in (toc.get("selectableTypes") or [])
        ]
        self.assertTrue(selectable, f"{provider_id}: no selectable backend item")
        self.assertTrue(all(str(item.get(toc["exportIdKey"]) or "") for item in selectable), provider_id)

    def test_backend_payload_builders_match_all_scan_provider_manifests(self) -> None:
        payloads = {}
        payloads["aliyun"] = {
            "nodes": [
                aliyun.node_to_dict(aliyun.Node("folder", "Folder", "folder", None, 0, {})),
                aliyun.node_to_dict(aliyun.Node("doc", "Doc", "document", "folder", 1, {})),
            ]
        }
        payloads["feishu-export"] = {"ordered": feishu.order_tree({
            "nodes": {
                "folder": {"wiki_token": "folder", "title": "Folder", "parent_wiki_token": "", "obj_type": 0, "sort_id": 0},
                "doc": {"wiki_token": "doc", "title": "Doc", "parent_wiki_token": "folder", "obj_type": 22, "url": "https://example.test/wiki/doc", "sort_id": 1},
            },
            "childMap": {"folder": ["doc"]},
            "rootList": ["folder"],
        })}
        ima_entries = [
            ima.KnowledgeEntry("kb", "KB", "folder", "Folder", "ima-kb:kb", [], True),
            ima.KnowledgeEntry("kb", "KB", "doc", "Doc", "ima-folder:kb:folder", [], False),
        ]
        with patch.object(ima, "scan_remote_tree", return_value=([ima.KnowledgeBase("kb", "KB")], ima_entries)):
            payloads["ima-export"] = ima.scan_toc(SimpleNamespace(), SimpleNamespace())
        one_root = onenote.TocNode("", "onenote-notebook:demo", "notebook", "Notebook", "", False, 0, [])
        one_doc = onenote.TocNode("page-id", "onenote-page:doc", "page", "Page", one_root.node_id, True, 1, [])
        payloads["onenote"] = onenote.toc_json([one_root, one_doc], [one_doc])
        payloads["wiz"] = wiz.toc_json({
            "account": {"kbGuid": "kb"}, "kbs": [{"kbGuid": "kb", "name": "KB"}],
            "folders": [{"kbGuid": "kb", "location": "/Folder/", "name": "Folder", "parentLocation": "/"}],
            "docs": [{"kbGuid": "kb", "docGuid": "doc-guid", "title": "Doc", "category": "/Folder/", "type": "note"}],
        })
        notebook = SimpleNamespace(guid="notebook", name="Notebook", stack="Stack")
        note = SimpleNamespace(guid="note-guid", title="Note", created=1, updated=2)
        storage = SimpleNamespace(
            notebooks=SimpleNamespace(iter_notebooks=lambda: [notebook]),
            notes=SimpleNamespace(iter_notes=lambda _guid: [note]),
        )
        with patch.object(yinxiang, "open_storage", return_value=storage):
            payloads["yinxiang"] = yinxiang.scan_toc(SimpleNamespace(database=Path("test.db")))
        youdao_nodes = [
            youdao.RemoteNode("folder", "Folder", True, node_id="youdao-folder:root", path_parts=["Folder"]),
            youdao.RemoteNode("doc-id", "Doc.note", False, "folder", "youdao-folder:root", "youdao-doc:doc", ["Folder", "Doc.note"]),
        ]
        fake_client = SimpleNamespace(request_count=2)
        with patch.object(youdao, "YoudaoClient", return_value=fake_client), patch.object(
            youdao, "build_remote_tree", return_value=(youdao_nodes, "folder")
        ):
            payloads["youdao"] = youdao.toc_json(SimpleNamespace(auth_file=None))

        class DummyCdp:
            def close(self):
                return None

        yuque_payload = {"book": {"name": "Book"}, "toc": [
            {"type": "TITLE", "uuid": "folder", "doc_id": "", "title": "Folder", "parent_uuid": ""},
            {"type": "DOC", "uuid": "doc", "doc_id": 123, "title": "Doc", "parent_uuid": "folder"},
        ]}
        with patch.object(yuque, "connect_book_browser", return_value=(DummyCdp(), None)), patch.object(
            yuque, "load_book", return_value=yuque_payload
        ), patch.object(yuque, "emit"):
            payloads["yuque"] = yuque.scan_book_toc(SimpleNamespace(
                book_url="https://www.yuque.com/user/book", auth_file=None, skip_auth_load=True,
                close_started_chrome=False,
            ))

        class TocCdp:
            def evaluate(self, _script, timeout=0):
                return {"href": "https://wx.zsxq.com/dweb2/column/x", "groups": [{
                    "groupIndex": 3, "groupTitle": "Section",
                    "topics": [{"groupIndex": 3, "topicIndex": 0, "title": "Article", "topicId": "topic"}],
                }]}

        with patch.object(zsxq, "navigate_with_retry"), patch.object(zsxq, "wait_eval"):
            payloads["zsxq-column"] = zsxq.collect_toc(TocCdp(), "https://wx.zsxq.com/dweb2/column/x")

        self.assertEqual(set(payloads), {
            "aliyun", "feishu-export", "ima-export", "onenote", "wiz", "yinxiang", "youdao", "yuque", "zsxq-column",
        })
        for provider_id, payload in payloads.items():
            with self.subTest(provider=provider_id):
                self.assert_manifest_matches_payload(provider_id, payload)

    def test_manifest_selection_arguments_are_accepted_by_each_backend_parser(self) -> None:
        parsers = {
            "aliyun": (aliyun.parse_args, "selected_doc_ids"),
            "feishu-export": (feishu.parse_args, "selected_doc_ids"),
            "ima-export": (ima.parse_args, "doc_id"),
            "onenote": (onenote.parse_args, "selected_doc_ids"),
            "wiz": (wiz.parse_args, "selected_doc_ids"),
            "yinxiang": (yinxiang.parse_args, "doc_id"),
            "youdao": (youdao.parse_args, "selected_doc_ids"),
            "yuque": (yuque.parse_args, "selected_doc_ids"),
            "zsxq-column": (zsxq.parse_args, "selected_toc_keys"),
        }
        for provider_id, (parse_args, destination) in parsers.items():
            toc = manifest(provider_id)["toc"]
            argv = [*toc.get("selectionPrefixArgs", []), toc["selectionArg"], "selected-id"]
            with self.subTest(provider=provider_id):
                args = parse_args(argv)
                self.assertEqual(getattr(args, destination), ["selected-id"])


if __name__ == "__main__":
    unittest.main()
