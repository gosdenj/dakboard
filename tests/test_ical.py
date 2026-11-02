"""iCal parser tests."""

import datetime
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard import ical  # noqa: E402

TZ = ZoneInfo("Europe/London")
NOW = datetime.datetime(2026, 10, 30, 9, 0, tzinfo=TZ)


def wrap(events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            + events + "END:VCALENDAR\r\n")


class IcalTests(unittest.TestCase):
    def parse(self, events, days=7):
        return ical.parse(wrap(events), days_ahead=days, now=NOW, tz=TZ)

    def test_simple_event(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:a@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DTEND;TZID=Europe/London:20261030T130000\r\n"
            "SUMMARY:Lunch\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Lunch")
        self.assertFalse(events[0]["all_day"])
        self.assertEqual(events[0]["start"][:16], "2026-10-30T12:00")

    def test_all_day_event(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:b@test\r\n"
            "DTSTART;VALUE=DATE:20261030\r\nDTEND;VALUE=DATE:20261031\r\n"
            "SUMMARY:Bank Holiday\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["all_day"])
        self.assertEqual(events[0]["summary"], "Bank Holiday")

    def test_utc_event(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:c@test\r\n"
            "DTSTART:20261030T180000Z\r\nDTEND:20261030T190000Z\r\n"
            "SUMMARY:UTC thing\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"][:16], "2026-10-30T18:00")

    def test_floating_time_is_local(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:d@test\r\n"
            "DTSTART:20261030T140000\r\nDTEND:20261030T150000\r\n"
            "SUMMARY:Floating\r\nEND:VEVENT\r\n")
        self.assertEqual(events[0]["start"][:16], "2026-10-30T14:00")

    def test_location_and_organiser(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:e@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DTEND;TZID=Europe/London:20261030T130000\r\n"
            "SUMMARY:Swimming\r\nLOCATION:Leisure Centre\r\n"
            "ORGANIZER;CN=Nan:mailto:nan@example.com\r\nEND:VEVENT\r\n")
        self.assertEqual(events[0]["location"], "Leisure Centre")
        self.assertEqual(events[0]["organiser"], "nan@example.com")

    def test_folded_line_is_unfolded(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:f@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DTEND;TZID=Europe/London:20261030T130000\r\n"
            "SUMMARY:A long summary that has\r\n  been folded\r\nEND:VEVENT\r\n")
        self.assertEqual(events[0]["summary"], "A long summary that has been folded")

    def test_escaped_characters(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:g@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DTEND;TZID=Europe/London:20261030T130000\r\n"
            "SUMMARY:Mum\\, Nan and Sam\\; Bob\\nline two\r\nEND:VEVENT\r\n")
        self.assertEqual(events[0]["summary"], "Mum, Nan and Sam; Bob\nline two")

    def test_weekly_recurrence(self):
        """A weekly event on Mondays, starting 26 Oct (which is in the past).

        The board must show the NEXT occurrence - 2 Nov - not the one that
        already happened, and not the same date four times.
        """
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:h@test\r\n"
            "DTSTART;TZID=Europe/London:20261026T090000\r\n"
            "DTEND;TZID=Europe/London:20261026T093000\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4\r\n"
            "SUMMARY:Swimming\r\nEND:VEVENT\r\n", days=30)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"][:10], "2026-11-02")
        self.assertEqual(events[0]["start"][11:16], "09:00")

    def test_weekly_recurrence_expands_to_distinct_dates(self):
        """The cursor must advance - a repeated date means a broken expansion."""
        from dakboard import ical as mod
        import datetime as dt
        tz = TZ
        start = dt.datetime(2026, 10, 26, 9, 0, tzinfo=tz)
        expanded = mod._expand_rrule("FREQ=WEEKLY;BYDAY=MO;COUNT=4", start, 40, tz)
        self.assertEqual(len(expanded), 4)
        self.assertEqual(len(set(expanded)), 4, "the same date came back more than once")
        self.assertEqual([d.date().isoformat() for d in expanded],
                         ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"])

    def test_duration_instead_of_dtend(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:i@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DURATION:PT1H30M\r\nSUMMARY:Long one\r\nEND:VEVENT\r\n")
        self.assertEqual(events[0]["end"][11:16], "13:30")

    def test_event_in_progress_is_not_dropped(self):
        """The window starts at the beginning of today, not now.

        An event that is running right now must still show on the board.
        """
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:j@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T083000\r\n"
            "DTEND;TZID=Europe/London:20261030T093000\r\n"
            "SUMMARY:Already running\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Already running")

    def test_event_before_today_is_excluded(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:k@test\r\n"
            "DTSTART;TZID=Europe/London:20261020T120000\r\n"
            "DTEND;TZID=Europe/London:20261020T130000\r\n"
            "SUMMARY:Last week\r\nEND:VEVENT\r\n")
        self.assertEqual(events, [])

    def test_event_beyond_horizon_is_excluded(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:l@test\r\n"
            "DTSTART;TZID=Europe/London:20261130T120000\r\n"
            "DTEND;TZID=Europe/London:20261130T130000\r\n"
            "SUMMARY:Next month\r\nEND:VEVENT\r\n", days=7)
        self.assertEqual(events, [])

    def test_events_are_sorted(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:m@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T180000\r\n"
            "DTEND;TZID=Europe/London:20261030T190000\r\nSUMMARY:Late\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:n@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T090000\r\n"
            "DTEND;TZID=Europe/London:20261030T100000\r\nSUMMARY:Early\r\nEND:VEVENT\r\n")
        self.assertEqual([e["summary"] for e in events], ["Early", "Late"])

    # -- UNTIL, the thing that broke the whole feed -------------------------
    def test_recurrence_with_a_date_until(self):
        """Google emits a DATE-valued UNTIL for all-day recurrences.

        A DATE parses to a NAIVE datetime, while DTSTART is always made
        aware, so comparing the two raised "can't compare offset-naive and
        offset-aware datetimes" - and that killed the ENTIRE feed, not just
        the one recurring event. Every other calendar went blank with it.
        """
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:u1@test\r\n"
            "DTSTART;VALUE=DATE:20261030\r\nDTEND;VALUE=DATE:20261031\r\n"
            "RRULE:FREQ=WEEKLY;UNTIL=20261231\r\n"
            "SUMMARY:All day recurring\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "All day recurring")
        self.assertTrue(events[0]["all_day"])

    def test_recurrence_with_a_utc_until(self):
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:u2@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T090000\r\n"
            "DTEND;TZID=Europe/London:20261030T100000\r\n"
            "RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z\r\n"
            "SUMMARY:Swimming\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"][:16], "2026-10-30T09:00")

    def test_recurrence_with_a_naive_until(self):
        """A local-time UNTIL is also naive, and just as common."""
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:u3@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T090000\r\n"
            "DTEND;TZID=Europe/London:20261030T100000\r\n"
            "RRULE:FREQ=WEEKLY;UNTIL=20261231T235959\r\n"
            "SUMMARY:Swimming\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 1)

    def test_a_date_until_covers_the_whole_of_that_day(self):
        """A DATE-valued UNTIL is inclusive of that day, not midnight of it."""
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:u4@test\r\n"
            "DTSTART;TZID=Europe/London:20261231T090000\r\n"
            "DTEND;TZID=Europe/London:20261231T100000\r\n"
            "RRULE:FREQ=DAILY;UNTIL=20261231\r\n"
            "SUMMARY:Last one\r\nEND:VEVENT\r\n", days=400)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"][:10], "2026-12-31")

    def test_exdate_uses_its_own_timezone(self):
        """An EXDATE can carry a TZID of its own.

        Ignoring it compares the wrong instant and drops the wrong event.
        """
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:x1@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T090000\r\n"
            "DTEND;TZID=Europe/London:20261030T100000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3\r\n"
            "EXDATE;TZID=Europe/London:20261031T090000\r\n"
            "SUMMARY:Swimming\r\nEND:VEVENT\r\n")
        # the 31st is excluded, the 30th and the 1st survive
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"][:10], "2026-10-30")

    def test_a_broken_event_does_not_kill_the_feed(self):
        """One unparseable event must not cost you the other twenty.

        This is the shape of the bug that bit: a single recurring event with
        a date-form UNTIL took down every event in the calendar.
        """
        events = self.parse(
            "BEGIN:VEVENT\r\nUID:bad@test\r\n"
            "DTSTART;VALUE=DATE:20261030\r\n"
            "RRULE:FREQ=WEEKLY;UNTIL=20261231\r\n"
            "SUMMARY:Recurring all day\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:good@test\r\n"
            "DTSTART;TZID=Europe/London:20261030T120000\r\n"
            "DTEND;TZID=Europe/London:20261030T130000\r\n"
            "SUMMARY:Lunch\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 2)
        self.assertIn("Lunch", [e["summary"] for e in events])

    def test_an_unparseable_event_does_not_kill_the_feed(self):
        """A block the parser cannot handle is skipped, not fatal.

        The exception has to be caught PER EVENT. Catching it around the
        whole feed would still lose every other event, which is exactly
        what happened when one recurring all-day event had a date-form
        UNTIL: one bad recurrence rule blanked the entire calendar.
        """
        from unittest import mock
        real = ical._parse_vevent
        calls = {"n": 0}

        def flaky(block, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TypeError("can't compare offset-naive and offset-aware datetimes")
            return real(block, *a, **kw)

        with mock.patch.object(ical, "_parse_vevent", flaky):
            events = self.parse(
                "BEGIN:VEVENT\r\nUID:broken@test\r\n"
                "DTSTART;VALUE=DATE:20261030\r\n"
                "SUMMARY:Recurring all day\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nUID:good@test\r\n"
                "DTSTART;TZID=Europe/London:20261030T120000\r\n"
                "DTEND;TZID=Europe/London:20261030T130000\r\n"
                "SUMMARY:Lunch\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nUID:good2@test\r\n"
                "DTSTART;TZID=Europe/London:20261030T180000\r\n"
                "DTEND;TZID=Europe/London:20261030T190000\r\n"
                "SUMMARY:Dinner\r\nEND:VEVENT\r\n")
        self.assertEqual(len(events), 2)
        self.assertEqual([e["summary"] for e in events], ["Lunch", "Dinner"])

    def test_empty_feed(self):
        self.assertEqual(ical.parse("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
                                    days_ahead=3, now=NOW, tz=TZ), [])

    def test_garbage_does_not_crash(self):
        self.assertEqual(ical.parse("not a calendar at all", days_ahead=3,
                                    now=NOW, tz=TZ), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
