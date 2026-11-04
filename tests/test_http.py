import httpx
import pytest

from tracker import http
from tracker.http import BlockedError, BudgetExceeded, Fetcher


def make_fetcher(handler, monkeypatch, budgets=None):
    monkeypatch.setattr(http, "HOST_BUDGETS", budgets or {})
    monkeypatch.setattr(http, "HOST_INTERVALS", {})
    f = Fetcher(min_interval=0)
    f.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                            event_hooks={"response": [f._on_response]})
    return f


def test_bot_challenge_redirect_stops_requests_to_that_host(monkeypatch):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://validate.perfdrive.com/?ssa=1"})

    f = make_fetcher(handler, monkeypatch)
    with pytest.raises(BlockedError):
        f.get("https://www.cde.ca.gov/a.asp")
    with pytest.raises(BlockedError):
        f.get("https://www.cde.ca.gov/b.asp")
    assert calls == ["https://www.cde.ca.gov/a.asp"]  # no retries, no further requests


def test_per_host_request_budget(monkeypatch):
    f = make_fetcher(lambda req: httpx.Response(200, text="ok"), monkeypatch,
                     budgets={"www.cde.ca.gov": 2})
    f.get("https://www.cde.ca.gov/1")
    f.get("https://www.cde.ca.gov/2")
    with pytest.raises(BudgetExceeded):
        f.get("https://www.cde.ca.gov/3")
    assert f.get("https://oag.ca.gov/x").text == "ok"  # other hosts unaffected


def test_empty_user_agent_setting_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRACKER_USER_AGENT", "")
    assert http.user_agent().startswith("CA-Education-Agency-Tracker/")
    monkeypatch.setenv("TRACKER_USER_AGENT", "Our Org Tracker (ops@example.org)")
    assert http.user_agent() == "Our Org Tracker (ops@example.org)"


def test_cde_requests_are_traced_with_cookie_names_only(monkeypatch):
    def handler(req):
        if req.url.host != "www.cde.ca.gov":
            return httpx.Response(200, text="ok")
        if req.url.path == "/first.asp":
            return httpx.Response(200, text="<script src='https://x.perfdrive.com/a.js'></script>",
                                  headers=[("set-cookie", "__uzma=secret1; Path=/"),
                                           ("set-cookie", "__uzmb=secret2; Path=/")])
        return httpx.Response(302, headers={"location": "https://validate.perfdrive.com/?ssa=1"})

    f = make_fetcher(handler, monkeypatch)
    resp = f.get("https://www.cde.ca.gov/first.asp")
    assert f.trace_text() == "HTTP 200; cookies sent: none; set: __uzma, __uzmb"
    assert http.bot_markers(resp.content) == ["perfdrive"]
    with pytest.raises(BlockedError):
        f.get("https://www.cde.ca.gov/second.asp")
    assert f.trace_text() == ("HTTP 302 (redirect to validate.perfdrive.com); "
                              "cookies sent: __uzma, __uzmb; set: none")
    assert "secret" not in f.trace_text()
    f.get("https://oag.ca.gov/x")  # other hosts aren't traced
    assert f.trace_text().startswith("HTTP 302")
