import unittest

from wandao_core.browser import WINDOWS_RESERVED_NAMES, sanitize_filename


def _stem(name: str) -> str:
    return name.split(".", 1)[0]


class SanitizeFilenameReservedNameTests(unittest.TestCase):
    def test_bare_reserved_names_are_escaped(self) -> None:
        for value in ("CON", "con.md", "COM1", "lpt9.txt", "NUL"):
            with self.subTest(value=value):
                result = sanitize_filename(value)

                self.assertNotIn(_stem(result).upper(), WINDOWS_RESERVED_NAMES)
                self.assertTrue(result.startswith("_"), result)

    def test_reserved_name_keeps_original_text_and_extension(self) -> None:
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("con.md"), "_con.md")
        self.assertEqual(sanitize_filename("COM1"), "_COM1")
        self.assertEqual(sanitize_filename("lpt9.txt"), "_lpt9.txt")
        self.assertEqual(sanitize_filename("NUL"), "_NUL")

    def test_reserved_name_detection_is_case_insensitive(self) -> None:
        for value in ("con", "Con", "cOn", "PRN", "prn.md", "AuX", "nul.markdown"):
            with self.subTest(value=value):
                self.assertTrue(sanitize_filename(value).startswith("_"))

    def test_ordinary_names_are_left_untouched(self) -> None:
        untouched = [
            "console.md",
            "companion",
            "中文标题",
            "nullable",
            "auxiliary.md",
            "printer.txt",
            "CONTENTS",
            "com.example.note",
            "lpt10.txt",
            "COM10",
            "COM0",
            "my-con.md",
            "con-notes.md",
            "会议纪要 2026",
        ]
        for value in untouched:
            with self.subTest(value=value):
                self.assertEqual(sanitize_filename(value), value)

    def test_escaping_does_not_change_unrelated_sanitizing_behaviour(self) -> None:
        self.assertEqual(sanitize_filename(""), "未命名")
        self.assertEqual(sanitize_filename("a/b:c"), "a-b-c")
        self.assertEqual(sanitize_filename("  spaced   out  "), "spaced out")
        self.assertEqual(sanitize_filename("trailing dots..."), "trailing dots")

    def test_truncation_never_leaves_a_trailing_dot(self) -> None:
        value = "x" * 89 + ".. tail"

        result = sanitize_filename(value)

        self.assertLessEqual(len(result), 90)
        self.assertFalse(result.endswith("."))
        self.assertFalse(result.endswith(" "))


if __name__ == "__main__":
    unittest.main()
