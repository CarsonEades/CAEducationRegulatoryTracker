"""Polite HTTP: identifiable user agent, per-host spacing, retries."""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

# GitHub Actions passes an unset repository variable as an empty string, and a
# blank User-Agent gets requests refused (HTTP 403), so treat empty as unset.
def user_agent() -> str:
    return (os.environ.get("TRACKER_USER_AGENT", "").strip()
            or "CA-Education-Agency-Tracker/0.1 (agenda monitoring; contact via repository owner)")


USER_AGENT = user_agent()
MAX_BYTES = 40 * 1024 * 1024
# Hosts that challenge fast clients get a slower pace (seconds between requests).
HOST_INTERVALS = {"www.cde.ca.gov": 8.0}
# Hosts that limit how many automated requests they accept get a per-run cap.
# Requests past the cap wait for a later run. cde.ca.gov follows the paced
# crawler's CDE_PAGES_PER_RUN (see tracker/paced.py), up to MAX_CDE_PAGES.
MAX_CDE_PAGES = 100


def _cde_budget() -> int:
    try:
        pages = int(float(os.environ.get("CDE_PAGES_PER_RUN", "").strip() or 1))
    except ValueError:
        pages = 1
    return max(1, min(pages, MAX_CDE_PAGES))


HOST_BUDGETS = {"www.cde.ca.gov": _cde_budget()}
# Most documents (PDFs etc.) downloaded from a host per run. A source's first run lists
# a year or more of agendas and reports; this spreads those downloads over several runs
# instead of hitting a site with hundreds of requests at once. Documents past the limit
# are downloaded in a later run.
HOST_DOC_BUDGETS = {
    "abgt.assembly.ca.gov": 40,
    "sbud.senate.ca.gov": 40,
    "trailerbill.dof.ca.gov": 40,
    "dof.ca.gov": 20,
    "ebudget.ca.gov": 10,
    "www.lao.ca.gov": 30,
}
# Bot-protection services some agency sites redirect to instead of serving the page.
CHALLENGE_HOSTS = ("validate.perfdrive.com",)


DOCUMENT_TYPES = ("pdf", "msword", "officedocument", "powerpoint", "excel", "octet-stream")
DOCUMENT_EXTS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")


def request_type(url: str, content_type: str | None = None) -> str:
    """'document', 'api' or 'page': sites often treat these differently (cde.ca.gov's bot
    protection checks web pages but not documents), so the health page breaks them down."""
    ctype = (content_type or "").lower()
    path = urlsplit(url).path.lower()
    if any(t in ctype for t in DOCUMENT_TYPES) or path.endswith(DOCUMENT_EXTS) or "/pdf/" in path:
        return "document"
    if "json" in ctype or "/api/" in path or path.startswith("/public/"):
        return "api"
    if not ctype and "/media/" in path:
        return "document"  # the legislature's /media/N links redirect to files
    return "page"


@dataclass
class RequestRecord:
    """One request as the site saw it, for crawl_log (time, outcome, type, status)."""
    url: str
    host: str
    requested_at: dt.datetime
    request_type: str
    outcome: str            # ok | challenged | error
    status: int | None = None
    detail: str | None = None


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    """The site answered with a bot-protection challenge instead of the page."""


class BudgetExceeded(FetchError):
    """This run already made as many requests to the host as it allows."""


class Fetcher:
    def __init__(self, min_interval: float = 3.0, retries: int = 3, timeout: float = 45.0):
        self.min_interval = min_interval
        self.retries = retries
        self._last_hit: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._doc_count: dict[str, int] = {}
        self._blocked: set[str] = set()
        # Every request this run, in order; the pipeline writes them to crawl_log.
        self.log: list[RequestRecord] = []
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
            event_hooks={"response": [self._check_challenge]},
        )

    @staticmethod
    def _check_challenge(resp: httpx.Response) -> None:
        location = resp.headers.get("location", "")
        if resp.is_redirect and any(h in location for h in CHALLENGE_HOSTS):
            raise BlockedError(f"{resp.request.url} redirected to a bot-protection challenge")

    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).netloc
        interval = HOST_INTERVALS.get(host, self.min_interval)
        wait = self._last_hit.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()
        self._sent_at = dt.datetime.now(dt.timezone.utc)  # when the request actually went out

    def get(self, url: str) -> httpx.Response:
        """GET with retries; the body is capped at MAX_BYTES."""
        return self.request("GET", url)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).netloc
        if host in self._blocked:
            raise BlockedError(f"{host} challenged an earlier request this run; skipping {url}")
        if self._count.get(host, 0) >= HOST_BUDGETS.get(host, 10**9):
            raise BudgetExceeded(f"request limit for {host} reached this run; will retry next run")
        if request_type(url) == "document":
            if self._doc_count.get(host, 0) >= HOST_DOC_BUDGETS.get(host, 10**9):
                raise BudgetExceeded(f"document limit for {host} reached this run; "
                                     "will download in a later run")
            self._doc_count[host] = self._doc_count.get(host, 0) + 1
        self._count[host] = self._count.get(host, 0) + 1
        self._sent_at = dt.datetime.now(dt.timezone.utc)
        try:
            resp = self._request(method, url, **kwargs)
        except BlockedError as e:
            self._blocked.add(host)
            self._record(url, host, self._sent_at, "challenged", None, None, str(e))
            raise
        except FetchError as e:
            status = None
            if str(e).startswith("HTTP "):
                try:
                    status = int(str(e).split()[1])
                except ValueError:
                    pass
            self._record(url, host, self._sent_at, "error", status, None, str(e))
            raise
        final = str(resp.request.url) if resp.request is not None else url
        redirect = f"redirected to {urlsplit(final).netloc}" if urlsplit(final).netloc != host else None
        self._record(url, host, self._sent_at, "ok", resp.status_code,
                     resp.headers.get("content-type"), redirect)
        return resp

    def _record(self, url, host, started, outcome, status, content_type, detail) -> None:
        self.log.append(RequestRecord(url=url, host=host, requested_at=started,
                                      request_type=request_type(url, content_type),
                                      outcome=outcome, status=status,
                                      detail=(detail or None) and detail[:500]))

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.retries):
            self._wait_for_host(url)
            try:
                with self.client.stream(method, url, **kwargs) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise FetchError(f"HTTP {resp.status_code} for {url}")
                    resp.raise_for_status()
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise FetchError(f"{url} is larger than {MAX_BYTES} bytes")
                    # iter_bytes() already decompressed the body, so drop the encoding headers.
                    headers = {k: v for k, v in resp.headers.items()
                               if k.lower() not in ("content-encoding", "content-length")}
                    return httpx.Response(
                        resp.status_code, headers=headers, content=bytes(body),
                        request=resp.request,
                    )
            except httpx.TransportError as e:
                if any(h in str(e) for h in CHALLENGE_HOSTS):
                    raise BlockedError(f"{url} redirected to a bot-protection challenge") from e
                last = e
                time.sleep(2 ** attempt)
                continue
            except httpx.HTTPStatusError as e:
                raise FetchError(f"HTTP {e.response.status_code} for {url}") from e
            except FetchError as e:
                if isinstance(e, BlockedError) or "larger than" in str(e):
                    raise  # retrying right away would only prolong a block
                last = e
                time.sleep(2 ** attempt)
        raise FetchError(str(last))

    def close(self) -> None:
        self.client.close()
