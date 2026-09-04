"""
Footprint read — a free, honest stand-in for a real order-flow footprint.

A real footprint needs every executed trade with its side (bid/ask). That data is not
free for FX/CFD, and this bot runs on free Yahoo candles. So this module rebuilds what a
footprint *tells you* out of OHLCV alone:

  delta            who controlled the bar (close location inside the range × volume)
  cDelta           who is controlling the last few bars, and whether price agrees with it
  volume profile   where the activity actually happened (POC) and where it is thin (LVN)
  absorption       heavy volume, no displacement — the passive side ate the aggressors

That is a *proxy*, and every message built from it says so. What is deliberately NOT
here: true bid/ask footprint, unfinished auctions at the open, large-trade print counts.
"""
from __future__ import annotations

import math

NAN = float("nan")


def bar_delta(o, h, l, c):
    """
    Signed share of the bar's activity: +1 all buyers, -1 all sellers, 0 on a doji.
    (close - open) / range is the classic accumulation proxy, and the open (not the bar
    midpoint) is the reference a footprint actually builds delta from.
    """
    rng = h - l
    if not rng > 0:
        return 0.0
    return max(-1.0, min(1.0, 2.0 * (c - o) / rng))


def delta_series(candles):
    return [bar_delta(c["o"], c["h"], c["l"], c["c"]) * float(c.get("v") or 0.0)
            for c in candles]


def analyze(candles, cfg):
    """
    candles: chronological dicts {t,o,h,l,c,v}; the LAST one may be forming.
    cfg keys (all optional): fp_window 20, fp_absorb_vol 1.6, fp_absorb_body 0.4,
    fp_strength 1.4, fp_bins 5. Returns None when there is not enough data.

    FX/CFD feeds (Yahoo, Twelve Data free) report no volume at all. In that case every
    bar is counted as one and `no_volume` is set: the read stays a *delta-of-bars* read
    and the caption says so — the profile becomes a time-at-price profile.
    """
    n = len(candles)
    cfg = cfg or {}
    w = int(cfg.get("fp_window", 20))
    if n < max(6, w // 2):
        return None
    win = candles[max(0, n - w):]
    last = n - 1
    cl = [c["c"] for c in candles]
    raw_v = [float(c.get("v") or 0.0) for c in win]
    no_vol = sum(1 for x in raw_v if x > 0) < max(3, len(raw_v) // 3)
    vol = [1.0 if no_vol else (x if x > 0 else 0.0) for x in raw_v]
    dl = [bar_delta(c["o"], c["h"], c["l"], c["c"]) for c in win]
    ds = [d * v for d, v in zip(dl, vol)]
    tot = sum(vol)

    out = {"bars": len(win), "i": last, "no_volume": no_vol}
    out["delta_pct"] = (100.0 * sum(ds) / tot) if tot > 0 else 0.0   # % of window activity
    out["volume"] = tot if not no_vol else 0.0

    # cumulative delta over the window, normalised by average per-bar activity
    cum, run = [], 0.0
    for d in ds:
        run += d
        cum.append(run)
    avg_v = tot / len(vol) if vol else 0.0
    out["cum_delta_v"] = (cum[-1] / avg_v) if avg_v > 0 else 0.0
    # "flow right now" = delta over the last 3 bars, not the whole window: a footprint
    # read that lags 20 bars is a trend indicator, and we already have one of those
    n_t = min(3, len(ds))
    out["cum_slope_v"] = (sum(ds[-n_t:]) / avg_v) if avg_v > 0 else 0.0

    out["price_chg_pct"] = 100.0 * (cl[last] / win[0]["c"] - 1.0) if win[0]["c"] else 0.0
    pr_sign = 1 if out["price_chg_pct"] > 0 else (-1 if out["price_chg_pct"] < 0 else 0)
    dl_sign = 1 if out["cum_slope_v"] > 0 else (-1 if out["cum_slope_v"] < 0 else 0)
    out["divergence"] = bool(pr_sign and dl_sign and pr_sign != dl_sign)
    # where in the window we are: a flow signal is only interesting at an edge, not
    # mid-range (this is also what makes a footprint read different from a momentum osc)
    lo_w, hi_w = min(c["l"] for c in win), max(c["h"] for c in win)
    span = (hi_w - lo_w) if hi_w > lo_w else 0.0
    out["range_span"] = span
    pos = ((cl[last] - lo_w) / span) if span > 0 else 0.5
    out["range_pos"] = pos
    edge = float(cfg.get("fp_edge", 0.72))
    out["at_high"] = span > 0 and pos >= edge
    out["at_low"] = span > 0 and pos <= 1.0 - edge

    # ---- absorption: heavy activity, no displacement -----------------------------
    med = _median(vol) or 0.0
    rngs = [c["h"] - c["l"] for c in win]
    med_rng = _median([r for r in rngs if r > 0]) or 0.0
    med_body = _median([abs(c["c"] - c["o"]) for c in win]) or 0.0
    thr_v = cfg.get("fp_absorb_vol", 1.6) * med
    thr_b = cfg.get("fp_absorb_body", 0.4)
    absorbed = []
    # with no size reference at all (flat synthetic series) absorption is meaningless
    if thr_v > 0 and med_body > 0 and med_rng > 0:
        for k, c in enumerate(win):
            small = (abs(c["c"] - c["o"]) <= thr_b * med_body) or \
                    ((c["h"] - c["l"]) <= thr_b * med_rng)
            if vol[k] >= thr_v and small:
                absorbed.append({"i": last - (len(win) - 1 - k),
                                 "at_close": c["c"] >= (c["h"] + c["l"]) / 2.0,
                                 "level": (c["h"] + c["l"]) / 2.0})
    out["absorbed"] = absorbed
    out["absorption"] = absorbed[-1] if absorbed else None

    # ---- profile (volume-at-price, or time-at-price when there is no volume) -----
    lo = lo_w
    hi = hi_w
    bins = int(cfg.get("fp_bins", 5))
    out["range"] = [lo, hi]
    if hi > lo:
        step = (hi - lo) / bins
        counts = [0.0] * bins
        for k, c in enumerate(win):
            j = max(0, min(bins - 1, int((c["c"] - lo) / step)))
            counts[j] += vol[k] if not no_vol else 1.0
        tot_p = sum(counts) or 1.0
        shares = [v / tot_p for v in counts]
        poc = max(range(bins), key=lambda j: counts[j])
        out["poc"] = lo + step * (poc + 0.5)
        out["poc_share"] = shares[poc]
        out["poc_kind"] = "time" if no_vol else "volume"
        out["lvn"] = [lo + step * (j + 0.5) for j, v in enumerate(shares)
                      if v < (1.0 / bins) * 0.5]
        out["hvns"] = [lo + step * (j + 0.5) for j, v in enumerate(shares)
                       if v > (1.0 / bins) * 1.75]
    else:
        out["poc"] = out["poc_share"] = NAN
        out["poc_kind"] = "none"
        out["lvn"] = out["hvns"] = []

    # ---- verdict -----------------------------------------------------------------
    strong = float(cfg.get("fp_strength", 1.4))
    s = out["cum_slope_v"]
    state = "buy" if s >= strong else ("sell" if s <= -strong else "flat")
    if state == "flat" and out["absorption"]:
        # absorption alone does not make a side — it only explains one. Kept as a note so
        # an alert can say "heavy volume, nothing moved" without inventing a direction.
        out["note"] = "absorption"
    out["state"] = state
    out["verdict"] = {"buy": "bull", "sell": "bear"}.get(state, "flat")
    out["delta_now"] = ds[-1] if ds else 0.0
    return out


def flip(prev, cur):
    """
    Did the footprint read change its mind? prev/cur are `analyze()` results (either may be
    None). Returns {dir, i} like the other event dicts, or None.

    Two moves count: a side *flipping* (buy -> sell) and a side *appearing* out of balance
    (flat -> buy). Flat -> flat and side -> flat are deliberately not events, so the noise
    around the threshold never turns into an alert.
    """
    if not prev or not cur:
        return None
    p, c = prev.get("state", "flat"), cur.get("state", "flat")
    if p == c or c == "flat":
        return None
    return {"i": cur.get("i"), "dir": "bull" if c == "buy" else "bear",
            "from": p, "to": c}


def is_signal(fp, cfg):
    """
    The extra conditions that turn a strong read into something worth a message. Defaults
    are deliberately strict, because the measured version of "just trade every flip" was
    noise: 674 signals on 45 days of XAUUSD 4h, 30-33% win rate at RR 1.6 (breakeven 38.5%).
    """
    if not fp or fp.get("state", "flat") == "flat":
        return False
    cfg = cfg or {}
    if cfg.get("fp_require_div", True) and not (fp.get("divergence") or fp.get("absorption")):
        return False
    if cfg.get("fp_require_edge", True) and not (fp.get("at_high") or fp.get("at_low")):
        return False
    return True


def caption_bits(fp):
    """Short Persian fragments for messages; safe with a partial dict."""
    if not fp:
        return "— بدون داده"
    fa = {"buy": "فشار خرید", "sell": "فشار فروش", "flat": "تعادل خرید/فروش"}
    s = f"{fa.get(fp.get('state', 'flat'), '—')}"
    s += f" · دلتای {fp.get('bars', 0)} کندل اخیر {fp.get('delta_pct', 0.0):+.1f}٪"
    s += f" · سیگما Δ {fp.get('cum_slope_v', 0.0):+.2f} کندل"
    if fp.get("divergence"):
        s += " · واگرایی دلتا/قیمت"
    if fp.get("absorption"):
        s += " · جذب نقدینگی"
    if fp.get("poc") == fp.get("poc") and fp.get("poc") is not None:
        kind = "زمان" if fp.get("poc_kind") == "time" else "حجم"
        s += f" · POC({kind}) {fp['poc']:.2f} ({100.0 * fp.get('poc_share', 0.0):.0f}٪)"
    if fp.get("no_volume"):
        s += " · بدون دیتای حجم → دلتا بر پایهٔ کندل"
    return s


def _median(vals):
    v = sorted(x for x in vals if x == x and x > 0)
    if not v:
        return 0.0
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0
