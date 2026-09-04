#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the Telegram text (msgfmt.py). No network, no Telegram.

The point of these tests is layout, not wording: the bot used to print one long mixed
Persian/Latin line per field and Telegram wrapped it into unreadable order. Run:
    python3 test_msgfmt.py -v
"""
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C        # noqa: E402
import msgfmt            # noqa: E402

SYM = {"name": "EURUSD", "label": "EUR/USD", "digits": 5, "pip": 0.0001}
NUM = re.compile(r"-?\d")


def res(**kw):
    base = {
        "price": 1.16306, "atr": 0.00080, "rsi": 55.5, "macd_hist": 0.0000128,
        "trend": "UP", "trend_flip": {"i": 250, "dir": "bull"},
        "ema_fast_v": 1.16280, "ema_slow_v": 1.16100,
        "tags": {"bull": ["FVG"], "bear": []}, "rsi_cross": {"dir": "neutral", "i": None,
                                                              "value": 55.5},
        "macd_cross": None, "footprint": None, "idea": None, "mode": "solo",
    }
    base.update(kw)
    return base


IDEA = {"side": "LONG", "entry": 1.16250, "sl": 1.16150, "tp": 1.16450, "rr": 2.0,
        "name": "fvg", "label": "FVG (gap)", "confluence": 1, "i": 250, "last_close": 1.16306}


class TestLayout(unittest.TestCase):
    def test_one_number_per_line(self):
        text = msgfmt.setup(SYM, "15m", res(idea=dict(IDEA)))
        codes = [l for l in text.splitlines() if l.startswith("<code>")]
        self.assertEqual(len(codes), 3, "entry / tp / sl must each be their own code line")
        for line in codes:
            self.assertEqual(len(re.findall(r"\d[\d,.]*", line)), 1, line)

    def test_no_long_lines(self):
        for text in (msgfmt.setup(SYM, "15m", res(idea=dict(IDEA))),
                     msgfmt.trend(SYM, "4h", res()),
                     msgfmt.footprint(SYM, "4h", res(footprint={"state": "buy", "bars": 20,
                                                                "delta_pct": 12.5,
                                                                "cum_slope_v": 1.9,
                                                                "absorption": {"x": 1},
                                                                "divergence": True}))):
            for line in text.splitlines():
                self.assertLessEqual(len(line), 60, f"line too long for a phone bubble: {line!r}")

    def test_all_labels_present_and_ordered(self):
        """Fixed fields, fixed order — the whole point of the rewrite."""
        text = msgfmt.setup(SYM, "15m", res(idea=dict(IDEA)))
        want = ["\U0001F3AF", "\u2705", "\u26D4", "\U0001F4D0", "\U0001F9ED",
                "\U0001F4CA", "\U0001F4C8", "\u2014"]
        marks = set(want) | {"\U0001F463"}
        got = [l[0] for l in text.splitlines() if l and l[0] in marks]
        self.assertEqual(got, want)
        old = C.CONFIG.get("footprint", False)
        C.CONFIG["footprint"] = True
        try:
            text2 = msgfmt.setup(SYM, "15m", res(idea=dict(IDEA)))
            got2 = [l[0] for l in text2.splitlines() if l and l[0] in marks]
            self.assertEqual(got2, want[:5] + ["\U0001F463"] + want[5:])
        finally:
            C.CONFIG["footprint"] = old

    def test_setup_header_names_symbol_and_timeframe(self):
        text = msgfmt.setup(SYM, "4h", res(idea=dict(IDEA)))
        self.assertIn("EUR/USD · 4h", text)
        self.assertIn("🟢 خرید", text)

    def test_no_unformatted_nan_or_none(self):
        r = res(idea=dict(IDEA), macd_hist=float("nan"), ema_fast_v=None)
        text = msgfmt.setup(SYM, "15m", r) + "\n" + msgfmt.trend(SYM, "15m", r)
        self.assertNotIn("nan", text)
        self.assertNotIn("None", text)
        self.assertIn("—", text)                      # the em dash is the "no data" marker

    def test_every_kind_builds(self):
        cases = {
            "rsi": res(rsi_cross={"dir": "overbought", "i": 250, "value": 71.2}),
            "macd": res(macd_cross={"i": 250, "dir": "bull"}),
            "trend": res(),
            "structure": res(),
        }
        for kind, r in cases.items():
            if kind == "structure":
                r = dict(r, recent_bos=[{"kind": "bullish", "level": 1.163, "i": 250}],
                         recent_sweep=[])
            text = msgfmt.for_kind(SYM, "15m", r, kind)
            self.assertTrue(text and len(text) > 10, kind)
            self.assertTrue(text.startswith("<b>"), kind)

    def test_neutral_rsi_and_missing_macd_say_nothing(self):
        self.assertIsNone(msgfmt.rsi(SYM, "15m", res()))
        self.assertIsNone(msgfmt.macd(SYM, "15m", res()))

    def test_pips_only_shown_when_sane(self):
        crypto = {"name": "BTCUSD", "label": "BITCOIN", "digits": 0, "pip": 1.0}
        big = msgfmt.setup(crypto, "4h", res(price=81000, idea={**IDEA, "entry": 81000,
                                                                "sl": 78000, "tp": 86000}))
        self.assertNotIn("پیپ", big)                  # 3000 "pips" on BTC is noise
        fx = msgfmt.setup(SYM, "15m", res(idea=dict(IDEA)))
        self.assertIn("پیپ", fx)

    def test_footprint_line_is_optional(self):
        r = res(idea=dict(IDEA), footprint={"state": "sell", "bars": 20, "delta_pct": -9.9,
                                           "cum_slope_v": -1.8})
        off = msgfmt.setup(SYM, "15m", dict(r, idea=dict(IDEA)))
        self.assertNotIn("👣", off)                   # footprint filter is off by default
        old = C.CONFIG.get("footprint", False)
        C.CONFIG["footprint"] = True
        try:
            self.assertIn("👣", msgfmt.setup(SYM, "15m", r))
        finally:
            C.CONFIG["footprint"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
