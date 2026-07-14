import unittest
from unittest.mock import patch

from wandao_core.browser import ExportError
from plugins.zsxq.backend import export_zsxq
from plugins.zsxq.backend.export_zsxq import ensure_converter_content


class ZsxqContentValidationTests(unittest.TestCase):
    def test_rejects_non_object_converter_result_with_context(self) -> None:
        for value in (None, "loading", []):
            with self.subTest(value=value), self.assertRaisesRegex(ExportError, "测试文章"):
                ensure_converter_content(value, "测试文章")

    def test_accepts_converter_object_unchanged(self) -> None:
        content = {"title": "测试文章", "markdown": "正文"}

        self.assertIs(ensure_converter_content(content, "测试文章"), content)

    def test_column_entry_rejects_a_non_object_converter_result(self) -> None:
        class ConverterReturnsNone:
            def evaluate(self, _expression: str, timeout: int):
                self.assertEqual(timeout, 60)
                return None

            def assertEqual(self, left, right):
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        with (
            patch.object(export_zsxq, "navigate_with_retry"),
            patch.object(export_zsxq, "wait_eval"),
            patch.object(export_zsxq, "expand_current_content"),
        ):
            with self.assertRaisesRegex(ExportError, "知识星球专栏正文"):
                export_zsxq.collect_entry_links(
                    ConverterReturnsNone(),
                    "https://wx.zsxq.com/columns/demo",
                    None,
                )


if __name__ == "__main__":
    unittest.main()
