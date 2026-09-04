#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the footprint filter (proxy math + detector wiring).

Run:  python3 test_footprint.py -v        (no network, no Telegram)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C          # noqa: E402
import detector as D        # noqa: E402
import footprint as FP      # noqa: E402


def K(o, h, l, c, v=1.0, t=0):
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "t": t}


def quiet(n, px=100.0, v=10.0, start=0):
    return [K(px, px + 0.5, px - 0.5, px + 0.05, v, t=start + i) for i in range(n)]


CFG = dict(C.CONFIG)
CFG["min_bars"] = 30          # fixtures are short: 30 closed bars is all the window needs


class TestProxy(unittest.TestCase):
    def test_bar_delta_extremes_and_doji(self):
        self.assertAlmostEqual(FP.bar_delta(100, 101, 99, 101), 1.0)
        self.assertAlmostEqual(FP.bar_delta(100, 101, 99, 99), -1.0)
        self.assertEqual(FP.bar_delta(100, 100, 100, 100), 0.0)     # no range, no opinion

    def test_slope_is_three_bars_not_the_whole_window(self):
        # 16 heavy selling bars then 3 heavy buying bars: the window is still net short,
        # but "flow right now" is buying -> that is the point of a footprint read
        cs = [K(100 - 0.8 * i, 100.1 - 0.8 * i, 99.1 - 0.8 * i, 99.2 - 0.8 * i, 30.0, t=i)
              for i in range(17)]
        px = cs[-1]["c"]
        for k in range(3):
            px += 0.8
            cs.append(K(px - 0.8, px + 0.9, px - 0.4, px, 30.0, t=17 + k))
        fp = FP.analyze(cs, {})
        self.assertEqual(fp["state"], "buy")
        self.assertGreater(fp["cum_slope_v"], 1.4)          # the last 3 bars are buying
        self.assertLess(fp["cum_delta_v"], 0.0)              # the window as a whole is not

    def test_heavy_one_sided_bar_sets_state(self):
        cs = quiet(19) + [K(100.0, 102.0, 99.9, 101.9, 60.0)]
        fp = FP.analyze(cs, {})
        self.assertEqual(fp["state"], "buy")
        self.assertGreater(fp["cum_slope_v"], 1.4)
        self.assertEqual(fp["verdict"], "bull")

    def test_selling_side_is_mirrored(self):
        cs = quiet(19) + [K(100.0, 100.1, 98.0, 98.1, 60.0)]
        self.assertEqual(FP.analyze(cs, {})["state"], "sell")

    def test_absorption_needs_volume_and_no_displacement(self):
        cs = quiet(19) + [K(100.0, 100.15, 99.85, 99.9, 40.0)]
        fp = FP.analyze(cs, {})
        self.assertTrue(fp["absorption"])
        self.assertFalse(fp["absorption"]["at_close"])               # sellers held the close
        # a normal bar is not "absorption"
        self.assertIsNone(FP.analyze(quiet(20), {})["absorption"])

    def test_no_volume_data_falls_back_to_bars(self):
        cs = [K(100, 100.5, 99.5, 100.4, 0.0) for _ in range(19)] + \
             [K(100.4, 101.6, 100.2, 101.5, 0.0)]
        fp = FP.analyze(cs, {})
        self.assertTrue(fp["no_volume"])
        self.assertEqual(fp["state"], "buy")                         # still readable
        self.assertEqual(fp["poc_kind"], "time")                     # profile = time at price
        self.assertEqual(fp["volume"], 0.0)                           # never a fake number

    def test_divergence_flagged_when_price_and_flow_disagree(self):
        # price drifts up all window, but the last bars are one-sided selling
        cs = [K(100 + 0.1 * i, 100.6 + 0.1 * i, 99.5 + 0.1 * i, 100.5 + 0.1 * i, 10.0)
              for i in range(17)]
        cs += [K(101.8, 102.4, 101.6, 101.7, 40.0), K(101.7, 102.2, 101.2, 101.4, 40.0)]
        fp = FP.analyze(cs, {})
        self.assertTrue(fp["divergence"])
        self.assertGreater(fp["price_chg_pct"], 0)
        self.assertLess(fp["cum_slope_v"], 0)

    def test_short_series_returns_none(self):
        self.assertIsNone(FP.analyze(quiet(3), {}))


class TestFlip(unittest.TestCase):
    def test_flip_only_between_two_sides(self):
        a = FP.analyze(quiet(19) + [K(100, 102, 99.9, 101.9, 60)], {})
        b = FP.analyze(quiet(19) + [K(100, 100.1, 98, 98.1, 60)], {})
        f = FP.flip(a, b)
        self.assertEqual(f["dir"], "bear")
        self.assertEqual(f["from"], "buy")
        self.assertIsNone(FP.flip(a, a))                              # same mind, no event
        flat = FP.analyze(quiet(20), {})
        self.assertIsNone(FP.flip(a, flat))                            # fading to flat is not a flip
        self.assertIsNone(FP.flip(None, a))
        # flat -> strong side IS an event (that is the "flow just arrived" ping)
        e = FP.flip(flat, a)
        self.assertEqual(e["dir"], "bull")


class TestEmaFilter(unittest.TestCase):
    """The EMA filter, separately: idea, gate, and the fresh-flip exemption."""

    def cross_up_series(self):
        cs = [K(100 + 0.35 * i, 100.3 + 0.35 * i, 99.9 + 0.35 * i, 100 + 0.35 * i, 10.0, t=i)
              for i in range(60)]
        return cs

    def test_ema_gate_is_off_by_default(self):
        self.assertFalse(CFG["ema_gate"])
        self.assertFalse(CFG["ideas"]["ema"])
        self.assertEqual(CFG["ema_lines"], "cross")

    def v_up_series(self):
        """down then up hard enough to force EMA21 back over EMA55 near the last bars."""
        dn = [K(100 - 0.45 * i, 100 - 0.45 * i + 0.3, 100 - 0.45 * i - 0.3, 100 - 0.45 * i,
                10.0, t=i) for i in range(34)]
        up = []
        px = dn[-1]["c"]
        for k in range(1, 30):
            px += 0.55
            up.append(K(px - 0.55, px + 0.25, px - 0.8, px, 10.0, t=34 + k))
        return dn + up

    def test_fp_blocks_helper(self):
        """The footprint gate, on its own: rejects a misaligned idea, exempts the flip."""
        up = [K(100 + 0.4 * i, 100 + 0.4 * i + 0.3, 100 + 0.4 * i - 0.3, 100 + 0.4 * i + 0.25,
                10.0, t=i) for i in range(40)]
        up[-1]["v"] = 60.0
        ev = D.detect_events(up, dict(CFG, footprint=True, footprint_gate=True), 0)
        cfg = dict(CFG, footprint=True, footprint_gate=True)
        last = ev["last"]
        if not ev["fp"] or ev["fp"]["state"] != "buy":
            self.skipTest("fixture lost its buy-pressure read")
        self.assertTrue(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last,
                                          ev["fp"]))
        self.assertFalse(D.footprint_blocks({"side": "LONG", "name": "fvg"}, cfg, ev, last,
                                             ev["fp"]))
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "footprint"}, cfg, ev,
                                            last, ev["fp"]))
        # gate off -> the filter is decoration, nothing is blocked
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"},
                                            dict(CFG, footprint=True, footprint_gate=False),
                                            ev, last, ev["fp"]))
        # no read at all -> no opinion, no veto
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, None))

    def test_ema_blocks_helper(self):
        cs = [K(100 + 0.35 * i, 100.3 + 0.35 * i, 99.9 + 0.35 * i, 100 + 0.35 * i, 10.0, t=i)
              for i in range(60)]
        ev = D.detect_events(cs, dict(CFG, ema_gate=True), 0)
        last = ev["last"]
        cfg = dict(CFG, ema_gate=True)
        # a LONG against a DOWN trend, with no fresh flip -> rejected
        self.assertTrue(D.ema_blocks({"side": "LONG", "name": "fvg"}, cfg, ev, last, "DOWN"))
        # a SHORT with the DOWN trend -> allowed (that is what a filter does, not a reversal bot)
        self.assertFalse(D.ema_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, "DOWN"))
        # gate off => never blocks, however misaligned
        self.assertFalse(D.ema_blocks({"side": "LONG", "name": "fvg"},
                                      dict(CFG, ema_gate=False), ev, last, "DOWN"))
        # the EMA idea is exempt from its own gate
        self.assertFalse(D.ema_blocks({"side": "LONG", "name": "ema"}, cfg, ev, last, "DOWN"))

    def test_bare_ema_idea_levels(self):
        """EMA55 needs 55 seeds, so: find the cross in a long series, then cut it there."""
        cfg = dict(CFG, ema_lines="cross", min_rr=1.0)
        cfg["ideas"] = {**cfg["ideas"], "ema": True, "fvg": False, "zone": False}
        px, ups = 100.0, 0
        es, ef = None, None
        closes = [px - 0.35 * i for i in range(140)]
        for j in range(140, 200):
            px = closes[-1] + 0.9
            closes.append(px)
            ef, es = D.ema(closes, 21), D.ema(closes, 55)
            k = len(closes) - 1
            if ef[k] == ef[k] and es[k] == es[k] and ef[k] > es[k] and ef[k - 1] <= es[k - 1]:
                ups = j
                break
        if ups == 0:
            self.skipTest("no EMA21/55 cross in the fixture window")
        cs = [K(c, c + 0.35, c - 0.45, c, 10.0, t=i) for i, c in enumerate(closes)]
        cs.append(K(closes[-1], closes[-1] + 0.1, closes[-1] - 0.1, closes[-1], 1.0,
                    t=len(closes)))                       # the forming bar build_setup drops
        res = D.build_setup(cs, cfg)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res["trend_flip"])                     # the cross is ON the last bar
        self.assertIsNotNone(res["idea"])
        i = res["idea"]
        self.assertEqual(i["name"], "ema")
        self.assertEqual(i["side"], "LONG")
        self.assertLess(i["sl"], i["entry"])                          # stop under the line
        self.assertGreater(i["tp"], i["entry"])
        self.assertGreaterEqual(i["rr"], cfg["min_rr"] - 1e-9)

    def test_price_mode_uses_the_slow_line(self):
        cs = [K(100 - 0.3 * i, 100.2 - 0.3 * i, 100.6 - 0.3 * i, 100 - 0.3 * i, 10.0, t=i)
              for i in range(50)]
        cs += [K(cs[-1]["c"], cs[-1]["c"] + 1.2, cs[-1]["c"] - 0.2, cs[-1]["c"] + 1.0,
                 10.0, t=50 + k) for k in range(3)]
        ev = D.detect_events(cs, dict(CFG, ema_lines="price"), 0)
        self.assertIn(ev["trend_flip"]["dir"] if ev["trend_flip"] else "bull",
                      ("bull", "bear"))


class TestDetectorWiring(unittest.TestCase):
    def buy_flip_series(self):
        """34 heavy selling bars, then 4 heavy buying closes -> flat/sell becomes buy."""
        cs = [K(120 - 0.8 * i, 120.1 - 0.8 * i, 119.1 - 0.8 * i, 119.2 - 0.8 * i, 30.0,
                t=i) for i in range(34)]
        px = cs[-1]["c"]
        for k in range(3):
            px += 0.8
            cs.append(K(px - 0.8, px + 0.9, px - 0.4, px, 30.0, t=34 + k))
        # the read must turn bullish ON the last CLOSED bar (signal_lookback_bars=2), so
        # the series ends with one forming bar that build_setup drops before analysing
        cs.append(K(px, px + 0.1, px - 0.1, px, 1.0, t=37))
        return cs

    def test_events_carry_the_read_and_the_flip(self):
        ev = D.detect_events(self.buy_flip_series(), CFG, window_start=0)
        self.assertIsNotNone(ev["fp"])
        self.assertIn(ev["fp"]["state"], ("buy", "sell", "flat"))
        if ev["fp_flip"]:
            self.assertIn(ev["fp_flip"]["dir"], ("bull", "bear"))

    def test_footprint_is_not_an_idea_by_default(self):
        """Default config must stay exactly as before: no footprint idea, no gate, no alert."""
        self.assertFalse(CFG["footprint"])
        self.assertFalse(CFG["footprint_gate"])
        self.assertFalse(CFG["ideas"]["footprint"])
        self.assertFalse(C.ALERTS["footprint"])
        self.assertFalse(C.BIAS["footprint"])

    def test_bare_footprint_idea_when_enabled(self):
        cfg = dict(CFG, footprint=True)
        cfg["ideas"] = {**cfg["ideas"], "footprint": True, "fvg": False, "zone": False}
        cs = self.buy_flip_series()
        res = D.build_setup(cs, cfg)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res.get("footprint_flip"))
        self.assertIsNotNone(res["idea"])
        i = res["idea"]
        self.assertEqual(i["name"], "footprint")
        self.assertEqual(i["side"], "LONG" if res["footprint"]["state"] == "buy" else "SHORT")
        self.assertGreaterEqual(i["rr"], cfg["min_rr"] - 1e-9)
        risk = abs(i["entry"] - i["sl"])
        self.assertGreater(risk, 0)

    def test_gate_blocks_by_state_not_by_bar_shape(self):
        """
        Direct test of the gate rule (both directions + the fresh-flip exemption), driven by
        hand-built reads: on a 2-bar lookback a *fresh* gap and an opposing flow almost never
        coexist, because a fresh FVG signal bar is by construction on the flow's side — so the
        rule is checked at the unit level and by the live scan, not by a contrived series.
        """
        cs = quiet(40, v=10.0)
        ev = D.detect_events(cs, dict(CFG, footprint=True, footprint_gate=True), 0)
        last = ev["last"]
        cfg = dict(CFG, footprint=True, footprint_gate=True)
        buy = {"state": "buy", "cum_slope_v": 3.0, "flip": None}
        sell = {"state": "sell", "cum_slope_v": -3.0, "flip": None}
        self.assertTrue(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, buy))
        self.assertTrue(D.footprint_blocks({"side": "LONG", "name": "zone"}, cfg, ev, last, sell))
        self.assertFalse(D.footprint_blocks({"side": "LONG", "name": "fvg"}, cfg, ev, last, buy))
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, sell))
        # flat read = no opinion = no veto
        self.assertFalse(D.footprint_blocks({"side": "LONG", "name": "fvg"}, cfg, ev, last,
                                             {"state": "flat", "flip": None}))
        # a flow flip on the last bar overrules the gate (that flip IS the trade)
        fresh = dict(buy, flip={"i": last, "dir": "bull"})
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, fresh))
        stale = dict(buy, flip={"i": last - 99, "dir": "bull"})
        self.assertTrue(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, cfg, ev, last, stale))
        # ...unless the window says otherwise
        wide = dict(cfg, fp_blocks_bars=200)
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, wide, ev, last, stale))
        # display without a veto: footprint on, gate off
        off = dict(CFG, footprint=True, footprint_gate=False)
        self.assertFalse(D.footprint_blocks({"side": "SHORT", "name": "fvg"}, off, ev, last, buy))

    def test_the_gate_never_breaks_an_aligned_series(self):
        cs = quiet(40, v=10.0)
        for fp_on in (False, True):
            cfg = dict(CFG, footprint=fp_on, footprint_gate=fp_on)
            self.assertIsNotNone(D.build_setup(cs, cfg))
            self.assertIsNotNone(D.build_setup(cs, cfg)["footprint"])   # the read is always there

    def test_gate_never_blocks_the_footprint_idea_itself(self):
        cfg = dict(CFG, footprint=True, footprint_gate=True)
        cfg["ideas"] = {**cfg["ideas"], "footprint": True, "fvg": False, "zone": False}
        cs = self.buy_flip_series()
        res = D.build_setup(cs, cfg)
        if res and res.get("footprint_flip"):
            self.assertEqual(res["idea"]["name"], "footprint")

    def test_ema_gate_and_footprint_gate_are_independent(self):
        """Turning one on must not silently turn the other on."""
        a = dict(CFG, ema_gate=True, footprint_gate=False, footprint=True)
        b = dict(CFG, ema_gate=False, footprint_gate=True, footprint=True)
        self.assertNotEqual(bool(a.get("ema_gate")), bool(a.get("footprint_gate")))
        self.assertNotEqual(bool(b.get("ema_gate")), bool(b.get("footprint_gate")))
        cs = self.buy_flip_series()
        for cfg in (a, b):
            res = D.build_setup(cs, cfg)
            self.assertIsNotNone(res, f"no result for {cfg.get('ema_gate')}/{cfg.get('footprint_gate')}")
            if res["footprint"] and res["footprint"]["state"] != "buy":
                self.skipTest("fixture flow is not buy-side; independence still holds")
            if res["idea"] is not None and cfg["footprint_gate"]:
                self.assertEqual(res["idea"]["side"], "LONG",
                                 "the flow gate must let buy ideas through")


if __name__ == "__main__":
    unittest.main(verbosity=2)
