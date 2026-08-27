import json
import unittest
import zlib

from plugins.yuque.backend.export_yuque import fetch_doc_markdown, lakesheet_content_to_markdown


def lakesheet_content(sheets: list[dict]) -> str:
    raw = json.dumps(sheets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw).decode("latin-1")
    return json.dumps({"format": "lakesheet", "sheet": compressed}, ensure_ascii=True)


class FakeCdp:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.expression = ""

    def evaluate(self, expression: str, timeout: int = 0) -> dict:
        self.expression = expression
        return self.result


class YuqueSheetExportTests(unittest.TestCase):
    def test_converts_lakesheet_cells_to_markdown_without_binary_garbage(self) -> None:
        content = lakesheet_content(
            [
                {
                    "name": "计划",
                    "data": {
                        "0": {"0": {"v": "任务"}, "1": {"v": "说明"}, "2": {"v": "完成"}},
                        "1": {"0": {"v": "导出"}, "1": {"v": "图片|附件\n也要保留"}, "2": {"v": True}},
                        "2": {"0": {"v": "合计"}, "2": {"f": "=SUM(C2:C2)"}},
                    },
                }
            ]
        )

        markdown = lakesheet_content_to_markdown(content, "测试表格")

        self.assertEqual(
            markdown,
            "# 测试表格\n\n"
            "## 计划\n\n"
            "| 任务 | 说明 | 完成 |\n"
            "| --- | --- | --- |\n"
            "| 导出 | 图片\\|附件<br>也要保留 | TRUE |\n"
            "| 合计 |  | =SUM(C2:C2) |\n",
        )
        self.assertNotIn("x\\u009c", markdown)

    def test_keeps_every_sheet_and_uses_only_populated_range(self) -> None:
        content = lakesheet_content(
            [
                {"name": "Sheet1", "rowCount": 200, "colCount": 26, "data": {"3": {"2": {"v": "A"}}, "4": {"2": {"v": "B"}}}},
                {"name": "第二张", "data": {"0": {"0": {"v": "键"}, "1": {"v": "值"}}, "1": {"0": {"v": "x"}, "1": {"v": 42}}}},
            ]
        )

        markdown = lakesheet_content_to_markdown(content, "多表")

        self.assertIn("## Sheet1\n\n| A |\n| --- |\n| B |", markdown)
        self.assertIn("## 第二张\n\n| 键 | 值 |\n| --- | --- |\n| x | 42 |", markdown)
        self.assertNotIn("rowCount", markdown)

    def test_fetch_document_routes_lakesheet_to_the_sheet_converter(self) -> None:
        content = lakesheet_content([{"name": "Sheet1", "data": {"0": {"0": {"v": "标题"}}, "1": {"0": {"v": "内容"}}}}])
        cdp = FakeCdp(
            {
                "data": {
                    "id": 1,
                    "title": "语雀表格",
                    "slug": "sheet-doc",
                    "type": "Sheet",
                    "format": "lakesheet",
                    "content": content,
                },
                "isLakeSheet": True,
                "images": [],
                "resources": [],
            }
        )

        result = fetch_doc_markdown(cdp, 123, {"url": "sheet-doc", "title": "后备标题"})

        self.assertIn("# 语雀表格", result["markdown"])
        self.assertIn("| 标题 |", result["markdown"])
        self.assertIn("isLakeSheet", cdp.expression)
        self.assertNotIn("content", result["data"])

    def test_fetch_regular_document_keeps_the_existing_converter_result(self) -> None:
        expected = {
            "data": {"id": 2, "title": "普通文档", "slug": "normal-doc", "type": "Doc", "format": "lake"},
            "markdown": "# 普通文档\n\n正文\n",
            "images": ["https://cdn.example.com/image.png"],
            "resources": [{"url": "https://cdn.example.com/image.png", "kind": "image"}],
        }
        cdp = FakeCdp(expected)

        result = fetch_doc_markdown(cdp, 123, {"url": "normal-doc", "title": "普通文档"})

        self.assertIs(result, expected)
        self.assertIn("isLakeSheet", cdp.expression)


if __name__ == "__main__":
    unittest.main()
