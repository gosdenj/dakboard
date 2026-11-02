"""Shared board state.

The SERVER owns everything that matters:
  * the photo rotation index, so every display in the house shows the same
    photo at the same moment
  * the cached calendar, weather and album list
  * the "now" clock value

The browser renders what it is told. It holds no secrets, fetches nothing
from Google, and keeps no state worth losing.
"""

import datetime
import json
import os
import threading
import time

from . import calendar as gcal
from . import photos as gphotos
from . import weather as gweather


class BoardState:
    def __init__(self, config, tz=None):
        self.config = config
        self.tz = tz or datetime.timezone.utc
        self._lock = threading.RLock()
        self._events = []
        self._weather = None
        self._album = {"title": "", "items": [], "pages": 0}
        self._album_cached = None
        self._photo_index = 0
        self._status = {
            "calendar": {"ok": None, "error": "", "at": None},
            "weather": {"ok": None, "error": "", "at": None},
            "photos": {"ok": None, "error": "", "at": None},
        }
        self._listeners = []
        self._started = time.time()

    # -- subscribers -------------------------------------------------------
    def subscribe(self):
        q = []
        q = _Queue()
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _publish(self, reason=""):
        with self._lock:
            payload = self.snapshot()
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait((reason, payload))
            except Exception:
                pass

    def publish_settings(self):
        """Push the current state after the settings changed.

        The board listens for the "state" event, so the reason has to be
        "state" - any other name is an event the browser does not handle,
        and the board would carry on rendering the old transition settings
        until something else happened to publish.
        """
        self._publish("state")

    # -- data --------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            album = self._album or {}
            items = list(album.get("items") or [])
            count = len(items)
            index = self._photo_index if count else 0
            current = items[index] if count else None
            return {
                "now": datetime.datetime.now(self.tz).isoformat(),
                "uptime": round(time.time() - self._started),
                "events": list(self._events),
                "weather": self._weather,
                "photo": {
                    "title": album.get("title", ""),
                    "index": index,
                    "count": count,
                    "total": album.get("total", count),
                    "url": gphotos.image_url(current) if current else None,
                    "item": current,
                },
                "status": {k: dict(v) for k, v in self._status.items()},
                "config": {
                    "layout": self.config.get("display.layout", "A"),
                    "theme": self.config.get("display.theme", "dark"),
                    "clock_24h": self.config.get("display.clock_24h", True),
                    "hide_cursor": self.config.get("display.hide_cursor", True),
                    "units": self.config.get("weather.units", "celsius"),
                    "show_week": self.config.get("weather.show_week", True),
                    "transition": self.config.get("photos.transition", "slide"),
                    "transition_ms": self.config.get("photos.transition_ms", 1000),
                    "rotate_seconds": self.config.get("photos.rotate_seconds", 6),
                    "shuffle": self.config.get("photos.shuffle", True),
                    "days_ahead": self.config.get("calendar.days_ahead", 3),
                },
            }

    # -- polling -----------------------------------------------------------
    def poll_calendar(self):
        mode = self.config.get("calendar.mode", "ical")
        days = int(self.config.get("calendar.days_ahead", 3) or 3)
        try:
            if mode == "oauth":
                client_id = self.config.get("calendar.client_id")
                client_secret = self.config.get("calendar.client_secret")
                refresh_token = self.config.get("calendar.refresh_token")
                if not (client_id and client_secret and refresh_token):
                    raise gcal.CalendarError(
                        "Calendar is set to OAuth but the client id, secret or "
                        "refresh token is missing.")
                cal_ids = self.config.get("calendar.calendar_ids") or ["primary"]
                events = gcal.fetch_events(client_id, client_secret, refresh_token,
                                           cal_ids, days_ahead=days, tz=self.tz)
            else:
                url = self.config.get("calendar.ical_url")
                if not url:
                    raise gcal.CalendarError("No calendar is configured yet.")
                events = gcal.fetch_ical(url, days_ahead=days, tz=self.tz)
            with self._lock:
                self._events = events
                self._status["calendar"] = {
                    "ok": True, "error": "",
                    "at": datetime.datetime.now(self.tz).isoformat(),
                    "count": len(events),
                }
            self._publish("calendar")
            return True
        except Exception as exc:
            with self._lock:
                self._status["calendar"] = {
                    "ok": False, "error": str(exc),
                    "at": datetime.datetime.now(self.tz).isoformat(),
                }
            self._publish("calendar")
            return False

    def poll_weather(self):
        try:
            lat = self.config.get("weather.latitude")
            lon = self.config.get("weather.longitude")
            place = self.config.get("weather.place")
            if not lat or not lon:
                if not place:
                    raise gweather.WeatherError("No place set for the weather.")
                lat, lon, resolved = gweather.geocode(place)
                # remember the resolved coordinates so we only geocode once
                self.config.set("weather.latitude", lat)
                self.config.set("weather.longitude", lon)
                self.config.set("weather.place", resolved)
                self.config.save()
            data = gweather.fetch(lat, lon, tz=str(self.tz), days=7)
            with self._lock:
                self._weather = data
                self._status["weather"] = {
                    "ok": True, "error": "",
                    "at": datetime.datetime.now(self.tz).isoformat(),
                }
            self._publish("weather")
            return True
        except Exception as exc:
            with self._lock:
                self._status["weather"] = {
                    "ok": False, "error": str(exc),
                    "at": datetime.datetime.now(self.tz).isoformat(),
                }
            self._publish("weather")
            return False

    def poll_photos(self):
        url = self.config.get("photos.album_url")
        if not url:
            with self._lock:
                self._status["photos"] = {
                    "ok": None, "error": "No album configured.",
                    "at": datetime.datetime.now(self.tz).isoformat(),
                }
            return False
        try:
            album = gphotos.fetch_album(url)
            with self._lock:
                self._album = album
                self._album_cached = album
                self._photo_index = self._photo_index % max(1, len(album["items"]))
                self._status["photos"] = {
                    "ok": True, "error": "",
                    "at": datetime.datetime.now(self.tz).isoformat(),
                    "count": len(album["items"]),
                }
            self._publish("photos")
            return True
        except Exception as exc:
            with self._lock:
                # keep the last good album so the board does not go blank
                if self._album_cached:
                    self._album = self._album_cached
                    error = f"{exc} (serving the last known album)"
                else:
                    self._album = {"title": "", "items": [], "pages": 0}
                    error = str(exc)
                self._status["photos"] = {
                    "ok": False, "error": error,
                    "at": datetime.datetime.now(self.tz).isoformat(),
                }
            self._publish("photos")
            return False

    # -- photo rotation ----------------------------------------------------
    def advance_photo(self):
        with self._lock:
            count = len(self._album.get("items") or [])
            if count < 2:
                return False
            if self.config.get("photos.shuffle", True):
                import random
                nxt = random.randrange(count)
                while nxt == self._photo_index and count > 2:
                    nxt = random.randrange(count)
            else:
                nxt = (self._photo_index + 1) % count
            self._photo_index = nxt
        self._publish("photo")
        return True

    def set_photo_index(self, index):
        with self._lock:
            count = len(self._album.get("items") or [])
            if not count:
                return False
            self._photo_index = max(0, min(count - 1, int(index)))
        self._publish("photo")
        return True


class _Queue:
    """A tiny unbounded queue - avoids importing queue for one use."""

    def __init__(self):
        self._items = []
        self._event = threading.Event()

    def put_nowait(self, item):
        self._items.append(item)
        self._event.set()

    def get(self, timeout=None):
        while not self._items:
            if not self._event.wait(timeout if timeout is not None else 1.0):
                return None
            self._event.clear()
            if not self._items:
                self._event.clear()
        return self._items.pop(0)
