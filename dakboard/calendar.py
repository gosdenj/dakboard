"""Google Calendar access: OAuth 2.0 (primary) and iCal (fallback).

OAuth notes that matter:
  * the consent screen MUST be set to "In production".  A "Testing" app
    issues refresh tokens that expire after 7 DAYS, which would silently
    break the board every week.
  * verification is NOT required for a self-hosted single-user app.
  * scope is calendar.readonly - the board only ever reads.
"""

import base64
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

from . import ical

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CAL_BASE = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"


class CalendarError(Exception):
    """Raised when the calendar cannot be read."""


# ---------------------------------------------------------------- OAuth ---

def auth_url(client_id, redirect_uri, state=None):
    """The Google consent URL for the settings page to open."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",      # ask for a refresh token
        "prompt": "consent",           # and always give one back
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def check_redirect_uri(uri):
    """Check a redirect URI against Google's rules.

    Returns (ok, message). Google rejects a raw IP address outright - the
    consent page says "Error 400: invalid_request" - and requires HTTPS for
    anything that is not loopback. Learning that from Google's error page
    is a miserable way to find out, so the board says it up front and
    suggests a redirect URI that will work.

    Loopback (localhost / 127.0.0.1) is exempt from both rules and any
    port and any path are accepted - that is the Desktop app flow.
    """
    if not uri or not uri.strip():
        return False, "No redirect URI is set."
    uri = uri.strip()
    try:
        parts = urllib.parse.urlsplit(uri)
    except ValueError:
        return False, f"That redirect URI could not be parsed: {uri}"
    host = (parts.hostname or "").lower()
    if not host:
        return False, f"That redirect URI has no host: {uri}"
    if host in ("localhost", "127.0.0.1", "::1"):
        return True, ""

    # a bare IP address is never allowed
    try:
        import ipaddress
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        return False, (
            "Google will not accept %s as a redirect URI - a raw IP address "
            "is rejected with 'Error 400: invalid_request'. Use "
            "http://localhost:8099/oauth/callback with a Desktop app client, "
            "or put the board behind HTTPS on a real hostname and use "
            "https://your-host/oauth/callback with a Web application client."
            % uri)

    if parts.scheme != "https":
        return False, (
            "Google requires HTTPS for a redirect URI unless it is localhost, "
            "and %s uses %s. Use http://localhost:8099/oauth/callback, or "
            "https://your-host/oauth/callback."
            % (uri, parts.scheme or "no scheme"))
    return True, ""


def check_client_id(client_id):
    """Sanity-check a Google OAuth client ID.

    Google's error for a bad client ID is a bare "Error 401: invalid_client"
    on a page with nothing you can act on. A client ID always looks like
    "1234567890-abcdefghij.apps.googleusercontent.com", so a value that
    does not is worth catching here - it is usually the client SECRET
    pasted into the wrong field, or the project ID.
    """
    if not client_id or not client_id.strip():
        return False, "No client ID is set."
    client_id = client_id.strip()
    if "apps.googleusercontent.com" not in client_id:
        return False, (
            "That does not look like a Google OAuth client ID. It should end "
            "with .apps.googleusercontent.com - for example "
            "1234567890-abcdefghij.apps.googleusercontent.com. Copy it from "
            "Google Cloud Console > APIs & Services > Credentials, not from "
            "the client secret field, and not from the project ID."
        )
    if len(client_id) > 250:
        return False, "That client ID is far too long - it may be a paste error."
    return True, ""


def _google_error(detail, fallback):
    """Turn Google's JSON error into something a human can act on.

    Google's responses are JSON with an "error" code and an
    "error_description". Showing the raw JSON was useless; naming the code
    and what to do about it is not.
    """
    try:
        payload = json.loads(detail)
    except ValueError:
        return f"{fallback}: {detail[:200]}"
    if not isinstance(payload, dict):
        return f"{fallback}: {detail[:200]}"
    code = payload.get("error", "") or ""
    desc = payload.get("error_description", "") or ""
    hints = {
        "invalid_request": "The redirect URI is not one Google accepts. Raw IP "
                           "addresses and plain HTTP are both rejected.",
        "redirect_uri_mismatch": "The redirect URI does not match the one "
                                 "registered on the OAuth client. They must be "
                                 "identical.",
        "invalid_client": "Google does not recognise that client ID or secret. "
                          "Check the client ID is the one ending in "
                          ".apps.googleusercontent.com (not the secret), that "
                          "the two belong together, and that the client has "
                          "not been deleted from the Cloud project.",
        "invalid_grant": "The authorisation code has expired or was already "
                         "used. Connect again.",
        "unauthorized_client": "This OAuth client is not allowed to request "
                               "that scope.",
    }
    hint = hints.get(code, "")
    text = desc or code or detail[:160]
    return f"{fallback}: {code} - {text}" + (f" {hint}" if hint else "")


def exchange_code(code, client_id, client_secret, redirect_uri):
    """Swap an authorisation code for tokens. Returns a dict."""
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise CalendarError(
            _google_error(detail, "Google rejected the authorisation code")
        ) from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Could not reach Google: {exc.reason}") from exc
    if "refresh_token" not in payload:
        raise CalendarError(
            "Google did not return a refresh token. Revoke the app's access in "
            "your Google account, then connect again."
        )
    return payload


def refresh_access_token(client_id, client_secret, refresh_token):
    """Get a fresh short-lived access token. Returns the token string."""
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise CalendarError(
            "The Google authorisation has expired or was revoked. "
            f"Reconnect the account. ({detail})"
        ) from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Could not reach Google: {exc.reason}") from exc
    token = payload.get("access_token")
    if not token:
        raise CalendarError("Google did not return an access token.")
    return token


def _api_get(path, token, params=None):
    url = f"{CAL_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": BROWSER_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise CalendarError("Google rejected the access token.") from exc
        raise CalendarError(f"Google Calendar API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Could not reach Google: {exc.reason}") from exc


def list_calendars(client_id, client_secret, refresh_token):
    """Every calendar the authorised account can see."""
    token = refresh_access_token(client_id, client_secret, refresh_token)
    data = _api_get("/users/me/calendarList", token)
    out = []
    for item in data.get("items", []):
        out.append({
            "id": item.get("id"),
            "summary": item.get("summary", "(no name)"),
            "primary": bool(item.get("primary")),
            "access": item.get("accessRole"),
            "colour": item.get("backgroundColor"),
        })
    return out


def fetch_events(client_id, client_secret, refresh_token, calendar_ids, days_ahead=3,
                 now=None, tz=None):
    """Events for the next `days_ahead` days across the given calendars.

    The window starts at the BEGINNING OF TODAY, not at "now" - otherwise an
    event that is currently running is dropped and the board goes blank in
    the middle of it.
    """
    now = now or datetime.datetime.now(tz)
    if tz is not None:
        now = now.astimezone(tz)
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = (now + datetime.timedelta(days=days_ahead)).replace(
        second=0, microsecond=0).isoformat()

    events = []
    errors = []
    for cal_id in (calendar_ids or ["primary"]):
        token = refresh_access_token(client_id, client_secret, refresh_token)
        try:
            data = _api_get(f"/calendars/{urllib.parse.quote(cal_id, safe='')}/events",
                            token, {
                                "timeMin": time_min,
                                "timeMax": time_max,
                                "singleEvents": "true",
                                "orderBy": "startTime",
                                "maxResults": "250",
                            })
        except CalendarError as exc:
            errors.append(f"{cal_id}: {exc}")
            continue
        for item in data.get("items", []):
            events.append(_normalise_event(item, cal_id))
    if not events and errors:
        raise CalendarError("; ".join(errors))
    events.sort(key=lambda e: e["sort_key"])
    return events


def _normalise_event(item, cal_id):
    start = item.get("start", {}) or {}
    end = item.get("end", {}) or {}
    all_day = "date" in start
    start_dt = start.get("dateTime") or start.get("date")
    end_dt = end.get("dateTime") or end.get("date")
    return {
        "summary": item.get("summary") or "(no title)",
        "location": item.get("location") or "",
        "description": item.get("description") or "",
        "start": start_dt,
        "end": end_dt,
        "all_day": all_day,
        "calendar": cal_id,
        "colour": item.get("colorId"),
        "organiser": (item.get("organizer") or {}).get("email", ""),
        "attendees": [a.get("email", "") for a in item.get("attendees", [])],
        "sort_key": start_dt or "",
    }


# ----------------------------------------------------------------- iCal ---

def fetch_ical(url, days_ahead=3, now=None, tz=None):
    """Read an iCal feed. Used when no OAuth is configured."""
    if not url:
        raise CalendarError("No iCal URL configured.")
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise CalendarError(f"iCal feed returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Could not reach the iCal feed: {exc.reason}") from exc
    return ical.parse(text, days_ahead=days_ahead, now=now, tz=tz)
