"""
Tests for the signal lifecycle (signals.py) and its message (msgfmt.outcome).

They are offline: the candles are hand-made fixtures, no network, no Telegram.
Emoji are written as \\U escapes so no tool can silently strip them out of this file.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import msgfmt            # noqa: E402
import signals           # noqa: E402

TP_OK = "\u2705"          # ✅
T0 = 1_700_000_000     # t of bar 0 in every fixture
SL_NO = "\u26d4"          # ⛔


def bars(rows):
    """rows: list of (h, l, c) -> a detector-shaped result dict."""
    data = [{"t": T0 + 900 * i, "o": c, "h": h, "l": l, "c": c, "v": 1.0}
            for i, (h, l, c) in enumerate(rows)]
    return {"data": data, "bars": data, "n": len(data), "last": len(data) - 1,
            "o": [d["o"] for d in data], "h": [h for h, _l, _c in rows],
            "l": [l for _h, l, _c in rows], "cl": [c for _h, _l, c in rows]}


def pos(side="LONG", entry=100.0, sl=98.0, tp=104.0, n_at=0):
    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "rr": 2.0, "n_at": n_at,
            "t": T0 + 900 * (n_at - 1 if n_at < 0 else n_at),
            "idea": "fvg+ob", "label": "GBPUSD"}


class Lifecycle(unittest.TestCase):
    def test_tp_is_reported_with_the_candle_that_hit_it(self):
        res = bars([(100, 99, 100), (104.2, 100, 103.5)])
        oc = signals.resolve(pos(), res, pip=2.0)
        self.assertEqual(oc["result"], "tp")
        self.assertAlmostEqual(oc["px"], 103.5)
        self.assertEqual(oc["bars"], 1)
        self.assertAlmostEqual(oc["pips"], 1.75, places=2)   # 3.5 / 2.0
        self.assertAlmostEqual(oc["rr_done"], 1.75, places=2)

    def test_sl_first_wins_over_a_later_tp(self):
        res = bars([(100, 99, 100), (100, 97.9, 98.5), (110, 99, 108.0)])
        oc = signals.resolve(pos(), res)
        self.assertEqual(oc["result"], "sl")

    def test_one_candle_through_both_levels_is_counted_as_a_loss(self):
        res = bars([(105, 97, 101)])
        self.assertEqual(signals.resolve(pos(n_at=-1), res)["result"], "sl")
        self.assertEqual(signals.resolve(pos(n_at=-1), res, pessimistic=False)["result"], "tp")

    def test_nothing_is_reported_while_both_levels_are_alive(self):
        res = bars([(101, 99, 100.5), (102, 99.5, 101.5)])
        self.assertIsNone(signals.resolve(pos(), res))

    def test_short_is_resolved_the_other_way_round(self):
        res = bars([(100.8, 95.9, 96.5)])          # wick to 95.9, never above the 101 stop
        oc = signals.resolve(pos(side="SHORT", entry=100.0, sl=101.0, tp=96.0, n_at=-1), res)
        self.assertEqual(oc["result"], "tp")
        self.assertAlmostEqual(oc["px"], 96.5)

    def test_closes_only_mode_ignores_a_wick(self):
        res = bars([(104.9, 100, 103.0)])          # wicked the TP, closed below it
        self.assertEqual(signals.resolve(pos(n_at=-1), res)["result"], "tp")
        self.assertIsNone(signals.resolve(pos(n_at=-1), res, touch=False))

    def test_a_plan_without_levels_is_never_remembered(self):
        self.assertIsNone(signals.snapshot("GBPUSD", "15m",
                                           {"side": "LONG", "entry": 100.0, "sl": float("nan"),
                                            "tp": 104.0}, bars([(100, 99, 100)])))
        good = signals.snapshot("GBPUSD", "15m",
                                {"side": "LONG", "entry": 100.0, "sl": 98.0, "tp": 104.0,
                                 "rr": 2.0, "label": "fvg"},
                                bars([(100, 99, 100)]))
        self.assertEqual(good["side"], "LONG")
        self.assertEqual(good["n_at"], 0)          # data["last"]: the last CLOSED bar
        # if the caller keeps the forming candle in the series, that bar must be ignored
        live = bars([(100, 99, 100), (104.5, 99, 104.4)])
        self.assertIsNone(signals.resolve(pos(), live, include_forming=True))
        self.assertEqual(signals.resolve(pos(), live)["result"], "tp")

    def test_expiry_forgets_a_stale_plan(self):
        res = bars([(100, 99, 100)] * 4)           # never moves, hours stay ~0.75
        res["data"][-1]["t"] += 400_000            # ...then a lot of time passes
        oc = signals.resolve(pos(), res, expire_hours=72.0)
        self.assertEqual(oc["result"], "expired")
        self.assertGreater(oc["age_h"], 72.0)

    def test_a_shifted_series_is_followed_by_time_not_by_index(self):
        res = bars([(100, 99, 100), (100, 99, 101.0), (104.2, 99, 103.5)])
        stale = pos(n_at=0)
        stale["t"] = res["data"][0]["t"]
        self.assertEqual(signals.resolve(stale, res)["result"], "tp")
        # same plan, but the stored index no longer matches the bar it was taken on:
        # it must be re-found by timestamp, not trusted blindly
        lost = dict(stale, n_at=999, t=res["data"][0]["t"])
        self.assertEqual(signals.resolve(lost, res)["result"], "tp")

    def test_an_ambiguous_candle_is_flagged(self):
        res = bars([(105, 97, 101)])
        oc = signals.resolve(pos(n_at=-1), res)
        self.assertTrue(oc.get("amb"), "a bar through both levels must be marked")
        clean = dict(pos(n_at=-1), tp=200.0, sl=50.0)   # only the TP can be touched
        self.assertFalse(signals.resolve(clean, bars([(205.0, 100.0, 201.0)]))["amb"])

    def test_the_window_offset_is_added_back(self):
        # a real fetch: the caller's series is longer than the detector's analysis window
        # and the anchor must land on the bar the alert was printed for, not on the window's
        # own first candles — otherwise the follow-up reads the wrong candles
        head = [{"t": T0 - 900 * (301 - i), "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0,
                 "v": 1.0} for i in range(301)]
        series = head + [{"t": T0 + 900 * i, "o": c, "h": h, "l": l, "c": c, "v": 1.0}
                         for i, (h, l, c) in enumerate([(100.0, 99.9, 100.0),
                                                         (104.2, 99.9, 103.5),
                                                         (105.0, 104.0, 104.5)])]
        # the detector only saw the two closed candles at the end of that list
        res = {"bars": series, "data": series[301:303], "window_start": 301, "last": 1, "n": 302}
        self.assertEqual(signals._anchor(res), 302)            # index of the newest closed bar
        snap = dict(pos(n_at=301), t=series[301]["t"])   # the bar the alert was printed on
        oc = signals.resolve(snap, res)
        self.assertEqual(oc["result"], "tp")                    # hit on bar 302, not 301
        self.assertAlmostEqual(oc["px"], 103.5)
        self.assertEqual(oc["bars"], 1)
        # and a plan whose anchor is taken from the *window* instead of the series must
        # still be re-found by timestamp
        oc2 = signals.resolve(dict(snap, n_at=1), res)
        self.assertEqual(oc2["result"], "tp")

    def test_prune_keeps_the_newest_plans_only(self):
        open_pos = {f"S{i}|15m": pos() for i in range(20)}
        kept = signals.prune(open_pos, keep=12)
        self.assertEqual(len(kept), 12)
        self.assertIn("S19|15m", kept)
        self.assertNotIn("S0|15m", kept)


class Message(unittest.TestCase):
    def setUp(self):
        self.sym = {"name": "GBPUSD", "label": "GBPUSD", "digits": 5}

    def _lines(self, oc):
        return msgfmt.outcome(self.sym, "15m", {**pos(), **oc}).split("\n")

    def test_ambiguous_note_is_short_and_honest(self):
        line = self._lines({"result": "sl", "px": 98.0, "pips": -1.0, "rr_done": -1.0,
                            "bars": 1, "hours": 0.5, "amb": True})[1]
        self.assertIn("\u26d4", line)
        self.assertLessEqual(len(line), 60)

    def test_result_wording(self):
        self.assertTrue(self._lines({"result": "tp", "px": 104.0, "pips": 19.0,
                                     "rr_done": 2.0, "bars": 3, "hours": 1.5})[1].startswith(TP_OK))
        self.assertTrue(self._lines({"result": "sl", "px": 98.0, "pips": -1.0,
                                      "rr_done": -1.0, "bars": 1, "hours": 0.5})[1].startswith(SL_NO))

    def test_layout_one_number_per_line_and_short_lines(self):
        text = "\n".join(self._lines({"result": "tp", "px": 104.0, "pips": 19.0,
                                      "rr_done": 2.0, "bars": 3, "hours": 1.5}))
        for line in text.split("\n"):
            self.assertLessEqual(len(line), 60, f"line too long: {line!r}")
        self.assertIn("<code>104.00000</code>", text)
        self.assertIn("<code>100.00000</code>", text)
        # entry and exit never share a line
        self.assertNotIn("100.00000</code> <code>104", text)
        self.assertEqual(text.count("<code>"), text.count("</code>"))

    def test_no_placeholder_leaks(self):
        text = "\n".join(self._lines({"result": "tp", "px": 104.0, "pips": 19.0,
                                      "rr_done": 2.0, "bars": 3, "hours": 1.5}))
        self.assertNotIn("None", text)
        self.assertNotIn("nan", text)

    def test_missing_numbers_render_as_em_dash_not_garbage(self):
        text = msgfmt.outcome(self.sym, "15m", {"result": "expired", "px": None,
                                                "entry": float("nan"), "idea": ""})
        self.assertIn("\u2014", text)
        self.assertNotIn("nan", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
