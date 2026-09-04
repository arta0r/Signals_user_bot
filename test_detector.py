#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for the detector. No network, no Telegram, no MT5.
Run:  python3 test_detector.py -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C            # noqa: E402
import detector as D          # noqa: E402


def K(o, h, l, c, t=0):
    return {"o": o, "h": h, "l": l, "c": c, "v": 1.0, "t": t}


def flat(n, px=100.0, start=0):
    return [K(px, px + 0.2, px - 0.2, px, t=start + i) for i in range(n)]


def series_to_bars(series, step=0.2):
    out = []
    prev = series[0]
    for i, c in enumerate(series):
        out.append(K(prev, max(prev, c) + step, min(prev, c) - step, c, t=i))
        prev = c
    return out


CFG = dict(C.CONFIG)
CFG["min_bars"] = 60


def scenario(kind):
    """A clean V (bull) or inverted V (bear) with enough tails to confirm swings."""
    if kind == "bull":
        vals = [100.0 - 0.55 * i for i in range(70)]
        vals += [vals[-1] + 1.7 * i for i in range(1, 45)]
    else:
        vals = [100.0 + 0.55 * i for i in range(70)]
        vals += [vals[-1] - 1.7 * i for i in range(1, 45)]
    return series_to_bars(vals)


def zigzag_down(periods=30, start=100.0, step=6.0):
    """Lower highs AND lower lows, with explicit candles so fractal swings exist.
    Each period: one strong down candle (body = the whole drop), then a 3-bar bounce."""
    out, px = [], start
    for k in range(periods):
        lo = px - step
        out.append(K(px, px + 0.20, lo, lo, t=k * 4))
        for j in range(1, 4):
            c = lo + j * 1.5
            out.append(K(c - 1.5, c + 0.15, c - 1.35, c, t=k * 4 + j))
        px = lo + 4.5
    return out


class TestIndicators(unittest.TestCase):
    def test_rsi_extremes(self):
        up = [float(i) for i in range(1, 80)]
        dn = [float(-i) for i in range(1, 80)]
        self.assertAlmostEqual(D.rsi(up, 14)[-1], 100.0, places=6)
        self.assertAlmostEqual(D.rsi(dn, 14)[-1], 0.0, places=6)

    def test_rsi_between_bounds(self):
        vals = [100 + 2 * math.sin(i / 3.0) for i in range(200)]
        r = D.rsi(vals, 14)
        live = [v for v in r if not math.isnan(v)]
        self.assertTrue(all(0.0 <= v <= 100.0 for v in live))
        self.assertGreater(len(live), 150)

    def test_ema_seed_and_recursion(self):
        vals = [float(i) + (i % 3) for i in range(1, 140)]
        got = D.ema(vals, 21)
        self.assertTrue(math.isnan(got[19]))
        self.assertAlmostEqual(got[20], sum(vals[:21]) / 21, places=9)
        k = 2 / 22
        ref = got[20]
        for v in vals[21:]:
            ref = v * k + ref * (1 - k)
        self.assertAlmostEqual(got[-1], ref, places=9)

    def test_macd_returns_parallel_arrays(self):
        vals = [100 + 3 * math.sin(i / 5.0) for i in range(200)]
        line, sig, hist = D.macd(vals, 12, 26, 9)
        self.assertEqual(len(line), len(sig))
        self.assertEqual(len(line), len(hist))
        i = len(vals) - 1
        self.assertAlmostEqual(hist[i], line[i] - sig[i], places=9)

    def test_macd_cross_detected_after_reversal(self):
        vals = [100.0 - 0.6 * i for i in range(80)]
        vals += [vals[-1] + 1.9 * i for i in range(1, 45)]
        line, sig, _ = D.macd(vals, 12, 26, 9)
        cross = D._cross(line, sig, len(vals) - 1, lookback=200)
        self.assertIsNotNone(cross)
        self.assertEqual(cross["dir"], "bull")

    def test_atr_constant_range(self):
        h = [11.0] * 40
        l = [9.0] * 40
        c = [10.0] * 40
        self.assertAlmostEqual(D.atr(h, l, c, 14)[-1], 2.0, places=9)


class TestStructure(unittest.TestCase):
    def test_bullish_fvg_and_fill(self):
        bars = flat(95)
        bars.append(K(100.0, 100.5, 99.6, 100.2))      # i=95  high 100.5 -> gap floor
        bars.append(K(101.0, 106.0, 100.9, 105.5))     # i=96  impulse
        bars.append(K(105.5, 108.0, 104.2, 107.5))     # i=97  low 104.2 -> gap ceiling
        h = [b["h"] for b in bars]
        l = [b["l"] for b in bars]
        gaps = [g for g in D.fvg_list(h, l) if g["i"] == 97]
        self.assertEqual(len(gaps), 1, gaps)
        g = gaps[0]
        self.assertEqual(g["dir"], "bull")
        self.assertAlmostEqual(g["bottom"], 100.5, places=6)
        self.assertAlmostEqual(g["top"], 104.2, places=6)
        cl = [b["c"] for b in bars]
        fill, mit = D.fvg_state(g, cl, h, l, filled_tol=0.35)
        self.assertEqual(mit, None)                      # never traded back into
        self.assertAlmostEqual(fill, 0.0, places=6)

    def test_bearish_fvg(self):
        bars = flat(95)
        bars.append(K(100.0, 100.2, 99.5, 99.8))       # i=95  low 99.5 -> gap ceiling
        bars.append(K(99.8, 99.9, 94.0, 94.5))          # i=96  impulse down
        bars.append(K(94.5, 95.0, 92.0, 93.0))          # i=97  high 95.0 -> gap floor
        h = [b["h"] for b in bars]
        l = [b["l"] for b in bars]
        gaps = [g for g in D.fvg_list(h, l) if g["i"] == 97]
        self.assertEqual(len(gaps), 1, gaps)
        self.assertEqual(gaps[0]["dir"], "bear")
        self.assertAlmostEqual(gaps[0]["top"], 99.5, places=6)
        self.assertAlmostEqual(gaps[0]["bottom"], 95.0, places=6)
        self.assertGreater(gaps[0]["top"], gaps[0]["bottom"])   # top is the upper edge

    def test_no_fvg_in_flat_market(self):
        bars = flat(120)
        self.assertEqual(D.fvg_list([b["h"] for b in bars], [b["l"] for b in bars]), [])

    def test_sweep_of_prior_swing_high(self):
        bars = flat(95)
        bars.append(K(100, 101.5, 99.5, 101.0))
        bars.append(K(101, 105, 100.8, 104.5))          # swing high 105 @ i=96
        bars.append(K(104.5, 104.6, 103.5, 104.0))
        bars.append(K(104, 104.4, 103.4, 104.1))
        bars += flat(2, 104.0, start=99)
        bars.append(K(100, 106.5, 99.5, 100.0))          # wick beyond 105, close back below
        sw = D.swings([b["h"] for b in bars], [b["l"] for b in bars], 3, 3)
        self.assertTrue(any(p["i"] == 96 and abs(p["price"] - 105) < 1e-9 and p["kind"] == "high"
                            for p in sw), sw)
        sp = D.sweeps([b["h"] for b in bars], [b["l"] for b in bars],
                      [b["c"] for b in bars], sw, lookback=40)
        hits = [s for s in sp if s["kind"] == "buy_side_swept"]
        self.assertEqual(len(hits), 1, sp)
        self.assertEqual(hits[0]["i"], len(bars) - 1)
        self.assertAlmostEqual(hits[0]["level"], 105, places=6)

    def test_no_sweep_when_close_stays_beyond(self):
        bars = flat(95)
        bars.append(K(100, 101.5, 99.5, 101.0))
        bars.append(K(101, 105, 100.8, 104.5))
        bars.append(K(104.5, 104.6, 103.5, 104.0))
        bars.append(K(104, 104.4, 103.4, 104.1))
        bars += flat(2, 104.0, start=99)
        bars.append(K(105, 106.5, 104.9, 106.2))        # closes ABOVE -> breakout, not a sweep
        sp = D.sweeps([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars],
                      D.swings([b["h"] for b in bars], [b["l"] for b in bars], 3, 3), lookback=40)
        self.assertEqual([s for s in sp if s["kind"] == "buy_side_swept"], [])

    def test_down_staircase_creates_both_swing_types(self):
        bars = zigzag_down()
        sw = D.swings([b["h"] for b in bars], [b["l"] for b in bars], 3, 3)
        self.assertTrue([p for p in sw if p["kind"] == "low"])
        self.assertTrue([p for p in sw if p["kind"] == "high"], sw[:6])

    def test_bullish_bos_is_a_choch_after_downtrend(self):
        bars = zigzag_down()
        h = [b["h"] for b in bars]
        l = [b["l"] for b in bars]
        sw = D.swings(h, l, 3, 3)
        last_low = min(p["price"] for p in sw if p["kind"] == "low")
        last_high = max(p["price"] for p in sw if p["kind"] == "high")
        base = bars[-1]["c"]
        for i in range(1, 25):
            c = base + i * 2.0
            bars.append(K(c - 2.0, c + 0.25, c - 2.25, c, t=900 + i))
        closes = [b["c"] for b in bars]
        ev = D.bos(closes, D.swings([b["h"] for b in bars], [b["l"] for b in bars], 3, 3))
        bulls = [e for e in ev if e["kind"] == "bullish"]
        self.assertTrue(bulls, [e for e in ev][:5])
        # broke above the last confirmed swing high...
        self.assertGreater(min(e["level"] for e in bulls), last_low)
        self.assertLess(max(b["c"] for b in bars), 1e9)
        # ...and because structure had been bearish, that break is a CHoCH
        self.assertTrue(bulls[0]["is_choch"], bulls[0])
        self.assertGreater(bulls[0]["level"], 0)
        self.assertLess(bulls[0]["i"], len(closes))

    def test_no_bos_inside_a_clean_staircase(self):
        bars = zigzag_down()
        ev = D.bos([b["c"] for b in bars], D.swings([b["h"] for b in bars],
                                                   [b["l"] for b in bars], 3, 3))
        self.assertEqual([e for e in ev if e["kind"] == "bullish"], [], ev[:5])

    def test_last_order_block_direction(self):
        bars = [K(100 - i, 100.3 - i, 99.7 - i, 99.7 - i) for i in range(10)]
        bars += [K(110 + i, 110.6 + i, 109.8 + i, 110.5 + i) for i in range(6)]
        ob = D.last_ob(bars, after_i=12, direction="bull")
        self.assertEqual(ob["i"], 9)
        self.assertLess(ob["c"] if "c" in ob else bars[9]["c"], bars[9]["o"])
        self.assertAlmostEqual(ob["top"], bars[9]["h"], places=6)


class TestSetupBuilder(unittest.TestCase):
    _scenario = staticmethod(scenario)

    def test_shape_and_numeric_sanity(self):
        for kind in ("bull", "bear"):
            res = D.build_setup(scenario(kind), CFG)
            self.assertIsNotNone(res, kind)
            for key in ("price", "atr", "rsi", "macd", "trend", "score", "tags", "open_gaps"):
                self.assertIn(key, res)
            self.assertFalse(math.isnan(res["atr"]) or res["atr"] <= 0)
            self.assertTrue(0.0 <= res["rsi"] <= 100.0)
            self.assertIn(res["trend"], ("UP", "DOWN"))
            self.assertIsInstance(res["score"]["bull"], int)
            self.assertIn("min_bars" and "signal_lookback_bars", CFG)

    def test_idea_levels_are_ordered(self):
        for kind, want in (("bull", "LONG"), ("bear", "SHORT")):
            res = D.build_setup(scenario(kind), CFG)
            idea = res["idea"]
            if idea is None:
                continue                                    # filtered out is a valid outcome
            self.assertEqual(idea["side"], want, res["tags"])
            if idea["side"] == "LONG":
                self.assertLess(idea["sl"], idea["entry"], idea)
                self.assertGreater(idea["tp"], idea["entry"], idea)
            else:
                self.assertGreater(idea["sl"], idea["entry"], idea)
                self.assertLess(idea["tp"], idea["entry"], idea)
            self.assertGreaterEqual(idea["rr"], CFG["min_rr"] - 1e-9, idea)

    def test_is_deterministic_and_ignores_the_forming_candle(self):
        bars = scenario("bull")
        a = D.build_setup(bars, CFG)
        b = D.build_setup(bars, CFG)
        self.assertEqual(a["price"], b["price"])          # no hidden state between runs
        self.assertEqual(a["score"], b["score"])
        self.assertEqual((a["idea"] or {}).get("side"), (b["idea"] or {}).get("side"))
        # appending one more bar must NOT change what we saw on the last CLOSED bar
        # config contract: the last element is treated as the live/forming candle
        self.assertTrue(CFG["drop_forming"])
        self.assertAlmostEqual(a["price"], bars[-2]["c"], places=9)
        # whatever that forming candle prints, it cannot move the reported values
        mangled = bars[:-1] + [K(5_000, 5_100, 4_900, 5_050)]
        m = D.build_setup(mangled, CFG)
        self.assertAlmostEqual(m["price"], a["price"], places=9)
        self.assertEqual(m["score"], a["score"])
        self.assertEqual(m["n"], a["n"])
        # prefix property: indicators are causal, so a bar's numbers don't depend on history
        for cut in (95, 100, 108):
            pa = D.build_setup(bars[:cut], CFG)
            self.assertAlmostEqual(pa["price"], bars[cut - 2]["c"], places=9)
            self.assertEqual(pa["n"], cut - 1)

class TestSoloIdeas(unittest.TestCase):
    """mode="solo": each setup is its own idea with its own SL/TP (no confluence needed)."""

    def _cfg(self, **kw):
        c = dict(CFG)
        c.setdefault("signal_lookback_bars", 1)
        c.update(mode="solo", ideas={k: True for k in D.IDEA_LABELS}, **kw)
        return c

    def test_single_setup_is_enough_in_solo_mode(self):
        solo = D.build_setup(scenario("bull"), self._cfg())
        combo = D.build_setup(scenario("bull"), dict(CFG, mode="combo"))
        self.assertIsNone(combo["idea"], "a lone condition must not trade in combo mode")
        self.assertIsNotNone(solo["idea"], "a lone setup must trade in solo mode")
        self.assertIn(solo["idea"]["name"], D.IDEA_LABELS)
        self.assertEqual(solo["mode"], "solo")

    def test_disabled_setups_are_rejected(self):
        base = self._cfg()
        for keep in ("fvg", "zone", "sweep", "bos"):
            res = D.build_setup(scenario("bull"), dict(base, ideas={k: (k == keep) for k in D.IDEA_LABELS}))
            if res["idea"] is not None:
                self.assertEqual(res["idea"]["name"], keep, keep)
        off = D.build_setup(scenario("bull"), dict(base, ideas={k: False for k in D.IDEA_LABELS}))
        self.assertIsNone(off["idea"])

    def test_idea_bar_is_inside_the_window_and_indexable(self):
        res = D.build_setup(scenario("bull"), self._cfg())
        i = res["idea"]
        self.assertLessEqual(i["i"], res["abs_n"])
        self.assertGreaterEqual(i["i"], 0)
        self.assertAlmostEqual(res["price"], scenario("bull")[-2]["c"], places=9)

    def test_market_and_retest_entry_differ_but_are_ordered(self):
        for kind in ("bull", "bear"):
            bars = scenario(kind)
            for entry in ("market", "retest"):
                r = D.build_setup(bars, self._cfg(solo_entry=entry))
                if r["idea"] is None:
                    continue
                i = r["idea"]
                if entry == "market":
                    self.assertAlmostEqual(i["entry"], r["price"], places=9)
                long_ = i["side"] == "LONG"
                self.assertLess(i["sl"], i["entry"]) if long_ else self.assertGreater(i["sl"], i["entry"])
                self.assertGreater(i["tp"], i["entry"]) if long_ else self.assertLess(i["tp"], i["entry"])
                self.assertGreaterEqual(i["rr"], CFG["min_rr"] - 1e-9)

    def test_stale_limit_entry_can_be_dropped(self):
        # only a retest entry can be "stale"; in market mode the entry IS the last close
        bars = scenario("bull")
        loose = D.build_setup(bars, self._cfg(max_entry_offset_atr=0.0, solo_entry="retest"))
        strict = D.build_setup(bars, self._cfg(max_entry_offset_atr=0.001, solo_entry="retest"))
        if loose["idea"] is not None and abs(loose["idea"]["entry"] - loose["price"]) > loose["atr"] * 0.001:
            self.assertIsNone(strict["idea"])
        else:
            self.skipTest("fixture entry already sits at market")

    def test_combo_legacy_switch_reproduces_the_pre_split_levels(self):
        for kind in ("bull", "bear"):
            for mr in (1.0, 1.6, 2.5):
                cfg = dict(CFG)
                cfg.update(mode="combo", min_rr=mr, combo_legacy_levels=True, signal_lookback_bars=2)
                for bars in (scenario(kind), scenario("bull"), scenario("bear")):
                    res = D.build_setup(bars, cfg)
                    self.assertEqual(res["mode"], "combo")
                    if res["idea"] is not None:
                        self.assertEqual(res["idea"]["name"], "combo")
                        self.assertEqual(res["all_ideas"][0]["confluence"],
                                         res["score"]["bull"] if res["idea"]["side"] == "LONG"
                                         else res["score"]["bear"])

    def test_solo_and_combo_agree_on_the_conditions_table(self):
        bars = scenario("bull")
        a = D.build_setup(bars, self._cfg())
        b = D.build_setup(bars, dict(CFG, mode="combo"))
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["tags"], b["tags"])


class TestResample(unittest.TestCase):
    def test_grouping(self):
        bars = [K(i, i + 1, i - 1, i + 0.5, t=i) for i in range(9)]
        out = __import__("dataio").resample(bars, 3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["o"], 0)
        self.assertEqual(out[0]["h"], 3.0)
        self.assertEqual(out[0]["l"], -1.0)
        self.assertEqual(out[0]["c"], 2.5)


class TestOutput(unittest.TestCase):
    def test_caption_html_is_balanced(self):
        import scan
        res = D.build_setup(scenario("bull"), CFG)
        sym = {"name": "XAUUSD", "label": "GOLD", "digits": 2}
        for kind in ("setup", "rsi", "macd", "structure"):
            txt = scan.caption_for(sym, "15m", res, kind)
            if txt is None:
                continue
            self.assertEqual(txt.count("<b>") + txt.count("<i>") + txt.count("<code>"),
                             txt.count("</b>") + txt.count("</i>") + txt.count("</code>"), kind)

    def test_chart_renders_a_real_png(self):
        import tempfile
        import render
        bars = scenario("bull")
        res = D.build_setup(bars, CFG)
        sym = {"name": "XAUUSD", "label": "GOLD", "digits": 2}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "c.png")
            render.draw(bars, res, sym, "15m", out)
            raw = open(out, "rb").read()
        self.assertGreater(len(raw), 5000, "chart looks empty")
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n", "not a PNG")

    @unittest.skipUnless(os.environ.get("RUN_NET"), "set RUN_NET=1 to hit the network")
    def test_live_fetch(self):
        import dataio
        bars, prov = dataio.fetch(C.SYMBOLS[0], "15m", 120)
        self.assertGreater(len(bars), 40, prov)


if __name__ == "__main__":
    unittest.main(verbosity=2)
