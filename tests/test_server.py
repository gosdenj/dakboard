"""Server API tests - the routes the settings page and the board depend on."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard import calendar as gcal  # noqa: E402
from dakboard.config import ConfigStore  # noqa: E402
from dakboard.server import serve  # noqa: E402
from dakboard.state import BoardState  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.config = ConfigStore(os.path.join(cls.tmp, "config.json"))
        cls.state = BoardState(cls.config)
        cls.httpd = serve(cls.config, cls.state, host="127.0.0.1", port=8198)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # -- helpers -----------------------------------------------------------
    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    def post(self, path, payload):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url(path), data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())

    def post_expecting_error(self, path, payload):
        """POST and read the body even when the server answers with an error.

        urllib raises on a non-2xx status, and an HTTPError still carries the
        body - which is the whole point: an error response that says WHAT
        went wrong is worth far more than an empty one.
        """
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url(path), data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    # -- tests -------------------------------------------------------------
    def test_health(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_state(self):
        status, data = self.get("/api/state")
        self.assertEqual(status, 200)
        for key in ("events", "weather", "photo", "status", "config", "now"):
            self.assertIn(key, data)

    def test_config_masks_secrets(self):
        self.config.set("calendar.ical_url", "https://super-secret.test/ical")
        self.config.set("calendar.refresh_token", "1//secret")
        self.config.save()
        status, data = self.get("/api/config")
        self.assertEqual(status, 200)
        self.assertIsNone(data["config"]["calendar"]["ical_url"])
        self.assertIsNone(data["config"]["calendar"]["refresh_token"])
        # but the page is told WHICH secrets exist, so it can say "saved"
        self.assertIn("calendar.ical_url", data["secrets_set"])

    def test_config_schema_matches_store(self):
        _status, data = self.get("/api/config")
        paths = {entry["path"] for entry in data["schema"]}
        from dakboard.config import DEFAULTS
        self.assertEqual(paths, set(DEFAULTS))

    def test_settings_save_and_reject(self):
        status, data = self.post("/api/settings", {"values": {
            "photos.rotate_seconds": 12,
            "photos.not_a_setting": 3,
        }})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("photos.rotate_seconds", data["saved"])
        self.assertIn("photos.not_a_setting", data["rejected"])
        self.assertIn("photos.not_a_setting", data["message"])

    def test_settings_rejects_non_dotted_paths(self):
        status, data = self.post("/api/settings", {"values": {"flatkey": "x"}})
        self.assertEqual(status, 200)
        self.assertIn("flatkey", data["rejected"])

    def test_saving_settings_pushes_the_new_state_to_the_board(self):
        """A changed setting has to reach the board straight away.

        The board renders the settings it was last given, so a change that
        was saved but not published kept the old transition on the wall
        until something else happened to publish.
        """
        q = self.state.subscribe()
        try:
            status, data = self.post("/api/settings", {
                "values": {"photos.transition_ms": 2500}})
            self.assertEqual(status, 200)
            item = q.get(timeout=5)
            self.assertIsNotNone(item, "the board was never told about the change")
            reason, payload = item
            # "state" is the event the board listens for - any other name
            # is an event the browser ignores
            self.assertEqual(reason, "state")
            self.assertEqual(payload["config"]["transition_ms"], 2500)
        finally:
            self.state.unsubscribe(q)

    def test_oauth_start_needs_a_client_id(self):
        self.config.set("calendar.client_id", "")
        self.config.set("calendar.redirect_uri", "")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("client id", data["error"])

    def test_oauth_start_builds_a_consent_url(self):
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "https://board.example.test/oauth/callback")
        _status, data = self.get("/api/oauth/start")
        self.assertTrue(data["ok"])
        url = data["url"]
        # access_type=offline is what makes Google return a refresh token
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("calendar.readonly", url)

    def test_oauth_start_has_both_paths(self):
        """The settings page and the OAuth redirect must both resolve."""
        for path in ("/api/oauth/start", "/oauth/start"):
            status, _data = self.get(path)
            self.assertEqual(status, 200, path)

    def test_oauth_callback_without_a_code(self):
        status, data = self.get("/oauth/callback")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])

    def test_calendars_needs_a_connection(self):
        """Listing calendars needs a refresh token, so it must say so."""
        self.config.set("calendar.refresh_token", "")
        status, data = self.get("/api/calendars")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("Connect", data["error"])

    def test_calendars_lists_the_account_calendars(self):
        """With a token, the endpoint returns the calendar list."""
        seen = {}

        def fake_list(client_id, client_secret, refresh_token):
            seen["token"] = refresh_token
            return [
                {"id": "primary", "summary": "Maya", "primary": True},
                {"id": "family@group.calendar.google.com", "summary": "Family"},
            ]

        original = gcal.list_calendars
        gcal.list_calendars = fake_list
        try:
            self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
            self.config.set("calendar.client_secret", "secret")
            self.config.set("calendar.refresh_token", "refresh-token")
            status, data = self.get("/api/calendars")
        finally:
            gcal.list_calendars = original
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(seen["token"], "refresh-token")
        self.assertEqual(len(data["calendars"]), 2)
        self.assertTrue(data["calendars"][0]["primary"])

    def test_calendars_reports_a_failure(self):
        def boom(client_id, client_secret, refresh_token):
            raise gcal.CalendarError("Google said no.")

        original = gcal.list_calendars
        gcal.list_calendars = boom
        try:
            self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
            self.config.set("calendar.client_secret", "secret")
            self.config.set("calendar.refresh_token", "refresh-token")
            status, data = self.get("/api/calendars")
        finally:
            gcal.list_calendars = original
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("Google said no.", data["error"])

    # -- the failure modes that used to be invisible ------------------------
    def test_a_broken_config_write_does_not_return_an_empty_response(self):
        """A failed save used to kill the request thread.

        The browser saw an empty response and reported "Failed to fetch" -
        no message, nothing to act on, and the board carried on serving the
        config it loaded at startup so everything looked fine except saving.
        """
        original_save = self.config.save
        self.config.save = self._boom_os
        try:
            status, body = self.post_expecting_error(
                "/api/test/calendar", {"values": {}})
        finally:
            self.config.save = original_save
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("Could not write the settings file", body["error"])
        self.assertIn("No space left on device", body["error"])

    def test_an_unexpected_error_does_not_return_an_empty_response(self):
        """Any exception in a handler must still produce a JSON body."""
        original = self.config.update
        self.config.update = self._boom
        try:
            status, body = self.post_expecting_error(
                "/api/test/calendar", {"values": {}})
        finally:
            self.config.update = original
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("RuntimeError", body["error"])

    @staticmethod
    def _boom(*a, **kw):
        raise RuntimeError("something went wrong")

    @staticmethod
    def _boom_os(*a, **kw):
        # what a full disk or a permissions problem actually looks like
        raise OSError(28, "No space left on device")

    # -- the redirect URI, which Google is fussy about -----------------------
    def test_oauth_start_rejects_a_raw_ip_redirect_uri(self):
        """Google rejects raw IPs with 'Error 400: invalid_request'.

        The board used to build the URL anyway and let the user hit Google's
        error page, which is not a page you can do anything with.
        """
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "http://192.168.1.6:8099/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertNotIn("url", data)
        self.assertIn("invalid_request", data["error"])
        self.assertIn("localhost", data["error"])

    def test_oauth_start_rejects_plain_http_on_a_hostname(self):
        """Google requires HTTPS for anything that is not loopback."""
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "http://board.local:8099/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("HTTPS", data["error"])

    def test_oauth_start_accepts_loopback(self):
        """A Desktop app client uses loopback, with any port and path."""
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "http://localhost:8099/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("accounts.google.com", data["url"])

    def test_oauth_start_rejects_a_client_id_that_is_not_one(self):
        """Google says "invalid_client" for anything it does not know.

        That is a bare error on a page with nothing to click, so the board
        checks the shape of the client ID first. A value that does not end
        in .apps.googleusercontent.com is nearly always the client SECRET
        pasted into the wrong field, or the project ID.
        """
        self.config.set("calendar.client_id", "GOCSPX-not-a-client-id")
        self.config.set("calendar.redirect_uri", "http://localhost:8099/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("apps.googleusercontent.com", data["error"])

    def test_oauth_start_accepts_a_real_client_id(self):
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "http://localhost:8099/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_oauth_start_accepts_https_on_a_real_hostname(self):
        """A Web application client uses HTTPS on a domain."""
        self.config.set("calendar.client_id", "123-abc.apps.googleusercontent.com")
        self.config.set("calendar.redirect_uri", "https://board.longie.net/oauth/callback")
        status, data = self.get("/api/oauth/start")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_static_files_are_served(self):
        for path in ("/", "/settings", "/static/base.css"):
            with urllib.request.urlopen(self.url(path), timeout=10) as resp:
                body = resp.read()
                self.assertEqual(resp.status, 200, path)
                self.assertGreater(len(body), 100, path)

    def test_path_traversal_is_blocked(self):
        try:
            urllib.request.urlopen(self.url("/static/../../etc/passwd"), timeout=10)
            self.fail("path traversal was not blocked")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (403, 404))

    def test_unknown_route_is_404(self):
        try:
            urllib.request.urlopen(self.url("/api/nope"), timeout=10)
            self.fail("unknown route did not 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_photo_control(self):
        status, data = self.post("/api/photo", {"action": "next"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_refresh_endpoint(self):
        status, data = self.post("/api/refresh", {"what": "weather"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])


class StateTests(unittest.TestCase):
    def test_photo_index_stays_in_range(self):
        tmp = tempfile.mkdtemp()
        config = ConfigStore(os.path.join(tmp, "config.json"))
        state = BoardState(config)
        state._album = {"title": "x", "items": [
            {"id": "1", "url": "u1", "w": 1, "h": 1},
            {"id": "2", "url": "u2", "w": 1, "h": 1},
        ]}
        state.set_photo_index(99)
        self.assertEqual(state._photo_index, 1)
        state.set_photo_index(-5)
        self.assertEqual(state._photo_index, 0)

    def test_snapshot_is_json_serialisable(self):
        tmp = tempfile.mkdtemp()
        config = ConfigStore(os.path.join(tmp, "config.json"))
        state = BoardState(config)
        json.dumps(state.snapshot())

    def test_failed_photo_poll_keeps_the_last_album(self):
        tmp = tempfile.mkdtemp()
        config = ConfigStore(os.path.join(tmp, "config.json"))
        state = BoardState(config)
        state._album = {"title": "kept", "items": [
            {"id": "1", "url": "u1", "w": 1, "h": 1}]}
        state._album_cached = state._album
        config.set("photos.album_url", "https://photos.app.goo.gl/does-not-exist")
        ok = state.poll_photos()
        self.assertFalse(ok)
        # the album survives a failed refresh - the board must not go blank
        self.assertEqual(len(state._album["items"]), 1)
        self.assertIn("last known", state.snapshot()["status"]["photos"]["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
