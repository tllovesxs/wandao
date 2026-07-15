import unittest
from unittest import mock

from plugins.feishu.backend import export_feishu as feishu


class FakeWikiPage:
    def __init__(
        self,
        *,
        ready_state: str = "complete",
        href: str = "https://example.feishu.cn/wiki/wiki-token",
        body_text: str = "A normal Feishu Wiki document with enough visible content.",
        title: str = "Document - Feishu",
        login_form: bool = False,
    ) -> None:
        self.ready_state = ready_state
        self.href = href
        self.body_text = body_text
        self.title = title
        self.login_form = login_form
        self.expressions: list[str] = []

    def evaluate(self, expression: str, timeout: int = 10):
        self.expressions.append(expression)
        compact = " ".join(expression.split())
        state = {
            "href": self.href,
            "url": self.href,
            "title": self.title,
            "readyState": self.ready_state,
            "ready_state": self.ready_state,
            "state": self.ready_state,
            "hasBody": True,
            "textLength": len(self.body_text),
            "text_length": len(self.body_text),
            "bodyText": self.body_text,
            "body_text": self.body_text,
            "text": self.body_text,
            "isLogin": self.login_form,
            "isLoginPage": self.login_form,
            "hasLoginForm": self.login_form,
            "login": self.login_form,
            "permissionDenied": False,
            "isPermissionDenied": False,
        }
        if "location.href" in compact and "document.readyState" in compact:
            return state
        if compact in {"location.href", "window.location.href"}:
            return self.href
        if compact == "document.readyState":
            return self.ready_state
        if "slice(0, 500)" in compact:
            return self.body_text[:500]
        if "querySelector" in compact and any(token in compact.lower() for token in ("login", "passport", "account")):
            return self.login_form
        if "document.readyState === 'complete'" in compact:
            return self.ready_state == "complete" and len(self.body_text) > 20
        if "document.readyState !== 'loading'" in compact:
            return self.ready_state != "loading" and len(self.body_text) > 20
        if "document.readyState" in compact:
            return self.ready_state != "loading" and len(self.body_text) > 20
        return state


class Clock:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return 0.0 if self.calls <= 2 else 2.0


class FeishuReadinessTests(unittest.TestCase):
    def test_interactive_wiki_page_is_ready_without_waiting_for_slow_load_event(self) -> None:
        page = FakeWikiPage(ready_state="interactive", body_text="x" * 1447)
        clock = Clock()

        with mock.patch.object(feishu.time, "time", side_effect=clock), mock.patch.object(feishu.time, "sleep"):
            feishu.wait_for_wiki_ready(page, timeout=1)

        self.assertFalse(
            any("document.readyState === 'complete'" in expression for expression in page.expressions),
            "Wiki readiness must not wait for Feishu's slow window load event",
        )

    def test_login_word_in_normal_document_does_not_make_page_look_logged_out(self) -> None:
        page = FakeWikiPage(
            body_text="登录凭证获取以后即可继续使用，这是知识库正文而不是登录页面。" + "正文" * 30,
        )
        clock = Clock()

        with mock.patch.object(feishu.time, "time", side_effect=clock), mock.patch.object(feishu.time, "sleep"):
            feishu.wait_for_wiki_ready(page, timeout=1)

    def test_explicit_login_page_is_not_reported_ready(self) -> None:
        page = FakeWikiPage(
            href="https://example.feishu.cn/accounts/page/login",
            body_text="登录 飞书账号",
            title="登录",
            login_form=True,
        )
        clock = Clock()

        with (
            mock.patch.object(feishu.time, "time", side_effect=clock),
            mock.patch.object(feishu.time, "sleep"),
            self.assertRaises(feishu.ExportError),
        ):
            feishu.wait_for_wiki_ready(page, timeout=1)


if __name__ == "__main__":
    unittest.main()
