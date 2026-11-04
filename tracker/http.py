"""Polite HTTP: identifiable user agent, per-host spacing, retries."""

from __future__ import annotations

import os
import time
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
# Bot-protection services some agency sites redirect to instead of serving the page.
CHALLENGE_HOSTS = ("validate.perfdrive.com",)


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
        self._blocked: set[str] = set()
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
        self._count[host] = self._count.get(host, 0) + 1
        try:
            return self._request(method, url, **kwargs)
        except BlockedError:
            self._blocked.add(host)
            raise

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
