import unittest

from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.money import cap_from_widget, display, from_micros, to_micros


class MoneyTest(unittest.TestCase):
    def test_round_trips_api_amounts(self):
        self.assertEqual(to_micros("0.653877"), 653877)
        self.assertEqual(to_micros("12"), 12_000_000)
        self.assertEqual(to_micros("-1.5"), -1_500_000)
        self.assertEqual(from_micros(653877), "0.653877")
        self.assertEqual(from_micros(-1_500_000), "-1.500000")

    def test_refuses_floats_and_junk(self):
        for value in (0.5, "1e3", "01.0", "1.0000001", "1000000000", "1\u0662", "1.\u0665", "", None, "$1"):
            with self.subTest(value=value):
                with self.assertRaises(CuddlerError) as caught:
                    to_micros(value)
                self.assertEqual(caught.exception.code, "invalid_amount")

    def test_cap_from_widget_rounds_down_to_micro_dollars(self):
        self.assertEqual(cap_from_widget(2), "2.000000")
        self.assertEqual(cap_from_widget(2.0), "2.000000")
        self.assertEqual(cap_from_widget(0.1234567), "0.123456")
        self.assertEqual(cap_from_widget(0.01), "0.010000")
        self.assertEqual(cap_from_widget(1000), "1000.000000")

    def test_cap_from_widget_enforces_bounds(self):
        for value in (0, 0.009, 1000.000001, float("nan"), float("inf"), "x"):
            with self.subTest(value=value):
                with self.assertRaises(CuddlerError) as caught:
                    cap_from_widget(value)
                self.assertEqual(caught.exception.code, "invalid_cap")

    def test_display_rounds_away_from_zero(self):
        self.assertEqual(display("0.653877"), "$0.66")
        self.assertEqual(display("2.000000"), "$2.00")
        self.assertEqual(display("0.004116"), "$0.0042")
        self.assertEqual(display("0.0099"), "$0.0099")
        self.assertEqual(display("0.009999"), "$0.01")
        self.assertEqual(display("0"), "$0.00")
        self.assertEqual(display("11.940000"), "$11.94")
        self.assertEqual(display("-1.5"), "-$1.50")
        self.assertEqual(display("-0.004116"), "-$0.0042")
        self.assertEqual(display("-11.941"), "-$11.95")
        self.assertEqual(display("-0"), "$0.00")


if __name__ == "__main__":
    unittest.main()
