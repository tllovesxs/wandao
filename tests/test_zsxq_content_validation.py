import unittest

from wandao_core.browser import ExportError
from plugins.zsxq.backend.export_zsxq import ensure_converter_content


class ZsxqContentValidationTests(unittest.TestCase):
    def test_rejects_non_object_converter_result_with_context(self) -> None:
        for value in (None, "loading", []):
            with self.subTest(value=value), self.assertRaisesRegex(ExportError, "测试文章"):
                ensure_converter_content(value, "测试文章")

    def test_accepts_converter_object_unchanged(self) -> None:
        content = {"title": "测试文章", "markdown": "正文"}

        self.assertIs(ensure_converter_content(content, "测试文章"), content)


if __name__ == "__main__":
    unittest.main()
