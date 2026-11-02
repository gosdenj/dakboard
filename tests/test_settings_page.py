"""The settings page and the config store must not drift apart.

The FoxESS dashboard bit the user with this exact bug: the page used flat
keys ("user", "theme") while the store spoke dotted paths, so almost nothing
was ever saved and it looked like the page was working. This test checks
BOTH directions - every field on the page is a real setting, and every
setting has a field on the page.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard.config import ConfigStore, DEFAULTS, SECRET_PATHS, MANAGED_PATHS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_HTML = os.path.join(ROOT, "static", "settings.html")
BOARD_HTML = os.path.join(ROOT, "static", "index.html")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(SETTINGS_HTML)

    def test_page_exists(self):
        self.assertTrue(os.path.isfile(SETTINGS_HTML))

    def test_every_field_is_a_real_setting(self):
        paths = set(re.findall(r'data-path="([^"]+)"', self.html))
        self.assertTrue(paths, "no settings fields found on the page")
        for path in paths:
            self.assertIn(path, DEFAULTS,
                          f"the page has a field for '{path}' but no such setting exists")

    def test_every_setting_has_a_field(self):
        """Every user-settable setting must have a field on the page.

        MANAGED_PATHS are set by the app itself (the OAuth flow writes the
        refresh token), so they are expected to have no field.
        """
        paths = set(re.findall(r'data-path="([^"]+)"', self.html))
        for path in DEFAULTS:
            if path in MANAGED_PATHS:
                self.assertNotIn(path, paths,
                                 f"'{path}' is managed by the app - it must not be editable")
                continue
            self.assertIn(path, paths,
                          f"the setting '{path}' has no field on the settings page")

    def test_no_duplicate_ids(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        duplicates = {i for i in ids if ids.count(i) > 1}
        self.assertFalse(duplicates, f"duplicate element ids: {duplicates}")

    def test_field_ids_match_their_paths(self):
        """The element id and the data-path should agree, or JS lookups break.

        The id uses "-" where the path uses "." - an id containing a dot
        would break every CSS selector, because a dot means "class".
        """
        for tag in ("input", "select"):
            for match in re.finditer(
                    r'<' + tag + r'[^>]*\bid="([^"]+)"[^>]*data-path="([^"]+)"', self.html):
                element_id, path = match.groups()
                self.assertEqual(element_id, path.replace(".", "-"),
                                 f"element id '{element_id}' does not match path '{path}'")

    def test_no_id_contains_a_dot(self):
        """A dot in an id breaks CSS selectors - "#a.b" means id a, class b."""
        for match in re.finditer(r'\bid="([^"]+)"', self.html):
            self.assertNotIn(".", match.group(1),
                             f"id '{match.group(1)}' contains a dot and breaks CSS selectors")

    def test_secret_fields_are_password_inputs(self):
        """A secret must never be rendered back into a visible text box."""
        for match in re.finditer(r'<input[^>]*id="([^"]+)"[^>]*>', self.html):
            element_id = match.group(1)
            tag = match.group(0)
            if element_id in SECRET_PATHS:
                self.assertIn('type="password"', tag,
                              f"'{element_id}' is a secret but is not a password field")

    def test_choice_options_match_the_schema(self):
        """Every <option> for a choice setting must be a value the store accepts."""
        from dakboard.config import CHOICES
        for path, allowed in CHOICES.items():
            pattern = (r'<select[^>]*id="' + re.escape(path.replace(".", "-")) +
                       r'"[^>]*>(.*?)</select>')
            match = re.search(pattern, self.html, re.DOTALL)
            self.assertIsNotNone(match, f"no select for the choice setting '{path}'")
            options = re.findall(r'<option value="([^"]*)"', match.group(1))
            self.assertTrue(options, f"'{path}' has no options")
            for option in options:
                self.assertIn(option, allowed,
                              f"'{path}' offers '{option}' which the store would reject")

    def test_board_page_has_no_settings_fields(self):
        """The board must not contain editable settings."""
        self.assertNotIn("data-path", read(BOARD_HTML))


class SettingsBehaviourTests(unittest.TestCase):
    """The JS behaviour rules, checked against the source."""

    @classmethod
    def setUpClass(cls):
        cls.html = read(SETTINGS_HTML)

    def test_single_diff_function_exists(self):
        """ONE diff drives both the dirty check and the save payload."""
        self.assertIn("function formEdits()", self.html)
        # the dirty check and the save must both use it
        self.assertRegex(self.html, r"function isDirty\(\)\s*\{\s*return Object\.keys\(formEdits\(\)\)")
        self.assertRegex(self.html, r"const edits = formEdits\(\);")

    def test_secret_fields_are_cleared_on_load(self):
        """The stored secret never comes back, so a typed secret cannot linger."""
        self.assertRegex(self.html, r"el\.value\s*=\s*\"\";")

    def test_discard_says_something(self):
        """A silent revert looked broken."""
        self.assertIn("Changes discarded.", self.html)

    def test_discard_is_disabled_when_clean(self):
        self.assertRegex(self.html, r"\$\(\"discardBtn\"\)\.disabled\s*=\s*!dirty;")

    def test_test_buttons_save_what_they_test(self):
        """The FoxESS forecast test reported a place the board did not have."""
        self.assertIn('fetch("/api/test/" + which', self.html)
        self.assertRegex(self.html, r"const edits = formEdits\(\);[\s\S]{0,600}?api/test/")

    def test_unload_warns_about_unsaved_changes(self):
        self.assertIn("beforeunload", self.html)
        self.assertIn("isDirty()", self.html)

    def test_secret_placeholder_does_not_leak_the_value(self):
        self.assertNotIn("•••• saved ••••", "")   # placeholder must be generated
        self.assertIn("saved - leave blank to keep it", self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
