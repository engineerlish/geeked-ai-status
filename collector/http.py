"""Minimal HTTP helpers on the standard library, with retries and short, bounded timeouts."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "geeked-ai-status/1 (+https://github.com/engineerlish/geeked-ai-status)"
TIMEOUT_SECONDS = 20
ATTEMPTS = 3


class FetchError(Exception):
    """A request failed in a way worth recording (network error, HTTP error, bad body)."""


def get_text(url: str, headers: dict[str, str] | None = None, *, attempts: int = ATTEMPTS) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            code = err.code
            err.close()
            # Client errors are not going to fix themselves: report them straight away.
            if 400 <= code < 500 and code != 429:
                raise FetchError(f"HTTP {code} from {_host(url)}") from None
            last = FetchError(f"HTTP {code}")
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise FetchError(f"{type(last).__name__} from {_host(url)}: {last}") from last


def get_json(url: str, headers: dict[str, str] | None = None, *, attempts: int = ATTEMPTS) -> Any:
    text = get_text(url, {"Accept": "application/json", **(headers or {})}, attempts=attempts)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise FetchError(f"invalid JSON from {_host(url)}: {err}") from err


def _host(url: str) -> str:
    # Never include the query string or credentials in an error that gets committed.
    return urllib.parse.urlparse(url).netloc or "unknown host"
