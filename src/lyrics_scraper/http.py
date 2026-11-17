"""HTTP client: throttled, retrying, and UTF-8-correct.

Two site-specific behaviours are handled here:

1. **Missing charset.** The server responds with a bare ``Content-Type:
   text/html`` and no ``charset``. ``requests`` then applies the RFC 2616
   default of ISO-8859-1 for ``text/*``, which turns every Tamil character into
   mojibake. We therefore always decode ``response.content`` as UTF-8.

2. **robots.txt crawl-delay.** The site publishes ``Crawl-delay: 10`` for
   ``User-agent: *``. :func:`discover_crawl_delay` reads it so the effective
   delay is never lower than what the site asks for.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional
from urllib.robotparser import RobotFileParser

import requests
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import AppConfig

logger = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """Non-retryable HTTP failure (404, 403, ...)."""


class RetryableHttpError(RuntimeError):
    """Retryable failure: timeout, connection reset, 429 or 5xx."""


def _parse_retry_after(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def discover_crawl_delay(
    base_url: str, user_agent: str
) -> Optional[float]:
    """Return the ``Crawl-delay`` the site asks for, or ``None`` if unavailable."""
    parser = RobotFileParser()
    parser.set_url(base_url.rstrip("/") + "/robots.txt")
    try:
        parser.read()
    except Exception as exc:  # pragma: no cover - network/parse variance
        logger.debug("could not read robots.txt: %s", exc)
        return None
    try:
        delay = parser.crawl_delay(user_agent)
    except Exception:  # pragma: no cover
        return None
    if delay is None:
        return None
    try:
        return float(delay)
    except (TypeError, ValueError):
        return None


class HttpClient:
    """A polite, resumable GET-only client."""

    def __init__(self, cfg: AppConfig, session: Optional[requests.Session] = None) -> None:
        self._cfg = cfg
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": cfg.scraping.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ta;q=0.8",
                "Connection": "keep-alive",
            }
        )
        self._last_request_at: float = 0.0
        self.request_count = 0
        self.effective_delay = float(cfg.scraping.delay_seconds)

        self._retry_kwargs = dict(
            stop=stop_after_attempt(max(1, int(cfg.scraping.max_retries))),
            wait=wait_exponential(
                multiplier=max(0.1, float(cfg.scraping.backoff_factor)), min=1, max=60
            ),
            retry=retry_if_exception_type(RetryableHttpError),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    # -- politeness -------------------------------------------------------
    def apply_robots_crawl_delay(self) -> float:
        """Raise the delay to the site's published crawl-delay when higher."""
        if not self._cfg.scraping.respect_robots_crawl_delay:
            return self.effective_delay
        published = discover_crawl_delay(
            self._cfg.site.base_url, self._cfg.scraping.user_agent
        )
        if published and published > self.effective_delay:
            logger.info(
                "robots.txt requests Crawl-delay %.1fs; using that instead of %.1fs",
                published,
                self.effective_delay,
            )
            self.effective_delay = published
        return self.effective_delay

    def set_delay(self, seconds: float) -> None:
        self.effective_delay = max(0.0, float(seconds))

    def _throttle(self) -> None:
        delay = self.effective_delay
        jitter = float(self._cfg.scraping.delay_jitter_seconds)
        target = delay + (random.uniform(0.0, jitter) if jitter > 0 else 0.0)
        if target <= 0:
            return
        if self._last_request_at:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < target:
                time.sleep(target - elapsed)

    # -- fetching ---------------------------------------------------------
    def get(self, url: str) -> str:
        """GET ``url`` and return the body decoded as UTF-8."""
        for attempt in Retrying(**self._retry_kwargs):
            with attempt:
                return self._get_once(url)
        raise HttpError(f"unreachable: {url}")  # pragma: no cover

    def _get_once(self, url: str) -> str:
        self._throttle()
        self._last_request_at = time.monotonic()
        self.request_count += 1
        try:
            response = self._session.get(
                url, timeout=float(self._cfg.scraping.timeout_seconds)
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise RetryableHttpError(f"network error for {url}: {exc}") from exc
        except requests.RequestException as exc:
            raise HttpError(f"request failed for {url}: {exc}") from exc

        status = response.status_code
        if status == 429 or status >= 500:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if retry_after:
                logger.warning(
                    "HTTP %s from %s; honouring Retry-After %.0fs", status, url, retry_after
                )
                time.sleep(retry_after)
            raise RetryableHttpError(f"HTTP {status} for {url}")
        if status == 404:
            raise HttpError(f"HTTP 404 Not Found for {url}")
        if status >= 400:
            raise HttpError(f"HTTP {status} for {url}")

        # See module docstring: never trust response.encoding / response.text here.
        return response.content.decode("utf-8", errors="replace")

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
