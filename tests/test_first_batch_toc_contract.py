import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def provider(provider_path: str) -> dict:
    return json.loads((REPO_ROOT / provider_path).read_text(encoding="utf-8"))


class FirstBatchTocContractTests(unittest.TestCase):
    def test_yuque_manifest_matches_scan_tree_and_export_contract(self) -> None:
        toc = provider("plugins/yuque/providers/yuque/provider.json")["toc"]

        self.assertEqual(
            toc,
            {
                "itemsPath": "toc",
                "idKey": "uuid",
                "exportIdKey": "doc_id",
                "titleKey": "title",
                "parentIdKey": "parent_uuid",
                "typeKey": "type",
                "selectableTypes": ["DOC"],
                "selectionArg": "--doc-id",
                "selectableWhenExportId": True,
            },
        )

    def test_aliyun_manifest_uses_nodes_and_parent_id(self) -> None:
        toc = provider("plugins/aliyun_thoughts/providers/aliyun/provider.json")["toc"]

        self.assertEqual(toc["itemsPath"], "nodes")
        self.assertEqual(toc["parentIdKey"], "parent_id")
        self.assertEqual(toc["selectionArg"], "--doc-id")

    def test_feishu_manifest_declares_document_only_selection(self) -> None:
        toc = provider("plugins/feishu/providers/feishu-export/provider.json")["toc"]

        self.assertEqual(toc["itemsPath"], "ordered")
        self.assertEqual(toc["idKey"], "wiki_token")
        self.assertEqual(toc["exportIdKey"], "wiki_token")
        self.assertEqual(toc["parentIdKey"], "parent_wiki_token")
        self.assertEqual(toc["selectableKey"], "selectable")
        self.assertEqual(toc["selectionArg"], "--doc-id")


if __name__ == "__main__":
    unittest.main()
