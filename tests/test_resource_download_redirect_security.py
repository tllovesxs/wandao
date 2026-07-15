import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from plugins.yuque.backend import export_yuque


class FakeResponse:
    def __init__(self, body: bytes = b"image", content_type: str = "image/png") -> None:
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def read(self) -> bytes:
        return self.body


class QueueOpener:
    def __init__(self, events):
        self.events = list(events)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


def redirect(location: str) -> urllib.error.HTTPError:
    headers = Message()
    headers["Location"] = location
    return urllib.error.HTTPError("https://origin.invalid/resource", 302, "Found", headers, None)


def request_headers(request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


class ResourceDownloadRedirectSecurityTests(unittest.TestCase):
    def test_yuque_cross_origin_redirect_drops_cookie_and_referer(self) -> None:
        opener = QueueOpener([redirect("https://cdn.example.com/resource.png"), FakeResponse()])
        cookies = [{"name": "session", "value": "secret", "domain": ".yuque.com", "path": "/", "secure": True}]

        with tempfile.TemporaryDirectory() as tmp, patch.object(export_yuque.urllib.request, "build_opener", return_value=opener) as build_opener:
            export_yuque.download_resource(
                "https://assets.yuque.com/resource.png",
                Path(tmp),
                timeout=5,
                cookies=cookies,
                referer="https://www.yuque.com/team/book",
            )

        self.assertIsInstance(build_opener.call_args.args[0], export_yuque._ResourceNoRedirect)
        self.assertEqual(request_headers(opener.requests[0]).get("cookie"), "session=secret")
        self.assertNotIn("cookie", request_headers(opener.requests[1]))
        self.assertNotIn("referer", request_headers(opener.requests[1]))

    def test_same_origin_redirect_rebuilds_matching_cookie(self) -> None:
        opener = QueueOpener([redirect("/next.png"), FakeResponse()])
        cookies = [{"name": "session", "value": "secret", "domain": ".yuque.com", "path": "/", "secure": True}]

        with tempfile.TemporaryDirectory() as tmp, patch.object(export_yuque.urllib.request, "build_opener", return_value=opener):
            export_yuque.download_resource("https://assets.yuque.com/first.png", Path(tmp), 5, cookies)

        self.assertEqual(request_headers(opener.requests[0]).get("cookie"), "session=secret")
        self.assertEqual(request_headers(opener.requests[1]).get("cookie"), "session=secret")

    def test_secure_cookie_and_suffix_boundary_are_respected(self) -> None:
        yuque_cookie = [{"name": "session", "value": "secret", "domain": ".yuque.com", "path": "/", "secure": True}]

        self.assertEqual(export_yuque.cookies_for_url(yuque_cookie, "http://www.yuque.com/file"), "")
        self.assertEqual(export_yuque.cookies_for_url(yuque_cookie, "https://notyuque.com/file"), "")

    def test_non_https_initial_and_redirect_targets_are_rejected_before_following(self) -> None:
        with patch.object(export_yuque.urllib.request, "build_opener") as yuque_opener:
            with self.assertRaises(export_yuque.ExportError):
                export_yuque.download_resource("http://assets.yuque.com/file", Path.cwd(), 5, [])
            with self.assertRaises(export_yuque.ExportError):
                export_yuque.download_resource("https://127.0.0.1/file", Path.cwd(), 5, [])
        yuque_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
