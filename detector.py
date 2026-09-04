"""
Detector library: indicators + ICT-style setup detection.

Everything here is pure (lists/floats in, dicts out) so it can be unit-tested
without network, Telegram, or matplotlib.
"""
from __future__ import annotations

import math

import footprint as FP

NAN = float("nan")


# --------------------------------------------------------------------- indicators
def ema(values, period):
    out = [NAN] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    k = 2.0 / (period + 1.0)
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def sma(values, period):
    out = [NAN] * len(values)
    if len(values) < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


def rsi(closes, period=14):
    """Wilder RSI (same definition as MT5 iRSI / TradingView)."""
    n = len(closes)
    out = [NAN] * n
    if n < period + 1:
        return out
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gains[i - 1]) / period
        al = (al * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line, histogram)."""
    n = len(closes)
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [NAN if (math.isnan(a) or math.isnan(b)) else a - b for a, b in zip(ef, es)]
    valid = [i for i, v in enumerate(line) if not math.isnan(v)]
    sig = [NAN] * n
    if valid:
        sub = [line[i] for i in valid]
        s = ema(sub, signal)
        for k, i in enumerate(valid):
            sig[i] = s[k]
    hist = [NAN if (math.isnan(a) or math.isnan(b)) else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def atr(highs, lows, closes, period=14):
    n = len(closes)
    out = [NAN] * n
    if n < period + 1:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1])))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


# --------------------------------------------------------------------- structure
def swings(highs, lows, left=3, right=3):
    """Fractal swing points. Returns list of dicts: {i, price, kind}."""
    out = []
    n = len(highs)
    for i in range(left, n - right):
        hi, lo = highs[i], lows[i]
        if all(highs[j] < hi for j in range(i - left, i)) and \
           all(highs[j] < hi for j in range(i + 1, i + right + 1)):
            out.append({"i": i, "price": hi, "kind": "high"})
        if all(lows[j] > lo for j in range(i - left, i)) and \
           all(lows[j] > lo for j in range(i + 1, i + right + 1)):
            out.append({"i": i, "price": lo, "kind": "low"})
    return out


def fvg_list(highs, lows):
    """3-candle Fair Value Gaps. Bullish: low[i] > high[i-2]; bearish: high[i] < low[i-2]."""
    out = []
    for i in range(2, len(highs)):
        if lows[i] > highs[i - 2]:
            out.append({"i": i, "dir": "bull", "top": lows[i], "bottom": highs[i - 2]})
        elif highs[i] < lows[i - 2]:
            out.append({"i": i, "dir": "bear", "top": lows[i - 2], "bottom": highs[i]})
    return out


def fvg_state(fv, closes, highs, lows, filled_tol=0.05, max_scan=None):
    """How much of a gap has been traded back. Returns (fill_fraction, mitigated_index|None)."""
    gap = fv["top"] - fv["bottom"]
    if gap <= 0:
        return 1.0, None
    worst, mit = 0.0, None
    end = len(closes) if not max_scan else min(len(closes), fv["i"] + 1 + max_scan)
    for i in range(fv["i"] + 1, end):
        if fv["dir"] == "bull":
            touched = max(0.0, fv["top"] - lows[i]) / gap
        else:
            touched = max(0.0, highs[i] - fv["bottom"]) / gap
        if touched > worst:
            worst = touched
        if worst >= 1.0 - filled_tol and mit is None:
            mit = i
    return min(1.0, worst), mit


def sweeps(highs, lows, closes, swing_pts, lookback=40, wick_min_frac=0.25):
    """
    Liquidity sweeps: a wick pokes beyond a prior swing level but the candle CLOSES
    back on the safe side (a stop hunt). Returns [{i, level, kind}].
    """
    out = []
    n = len(closes)
    sw_high = [p for p in swing_pts if p["kind"] == "high"]
    sw_low = [p for p in swing_pts if p["kind"] == "low"]
    for i in range(3, n):
        rng = highs[i] - lows[i]
        if rng <= 0:
            continue
        prev_hi = [p["price"] for p in sw_high if i - lookback <= p["i"] <= i - 3]
        prev_lo = [p["price"] for p in sw_low if i - lookback <= p["i"] <= i - 3]
        if prev_hi:
            lvl = max(prev_hi)
            upper_body = max(closes[i], closes[i - 1] if i else closes[i])
            if highs[i] > lvl and closes[i] < lvl and (highs[i] - upper_body) / rng >= wick_min_frac:
                out.append({"i": i, "level": lvl, "kind": "buy_side_swept"})
        if prev_lo:
            lvl = min(prev_lo)
            lower_body = min(closes[i], closes[i - 1] if i else closes[i])
            if lows[i] < lvl and closes[i] > lvl and (lower_body - lows[i]) / rng >= wick_min_frac:
                out.append({"i": i, "level": lvl, "kind": "sell_side_swept"})
    return out


def opens_proxy(closes, i):
    return closes[i - 1] if i > 0 else closes[i]


def bos(closes, swing_pts, left=3, right=3):
    """
    Break of structure / change of character.
    A CLOSE beyond the most recent opposite swing creates a structure event.
    Returns list of {i, kind: 'bullish'|'bearish', level, prev_dir}.
    """
    out = []
    if not swing_pts:
        return out
    trend = 0
    ordered = sorted(swing_pts, key=lambda p: p["i"])
    last_high = None
    last_low = None
    for p in ordered:
        if p["kind"] == "high":
            last_high = p
        else:
            last_low = p
    # walk bars, tracking the most recent confirmed swing levels
    idx_high = {}
    idx_low = {}
    for p in ordered:
        idx_high.setdefault(p["i"], p["price"])
        idx_low.setdefault(p["i"], p["price"])
    cur_h = cur_l = None
    for i in range(len(closes)):
        if i in idx_high:
            cur_h = idx_high[i]
        if i in idx_low:
            cur_l = idx_low[i]
        if cur_h is None or cur_l is None:
            continue
        if closes[i] > cur_h and trend <= 0:
            out.append({"i": i, "kind": "bullish", "level": cur_h,
                        "prev_dir": "down" if trend < 0 else "range",
                        "is_choch": trend < 0})
            trend = 1
        elif closes[i] < cur_l and trend >= 0:
            out.append({"i": i, "kind": "bearish", "level": cur_l,
                        "prev_dir": "up" if trend > 0 else "range",
                        "is_choch": trend > 0})
            trend = -1
    return out


def last_ob(candles, after_i, direction, max_back=12):
    """
    Order block right before the impulse: for a bullish leg, the last down candle;
    for a bearish leg, the last up candle. candles = list of dicts with o/h/l/c.
    Returns {top, bottom, i} or None.
    """
    rng = range(min(after_i, len(candles) - 1), max(0, after_i - max_back) - 1, -1)
    for i in rng:
        k = candles[i]
        body_down = k["c"] < k["o"]
        body_up = k["c"] > k["o"]
        if direction == "bull" and body_down:
            return {"i": i, "top": k["h"], "bottom": k["l"]}
        if direction == "bear" and body_up:
            return {"i": i, "top": k["h"], "bottom": k["l"]}
    return None


# --------------------------------------------------------------------- setup
IDEA_LABELS = {
    "fvg": "FVG (gap)",
    "zone": "demand/supply zone",
    "sweep": "liquidity sweep",
    "bos": "BOS/CHoCH",
    "ema": "EMA trend",
    "footprint": "footprint flow",
}


def detect_events(candles, cfg, window_start=0):
    """
    Shared, cheap pass over the closed candles: every raw structure event that is still
    fresh. `window_start` is how many bars were dropped by the analysis-window cap, so
    event indices reported here are absolute (match the caller's own bar list).
    """
    data = candles
    n = len(data)
    last = n - 1
    o = [c["o"] for c in data]
    h = [c["h"] for c in data]
    l = [c["l"] for c in data]
    cl = [c["c"] for c in data]
    sw = swings(h, l, cfg["swing_left"], cfg["swing_right"])
    fresh = lambda i: last - i <= cfg["signal_lookback_bars"]      # noqa: E731
    recent_bos = [b for b in bos(cl, sw) if fresh(b["i"])]
    recent_sweep = [s for s in sweeps(h, l, cl, sw, lookback=cfg["sweep_lookback"])
                    if fresh(s["i"])]
    # the swept level is the swing price; the actual liquidity sits on the sweep candle's
    # wick, so anchor stops on whichever is further out (old combo used the wick too)
    for s in recent_sweep:
        s["extreme"] = h[s["i"]] if s["kind"] == "buy_side_swept" else l[s["i"]]
    open_gaps = []
    for g in fvg_list(h, l):
        if not fresh(g["i"]):
            continue
        fill, mit = fvg_state(g, cl, h, l, cfg["fvg_fill_tol"],
                              max_scan=cfg["signal_lookback_bars"] + 2)
        if mit is None:
            g = dict(g)
            g["fill"] = fill
            g["mid"] = (g["top"] + g["bottom"]) / 2.0
            g["abs_i"] = g["i"] + window_start
            open_gaps.append(g)
    for b in recent_bos:
        b["abs_i"] = b["i"] + window_start
    for s in recent_sweep:
        s["abs_i"] = s["i"] + window_start
    bull_gap = next((g for g in open_gaps if g["dir"] == "bull" and g["top"] <= cl[last] * 1.002), None)
    bear_gap = next((g for g in open_gaps if g["dir"] == "bear" and g["bottom"] >= cl[last] * 0.998), None)
    bull_bos = next((b for b in recent_bos if b["kind"] == "bullish"), None)
    bear_bos = next((b for b in recent_bos if b["kind"] == "bearish"), None)
    sellside_swept = next((s for s in reversed(recent_sweep) if s["kind"] == "sell_side_swept"), None)
    buyside_swept = next((s for s in reversed(recent_sweep) if s["kind"] == "buy_side_swept"), None)
    ob_bull = last_ob(data, bull_bos["i"] - window_start, "bull") if bull_bos else None
    ob_bear = last_ob(data, bear_bos["i"] - window_start, "bear") if bear_bos else None
    for ob in (ob_bull, ob_bear):
        if ob:
            ob["abs_i"] = ob["i"] + window_start
    ef, es = ema(cl, cfg["ema_fast"]), ema(cl, cfg["ema_slow"])
    # the EMA filter's own event: fast/slow cross, or the close crossing the slow line
    if cfg.get("ema_lines", "cross") == "price":
        flip = _cross(cl, es, last, cfg["signal_lookback_bars"])
    else:
        flip = _cross(ef, es, last, cfg["signal_lookback_bars"])
    # the footprint read: delta / cumulative delta / absorption / volume profile, plus the
    # bar it changed its mind (that flip is what the standalone alert keys on)
    fp = FP.analyze(data, cfg)
    fp_prev = FP.analyze(data[:-1], cfg) if n > 1 else None
    fp_flip = FP.flip(fp_prev, fp)
    if fp_flip and last - fp_flip["i"] > cfg.get("signal_lookback_bars", 2):
        fp_flip = None
    # measured: "trade every flip" was noise (674 signals / 45d on gold 4h, 30-33% win),
    # so a flip only counts as a signal with an edge + a divergence/absorption behind it
    if fp_flip and not FP.is_signal(fp, cfg):
        fp_flip = None
    if fp:
        fp["flip"] = fp_flip
    return {"data": data, "n": n, "last": last, "o": o, "h": h, "l": l, "cl": cl,
            "ema_fast": ef, "ema_slow": es, "sw": sw, "trend_flip": flip,
            "fp": fp, "fp_flip": fp_flip,
            "atr": atr(h, l, cl, cfg["atr_period"])[last],
            "recent_bos": recent_bos, "recent_sweep": recent_sweep, "open_gaps": open_gaps,
            "bull_gap": bull_gap, "bear_gap": bear_gap, "bull_bos": bull_bos, "bear_bos": bear_bos,
            "sellside_swept": sellside_swept, "buyside_swept": buyside_swept,
            "ob_bull": ob_bull, "ob_bear": ob_bear}


def trade_idea(ev, cfg, side, zone=None, gap=None, sweep=None, bos_ev=None,
               floor=None, ceil=None, tp_at_liquidity=False, combo=False):
    """
    ONE idea -> ONE trade plan. Each branch is a standalone setup with its own
    entry / stop / target, so it can be judged on its own (mode "solo").

      gap   -> entry at the gap mid (or at price, if price already sat inside), stop outside the gap
      zone  -> entry at the near half of the OB, stop just outside the zone
      sweep -> entry at the sweep close, stop beyond the swept wick
      bos   -> entry at the breaking close, stop at the last extreme (floor/ceil)

    Target = the liquidity resting on the other side, never closer than min_rr.
    Returns None when the level geometry is broken or the reward is too small.

    combo=True keeps the historic (pre-split) risk model: the stop is the swept level
    widened by the 25-bar extreme, so combo alerts behave exactly as before the split.
    """
    last, a = ev["last"], ev["atr"]
    px, long_ = ev["cl"][last], side == "LONG"
    slb = cfg.get("solo_sl_buffer", 0.5 * cfg["sl_atr"])
    lo25 = min(ev["l"][max(0, last - 25):last + 1])
    hi25 = max(ev["h"][max(0, last - 25):last + 1])
    # `floor`/`ceil` stay None unless the caller anchored them on a swept level; a None
    # anchor means "use the 25-bar extreme" (which is also what combo did before the split)
    want_px = cfg.get("solo_entry", "retest") == "market"
    if long_:
        if combo and cfg.get("combo_legacy_levels", False):
            anchor = gap or zone or {"bottom": (zone or {}).get("bottom", px),
                                     "top": (zone or {}).get("top", px)}
            entry = max(anchor["bottom"], px - 0.65 * a) if gap is None else (anchor["top"] + anchor["bottom"]) / 2.0
            entry = min(entry, px) if gap is None else entry
            stop = (lo25 if floor is None else min(floor, lo25)) - cfg["sl_atr"] * a
        elif gap is not None:
            # "retest": wait for price to come back into the gap; "market": take the close now
            entry = px if want_px else (gap["mid"] if px >= gap["top"] else px)
            stop = gap["bottom"] - slb * a
        elif zone is not None:
            entry = px if want_px else ((zone["top"] + zone["bottom"]) / 2.0 if px <= zone["top"] else px)
            stop = zone["bottom"] - slb * a
        elif sweep is not None:
            entry = px if want_px else ev["cl"][sweep["i"]]
            stop = min(ev["l"][sweep["i"]], floor or lo25) - 0.25 * slb * a
        elif bos_ev is not None:
            entry = px if want_px else ev["cl"][bos_ev["i"]]
            stop = (floor or lo25) - 0.25 * slb * a
        elif gap is None and zone is None and sweep is None and bos_ev is None and \
                ev.get("fp_flip") and (ev["fp"]["state"] if ev.get("fp") else "flat") == "buy":
            # the footprint idea on its own: take the close after a buy-pressure flip, stop
            # under the window low (that is where the absorbed sellers actually were)
            entry = px
            lo_w = min(ev["l"][max(0, last - int(cfg.get("fp_window", 20))):last + 1])
            stop = lo_w - 0.75 * slb * a
        elif gap is None and zone is None and sweep is None:
            # the EMA idea on its own: buy the retest of the line, stop under the line
            line = _ema_line(ev, cfg, long_)
            entry = max(px, line)
            stop = min(line, px) - 0.5 * slb * a
        else:
            return None
        if combo and not cfg.get("combo_legacy_levels", False):
            stop = (lo25 if floor is None else min(floor, lo25)) - cfg["sl_atr"] * a
        other = max((s["level"] for s in ev["recent_sweep"] if s["kind"] == "buy_side_swept"),
                    default=None)
        tgt = other if other and other > px else max(ev["h"][max(0, last - 40):last + 1])
        tp = tgt if (tp_at_liquidity and tgt > px) else max(
            tgt, entry + cfg["min_rr"] * (entry - stop))
    else:
        if combo and cfg.get("combo_legacy_levels", False):
            anchor = gap or zone or {"bottom": (zone or {}).get("bottom", px),
                                     "top": (zone or {}).get("top", px)}
            entry = min(anchor["top"], px + 0.65 * a) if gap is None else (anchor["top"] + anchor["bottom"]) / 2.0
            entry = max(entry, px) if gap is None else entry
            stop = (hi25 if ceil is None else ceil) + cfg["sl_atr"] * a
        elif gap is not None:
            entry = px if want_px else (gap["mid"] if px <= gap["bottom"] else px)
            stop = gap["top"] + slb * a
        elif zone is not None:
            entry = px if want_px else ((zone["top"] + zone["bottom"]) / 2.0 if px >= zone["bottom"] else px)
            stop = zone["top"] + slb * a
        elif sweep is not None:
            entry = px if want_px else ev["cl"][sweep["i"]]
            stop = max(ev["h"][sweep["i"]], ceil or hi25) + 0.25 * slb * a
        elif bos_ev is not None:
            entry = px if want_px else ev["cl"][bos_ev["i"]]
            stop = (ceil or hi25) + 0.25 * slb * a
        elif gap is None and zone is None and sweep is None and bos_ev is None and \
                ev.get("fp_flip") and (ev["fp"]["state"] if ev.get("fp") else "flat") == "sell":
            # the footprint idea on its own: take the close after a sell-pressure flip
            entry = px
            hi_w = max(ev["h"][max(0, last - int(cfg.get("fp_window", 20))):last + 1])
            stop = hi_w + 0.75 * slb * a
        elif gap is None and zone is None and sweep is None:
            # the EMA idea on its own: sell the retest of the line, stop above the line
            line = _ema_line(ev, cfg, long_)
            entry = min(px, line)
            stop = max(line, px) + 0.5 * slb * a
        else:
            return None
        if combo and not cfg.get("combo_legacy_levels", False):
            stop = (ceil if ceil is not None else hi25) + cfg["sl_atr"] * a
        other = min((s["level"] for s in ev["recent_sweep"] if s["kind"] == "sell_side_swept"),
                    default=None)
        tgt = other if other and other < px else min(ev["l"][max(0, last - 40):last + 1])
        tp = tgt if (tp_at_liquidity and tgt < px) else min(
            tgt, entry - cfg["min_rr"] * (stop - entry))
    # a limit entry that sits far from the market is a stale idea, not a trade
    moat = cfg.get("max_entry_offset_atr", 0.0)
    if moat and abs(entry - px) > moat * a:
        return None
    risk = abs(entry - stop)
    if not risk > 0 or not (tp - entry) * (1.0 if long_ else -1.0) > 0:
        return None
    rr = abs(tp - entry) / risk
    if rr < cfg["min_rr"] - 1e-9:
        return None
    return {"side": side, "entry": entry, "sl": stop, "tp": tp, "rr": rr, "risk": risk,
            "last_close": px}


def _enrich(raw, key, i_bar, label, confluence):
    out = dict(raw)
    out.update({"name": key, "label": label, "i": i_bar, "confluence": confluence})
    return out


def ema_blocks(idea, cfg, ev, last, trend):
    """
    The EMA filter, on its own: True = reject this idea because it fights the EMA trend.
    A flip inside `ema_gate_bars` is treated as fresh and allowed (that IS the signal).
    """
    if not cfg.get("ema_gate", False) or not idea:
        return False
    if idea.get("name") == "ema":
        return False
    aligned = (idea["side"] == "LONG") == (trend == "UP")
    if aligned:
        return False
    f = ev.get("trend_flip")
    return not (f and last - f["i"] <= cfg.get("ema_gate_bars", 12))


def footprint_blocks(idea, cfg, ev, last, fp):
    """
    The footprint filter on its own: True = reject this idea because the tape flow in the
    last `fp_window` bars is against it. A flow flip inside `fp_blocks_bars` overrules that
    (the flip is the trade, not a reason to skip it).
    """
    if not cfg.get("footprint", False) or not cfg.get("footprint_gate", False) or not idea:
        return False
    if idea.get("name") == "footprint":
        return False
    if not fp or fp.get("state", "flat") == "flat":
        return False
    aligned = (idea["side"] == "LONG") == (fp["state"] == "buy")
    if aligned:
        return False
    f = fp.get("flip")
    return not (f and last - f["i"] <= int(cfg.get("fp_blocks_bars", 12)))


def _ema_line(ev, cfg, long_):
    """The level the EMA idea defends: the slow line, or the nearer of fast/slow."""
    last = ev["last"]
    if cfg.get("ema_lines", "cross") == "price":
        return ev["ema_slow"][last]
    vals = [v for v in (ev["ema_fast"][last], ev["ema_slow"][last]) if v == v]
    return (min if long_ else max)(vals) if vals else ev["cl"][last]


def ideas_from(ev, cfg, mode="solo"):
    """Enabled setups, each one separate. solo -> config order; combo -> confluence first."""
    want = cfg.get("ideas", {}) or {}
    out = []
    for side in ("LONG", "SHORT"):
        long_ = side == "LONG"
        gap = ev["bull_gap"] if long_ else ev["bear_gap"]
        zone = ev["ob_bull"] if long_ else ev["ob_bear"]
        swept = ev["sellside_swept"] if long_ else ev["buyside_swept"]
        bos_ev = ev["bull_bos"] if long_ else ev["bear_bos"]
        conf = sum(1 for x in (gap, zone, swept, bos_ev) if x)
        fl = ev.get("trend_flip")
        ema_ev = None
        if fl and (fl["dir"] == "bull") == long_:
            ema_ev = {"i": fl["i"], "abs_i": fl["i"] + ev.get("window_start", 0)}
        fp_ev = None
        fpf = ev.get("fp_flip")
        if fpf and (fpf["dir"] == "bull") == long_:
            fp_ev = {"i": fpf["i"], "abs_i": fpf["i"] + ev.get("window_start", 0)}
        for key, raw_ev in (("fvg", gap), ("zone", zone), ("sweep", swept), ("bos", bos_ev),
                            ("ema", ema_ev), ("footprint", fp_ev)):
            if raw_ev is None:
                continue
            # the sweep idea anchors its stop on the swept level (same as the combo rule);
            # the bare BOS idea keeps the 25-bar extreme, otherwise the stop gets absurd
            floor = (min(s["level"] for s in ev["recent_sweep"] if s["kind"] == "sell_side_swept")
                     if (long_ and swept and key == "sweep") else None)
            ceil_ = (max(s["level"] for s in ev["recent_sweep"] if s["kind"] == "buy_side_swept")
                     if (not long_ and swept and key == "sweep") else None)
            if mode != "combo":
                w = want.get(key, True)
                if w is False or (isinstance(w, dict) and not w.get("enabled", True)):
                    continue
            raw = trade_idea(ev, cfg, side, zone=zone, gap=gap, sweep=swept, bos_ev=bos_ev,
                             floor=floor, ceil=ceil_,
                             tp_at_liquidity=bool(cfg.get("target_at_liquidity", False)),
                             combo=(mode == "combo"))
            if raw is None:
                continue
            out.append(_enrich(raw, key, raw_ev.get("abs_i", raw_ev["i"]), IDEA_LABELS[key], conf))
    if mode == "combo":
        out.sort(key=lambda d: (-d["confluence"], -d["rr"]))
    else:
        order = cfg.get("idea_order") or list(IDEA_LABELS)
        out.sort(key=lambda d: (order.index(d["name"]) if d["name"] in order else 9, -d["rr"]))
    return out


def build_setup(candles, cfg):
    """
    candles: list of dicts {t,o,h,l,c,v} (chronological). Only CLOSED bars are used
    (the last element is assumed still forming and is excluded).
    Returns a dict describing conditions + a trade idea, or None if nothing to say.

    CONFIG["mode"] decides how setups become a trade:
      "solo"  -> each enabled setup (FVG / zone / sweep / BOS) is its own idea, with its
                 own SL and TP; no confluence required
      "combo" -> the old behaviour: >= min_conditions of the four tags must agree
    """
    n_all = len(candles)
    if n_all < cfg["min_bars"]:
        return None
    # cap the analysis window: everything we look at lives in the last few dozen bars, and
    # replaying history bar-by-bar (cadence probe / backtests) stays linear instead of O(n^2)
    win = cfg.get("analysis_bars")
    if win and n_all > win:
        base = n_all - win
        candles = candles[base:]
        n_all = len(candles)
    else:
        base = 0
    data = candles[: n_all - 1] if cfg.get("drop_forming", True) else candles
    n = len(data)
    if n < cfg["min_bars"]:
        return None
    ev = detect_events(data, cfg, window_start=base)
    ev["window_start"] = base
    ev["abs_n"] = n_all + base          # bar count of the FULL input series (not the window)
    a = ev["atr"]
    if math.isnan(a) or a <= 0:
        return None
    cl, last = ev["cl"], ev["last"]
    r = rsi(cl, cfg["rsi_period"])[last]
    line, sigl, hist = macd(cl, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    e_f = ema(cl, cfg["ema_fast"])[last]
    e_s = ema(cl, cfg["ema_slow"])[last]

    conds = {
        "bull": [(ev["bull_gap"], "FVG"), (ev["ob_bull"], "demand zone"),
                 (ev["sellside_swept"], "sell-side swept"), (ev["bull_bos"], "BOS/CHoCH up")],
        "bear": [(ev["bear_gap"], "FVG"), (ev["ob_bear"], "supply zone"),
                 (ev["buyside_swept"], "buy-side swept"), (ev["bear_bos"], "BOS/CHoCH down")],
    }
    score = {k: sum(1 for x, _ in v if x) for k, v in conds.items()}
    tags = {k: [name for x, name in v if x] for k, v in conds.items()}
    mode = cfg.get("mode", "combo")
    idea, side, gated = None, None, False

    if mode == "combo":
        if cfg.get("require_sweep", False):
            if ev["sellside_swept"] is None:
                score["bull"] = 0
            if ev["buyside_swept"] is None:
                score["bear"] = 0
        if score["bull"] >= cfg["min_conditions"] and score["bull"] >= score["bear"]:
            side = "LONG"
        elif score["bear"] >= cfg["min_conditions"]:
            side = "SHORT"
        if side and (cfg.get("require_trend_align", False) or cfg.get("ema_gate", False)) and \
           ((side == "LONG" and e_f < e_s) or (side == "SHORT" and e_f > e_s)):
            gated, side = True, None
        if side and cfg.get("footprint", False) and cfg.get("footprint_gate", False) and \
           ((side == "LONG" and ev["fp"] and ev["fp"]["state"] == "sell") or
            (side == "SHORT" and ev["fp"] and ev["fp"]["state"] == "buy")):
            gated, side = True, None
        if side:
            long_ = side == "LONG"
            # combo rule (unchanged from before the split): stop on the swept extreme,
            # widened by the 25-bar low/high when nothing was swept
            floor = ev["sellside_swept"]["level"] if (long_ and ev["sellside_swept"]) else None
            ceil_ = ev["buyside_swept"]["level"] if ((not long_) and ev["buyside_swept"]) else None
            raw = trade_idea(ev, cfg, side, zone=ev["ob_bull"] if long_ else ev["ob_bear"],
                             gap=ev["bull_gap"] if long_ else ev["bear_gap"],
                             sweep=ev["sellside_swept"] if long_ else ev["buyside_swept"],
                             bos_ev=ev["bull_bos"] if long_ else ev["bear_bos"],
                             floor=floor, ceil=ceil_,
                             tp_at_liquidity=bool(cfg.get("target_at_liquidity", False)),
                             combo=True)
            if raw is not None:
                idea = _enrich(raw, "combo", last,
                               " + ".join(tags["bull"] if long_ else tags["bear"]),
                               score["bull"] if long_ else score["bear"])
    else:
        for cand in ideas_from(ev, cfg, "solo"):
            w = (cfg.get("ideas", {}) or {}).get(cand["name"], True)
            gate_on = w.get("require_trend_align", cfg.get("require_trend_align", False)) \
                if isinstance(w, dict) else cfg.get("require_trend_align", False)
            if (gate_on and ((cand["side"] == "LONG" and e_f < e_s) or
                             (cand["side"] == "SHORT" and e_f > e_s))) or \
               ema_blocks(cand, cfg, ev, last, "UP" if e_f > e_s else "DOWN") or \
               footprint_blocks(cand, cfg, ev, last, ev.get("fp")):
                gated = True
                continue
            idea = cand
            break

    return {
        "price": cl[last], "atr": a, "rsi": r,
        "macd": line[last], "macd_sig": sigl[last], "macd_hist": hist[last],
        "macd_cross": _cross(line, sigl, last, cfg["signal_lookback_bars"]),
        "trend_flip": ev.get("trend_flip"), "ema_fast_v": e_f, "ema_slow_v": e_s,
        "footprint": ev.get("fp"), "footprint_flip": ev.get("fp_flip"),
        "rsi_cross": _rsi_cross(cl, cfg),
        "trend": "UP" if (e_f > e_s) else "DOWN",
        "score": score, "tags": tags, "idea": idea, "gated": gated,
        "mode": mode, "all_ideas": ideas_from(ev, cfg, mode) if mode == "combo" else [idea] if idea else [],
        "bull_gap": ev["bull_gap"], "bear_gap": ev["bear_gap"],
        "ob_bull": ev["ob_bull"], "ob_bear": ev["ob_bear"],
        "recent_sweep": ev["recent_sweep"], "recent_bos": ev["recent_bos"],
        "open_gaps": ev["open_gaps"], "swings": ev["sw"], "n": n,
        "window_start": base, "abs_n": ev["abs_n"],
    }



def _cross(a, b, last, lookback):
    for i in range(last, max(0, last - lookback) - 1, -1):
        if i < 1 or any(math.isnan(x) for x in (a[i], b[i], a[i - 1], b[i - 1])):
            continue
        if a[i] > b[i] and a[i - 1] <= b[i - 1]:
            return {"i": i, "dir": "bull"}
        if a[i] < b[i] and a[i - 1] >= b[i - 1]:
            return {"i": i, "dir": "bear"}
    return None


def _rsi_cross(cl, cfg):
    series = rsi(cl, cfg["rsi_period"])
    last = len(series) - 1
    for i in range(last, max(0, last - cfg["signal_lookback_bars"]) - 1, -1):
        if i < 1 or math.isnan(series[i]) or math.isnan(series[i - 1]):
            continue
        if series[i] > cfg["rsi_hi"] >= series[i - 1]:
            return {"i": i, "dir": "overbought", "value": series[i]}
        if series[i] < cfg["rsi_lo"] <= series[i - 1]:
            return {"i": i, "dir": "oversold", "value": series[i]}
    return {"dir": "overbought" if series[last] > cfg["rsi_hi"] else
                    ("oversold" if series[last] < cfg["rsi_lo"] else "neutral"),
            "value": series[last], "i": None}
