"""Config store tests.

The dotted-path contract is the thing everything else sits on, so it gets
the most attention here.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard.config import ConfigStore, DEFAULTS, EDITABLE, SECRET_PATHS  # noqa: E402


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "config.json")
        self.store = ConfigStore(self.path)

    def test_every_default_has_a_path(self):
        for path in DEFAULTS:
            self.assertIn(path, EDITABLE)
            self.assertIsNotNone(self.store.get(path))

    def test_get_and_set_dotted(self):
        self.assertTrue(self.store.set("calendar.mode", "oauth")[0])
        self.assertEqual(self.store.get("calendar.mode"), "oauth")
        self.assertEqual(self.store.get("calendar.days_ahead"), 3)

    def test_set_rejects_unknown_path(self):
        ok, reason = self.store.set("calendar.nope", "x")
        self.assertFalse(ok)
        self.assertIn("unknown setting", reason)

    def test_save_reports_rejected_paths(self):
        self.store.set("photos.album_url", "https://example.test/x")
        saved, rejected = self.store.save()
        self.assertIn("photos.album_url", saved)
        self.assertEqual(rejected, [])

    def test_save_keeps_groups(self):
        """REGRESSION: pruning must not delete group containers.

        "calendar" is not itself in EDITABLE - "calendar.mode" is - so a
        naive prune drops the whole group and silently wipes the config.
        """
        self.store.set("calendar.mode", "oauth")
        self.store.set("photos.album_url", "https://example.test/x")
        self.store.set("weather.place", "Hertford")
        self.store.set("display.layout", "B")
        self.store.save()

        with open(self.path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        for group in ("calendar", "photos", "weather", "display"):
            self.assertIn(group, saved, f"group '{group}' was dropped on save")
        self.assertEqual(saved["calendar"]["mode"], "oauth")
        self.assertEqual(saved["photos"]["album_url"], "https://example.test/x")
        self.assertEqual(saved["weather"]["place"], "Hertford")
        self.assertEqual(saved["display"]["layout"], "B")

    def test_reload_keeps_values(self):
        self.store.set("calendar.days_ahead", 7)
        self.store.save()
        again = ConfigStore(self.path)
        self.assertEqual(again.get("calendar.days_ahead"), 7)

    def test_masks_secrets(self):
        self.store.set("calendar.ical_url", "https://secret.test/ical")
        self.store.set("calendar.refresh_token", "1//secret")
        masked = self.store.masked()
        self.assertIsNone(masked["calendar"]["ical_url"])
        self.assertIsNone(masked["calendar"]["refresh_token"])
        # the real values are still there
        self.assertEqual(self.store.get("calendar.ical_url"), "https://secret.test/ical")

    def test_secret_paths_are_actually_declared(self):
        for path in SECRET_PATHS:
            self.assertIn(path, EDITABLE)

    def test_coerces_types(self):
        self.store.set("calendar.days_ahead", "9")
        self.assertEqual(self.store.get("calendar.days_ahead"), 9)
        self.store.set("photos.shuffle", "false")
        self.assertIs(self.store.get("photos.shuffle"), False)
        self.store.set("calendar.calendar_ids", "a, b ,c")
        self.assertEqual(self.store.get("calendar.calendar_ids"), ["a", "b", "c"])

    def test_choice_falls_back_to_default(self):
        self.store.set("display.layout", "Z")
        self.assertEqual(self.store.get("display.layout"), "A")
        self.store.set("display.layout", "C")
        self.assertEqual(self.store.get("display.layout"), "C")

    def test_update_reports_rejected(self):
        saved, rejected = self.store.update({
            "photos.rotate_seconds": 12,
            "photos.bogus": 1,
            "not.a.setting": 2,
        })
        self.assertIn("photos.rotate_seconds", saved)
        self.assertEqual(sorted(rejected), ["not.a.setting", "photos.bogus"])

    def test_is_set(self):
        self.assertFalse(self.store.is_set("calendar.ical_url"))
        self.store.set("calendar.ical_url", "https://x.test")
        self.assertTrue(self.store.is_set("calendar.ical_url"))

    def test_describe_covers_every_setting(self):
        described = self.store.describe()
        self.assertEqual(len(described), len(DEFAULTS))
        for entry in described:
            self.assertIn(entry["path"], EDITABLE)
            self.assertEqual(entry["secret"], entry["path"] in SECRET_PATHS)

    def test_file_permissions_are_private(self):
        self.store.set("calendar.refresh_token", "1//secret")
        self.store.save()
        mode = os.stat(self.path).st_mode & 0o777
        self.assertEqual(mode, 0o600, "the config holds secrets - it must not be world readable")

    def test_credentials_are_trimmed(self):
        """A paste leaves a trailing newline behind.

        A client id with a newline in it makes Google answer
        "invalid_client", and there is nothing on that page to suggest
        the paste was the problem.
        """
        self.store.update({
            "calendar.client_id": "  123-abc.apps.googleusercontent.com\n",
            "calendar.client_secret": "GOCSPX-abc\n",
            "calendar.ical_url": "https://example.test/x.ics  \n",
        })
        self.assertEqual(self.store.get("calendar.client_id"), "123-abc.apps.googleusercontent.com")
        self.assertEqual(self.store.get("calendar.client_secret"), "GOCSPX-abc")
        self.assertEqual(self.store.get("calendar.ical_url"), "https://example.test/x.ics")

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        store = ConfigStore(self.path)
        self.assertEqual(store.get("calendar.mode"), "ical")


if __name__ == "__main__":
    unittest.main(verbosity=2)
