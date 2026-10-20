"""Shared HTTP behaviour for connectors: timeouts, retries with backoff, no secrets in logs."""
import logging
import time

import httpx

log = logging.getLogger("connectors")
RETRY_STATUS = {429, 500, 502, 503, 504}


def request_with_retries(client: httpx.Client, method: str, url: str, attempts: int = 3, **kwargs) -> httpx.Response:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code not in RETRY_STATUS:
                response.raise_for_status()
                return response
            reason = f"HTTP {response.status_code}"
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        log.warning("connector retry", extra={"extra_fields": {"host": httpx.URL(url).host, "attempt": attempt,
                                                                "reason": reason}})
        if attempt == attempts:
            raise RuntimeError(f"{method} {httpx.URL(url).host} failed: {reason}")
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")
