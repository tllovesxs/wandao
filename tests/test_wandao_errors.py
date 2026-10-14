import unittest

from wandao_core.errors import normalize_error


class WandaoErrorTests(unittest.TestCase):
    def test_cdp_disconnect_has_a_browser_recovery_message(self) -> None:
        info = normalize_error("CDPConnectionError: CDP 浏览器连接中断：[WinError 10054]")

        self.assertEqual(info["code"], "CDP_CONNECTION_LOST")
        self.assertEqual(info["category"], "browser")
        self.assertTrue(info["retryable"])

    def test_resource_response_404_is_retryable_resource_error(self) -> None:
        info = normalize_error("图片响应 HTTP 404")

        self.assertEqual(info["code"], "RESOURCE_DOWNLOAD_FAILED")
        self.assertEqual(info["category"], "resource")
        self.assertTrue(info["retryable"])

    def test_plain_404_remains_document_not_found(self) -> None:
        info = normalize_error("HTTP 404 Not Found")

        self.assertEqual(info["code"], "NOT_FOUND")
        self.assertEqual(info["category"], "not_found")
        self.assertFalse(info["retryable"])
