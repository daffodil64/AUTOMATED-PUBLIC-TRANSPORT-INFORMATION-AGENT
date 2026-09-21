"""
Unit tests for the Public Transport Information Agent.

Run from the project folder:
    python -m unittest -v
"""
import unittest

import app

NINE = 9 * 60  # 09:00


class BaseCase(unittest.TestCase):
    """Every test starts with no delays, and the original alerts are restored."""

    def setUp(self):
        self._saved = dict(app.ALERTS)
        app.ALERTS.clear()

    def tearDown(self):
        app.ALERTS.clear()
        app.ALERTS.update(self._saved)


class TimeTests(BaseCase):
    def test_parse_time_formats(self):
        self.assertEqual(app.parse_time("18:30"), 1110)
        self.assertEqual(app.parse_time("6:30 pm"), 1110)
        self.assertEqual(app.parse_time("9"), 540)
        self.assertEqual(app.parse_time("12 am"), 0)

    def test_parse_time_invalid(self):
        self.assertIsNone(app.parse_time("25:99"))
        self.assertIsNone(app.parse_time("abc"))

    def test_now_is_accepted(self):
        self.assertIsNotNone(app.parse_time("now"))
        self.assertIsNotNone(app.parse_time(""))

    def test_fmt_and_to_min(self):
        self.assertEqual(app.fmt(app.to_min("09:06")), "09:06")
        self.assertEqual(app.fmt(1440 + 5), "00:05")


class ScheduleTests(BaseCase):
    def test_m1_departures_from_airport(self):
        m1 = app.ROUTE_BY_ID["M1"]
        deps = [app.fmt(x) for x in app.upcoming(m1, 0, 0, NINE, 3)]
        self.assertEqual(deps, ["09:06", "09:14", "09:22"])

    def test_no_boarding_at_terminus_end(self):
        m1 = app.ROUTE_BY_ID["M1"]
        self.assertEqual(app.upcoming(m1, len(m1.stops) - 1, 0, NINE), [])
        self.assertEqual(app.upcoming(m1, 0, 1, NINE), [])

    def test_no_service_after_last_departure(self):
        m1 = app.ROUTE_BY_ID["M1"]
        self.assertEqual(app.upcoming(m1, 0, 0, 23 * 60 + 59), [])

    def test_delay_shifts_departures(self):
        b10 = app.ROUTE_BY_ID["B10"]
        i = b10.stops.index("Market Square")
        before = app.upcoming(b10, i, 0, NINE, 1)[0]
        app.ALERTS["B10"] = {"delay": 7, "msg": "test"}
        after = app.upcoming(b10, i, 0, NINE, 1)[0]
        self.assertEqual(app.fmt(before), "09:04")
        self.assertEqual(app.fmt(after), "09:11")


class PlannerTests(BaseCase):
    def best(self, a, b, t=NINE):
        js = app.find_journeys(a, b, t)
        return min(js, key=lambda j: (j.arr, j.transfers, j.fare))

    def test_airport_to_riverside(self):
        j = self.best("Airport", "Riverside")
        self.assertEqual([l.route.id for l in j.legs], ["M1", "M2"])
        self.assertEqual((app.fmt(j.dep), app.fmt(j.arr)), ("09:06", "09:27"))
        self.assertEqual(j.fare, 45)
        self.assertEqual(j.transfers, 1)

    def test_transfer_buffer_respected(self):
        j = self.best("Airport", "Riverside")
        self.assertGreaterEqual(j.legs[1].dep - j.legs[0].arr, app.TRANSFER_BUFFER)

    def test_all_pairs_connected_at_nine(self):
        for a in app.STOPS:
            for b in app.STOPS:
                if a != b:
                    self.assertTrue(app.find_journeys(a, b, NINE), f"{a} -> {b}")

    def test_no_journeys_late_night(self):
        self.assertEqual(app.find_journeys("Airport", "Riverside", 23 * 60 + 30), [])

    def test_at_most_three_options(self):
        opts = app.pick_options(app.find_journeys("Airport", "Riverside", NINE))
        self.assertLessEqual(len(opts), 3)
        self.assertGreaterEqual(len(opts), 1)

    def test_delay_moves_journey(self):
        app.ALERTS["B10"] = {"delay": 7, "msg": "test"}
        j = self.best("Bus Terminal", "City Hospital")
        self.assertEqual((app.fmt(j.dep), app.fmt(j.arr)), ("09:07", "09:15"))


class FareTests(BaseCase):
    def test_concessions(self):
        cheapest = min(app.find_journeys("Airport", "Riverside", NINE), key=lambda j: j.fare)
        self.assertEqual(cheapest.fare, 34)
        self.assertEqual(round(cheapest.fare * app.DISCOUNTS["Student (30% off)"]), 24)
        self.assertEqual(round(cheapest.fare * app.DISCOUNTS["Senior (50% off)"]), 17)


class InputHandlingTests(BaseCase):
    def test_same_stop(self):
        self.assertIn("same stop", app.plan_trip("Airport", "Airport", "now"))

    def test_missing_stop(self):
        self.assertIn("Pick both", app.plan_trip(None, "Riverside", "now"))

    def test_bad_time(self):
        self.assertIn("couldn't read", app.plan_trip("Airport", "Riverside", "abc"))

    def test_late_night_message(self):
        self.assertIn("No service", app.plan_trip("Airport", "Riverside", "11:30 pm"))

    def test_board_after_service(self):
        msg, rows = app.departures("Central Station", "11:30 pm")
        self.assertIn("Service has ended", msg)
        self.assertEqual(rows, [])


class LanguageTests(BaseCase):
    def test_find_stops_order_and_typos(self):
        self.assertEqual(app.find_stops("from univarsity to hospital"),
                         ["University", "City Hospital"])

    def test_bus_station_is_not_central(self):
        self.assertEqual(app.find_stops("bus station"), ["Bus Terminal"])

    def test_no_stops(self):
        self.assertEqual(app.find_stops("asdf qwerty"), [])

    def test_find_routes(self):
        self.assertEqual([r.id for r in app.find_routes("tell me about b10")], ["B10"])
        self.assertEqual([r.id for r in app.find_routes("the red line")], ["M1"])

    def test_agent_help_and_fallback(self):
        self.assertIn("Stops I know", app.agent_reply("hello"))
        self.assertIn("didn't catch", app.agent_reply("asdf qwerty"))

    def test_agent_trip_question(self):
        reply = app.agent_reply("How do I get from Airport to Riverside at 9:00 am?")
        self.assertIn("Airport → Riverside", reply)
        self.assertIn("09:27", reply)

    def test_agent_route_info(self):
        self.assertIn("City Circular", app.agent_reply("Tell me about route B10"))

    def test_agent_alert_status(self):
        app.ALERTS["B10"] = {"delay": 7, "msg": "Road works"}
        self.assertIn("+7 min", app.agent_reply("Any delays today?"))
        app.ALERTS.clear()
        self.assertIn("running normally", app.agent_reply("Any delays today?"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
