import json
import unittest
from pathlib import Path

from export_aliyun_thoughts import page_fetch_json


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeCDP:
    def __init__(self) -> None:
        self.expression = ""

    def evaluate(self, expression: str, timeout: int) -> dict:
        self.expression = expression
        self.timeout = timeout
        return {"result": []}


class ProviderActionContractTests(unittest.TestCase):
    def test_login_actions_receive_their_required_target_url(self) -> None:
        required_login_fields = {
            "plugins/aliyun_thoughts/providers/aliyun/provider.json": "workspace_url",
            "plugins/feishu/providers/feishu-export/provider.json": "wiki_url",
            "plugins/feishu/providers/feishu-import/provider.json": "wiki_url",
            "plugins/yuque/providers/yuque/provider.json": "book_url",
            "plugins/yuque/providers/yuque-import/provider.json": "target_book_url",
            "plugins/zsxq/providers/zsxq-group/provider.json": "entry_url",
            "plugins/zsxq/providers/zsxq-column/provider.json": "entry_url",
        }
        for relative_path, field_name in required_login_fields.items():
            with self.subTest(provider=relative_path):
                manifest = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
                field = next(item for item in manifest["fields"] if item["name"] == field_name)
                self.assertIn("login", field["actions"])

    def test_browser_fetch_expands_relative_api_path(self) -> None:
        cdp = FakeCDP()

        page_fetch_json(cdp, "/api/workspaces/demo/nodes?pageSize=1000")

        self.assertIn('https://thoughts.aliyun.com/api/workspaces/demo/nodes?pageSize=1000', cdp.expression)
        self.assertEqual(cdp.timeout, 60)
