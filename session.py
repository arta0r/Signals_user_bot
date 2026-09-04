#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Session bias: at the start of each trading session (Sydney / Tokyo / London / New York)
read the pre-session price action and answer ONE question — is this session bullish or
bearish? Free data only (Yahoo), no Telegram needed to test it.

    python3 session.py --print                  # the session that is starting / just started
    python3 session.py --print --at 2026-09-04T07:00 --session london
    python3 session.py --post                   # send it to Telegram (used by the workflow)

The verdict is a weighted score of five checks on the window BEFORE the open:
trend (EMA fast vs slow), where price sits inside that window, RSI side of 50, MACD
histogram sign, and whether the last bar broke the window's extreme. Inverted symbols
(DXY) count against gold's upside. It is an honest read of the tape, not a prediction:
a "flat" verdict is a real answer and you should sit that one out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import footprint as FP          # noqa: E402
import dataio               # noqa: E402
import detector as D        # noqa: E402

FLAT_BAND = 2               # |score| below this -> "flat"


# ------------------------------------------------------------------ session clock
def session_at(now: dt.datetime, name: str) -> dt.datetime | None:
    """Today's UTC start of `name` (None if the config has no such session)."""
    for s in C.SESSIONS:
        if s["name"] == name:
            return now.replace(hour=s["utc_hour"], minute=s.get("utc_minute", 0),
                               second=0, microsecond=0)
    return None


def _start_of(now: dt.datetime, s: dict, day_off: int = 0) -> dt.datetime:
    return (now + dt.timedelta(days=day_off)).replace(
        hour=s["utc_hour"], minute=s.get("utc_minute", 0), second=0, microsecond=0)


def last_open(now: dt.datetime, s: dict) -> dt.datetime:
    """The most recent open of session `s` at or before `now` (wraps over midnight)."""
    cands = [_start_of(now, s, d) for d in (0, -1)]
    past = [c for c in cands if c <= now]
    return max(past) if past else min(cands)


def current_session(now: dt.datetime, window_min: int = 90):
    """The session opening right now (None in the gaps between sessions).

    `window_min` = how long after an open we still call it "this session's start"
    (default 90 min, so a delayed cron run still labels the right session). A run up to
    30 minutes early is accepted too, and the previous day's last open is considered so
    the small hours of the UTC day are attributed correctly.
    """
    best = None
    for day_off in (0, -1):
        for s in C.SESSIONS:
            mins = (now - _start_of(now, s, day_off)).total_seconds() / 60.0
            if -30 <= mins < window_min and (best is None or abs(mins) < abs(best[0])):
                best = (mins, s)
    return best[1] if best else None


def next_session(now: dt.datetime) -> tuple:
    """(session, minutes_until_open) for the next open from `now`."""
    out = None
    for day_off in (0, 1):
        for s in C.SESSIONS:
            m = (_start_of(now, s, day_off) - now).total_seconds() / 60.0
            if m <= 0:
                continue
            if out is None or m < out[1]:
                out = (s, m)
    return out


def session_for(now: dt.datetime, window_min: int = 90) -> tuple:
    """What a run at `now` should report: (session, minutes_until_open, is_live)."""
    cur = current_session(now, window_min)
    if cur:
        return cur, 0.0, True
    s, m = next_session(now)
    return s, m, False


# ------------------------------------------------------------------ the analysis
def _window(bars, until_ts, hours):
    lo = until_ts - hours * 3600
    return [b for b in bars if lo <= b["t"] <= until_ts]


def symbol_bias(candles, cfg, until_ts=None):
    """
    Pure function: candles (list of {t,o,h,l,c,v}) -> dict with score/verdict/parts.
    `until_ts` cuts the series at the session open so the verdict only sees the past.
    """
    if until_ts:
        candles = [b for b in candles if b["t"] <= until_ts]
    need = max(cfg["min_bars"], cfg["ema_slow"] + 5)
    if len(candles) < need:
        return None
    cl = [c["c"] for c in candles]
    hi = [c["h"] for c in candles]
    lo = [c["l"] for c in candles]
    last = len(cl) - 1
    px = cl[last]
    w = _window(candles, candles[last]["t"], cfg.get("pre_session_hours", 12)) or candles[-24:]
    w_hi, w_lo = max(b["h"] for b in w), min(b["l"] for b in w)
    span = max(w_hi - w_lo, 1e-9)
    a = D.atr(hi, lo, cl, cfg["atr_period"])[last]
    r = D.rsi(cl, cfg["rsi_period"])[last]
    line, sig, hist = D.macd(cl, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    e_f = D.ema(cl, cfg["ema_fast"])[last]
    e_s = D.ema(cl, cfg["ema_slow"])[last]
    rng = D.atr(hi, lo, cl, cfg["atr_period"])
    rng_last = rng[last] if not math.isnan(rng[last]) else span / 4

    parts = {}
    parts["trend"] = 1 if (e_f > e_s) else -1
    flip = D._cross(D.ema(cl, cfg["ema_fast"]), D.ema(cl, cfg["ema_slow"]), last, 1)
    parts["position"] = 1 if (px - w_lo) / span > 0.60 else (-1 if (px - w_lo) / span < 0.40 else 0)
    parts["momentum"] = 0 if r != r else (1 if r > 52 else -1 if r < 48 else 0)
    parts["macd"] = 1 if (hist[last] > 0) else (-1 if hist[last] < 0 else 0)
    parts["break"] = 1 if px >= w_hi else (-1 if px <= w_lo else 0)
    score = sum(parts.values())
    fp = FP.analyze(candles, cfg)                       # footprint read (own filter)
    prev = w[0]["c"] if len(w) > 1 else cl[0]
    return {"price": px, "score": score, "verdict": ("up" if score > FLAT_BAND else
                                                      "down" if score < -FLAT_BAND else "flat"),
            "ema_up": e_f > e_s, "ema_flip": (flip or {}).get("dir"),
            "ema_gap_pct": 100.0 * (e_f - e_s) / px if px else 0.0,
            "fp_state": fp["state"] if fp else "flat",
            "fp_delta_pct": fp["delta_pct"] if fp else 0.0,
            "fp_slope": fp["cum_slope_v"] if fp else 0.0,
            "fp_div": bool(fp and fp.get("divergence")),
            "fp_absorb": bool(fp and fp.get("absorption")),
            "fp_novol": bool(fp and fp.get("no_volume")),
            "fp_flip": (fp.get("flip") or {}).get("dir") if fp else None,
            "parts": parts, "rsi": r, "macd_hist": hist[last], "ema_fast": e_f, "ema_slow": e_s,
            "atr": a if a == a else rng_last, "range_hi": w_hi, "range_lo": w_lo,
            "position": (px - w_lo) / span, "chg_pct": 100.0 * (px - prev) / prev if prev else 0.0,
            "bars": len(candles), "window_bars": len(w)}


def report(per_symbol, session, cfg, until_ts=None, live=True):
    """Aggregate the per-symbol scores into one verdict for the session."""
    tot = 0.0
    n = 0
    rows = []
    fp_gated = False
    for name, b in per_symbol.items():
        if not b:
            rows.append((name, None))
            continue
        w = 1.0 if C.GOLD_UP.get(name, True) else -1.0
        tot += w * b["score"]
        n += 1
        b["gold_contrib"] = int(w * b["score"])        # what this symbol adds to the gold read
        b["gold_side"] = ("up" if b["gold_contrib"] > FLAT_BAND else
                          "down" if b["gold_contrib"] < -FLAT_BAND else "flat")
        rows.append((name, b))
    avg = tot / n if n else 0.0
    verdict = "up" if avg > 0.6 else "down" if avg < -0.6 else "flat"
    gated = False
    if cfg.get("footprint_filter", False) and verdict != "flat" and n:
        # same veto, asked of the tape: if most symbols' flow is the other way -> no verdict
        want = "buy" if verdict == "up" else "sell"
        known = [b for _, b in rows if b and b.get("fp_state") in ("buy", "sell")]
        if known and sum(1 for b in known if b["fp_state"] == want) * 2 < len(known):
            verdict, fp_gated = "flat", True
    if cfg.get("ema_filter", True) and verdict != "flat" and n:
        # the filter's own vote: how many symbols' EMA agrees with the verdict
        want = verdict == "up"
        known = [b for b, nm in ((b, nm) for nm, b in rows) if b and "ema_up" in b]
        agree = sum(1 for b in known if b["ema_up"] is want)
        if known and agree * 2 < len(known):     # majority of EMAs disagree -> no verdict
            verdict, gated = "flat", True
    return {"session": session, "verdict": verdict, "avg": avg, "n": n,
            "rows": rows, "at": until_ts, "live": live, "ema_gated": gated,
            "fp_gated": fp_gated}


# ------------------------------------------------------------------ telegram text
FA = {"up": "صعودی", "down": "نزولی", "flat": "خنثی / بی‌جهت"}
EMO = {"up": "🟢", "down": "🔴", "flat": "⚪"}


def caption(rep, digits_by_symbol=None, cfg=None) -> str:
    digits_by_symbol = digits_by_symbol or {}
    cfg_fp = (cfg or C.BIAS).get("footprint", False)
    s = rep["session"]
    at = ""
    if rep.get("at"):
        at = " · " + dt.datetime.fromtimestamp(rep["at"], dt.timezone.utc).strftime("%H:%M UTC")
    live = rep.get("live", True)
    head = "گشایش سشن" if live else "پیش‌خوانی سشن"
    if not live:
        at += f" — {(rep.get('in_min') or 0) / 60:.1f} ساعت تا گشایش"
    if rep.get("fp_gated"):
        at += "\n👣 فیلتر فوترپراینت: فلوِ بیشترِ نمادها مخالف بود → رایِ جهت خنثی شد"
    if rep.get("ema_gated"):
        at += "\n⚖️ فیلتر EMA majorityِ مخالف داشت → رایِ جهت خنثی اعلام شد"
    lines = [f"{EMO[rep['verdict']]} <b>{head} {s['label_fa']}</b>{at}",
             f"جهت غالب این سشن: <b>{FA[rep['verdict']]}</b> "
             f"(امتیاز میانگین {rep['avg']:+.1f} از {rep['n']} نماد)"]
    ok = [r for r in rep["rows"] if r[1]]
    if ok:
        lines.append("")
        lines.append("  ".join(f"{nm} {EMO[b['gold_side']]}({b['gold_contrib']:+d})" for nm, b in ok))
    for nm, b in rep["rows"]:
        if not b:
            lines.append(f"\n{nm}: داده کافی نبود")
            continue
        d = digits_by_symbol.get(nm, 2)
        f = lambda v: f"{v:,.{d}f}"
        inv = not C.GOLD_UP.get(nm, True)
        tag = " (هرچه بالا، به ضرر طلا)" if inv else ""
        lines.append(f"\n<b>{nm}</b>{tag} — حرکتِ خودِ نماد: {FA[b['verdict']]} · امتیاز {b['score']:+d} "
                     f"· سهم در قرائتِ طلا: {b['gold_contrib']:+d}")
        lines.append(f"قیمت {f(b['price'])} · تغییر پیش‌گشایش {b['chg_pct']:+.2f}٪ · "
                     f"RSI {b['rsi']:.0f} · MACD hist {b['macd_hist']:+,.{d + 1}f}")
        lines.append(f"موقعیت در رنج {b['position'] * 100:.0f}٪ "
                     f"[{f(b['range_lo'])} … {f(b['range_hi'])}] · ATR {f(b['atr'])}")
        parts = " ".join(f"{k}:{v:+d}" for k, v in b["parts"].items())
        lines.append(f"<i>چک‌ها → {parts}</i>")
        fl = b.get("ema_flip")
        ema_line = "" if "ema_up" not in b else (
            f"📊 فیلتر EMA: {FA['up'] if b['ema_up'] else FA['down']} "
            f"(فاصلهٔ ۲۱ تا ۵۵ = {b.get('ema_gap_pct', 0.0):+.2f}٪)")
        if fl:
            ema_line += f" — کراس همین الان: {'صعودی' if fl == 'bull' else 'نزولی'}"
        if ema_line:
            lines.append(ema_line)
        if cfg_fp and b.get("fp_state"):
            fs = {"buy": "فشار خرید 🟢", "sell": "فشار فروش 🔴"}.get(b["fp_state"], "تعادل ⚪")
            one = (f"👣 فیلتر فوترپراینت: {fs} · دلتای پنجره {b['fp_delta_pct']:+.1f}٪ · "
                   f"شیب دلتا {b['fp_slope']:+.2f} کندل")
            if b.get("fp_div"):
                one += " · واگرایی دلتا/قیمت"
            if b.get("fp_absorb"):
                one += " · جذب سفارشات"
            if b.get("fp_flip"):
                one += f" · فلو همین الان عوض سمت داد ({'خرید' if b['fp_flip'] == 'bull' else 'فروش'})"
            if b.get("fp_novol"):
                one += " · (حجم واقعیِ فارکس در دسترس نیست → دلتای تخمینی)"
            lines.append(one)
    lines.append("\nتفسیر: این قرائتِ رفتار قیمتِ قبل از گشایش است، نه پیش‌بینی. "
                 "«خنثی» یعنی وارد نشو.")
    return "\n".join(lines)


def build(dry_cfg=None, session_name=None, at=None, only=None):
    cfg = dict(C.CONFIG)
    cfg.update(dry_cfg or {})
    now = at or dt.datetime.now(dt.timezone.utc)
    if isinstance(now, str):
        now = dt.datetime.fromisoformat(now).replace(tzinfo=dt.timezone.utc)
    ts = int(now.timestamp())
    mins_to_open = 0.0
    if session_name:                       # explicit --session: read up to THIS session's open
        session = next((x for x in C.SESSIONS if x["name"] == session_name), None)
        if session is None:
            raise SystemExit(f"unknown session '{session_name}' "
                             f"(options: {', '.join(x['name'] for x in C.SESSIONS)})")
        stamp = last_open(now, session)      # today's open if it already happened, else yesterday's
        now, ts = stamp, int(stamp.timestamp())                    # never read future bars
        live = True
    else:
        session, mins_to_open, live = session_for(now)
    names = [x.strip().upper() for x in only.split(",")] if only else C.SESSION_BIAS_SYMBOLS
    by_name = {s["name"]: s for s in C.SYMBOLS}
    pre = cfg.get("pre_session_hours", 12)
    tf = cfg.get("bias_timeframe", "1h")
    span_bars = int(pre * (4 if tf == "15m" else 2 if tf == "30m" else 1)) + 40
    # if --at is in the past, ask for enough history to reach back to it (hourly only)
    back = max(0, int(dt.datetime.now(dt.timezone.utc).timestamp()) - ts)
    extra = int(back / 3600) + 24 if tf == "1h" else int(back / 900) + 24
    limit = min(2000, max(260, C.CONFIG["min_bars"] + span_bars + extra))
    per, digits = {}, {}
    for nm in names:
        sym = by_name.get(nm)
        if not sym:
            print(f"  ! unknown symbol {nm}", file=sys.stderr)
            continue
        digits[nm] = sym.get("digits", 2)
        # candles around the open. dataio returns the most recent N bars, so an --at in
        # the far past is only readable as far back as the provider keeps that timeframe.
        try:
            bars, prov = dataio.fetch(sym, tf, limit, cfg.get("td_api_key", ""))
        except Exception as e:                                # noqa: BLE001
            print(f"  {nm} data error: {str(e)[:120]}", file=sys.stderr)
            per[nm] = None
            continue
        # cut at the open, then keep only what the indicators need
        window = [b for b in bars if b["t"] <= ts][-max(90, cfg["min_bars"]):]
        cfg2 = dict(cfg)
        cfg2["min_bars"] = min(cfg["min_bars"], len(window))
        per[nm] = symbol_bias(window, cfg2, until_ts=None)
        if per[nm]:
            per[nm]["prov"] = prov
    rep = report(per, session, cfg, until_ts=ts, live=live)
    rep["in_min"] = mins_to_open
    rep["digits"] = digits
    return rep, caption(rep, digits, cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print", action="store_true", help="print the report and exit")
    ap.add_argument("--post", action="store_true", help="send it to Telegram")
    ap.add_argument("--session", default=None, help="sydney|tokyo|london|newyork (default: auto)")
    ap.add_argument("--at", default=None, help="ISO UTC time, e.g. 2026-09-04T07:00 (for testing)")
    ap.add_argument("--symbols", default=None, help="override the watch list")
    ap.add_argument("--pre-hours", type=int, default=None, help="how far back to read")
    ap.add_argument("--footprint", choices=("off", "on", "veto"), default=None,
                    help="footprint filter: off = hidden, on = show the line, "
                         "veto = also force the read to flat when the flow disagrees")
    a = ap.parse_args()
    ov = {}
    if a.pre_hours:
        ov["pre_session_hours"] = a.pre_hours
    if a.footprint == "on":
        ov["footprint"] = True
    elif a.footprint == "veto":
        ov["footprint"] = True
        ov["footprint_filter"] = True
    elif a.footprint == "off":
        ov["footprint"] = False
        ov["footprint_filter"] = False
    rep, text = build(ov, a.session, a.at, a.symbols)
    plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(f"\n{plain}\n")
    if a.post:
        import notify
        token = os.environ.get("TG_TOKEN") or ""
        chat = os.environ.get("TG_CHAT_ID") or ""
        tg = notify.TG(token, chat)
        if not tg.enabled:
            print("TG_TOKEN / TG_CHAT_ID not set — nothing sent", file=sys.stderr)
            return 1
        print("sent:", tg.send(text).get("ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
