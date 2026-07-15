import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from plugins.youdao.backend import export_youdao


class _Response:
    headers = {"Content-Type": "application/octet-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return b"ok"

    def geturl(self) -> str:
        return "https://cdn.example.test/resource.bin"


class _RedirectingOpener:
    def __init__(self) -> None:
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://cdn.example.test/resource.bin"},
                None,
            )
        return _Response()


class YoudaoCookieScopeTests(unittest.TestCase):
    def test_only_trusted_https_youdao_urls_match_resources_and_cookies(self) -> None:
        cookies = [
            {"name": "YNOTE_CSTK", "value": "secret", "domain": ".note.youdao.com", "path": "/", "secure": True},
            {"name": "YNOTE_SESS", "value": "session", "domain": ".note.youdao.com", "path": "/", "secure": True},
        ]
        self.assertTrue(export_youdao.is_youdao_https_url("https://note.youdao.com/yws/file"))
        self.assertTrue(export_youdao.is_youdao_https_url("https://assets.note.youdao.com/file"))
        self.assertFalse(export_youdao.is_youdao_https_url("https://attacker.test/note.youdao.com/file"))
        self.assertFalse(export_youdao.is_youdao_https_url("http://note.youdao.com/file"))
        self.assertIn("YNOTE_CSTK=secret", export_youdao.cookie_header(cookies, "https://note.youdao.com/yws/file"))
        self.assertEqual(export_youdao.cookie_header(cookies, "https://attacker.test/note.youdao.com/file"), "")
        self.assertIsNone(export_youdao.IMAGE_LINK_RE.search("![x](https://attacker.test/note.youdao.com/file.png)"))
        self.assertIsNotNone(export_youdao.IMAGE_LINK_RE.search("![x](https://note.youdao.com/file.png)"))

    def test_cross_domain_redirect_rebuilds_request_without_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {"name": "YNOTE_CSTK", "value": "secret", "domain": ".note.youdao.com", "path": "/", "secure": True},
                            {"name": "YNOTE_SESS", "value": "session", "domain": ".note.youdao.com", "path": "/", "secure": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            client = export_youdao.YoudaoClient(auth_file)
            opener = _RedirectingOpener()
            with patch("urllib.request.build_opener", return_value=opener):
                result = client.download_url("https://note.youdao.com/resource.png")

            self.assertEqual(result.content, b"ok")
            self.assertEqual(len(opener.requests), 2)
            self.assertIn("YNOTE_CSTK=secret", opener.requests[0].get_header("Cookie"))
            self.assertIsNone(opener.requests[1].get_header("Cookie"))
            self.assertIsNone(opener.requests[1].get_header("Origin"))

    def test_non_youdao_resource_url_is_rejected_before_opening_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            auth_file.write_text(
                json.dumps({"cookies": [{"name": "YNOTE_CSTK", "value": "secret"}, {"name": "YNOTE_SESS", "value": "session"}]}),
                encoding="utf-8",
            )
            client = export_youdao.YoudaoClient(auth_file)
            with self.assertRaises(export_youdao.YoudaoError):
                client.download_url("https://attacker.test/note.youdao.com/resource.png")


if __name__ == "__main__":
    unittest.main()
