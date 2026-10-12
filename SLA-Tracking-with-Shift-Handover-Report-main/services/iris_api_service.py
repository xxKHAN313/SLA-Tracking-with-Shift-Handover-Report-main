"""
services/iris_api_service.py
------------------------------
Thin wrapper around the DFIR-IRIS REST API.

Uses the ``requests`` library directly (rather than the dfir-iris-client
package) so the integration has no hard dependency beyond ``requests``, and so
the exact endpoint paths/response shapes are easy for an intern to see and
adjust here in one place if the real IRIS instance differs from what's
assumed below.

If your IRIS version's endpoints or response JSON differ, you only need to
change the request paths / response parsing in THIS file - normalize_ticket()
and the SLA engine don't care where the dicts came from.

Gap #6: Pagination now uses the IRIS response envelope fields (``total``,
``last_page``, ``current_page``) to loop until all pages are consumed.

Gap #7: All HTTP calls go through ``_request_with_retry()`` which implements
exponential backoff with retries on transient failures (timeouts, 429, 5xx).
"""

import logging
from functools import lru_cache
import time as _time

import requests
from flask import current_app

logger = logging.getLogger(__name__)


class IrisApiError(Exception):
    pass


# --- Gap #7: Retry / backoff configuration ---

_MAX_RETRIES = 3
_INITIAL_BACKOFF_SECONDS = 1.0
_BACKOFF_MULTIPLIER = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


from urllib.parse import urlparse

def is_valid_external_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _get_config():
    """Pulls IRIS connection settings from Flask config (env-driven), with
    DB-stored Setting overrides taking precedence if present."""
    from models import Setting

    base_url = Setting.get("iris_base_url") or current_app.config.get("IRIS_BASE_URL")
    api_key = current_app.config.get("IRIS_API_KEY")  # secret - env only, never DB
    verify_ssl = current_app.config.get("IRIS_VERIFY_SSL", True)
    timeout = current_app.config.get("IRIS_TIMEOUT_SECONDS", 30)

    if not base_url or not is_valid_external_url(base_url):
        raise IrisApiError("IRIS base URL is invalid or not configured. Use a valid HTTP/HTTPS URL.")
    if not api_key:
        raise IrisApiError("IRIS API key is not configured. Set IRIS_API_KEY in .env.")

    return base_url.rstrip("/"), api_key, verify_ssl, timeout


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, *, headers=None, params=None,
                         verify=True, timeout=30):
    """
    Gap #7: HTTP request wrapper with exponential backoff.

    Retries up to ``_MAX_RETRIES`` times on:
      - Connection errors and timeouts
      - HTTP 429 (rate limited) and 5xx server errors

    Logs each retry attempt. Raises ``IrisApiError`` if all retries fail.
    """
    backoff = _INITIAL_BACKOFF_SECONDS
    last_exc = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url,
                headers=headers,
                params=params,
                verify=verify,
                timeout=timeout,
            )

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                logger.warning(
                    "IRIS API returned %s for %s — retrying in %.1fs (attempt %d/%d)",
                    resp.status_code, url, backoff, attempt + 1, _MAX_RETRIES,
                )
                _time.sleep(backoff)
                backoff *= _BACKOFF_MULTIPLIER
                continue

            return resp

        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "IRIS API request to %s failed (%s) — retrying in %.1fs (attempt %d/%d)",
                    url, exc, backoff, attempt + 1, _MAX_RETRIES,
                )
                _time.sleep(backoff)
                backoff *= _BACKOFF_MULTIPLIER
            else:
                raise IrisApiError(
                    f"IRIS API request to {url} failed after {_MAX_RETRIES} retries: {last_exc}"
                ) from last_exc

    # Should not reach here, but safety net
    raise IrisApiError(f"IRIS API request to {url} failed after all retries")


def test_connection():
    """Pings the IRIS API to verify the base URL and API key work.
    Returns (success: bool, message: str)."""
    try:
        base_url, api_key, verify_ssl, timeout = _get_config()
    except IrisApiError as exc:
        return False, str(exc)

    try:
        # DFIR-IRIS exposes a ping/version endpoint; adjust path if your
        # instance differs (e.g. /api/versions on some deployments).
        resp = _request_with_retry(
            "GET",
            f"{base_url}/api/ping",
            headers=_headers(api_key),
            verify=verify_ssl,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True, "Connected to DFIR-IRIS successfully."
        return False, f"IRIS responded with status {resp.status_code}: {resp.text[:200]}"
    except (requests.exceptions.RequestException, IrisApiError) as exc:
        return False, f"Connection failed: {exc}"


def fetch_cases(page=1, per_page=50):
    """Fetch a page of cases from DFIR-IRIS.
    Returns a tuple: (list of raw case dicts, pagination_meta dict)."""
    base_url, api_key, verify_ssl, timeout = _get_config()

    resp = _request_with_retry(
        "GET",
        f"{base_url}/manage/cases/list",
        headers=_headers(api_key),
        params={"page": page, "per_page": per_page},
        verify=verify_ssl,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise IrisApiError(f"fetch_cases failed: HTTP {resp.status_code} - {resp.text[:300]}")

    payload = resp.json()

    # DFIR-IRIS typically wraps list results under a "data" key,
    # with pagination meta at the top level.
    data = payload.get("data", payload if isinstance(payload, list) else [])

    # Gap #6: extract pagination envelope
    pagination_meta = {
        "total": payload.get("total"),
        "last_page": payload.get("last_page"),
        "current_page": payload.get("current_page", page),
    }

    return data, pagination_meta


def fetch_alerts(page=1, per_page=50):
    """Fetch a page of alerts from DFIR-IRIS.
    Returns a tuple: (list of raw alert dicts, pagination_meta dict)."""
    base_url, api_key, verify_ssl, timeout = _get_config()

    resp = _request_with_retry(
        "GET",
        f"{base_url}/api/alerts/filter",
        headers=_headers(api_key),
        params={"page": page, "per_page": per_page},
        verify=verify_ssl,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise IrisApiError(f"fetch_alerts failed: HTTP {resp.status_code} - {resp.text[:300]}")

    payload = resp.json()
    data = payload.get("data", payload if isinstance(payload, list) else [])
    pagination_meta = {
        "total": payload.get("total"),
        "last_page": payload.get("last_page"),
        "current_page": payload.get("current_page", page),
    }

    return data, pagination_meta


def fetch_case_by_id(case_id):
    """Fetch a single case's full detail by ID."""
    base_url, api_key, verify_ssl, timeout = _get_config()

    resp = _request_with_retry(
        "GET",
        f"{base_url}/manage/cases/{case_id}",
        headers=_headers(api_key),
        verify=verify_ssl,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise IrisApiError(f"fetch_case_by_id({case_id}) failed: HTTP {resp.status_code}")

    return resp.json().get("data", {})


def fetch_all_cases(max_pages=100, per_page=50):
    """
    Gap #6: Paginate through fetch_cases() using the IRIS response envelope's
    pagination fields (``total``, ``last_page``, ``current_page``).

    Falls back to the previous heuristic (empty page or batch < per_page) if
    the envelope doesn't include pagination fields.

    Logs a warning if ``max_pages`` is reached before all data is consumed.
    """
    all_cases = []
    for page in range(1, max_pages + 1):
        batch, meta = fetch_cases(page=page, per_page=per_page)
        if not batch:
            break
        all_cases.extend(batch)

        # Use envelope pagination if available
        last_page = meta.get("last_page")
        if last_page is not None:
            if page >= last_page:
                break
        else:
            # Fallback: stop if this page was not full
            if len(batch) < per_page:
                break

    else:
        # max_pages reached without exhausting data
        total = meta.get("total", "unknown")
        logger.warning(
            "fetch_all_cases hit max_pages=%d with only %d cases fetched "
            "(IRIS reports total=%s). Consider increasing max_pages.",
            max_pages, len(all_cases), total,
        )

    return all_cases


def fetch_all_alerts(max_pages=100, per_page=50):
    """
    Gap #6: Paginate through fetch_alerts() using the same envelope-aware
    logic as fetch_all_cases().
    """
    all_alerts = []
    for page in range(1, max_pages + 1):
        batch, meta = fetch_alerts(page=page, per_page=per_page)
        if not batch:
            break
        all_alerts.extend(batch)

        last_page = meta.get("last_page")
        if last_page is not None:
            if page >= last_page:
                break
        else:
            if len(batch) < per_page:
                break
    else:
        total = meta.get("total", "unknown")
        logger.warning(
            "fetch_all_alerts hit max_pages=%d with only %d alerts fetched "
            "(IRIS reports total=%s). Consider increasing max_pages.",
            max_pages, len(all_alerts), total,
        )

    return all_alerts


def fetch_customers():
    """Fetch all customers from DFIR-IRIS.
    Returns a list of raw customer dicts."""
    base_url, api_key, verify_ssl, timeout = _get_config()

    resp = _request_with_retry(
        "GET",
        f"{base_url}/manage/customers/list",
        headers=_headers(api_key),
        verify=verify_ssl,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise IrisApiError(f"fetch_customers failed: HTTP {resp.status_code} - {resp.text[:300]}")

    payload = resp.json()
    data = payload.get("data", payload if isinstance(payload, list) else [])
    return data


def fetch_customer_by_id(customer_id):
    """Fetch a single customer's details by ID from DFIR-IRIS."""
    base_url, api_key, verify_ssl, timeout = _get_config()

    resp = _request_with_retry(
        "GET",
        f"{base_url}/manage/customers/{customer_id}",
        headers=_headers(api_key),
        verify=verify_ssl,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise IrisApiError(f"fetch_customer_by_id({customer_id}) failed: HTTP {resp.status_code}")

    return resp.json().get("data", {})


@lru_cache(maxsize=1)
def fetch_classifications():
    """Fetch the list of case classifications from DFIR-IRIS."""
    try:
        base_url, api_key, verify_ssl, timeout = _get_config()
    except IrisApiError as exc:
        logger.warning("Could not load IRIS config for fetching classifications: %s", exc)
        return []

    try:
        resp = _request_with_retry(
            "GET",
            f"{base_url}/manage/case-classifications/list",
            headers=_headers(api_key),
            verify=verify_ssl,
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.warning("Failed to fetch classifications: HTTP %s", resp.status_code)
            return []
        payload = resp.json()
        if isinstance(payload, dict):
            return payload.get("data", [])
        elif isinstance(payload, list):
            return payload
        return []
    except Exception as exc:
        logger.warning("Failed to fetch classifications: %s", exc)
        return []

