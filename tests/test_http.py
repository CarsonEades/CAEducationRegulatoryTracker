import httpx
import pytest

from tracker import http
from tracker.http import BlockedError, BudgetExceeded, Fetcher


def make_fetcher(handler, monkeypatch, budgets=None):
    monkeypatch.setattr(http, "HOST_BUDGETS", budgets or {})
    monkeypatch.setattr(http, "HOST_INTERVALS", {})
    f = Fetcher(min_interval=0)
    f.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                            event_hooks={"response": [f._check_challenge]})
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
