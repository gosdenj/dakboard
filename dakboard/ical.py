"""A small iCalendar (RFC 5545) parser - enough for Google Calendar feeds.

Handles what a family board actually sees:
  * VEVENT with DTSTART/DTEND, in UTC, with a TZID, or as a floating time
  * all-day events (VALUE=DATE)
  * RRULE with FREQ=DAILY|WEEKLY|MONTHLY|YEARLY and INTERVAL/BYDAY
  * EXDATE
  * folded lines (a line continued with a leading space)
  * escaped characters in TEXT values
"""

import datetime
import re

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _unfold(text):
    """RFC 5545 line folding: a CRLF followed by a space or tab is one line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)


def _unescape(value):
    return (value.replace("\\n", "\n")
                 .replace("\\N", "\n")
                 .replace("\\,", ",")
                 .replace("\\;", ";")
                 .replace("\\\\", "\\"))


def _parse_params(raw):
    """Split 'DTSTART;TZID=Europe/London:20261030T090000' into (name, params, value)."""
    parts = raw.split(":")
    if len(parts) < 2:
        return raw.strip().upper(), {}, ""
    head = parts[0]
    value = ":".join(parts[1:])
    pieces = head.split(";")
    name = pieces[0].strip().upper()
    params = {}
    for piece in pieces[1:]:
        if "=" in piece:
            key, val = piece.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
        else:
            params[piece.strip().upper()] = ""
    return name, params, value


def _parse_datetime(value, params):
    """Return (datetime|None, is_date).

    The result is always timezone-aware when possible:
      * a trailing Z means UTC
      * a TZID parameter means that zone
      * a floating time (no Z, no TZID) is left naive and is treated as
        local time by the caller
    """
    if not value:
        return None, False
    value = value.strip()
    is_date = params.get("VALUE", "").upper() == "DATE" or len(value) == 8
    if is_date:
        try:
            return datetime.datetime.strptime(value[:8], "%Y%m%d"), True
        except ValueError:
            return None, True
    try:
        if value.endswith("Z"):
            return (datetime.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
                    .replace(tzinfo=datetime.timezone.utc)), False
        return datetime.datetime.strptime(value, "%Y%m%dT%H%M%S"), False
    except ValueError:
        try:
            return datetime.datetime.strptime(value[:15], "%Y%m%dT%H%M%S"), False
        except ValueError:
            return None, False


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _localise(dt, tzid, tz):
    """Put a parsed datetime into the target timezone.

    * explicit TZID  -> that zone, then converted to the target zone
    * naive + target -> assumed to be in the target zone
    * aware          -> converted to the target zone
    """
    if dt is None:
        return None
    if tzid:
        zone = _zone(tzid)
        if zone is not None:
            return dt.replace(tzinfo=zone).astimezone(tz)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _parse_duration(value):
    match = re.match(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
                      value.strip())
    if not match:
        return None
    sign, weeks, days, hours, minutes, seconds = match.groups()
    delta = datetime.timedelta(
        weeks=int(weeks or 0), days=int(days or 0),
        hours=int(hours or 0), minutes=int(minutes or 0), seconds=int(seconds or 0))
    return -delta if sign == "-" else delta


def _localise_until(value, tzid, tz):
    """Put an RRULE's UNTIL into the same frame as DTSTART.

    UNTIL comes in three shapes and only one of them is safe to compare
    directly:

      * a DATE  - "20251231" - which is what Google emits for an all-day
        recurring event. Parsed, that is a NAIVE datetime.
      * a local time - "20251231T235959" - also naive.
      * UTC - "20251231T235959Z" - aware.

    DTSTART is always made aware before it gets here, so comparing a naive
    UNTIL with it raises "can't compare offset-naive and offset-aware
    datetimes" - which took the entire calendar feed down, not just one
    event. A DATE-valued UNTIL also covers that whole day, inclusively.
    """
    until, is_date = _parse_datetime(value, {})
    if until is None:
        return None
    if until.tzinfo is None:
        # a naive UNTIL is the same wall clock as DTSTART
        until = _localise(until, tzid, tz)
    else:
        until = until.astimezone(tz)
    if is_date:
        until = (until + datetime.timedelta(days=1)
                 - datetime.timedelta(microseconds=1))
    return until


def _expand_rrule(rule, dtstart, horizon_days, tz, tzid=None):
    """Expand an RRULE into concrete start datetimes inside the horizon."""
    if dtstart is None:
        return []
    pieces = {}
    for part in rule.split(";"):
        if "=" in part:
            key, val = part.split("=", 1)
            pieces[key.strip().upper()] = val.strip().upper()

    freq = pieces.get("FREQ")
    interval = int(pieces.get("INTERVAL", "1") or 1)
    until = None
    if pieces.get("UNTIL"):
        until = _localise_until(pieces["UNTIL"], tzid, tz)
    count = int(pieces["COUNT"]) if pieces.get("COUNT") else None
    byday = [d for d in pieces.get("BYDAY", "").split(",") if d]
    bymonthday = [int(d) for d in pieces.get("BYMONTHDAY", "").split(",") if d]

    horizon = dtstart + datetime.timedelta(days=horizon_days)
    results = []
    current = dtstart
    guard = 0

    while guard < 2000:
        guard += 1
        if until and current > until:
            break
        if count is not None and len(results) >= count:
            break
        if current > horizon and len(results) > 0:
            break
        if current >= horizon:
            break

        if freq == "DAILY":
            results.append(current)
            current += datetime.timedelta(days=interval)
        elif freq == "WEEKLY":
            if byday:
                base = current
                week_start = base - datetime.timedelta(days=base.weekday())
                for token in byday:
                    day = _WEEKDAYS.get(token[-2:])
                    if day is None:
                        continue
                    candidate = week_start + datetime.timedelta(days=day)
                    candidate = candidate.replace(
                        hour=dtstart.hour, minute=dtstart.minute,
                        second=dtstart.second, microsecond=dtstart.microsecond)
                    if candidate >= dtstart and candidate <= horizon:
                        results.append(candidate)
            else:
                results.append(current)
            # advance the cursor in BOTH paths - not advancing it here makes
            # the loop repeat the same week until COUNT is reached
            current += datetime.timedelta(weeks=interval)
        elif freq == "MONTHLY":
            results.append(current)
            month = current.month + interval
            year = current.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = min(current.day, _days_in_month(year, month))
            current = current.replace(year=year, month=month, day=day)
        elif freq == "YEARLY":
            results.append(current)
            try:
                current = current.replace(year=current.year + interval)
            except ValueError:      # 29 Feb
                current = current.replace(year=current.year + interval, day=28)
        else:
            break

    results.sort()
    if bymonthday:
        results = [r for r in results if r.day in bymonthday]
    return results[:500]


def _days_in_month(year, month):
    import calendar
    return calendar.monthrange(year, month)[1]


def parse(text, days_ahead=3, now=None, tz=None):
    """Parse an iCal feed into event dicts for the next `days_ahead` days.

    The window starts at the BEGINNING OF TODAY, not at "now" - otherwise an
    event that is currently running is dropped and the board goes blank for
    the middle of it.

    An event that started BEFORE the window is kept when it is still
    running: an overnight event is happening now, and the board has to show
    it. One that finished before the window is history, and is left out.
    """
    tz = tz or datetime.timezone.utc
    now = now or datetime.datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    start_window = now.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = start_window + datetime.timedelta(days=days_ahead)

    text = _unfold(text)
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL):
        try:
            event = _parse_vevent(block, start_window, horizon, tz, now)
        except Exception:
            # One malformed event must never cost you the whole calendar.
            # A date-form UNTIL on a recurring all-day event used to raise
            # a TypeError here, and because the exception escaped the loop
            # it took every OTHER event with it - the board went blank for
            # the whole family over a single recurrence rule.
            continue
        if event:
            events.append(event)

    events.sort(key=lambda e: e["sort_key"])
    return events


def _parse_vevent(block, start_window, horizon, tz, now=None):
    fields = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        name, params, value = _parse_params(line)
        fields.setdefault(name, (params, value))

    if "DTSTART" not in fields:
        return None
    params, value = fields["DTSTART"]
    dtstart, all_day = _parse_datetime(value, params)
    if dtstart is None:
        return None

    tzid = params.get("TZID")
    dtstart = _localise(dtstart, tzid, tz)

    duration = None
    if "DTEND" in fields:
        p2, v2 = fields["DTEND"]
        dtend, _ = _parse_datetime(v2, p2)
        if dtend:
            dtend = _localise(dtend, fields["DTEND"][0].get("TZID"), tz)
        if dtend and dtend > dtstart:
            duration = dtend - dtstart
    elif "DURATION" in fields:
        duration = _parse_duration(fields["DURATION"][1])

    summary = _unescape(fields.get("SUMMARY", ({}, "(no title)"))[1])
    location = _unescape(fields.get("LOCATION", ({}, ""))[1])
    description = _unescape(fields.get("DESCRIPTION", ({}, ""))[1])
    organiser = _unescape(fields.get("ORGANIZER", ({}, ""))[1]).replace("mailto:", "")

    excluded = set()
    for params, raw in re.findall(r"EXDATE([^:]*):([0-9TZ,]+)", block):
        # the EXDATE can carry its own TZID - ignoring it would compare the
        # wrong instant and drop the wrong event
        ex_tzid = _parse_params("EXDATE;" + params if params else "EXDATE:")[1].get("TZID")
        for piece in raw.split(","):
            parsed, _ = _parse_datetime(piece, {})
            if parsed:
                excluded.add(_localise(parsed, ex_tzid, tz))

    rrule = fields.get("RRULE", ({}, ""))[1]
    occurrences = []
    if rrule:
        span_days = max(1, (horizon - dtstart).days + 1)
        for start in _expand_rrule(rrule, dtstart, span_days, tz, tzid):
            occurrences.append(start)
    else:
        occurrences = [dtstart]

    out = None
    for occ in occurrences:
        if occ in excluded:
            continue
        end = occ + duration if duration else occ + datetime.timedelta(hours=1)
        if occ > horizon:
            continue
        # An event that started before the window but is STILL RUNNING is
        # kept: it is happening right now, and the board shows what is
        # happening now. One that finished before the window is history.
        # Without this an event that started last night was never fetched,
        # so the board never showed it at all.
        if occ < start_window and (now is None or end <= now):
            continue
        out = {
            "summary": summary,
            "location": location,
            "description": description,
            "start": occ.isoformat(),
            "end": end.isoformat(),
            "all_day": all_day,
            "calendar": "ical",
            "colour": None,
            "organiser": organiser,
            "attendees": [],
            "sort_key": occ.isoformat(),
        }
        break        # one occurrence per event is enough for a board
    return out
