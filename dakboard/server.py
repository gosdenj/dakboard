"""The board server: static files, JSON APIs and a Server-Sent Events stream.

Python standard library only - no pip dependencies.  The Google APIs are plain
HTTPS + JSON, the ICS format is text and the HTML parsing is regex + json,
so there is nothing here that needs a third-party package.
"""

import json
import mimetypes
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import calendar as gcal
from . import photos as gphotos
from . import weather as gweather

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(HERE), "static")


class BoardError(Exception):
    """An error whose message is meant for the user, shown as-is."""


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "dakboard/1.0"
    protocol_version = "HTTP/1.1"

    # set by the server factory
    state = None
    config = None

    def log_message(self, fmt, *args):
        if os.environ.get("DAKBOARD_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers -----------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, status=200, cache=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        """Every route is wrapped: an exception must never reach the browser
        as an empty response.

        An unhandled exception here kills the handler thread before a
        response is written, and the browser reports that as "Failed to
        fetch" - which says nothing about what went wrong and is impossible
        to diagnose from the other end. Turning it into a JSON error with
        the real message costs one try/except and saves hours.
        """
        try:
            self._route_get()
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc):
        """Report an unexpected failure as JSON, and log the traceback."""
        import traceback
        traceback.print_exc()
        if isinstance(exc, BoardError):
            message = str(exc)
        else:
            message = "%s: %s" % (type(exc).__name__, exc)
        self._send_json({
            "ok": False,
            "error": message,
            "hint": "The board log has the full traceback.",
        }, 500)

    def _persist(self):
        """Save the config, explaining a write failure in plain English.

        This used to be the worst failure mode in the app: a permission
        error or a full disk killed the request thread before a response was
        written, and the browser reported that as "Failed to fetch" - no
        message, no clue, nothing to act on. The board kept serving the
        config it had loaded at startup, so it looked like everything except
        saving was fine.
        """
        try:
            return self.config.save()
        except OSError as exc:
            directory = os.path.dirname(self.config.path) or "."
            raise BoardError(
                "Could not write the settings file %s: %s. "
                "Check that %s exists and is writable by the board's user. "
                "In Docker that is the data volume - a volume created by "
                "Docker as root cannot be written by the container's "
                "unprivileged user, and a full disk fails the same way."
                % (self.config.path, exc.strerror or exc, directory)
            ) from exc

    def _route_get(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            return self._serve_static("index.html")
        if route == "/settings":
            return self._serve_static("settings.html")
        if route.startswith("/static/"):
            return self._serve_static(route[len("/static/"):])
        if route == "/api/health":
            return self._send_json({"ok": True, "uptime": self.state.snapshot()["uptime"]})
        if route == "/api/state":
            return self._send_json(self.state.snapshot())
        if route == "/api/config":
            return self._send_json({
                "config": self.config.masked(),
                "schema": self.config.describe(),
                "secrets_set": self.config.secrets_set(),
            })
        if route == "/api/status":
            return self._send_json(self.state.snapshot()["status"])
        if route == "/api/calendars":
            return self._list_calendars()
        if route == "/api/stream":
            return self._stream()
        if route in ("/oauth/callback", "/api/oauth/callback"):
            return self._oauth_callback(query)
        if route in ("/oauth/start", "/api/oauth/start"):
            return self._oauth_start(query)
        return self._send_json({"error": "not found"}, 404)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        try:
            self._route_post()
        except Exception as exc:
            self._fail(exc)

    def _route_post(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route == "/api/settings":
            return self._save_settings()
        if route == "/api/test/calendar":
            return self._test_calendar()
        if route == "/api/test/photos":
            return self._test_photos()
        if route == "/api/test/weather":
            return self._test_weather()
        if route == "/api/photo":
            return self._set_photo()
        if route == "/api/refresh":
            return self._refresh()
        return self._send_json({"error": "not found"}, 404)

    # -- static ------------------------------------------------------------
    def _serve_static(self, rel):
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            return self._send_json({"error": "forbidden"}, 403)
        path = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(path):
            return self._send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            body = fh.read()
        return self._send_bytes(body, ctype, cache="no-cache")

    # -- SSE ---------------------------------------------------------------
    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.state.subscribe()
        try:
            # send the current state straight away so a client is never blank
            self._sse_send("state", self.state.snapshot())
            while True:
                item = q.get(timeout=15)
                if item is None:
                    self.wfile.write(b": keepalive\n\n")   # keep the socket warm
                    self.wfile.flush()
                    continue
                reason, payload = item
                self._sse_send(reason or "state", payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.unsubscribe(q)

    def _sse_send(self, event, data):
        body = json.dumps(data)
        chunk = f"event: {event}\ndata: {body}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    # -- settings ----------------------------------------------------------
    def _save_settings(self):
        payload = self._read_json()
        values = payload.get("values") or {}
        saved, rejected = self.config.update(values)
        self._persist()
        # apply anything that changes what the pollers should do
        if any(p.startswith("calendar.") for p in saved):
            threading.Thread(target=self.state.poll_calendar, daemon=True).start()
        if any(p.startswith("weather.") for p in saved):
            threading.Thread(target=self.state.poll_weather, daemon=True).start()
        if any(p.startswith("photos.") for p in saved):
            threading.Thread(target=self.state.poll_photos, daemon=True).start()
        message = "Settings saved."
        if rejected:
            message += " Ignored unknown settings: " + ", ".join(rejected)
        return self._send_json({
            "ok": True, "saved": saved, "rejected": rejected, "message": message,
        })

    # -- test buttons (they SAVE what they tested) ------------------------
    def _list_calendars(self):
        """Every calendar the connected account can see.

        This is what the settings page's "List my calendars" button calls.
        It needs a refresh token, so it only works once the account has been
        connected - hence the "connect first" message on failure.
        """
        client_id = self.config.get("calendar.client_id")
        client_secret = self.config.get("calendar.client_secret")
        refresh_token = self.config.get("calendar.refresh_token")
        if not (client_id and client_secret and refresh_token):
            return self._send_json({
                "ok": False,
                "error": "Connect a Google account first, then list the calendars.",
            })
        try:
            calendars = gcal.list_calendars(client_id, client_secret, refresh_token)
        except gcal.CalendarError as exc:
            return self._send_json({"ok": False, "error": str(exc)})
        return self._send_json({"ok": True, "calendars": calendars})

    def _test_calendar(self):
        payload = self._read_json()
        values = payload.get("values") or {}
        saved, rejected = self.config.update(values)
        self._persist()
        ok = self.state.poll_calendar()
        status = self.state.snapshot()["status"]["calendar"]
        return self._send_json({
            "ok": ok,
            "saved": saved,
            "rejected": rejected,
            "error": status.get("error", ""),
            "count": status.get("count", 0),
            "message": ("Calendar works - %d events." % status.get("count", 0)) if ok
                       else status.get("error", "Calendar failed."),
        })

    def _test_photos(self):
        payload = self._read_json()
        values = payload.get("values") or {}
        saved, rejected = self.config.update(values)
        self._persist()
        ok = self.state.poll_photos()
        status = self.state.snapshot()["status"]["photos"]
        return self._send_json({
            "ok": ok,
            "saved": saved,
            "rejected": rejected,
            "error": status.get("error", ""),
            "count": status.get("count", 0),
            "message": ("Album works - %d photos." % status.get("count", 0)) if ok
                       else status.get("error", "Album failed."),
        })

    def _test_weather(self):
        payload = self._read_json()
        values = payload.get("values") or {}
        saved, rejected = self.config.update(values)
        self._persist()
        ok = self.state.poll_weather()
        status = self.state.snapshot()["status"]["weather"]
        return self._send_json({
            "ok": ok,
            "saved": saved,
            "rejected": rejected,
            "error": status.get("error", ""),
            "message": ("Weather works." if ok else status.get("error", "Weather failed.")),
        })

    # -- photo control -----------------------------------------------------
    def _set_photo(self):
        payload = self._read_json()
        action = payload.get("action")
        if action == "next":
            self.state.advance_photo()
        elif action == "index":
            self.state.set_photo_index(payload.get("index", 0))
        return self._send_json({"ok": True})

    def _refresh(self):
        what = (self._read_json() or {}).get("what", "all")
        if what in ("all", "calendar"):
            threading.Thread(target=self.state.poll_calendar, daemon=True).start()
        if what in ("all", "weather"):
            threading.Thread(target=self.state.poll_weather, daemon=True).start()
        if what in ("all", "photos"):
            threading.Thread(target=self.state.poll_photos, daemon=True).start()
        return self._send_json({"ok": True, "message": "Refreshing."})

    # -- OAuth -------------------------------------------------------------
    def _oauth_start(self, query):
        """The Google consent URL.

        These return 200 with ok=false rather than an HTTP error code: the
        settings page reads the body, and "not configured yet" is a normal
        state, not a malformed request.

        The redirect URI is checked against Google's rules FIRST. Google
        rejects a raw IP address with "Error 400: invalid_request" on a
        page the user cannot do anything with, so the board says what is
        wrong and suggests a redirect URI that will work.
        """
        client_id = self.config.get("calendar.client_id")
        redirect_uri = self.config.get("calendar.redirect_uri")
        if not client_id or not redirect_uri:
            return self._send_json({
                "ok": False,
                "error": "Set the client id and redirect URI first.",
            })
        ok, message = gcal.check_client_id(client_id)
        if not ok:
            return self._send_json({"ok": False, "error": message})
        ok, message = gcal.check_redirect_uri(redirect_uri)
        if not ok:
            return self._send_json({"ok": False, "error": message})
        url = gcal.auth_url(client_id, redirect_uri)
        return self._send_json({"ok": True, "url": url})

    def _oauth_callback(self, query):
        code = (query.get("code") or [None])[0]
        error = (query.get("error") or [None])[0]
        if error:
            return self._send_json({"ok": False, "error": f"Google said: {error}"})
        if not code:
            return self._send_json({"ok": False, "error": "No authorisation code."})
        client_id = self.config.get("calendar.client_id")
        client_secret = self.config.get("calendar.client_secret")
        redirect_uri = self.config.get("calendar.redirect_uri")
        if not (client_id and client_secret and redirect_uri):
            return self._send_json({
                "ok": False,
                "error": "Client id, secret or redirect URI is missing.",
            })
        try:
            tokens = gcal.exchange_code(code, client_id, client_secret, redirect_uri)
        except gcal.CalendarError as exc:
            return self._send_json({"ok": False, "error": str(exc)})
        self.config.set("calendar.refresh_token", tokens["refresh_token"])
        self.config.set("calendar.mode", "oauth")
        self._persist()
        threading.Thread(target=self.state.poll_calendar, daemon=True).start()
        return self._send_json({
            "ok": True,
            "message": "Google Calendar connected. The board will use it from now on.",
        })


def serve(config, state, host="0.0.0.0", port=8099):
    handler = type("BoundHandler", (BoardHandler,), {"state": state, "config": config})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
