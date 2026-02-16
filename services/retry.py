"""
Exponential-backoff retry utility for HTTP requests.

Retries on: ConnectionError, Timeout, HTTP 429 / 500 / 502 / 503 / 504
Does NOT retry: 400, 401, 403, 404 (client errors are not transient)
Respects Retry-After header on 429
"""
import time
import logging
import requests as http_requests

log = logging.getLogger(__name__)

# HTTP status codes worth retrying
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Exceptions worth retrying
RETRYABLE_EXCEPTIONS = (
    http_requests.exceptions.ConnectionError,
    http_requests.exceptions.Timeout,
    http_requests.exceptions.ChunkedEncodingError,
)


def retry_request(
    request_func,
    *,
    max_retries=3,
    base_delay=2,
    max_delay=30,
    label="HTTP request",
):
    """Execute *request_func* with exponential-backoff retries.

    Parameters
    ----------
    request_func : callable
        A zero-arg callable that performs the HTTP request and returns
        a ``requests.Response``.  Example::

            lambda: http_requests.get(url, headers=h, params=p)

    max_retries : int
        How many times to retry after the initial attempt (default 3).
    base_delay : float
        Initial back-off delay in seconds (doubles each retry).
    max_delay : float
        Cap on the delay between retries.
    label : str
        Human-readable label for log messages.

    Returns
    -------
    requests.Response
        The successful response object.

    Raises
    ------
    requests.HTTPError
        If all retries are exhausted or a non-retryable status is returned.
    """
    last_exc = None

    for attempt in range(max_retries + 1):
        try:
            resp = request_func()

            # Success — return immediately
            if resp.status_code < 400:
                return resp

            # Non-retryable client error — raise right away
            if resp.status_code < 500 and resp.status_code != 429:
                resp.raise_for_status()

            # Retryable server error or 429
            if resp.status_code in RETRYABLE_STATUS_CODES:
                if attempt == max_retries:
                    resp.raise_for_status()

                delay = _compute_delay(resp, attempt, base_delay, max_delay)
                log.warning(
                    "%s returned %s — retry %d/%d in %.1fs",
                    label, resp.status_code, attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
                continue

            # Any other error status — raise immediately
            resp.raise_for_status()

        except RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            log.warning(
                "%s raised %s — retry %d/%d in %.1fs",
                label, type(exc).__name__, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)

    # Should not reach here, but just in case:
    if last_exc:
        raise last_exc


def _compute_delay(resp, attempt, base_delay, max_delay):
    """Compute backoff delay, respecting Retry-After on 429."""
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except (ValueError, TypeError):
                pass
    return min(base_delay * (2 ** attempt), max_delay)
