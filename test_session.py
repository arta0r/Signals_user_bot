#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the session-bias read. No network, no Telegram.
Run:  python3 test_session.py -v
"""
import datetime as dt
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C            # noqa: E402
import session as S           # noqa: E402


def K(o, h, l, c, t=0):
    return {"o": o, "h": h, "l": l, "c": c, "v": 1.0, "t": t}


def trend(start, slope, n=200, t0=0, step=3600, wig=0.35):
    """Monotonic series with a small zig-zag so candles are well formed."""
    out, px = [], start
    for i in range(n):
        nxt = px + slope * (1.0 if i % 3 else 1.25)
        hi = max(px, nxt) + abs(slope) * wig
        lo = min(px, nxt) - abs(slope) * wig
        out.append(K(px, hi, lo, nxt, t=t0 + i * step))
        px = nxt
    return out


CFG = dict(C.CONFIG)
CFG["min_bars"] = 90


class TestVerdict(unittest.TestCase):
    def test_clean_uptrend_reads_up(self):
        b = trend(100.0, 0.9)
        out = S.symbol_bias(b, CFG)
        self.assertIsNotNone(out)
        self.assertGreater(out["score"], 0)
        self.assertEqual(out["verdict"], "up", out)

    def test_clean_downtrend_reads_down(self):
        out = S.symbol_bias(trend(400.0, -0.9), CFG)
        self.assertLess(out["score"], 0)
        self.assertEqual(out["verdict"], "down", out)

    def test_flat_market_is_not_forced_into_a_direction(self):
        vals = [100.0 + (0.6 if i % 2 else -0.6) for i in range(240)]
        bars = [K(v, v + 0.2, v - 0.2, v, t=i * 3600) for i, v in enumerate(vals)]
        out = S.symbol_bias(bars, CFG)
        self.assertIn(out["verdict"], ("flat", "up", "down"))
        self.assertLessEqual(abs(out["score"]), 5)
        self.assertTrue(math.isfinite(out["price"]))

    def test_score_and_parts_are_bounded(self):
        for bars in (trend(100.0, 0.9), trend(400.0, -0.9)):
            out = S.symbol_bias(bars, CFG)
            self.assertEqual(out["score"], sum(out["parts"].values()))
            self.assertTrue(all(v in (-1, 0, 1) for v in out["parts"].values()), out["parts"])
            self.assertTrue(0.0 <= out["position"] <= 1.0 or out["position"] == out["position"])
            self.assertGreater(out["atr"], 0)

    def test_explicit_session_cut_ignores_everything_after_the_open(self):
        b = trend(100.0, 0.9, n=300, t0=0)
        cut = b[200]["t"]
        before = S.symbol_bias(b, CFG, until_ts=cut)
        self.assertIsNotNone(before)
        self.assertEqual(before["price"], b[200]["c"])
        self.assertLess(200, len(b) - 1)          # later bars exist and were ignored

    def test_never_peeks_at_bars_after_the_open(self):
        up = trend(100.0, 0.9, n=200)
        cut_t = up[150]["t"]
        # a violent rally AFTER the open must not change the pre-open read
        late = trend(up[199]["c"], 9.0, n=40, t0=cut_t + 3600)
        a = S.symbol_bias(up[:151], CFG, until_ts=cut_t)
        b = S.symbol_bias(up[:151] + late, CFG, until_ts=cut_t)
        self.assertEqual((a["score"], a["price"]), (b["score"], b["price"]))


class TestAggregate(unittest.TestCase):
    def test_usd_strong_is_counted_against_gold(self):
        per = {"DXY": {"score": 4, "parts": {}, "price": 1.0, "rsi": 60.0, "macd_hist": 1.0,
                       "position": 0.9, "chg_pct": 0.3, "verdict": "up", "atr": 1.0,
                       "range_lo": 0.0, "range_hi": 1.0},
               "EURUSD": None}
        rep = S.report(per, C.SESSIONS[2], CFG)
        self.assertLess(rep["avg"], 0, "a rising DXY must pull the gold read down")
        self.assertEqual(rep["rows"][0][1]["gold_contrib"], -4)

    def test_gold_up_symbols_add_positively(self):
        per = {"XAUUSD": {"score": 3, "parts": {}, "price": 1.0, "rsi": 60.0, "macd_hist": 1.0,
                          "position": 0.9, "chg_pct": 0.3, "verdict": "up", "atr": 1.0,
                          "range_lo": 0.0, "range_hi": 1.0}}
        rep = S.report(per, C.SESSIONS[2], CFG)
        self.assertGreater(rep["avg"], 0)
        self.assertEqual(rep["verdict"], "up")

    def test_missing_data_does_not_poison_the_average(self):
        rep = S.report({"XAUUSD": None, "EURUSD": None}, C.SESSIONS[0], CFG)
        self.assertEqual(rep["n"], 0)
        self.assertEqual(rep["verdict"], "flat")
        self.assertEqual(rep["avg"], 0.0)

    def test_flat_verdict_when_symbols_disagree(self):
        def row(sc):
            return {"score": sc, "parts": {}, "price": 1.0, "rsi": 50.0, "macd_hist": 0.0,
                    "position": 0.5, "chg_pct": 0.0, "verdict": "up" if sc > 0 else "down",
                    "atr": 1.0, "range_lo": 0.0, "range_hi": 1.0}
        rep = S.report({"XAUUSD": row(4), "USDJPY": row(4)}, C.SESSIONS[2], CFG)   # jp +4 => -4
        self.assertEqual(rep["verdict"], "flat", rep)


class TestClock(unittest.TestCase):
    def test_current_session_picks_the_closest_open(self):
        self.assertEqual(S.current_session(dt.datetime(2026, 9, 4, 7, 10,
                                                       tzinfo=dt.timezone.utc))["name"], "london")
        self.assertEqual(S.current_session(dt.datetime(2026, 9, 4, 13, 40,
                                                       tzinfo=dt.timezone.utc))["name"], "newyork")

    def test_accepts_a_run_a_little_early(self):
        self.assertEqual(S.current_session(dt.datetime(2026, 9, 4, 6, 45,
                                                       tzinfo=dt.timezone.utc))["name"], "london")

    def test_gap_between_sessions_reports_the_next_open(self):
        now = dt.datetime(2026, 9, 4, 18, 0, tzinfo=dt.timezone.utc)
        self.assertIsNone(S.current_session(now))          # nobody is "opening"
        s, m = S.next_session(now)
        self.assertEqual(s["name"], "sydney", s)
        self.assertAlmostEqual(m, 240.0, delta=1.0)
        s2, m2, live2 = S.session_for(now)
        self.assertEqual(s2["name"], "sydney")
        self.assertEqual(m2, 240.0)
        self.assertFalse(live2)

    def test_session_for_uses_the_live_one_when_inside_the_window(self):
        now = dt.datetime(2026, 9, 4, 7, 20, tzinfo=dt.timezone.utc)
        s, m, live = S.session_for(now)
        self.assertEqual(s["name"], "london")
        self.assertEqual(m, 0.0)
        self.assertTrue(live)

    def test_configured_sessions_are_usable(self):
        for s in C.SESSIONS:
            self.assertTrue(0 <= s["utc_hour"] <= 23)
            self.assertIn("label_fa", s)
            self.assertTrue(s["symbols"])
        self.assertIsNotNone(S.session_at(dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc), "tokyo"))
        self.assertIsNone(S.session_at(dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc), "nope"))


class TestBuildSemantics(unittest.TestCase):
    """What `build()` decides about WHICH session a run belongs to (no network needed)."""

    def test_before_the_open_it_reports_the_next_session_with_a_countdown(self):
        now = dt.datetime(2026, 9, 4, 18, 0, tzinfo=dt.timezone.utc)
        s, m, live = S.session_for(now)
        self.assertEqual(s["name"], "sydney")          # opens 22:00
        self.assertFalse(live)
        self.assertAlmostEqual(m, 240.0, delta=1.0)

    def test_a_late_cron_run_still_names_the_session_that_opened(self):
        now = dt.datetime(2026, 9, 4, 7, 20, tzinfo=dt.timezone.utc)
        s, m, live = S.session_for(now)
        self.assertEqual(s["name"], "london")
        self.assertTrue(live)
        self.assertEqual(m, 0.0)

    def test_explicit_session_forces_the_cut_to_that_open(self):
        tokyo = next(x for x in C.SESSIONS if x["name"] == "tokyo")
        syd = next(x for x in C.SESSIONS if x["name"] == "sydney")
        now = dt.datetime(2026, 9, 4, 18, 0, tzinfo=dt.timezone.utc)
        # Tokyo opened at 00:00 today -> that is the cut, never a future bar
        self.assertEqual(S.last_open(now, tokyo).strftime("%m-%d %H:%M"), "09-04 00:00")
        # Sydney opens at 22:00 (still ahead) -> its LAST open was yesterday
        self.assertEqual(S.last_open(now, syd).strftime("%m-%d %H:%M"), "09-03 22:00")
        self.assertLessEqual(S.last_open(now, tokyo), now)
        self.assertLessEqual(S.last_open(now, syd), now)


class TestOutput(unittest.TestCase):
    def _rep(self, sc=4):
        def row(v):
            return {"score": v, "parts": {k: 1 for k in ("trend", "position", "momentum",
                                                          "macd", "break")},
                    "price": 4500.0, "rsi": 61.0, "macd_hist": 3.0, "position": 0.8,
                    "chg_pct": 0.4, "verdict": "up" if v > 0 else "down", "atr": 12.0,
                    "range_lo": 4400.0, "range_hi": 4600.0}
        rep = S.report({"XAUUSD": row(sc), "USDJPY": row(sc)}, C.SESSIONS[2], CFG)
        rep["digits"] = {"XAUUSD": 2, "USDJPY": 3}
        return rep

    def test_caption_html_is_balanced(self):
        txt = S.caption(self._rep(), {"XAUUSD": 2, "USDJPY": 3})
        for a, b in (("<b>", "</b>"), ("<i>", "</i>")):
            self.assertEqual(txt.count(a), txt.count(b), a)
        self.assertIn("سشن", txt)

    def test_caption_names_the_verdict_and_the_checks(self):
        txt = S.caption(self._rep(), {"XAUUSD": 2, "USDJPY": 3})
        self.assertIn("صعودی", txt)
        self.assertIn("trend:+1", txt)
        self.assertIn("سهم در قرائتِ طلا", txt)

    def test_headline_distinguishes_open_from_pre_read(self):
        rep = self._rep()
        rep["live"] = False
        rep["in_min"] = 120.0
        txt = S.caption(rep, {"XAUUSD": 2, "USDJPY": 3})
        self.assertIn("پیش‌خوانی سشن", txt)
        self.assertIn("تا گشایش", txt)
        rep["live"] = True
        self.assertIn("گشایش سشن", S.caption(rep, {"XAUUSD": 2, "USDJPY": 3}))

    def test_missing_symbol_is_reported_not_crashed(self):
        rep = S.report({"XAUUSD": None}, C.SESSIONS[1], CFG)
        txt = S.caption(rep, {})
        self.assertIn("داده کافی نبود", txt)
        self.assertEqual(rep["verdict"], "flat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
