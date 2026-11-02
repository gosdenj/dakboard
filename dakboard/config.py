"""Dotted-path configuration store.

The contract that must not drift:
  * every setting is addressed by a DOTTED path ("calendar.mode"), never flat
  * EDITABLE is the single source of truth for what may be written
  * SECRET_PATHS are masked when the config is handed to the browser
  * save() refuses anything not in EDITABLE and REPORTS what it refused,
    so a typo on the settings page can never look like it saved
"""

import json
import os
import tempfile
import threading

# --- every setting the board understands -----------------------------------
# type: (default, kind)  kind drives the settings page control
DEFAULTS = {
    # calendar
    "calendar.mode":            ("ical", "choice"),      # ical | oauth
    "calendar.ical_url":        ("", "secret"),
    "calendar.client_id":       ("", "text"),
    "calendar.client_secret":   ("", "secret"),
    "calendar.redirect_uri":    ("", "text"),
    "calendar.refresh_token":   ("", "secret"),
    "calendar.calendar_ids":    ([], "list"),
    "calendar.days_ahead":      (3, "number"),
    # photos
    "photos.album_url":         ("", "text"),
    "photos.transition":        ("slide", "choice"),    # fade | slide | kenburns
    "photos.transition_ms":     (1000, "number"),
    "photos.rotate_seconds":    (6, "number"),
    "photos.shuffle":           (True, "bool"),
    "photos.refresh_minutes":   (30, "number"),
    # weather
    "weather.place":            ("Welwyn Garden City", "text"),
    "weather.latitude":         ("", "text"),
    "weather.longitude":        ("", "text"),
    "weather.units":            ("celsius", "choice"),  # celsius | fahrenheit
    "weather.show_week":        (True, "bool"),
    # display
    "display.layout":           ("A", "choice"),        # A | B | C
    "display.theme":            ("dark", "choice"),     # dark | light
    "display.clock_24h":        (True, "bool"),
    "display.hide_cursor":      (True, "bool"),
}

# paths whose values never travel back to the browser
SECRET_PATHS = {
    "calendar.ical_url",
    "calendar.client_secret",
    "calendar.refresh_token",
}

EDITABLE = set(DEFAULTS)

# Settings the application manages itself - the user never types them, so
# they have no field on the settings page. The OAuth flow writes the
# refresh token; the geocoder writes the coordinates. They must never be
# hand-editable.
MANAGED_PATHS = {
    "calendar.refresh_token",
    "weather.latitude",
    "weather.longitude",
}


def _get_path(data, path):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def _set_path(data, path, value):
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _del_path(data, path):
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            return
        cur = nxt
    cur.pop(parts[-1], None)


def _coerce(path, value, kind):
    """Coerce a value to the type the setting expects.

    Settings arrive as JSON from the browser, so a number could arrive as a
    string. Coercing here keeps the rest of the app free of type checks.
    """
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind == "number":
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULTS[path][0]
    if kind == "list":
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return list(value)
        return DEFAULTS[path][0]
    if kind == "choice":
        allowed = CHOICES.get(path)
        if allowed and value not in allowed:
            return DEFAULTS[path][0]
        return value
    # strip the whitespace a copy-paste leaves behind. A trailing newline
    # in a client id makes Google answer "invalid_client", and there is
    # nothing on the page to suggest the paste was the problem.
    return "" if value is None else str(value).strip()


# valid values for choice settings
CHOICES = {
    "calendar.mode": ("ical", "oauth"),
    "photos.transition": ("fade", "slide", "kenburns"),
    "weather.units": ("celsius", "fahrenheit"),
    "display.layout": ("A", "B", "C"),
    "display.theme": ("dark", "light"),
}


class ConfigStore:
    """Loads, validates and persists the board configuration."""

    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "DAKBOARD_CONFIG",
            os.path.join(os.environ.get("DAKBOARD_DATA", "/data"), "config.json"),
        )
        self._lock = threading.RLock()
        self._data = {}
        self.load()

    # -- lifecycle ---------------------------------------------------------
    def load(self):
        with self._lock:
            self._data = {}
            for p, (default, _kind) in DEFAULTS.items():
                _set_path(self._data, p, default)
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        stored = json.load(fh)
                except (OSError, ValueError):
                    stored = {}
                for p in EDITABLE:
                    try:
                        raw = _get_path(stored, p)
                    except KeyError:
                        continue
                    _set_path(self._data, p, _coerce(p, raw, DEFAULTS[p][1]))

    def save(self):
        """Persist atomically. Returns (saved_paths, rejected_paths)."""
        with self._lock:
            rejected = self._prune()
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, indent=2, sort_keys=True)
                    fh.write("\n")
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return sorted(EDITABLE), sorted(rejected)

    def _prune(self):
        """Remove any key that is not EDITABLE, and record what was removed.

        The FoxESS store dropped unknown keys SILENTLY, which is how a
        settings page using the wrong key names looked like it saved and
        didn't. We still drop them, but we say so.

        A GROUP ("calendar", "photos", ...) is not itself in EDITABLE - its
        members are - so a group is kept when any editable path starts with
        it. Getting this wrong silently wipes the whole config.
        """
        rejected = []

        def walk(node, prefix):
            if not isinstance(node, dict):
                return
            for key in list(node.keys()):
                path = f"{prefix}.{key}" if prefix else key
                value = node[key]
                if path in EDITABLE:
                    if isinstance(value, dict):
                        walk(value, path)
                    continue
                if isinstance(value, dict) and any(
                        p.startswith(path + ".") for p in EDITABLE):
                    walk(value, path)      # a group of editable settings
                    continue
                node.pop(key)              # not editable: drop it
                rejected.append(path)

        walk(self._data, "")
        return rejected

    # -- access ------------------------------------------------------------
    def get(self, path, default=None):
        with self._lock:
            try:
                return _get_path(self._data, path)
            except KeyError:
                return default

    def set(self, path, value):
        """Set one path. Returns (ok, reason)."""
        with self._lock:
            if path not in EDITABLE:
                return False, f"unknown setting: {path}"
            _set_path(self._data, path, _coerce(path, value, DEFAULTS[path][1]))
            return True, ""

    def update(self, values):
        """Apply a dict of dotted paths. Returns (saved, rejected)."""
        saved, rejected = [], []
        with self._lock:
            for path, value in (values or {}).items():
                if path not in EDITABLE:
                    rejected.append(path)
                    continue
                _set_path(self._data, path, _coerce(path, value, DEFAULTS[path][1]))
                saved.append(path)
        return sorted(saved), sorted(rejected)

    def all(self):
        """Full config, nested."""
        with self._lock:
            import copy
            return copy.deepcopy(self._data)

    def masked(self):
        """Config for the browser: nested, with secret values removed.

        Secrets are not sent as empty strings - they are sent as None so the
        page can tell "not set" from "set but hidden". A field that came back
        as "" would look unset and invite the user to retype it.
        """
        with self._lock:
            import copy
            data = copy.deepcopy(self._data)
            for path in SECRET_PATHS:
                try:
                    _set_path(data, path, None)
                except KeyError:
                    pass
            return data

    def is_set(self, path):
        v = self.get(path)
        return v not in (None, "", [], {})

    def secrets_set(self):
        """Which secret paths currently hold a value.

        The browser is told WHICH secrets exist without being told WHAT they
        are, so the settings page can say "saved" instead of leaving the
        field looking empty and inviting the user to retype it.
        """
        return sorted(p for p in SECRET_PATHS if self.is_set(p))

    def describe(self):
        """Metadata the settings page uses to build itself."""
        out = []
        for path, (default, kind) in DEFAULTS.items():
            out.append({
                "path": path,
                "kind": kind,
                "default": default,
                "secret": path in SECRET_PATHS,
                "choices": list(CHOICES.get(path, ())),
            })
        return out
