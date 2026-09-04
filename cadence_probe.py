#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
How often does each setup actually fire? This is what should decide min_conditions,
signal_lookback_bars and the extra setups — not taste.

It replays history bar-by-bar (one scan per bar, exactly like cron would) and counts
distinct setup events per 30 days, per timeframe.

  python3 tools/cadence_probe.py --days 90
  python3 tools/cadence_probe.py --symbols XAUUSD,EURUSD --timeframes 15m,4h --min-conditions 3
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C     # noqa: E402
import dataio          # noqa: E402
import detector as D   # noqa: E402


def replay(bars, cfg, t0, t1):
    """Forward pass; a setup event is counted once, at the bar it first appeared."""
    events = []
    seen_sig = set()
    lo = max(cfg["ema_slow"] + cfg["rsi_period"] + 4, 60)
    for cut in range(lo, len(bars) + 1):
        ts = bars[cut - 1]["t"]
        if t1 and ts > t1:
            continue
        if t0 and ts < t0:
            continue
        res = D.build_setup(bars[:cut], cfg)
        if not res or not res["idea"]:
            continue
        key = (res.get("abs_n", res["n"]), res["idea"]["side"])   # pins the signal bar
        if key in seen_sig:
            continue
        seen_sig.add(key)
        i = res["idea"]
        # cut-1 is the (still forming) bar at replay time, so cut-2 is the last CLOSED
        # bar -- the one every level in this idea is measured from
        events.append({"t": ts, "sig_i": cut - 2, "bar_i": cut - 2, "side": i["side"],
                       "rr": i["rr"], "entry": i["entry"],
                       "sl": i["sl"], "tp": i["tp"], "name": i.get("name", "combo"),
                       "tags": "|".join(res["tags"]["bull"] if res["idea"]["side"] == "LONG"
                                        else res["tags"]["bear"]),
                       "score": res["score"]["bull"] if res["idea"]["side"] == "LONG"
                                else res["score"]["bear"]})
    return events


def outcome(bars, ev, cfg, horizon=200, fill_window=24, tie="pessimistic"):
    """
    How did this idea play out? Judged on the idea's OWN entry/SL/TP.

    Two things a naive replay gets wrong, both fixed here:
      * the signal bar itself must not decide the trade (levels are measured at that bar's
        close, and its own wick would otherwise be reused as the outcome);
      * a limit entry only counts once price actually traded at it, else a retest that
        never happened looks like a free tight-stop win.
    When one bar spans both levels, `tie` decides who got there first: "pessimistic"
    always gives it to the stop, "open" uses proximity to the bar open (usually too kind).
    Returns (verdict, bars_held): win | loss | open | missed | n/a
    """
    if "bar_i" in ev:
        idx = int(ev["bar_i"])
    else:
        idx = max(i for i, b in enumerate(bars) if b["t"] <= ev["t"])
    sig_i = max(idx, int(ev.get("sig_i", idx)))
    long_ = ev["side"] == "LONG"
    entry, stop, tgt = ev.get("entry"), ev.get("sl"), ev.get("tp")
    if entry is None or stop is None or tgt is None:
        a = D.atr([b["h"] for b in bars[:idx + 1]], [b["l"] for b in bars[:idx + 1]],
                  [b["c"] for b in bars[:idx + 1]], cfg["atr_period"])[idx]
        if not a or math.isnan(a):
            return "n/a", 0
        entry = bars[idx]["c"]
        stop = entry - cfg["sl_atr"] * a if long_ else entry + cfg["sl_atr"] * a
        tgt = entry + cfg["min_rr"] * cfg["sl_atr"] * a if long_ else entry - cfg["min_rr"] * cfg["sl_atr"] * a

    def hit(b, level):
        return b["l"] <= level if long_ else b["h"] >= level

    start = sig_i + 1                       # never evaluate on the signal bar
    end = min(start + horizon, len(bars))
    waiting = abs(entry - bars[sig_i]["c"]) > 1e-9   # market-on-close vs limit retest
    for k in range(start, end):
        b = bars[k]
        if waiting:
            if (b["l"] <= entry) if long_ else (b["h"] >= entry):
                waiting = False              # the retest happened on this bar
            elif k - start > fill_window:
                return "missed", 0
            else:
                continue
        # SL is the level in the trade direction, TP is the opposite one
        sl_hit = hit(b, stop)
        tp_hit = (b["h"] >= tgt) if long_ else (b["l"] <= tgt)
        if sl_hit and tp_hit:
            if tie == "pessimistic":
                return "loss", k - sig_i
            d_sl, d_tp = abs(b["o"] - stop), abs(b["o"] - tgt)
            return ("loss" if d_sl <= d_tp else "win"), k - sig_i
        if sl_hit:
            return "loss", k - sig_i
        if tp_hit:
            return "win", k - sig_i
    return "open", 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--symbols", default="XAUUSD,EURUSD,USDJPY,BTCUSD,SPX500")
    ap.add_argument("--timeframes", default="5m,15m,4h")
    ap.add_argument("--min-conditions", type=int, default=C.CONFIG["min_conditions"])
    ap.add_argument("--lookback", type=int, default=C.CONFIG["signal_lookback_bars"])
    ap.add_argument("--min-rr", type=float, default=C.CONFIG["min_rr"])
    ap.add_argument("--mode", choices=("solo", "combo"), default=C.CONFIG.get("mode", "combo"))
    ap.add_argument("--ideas", default="fvg,zone,sweep,bos",
                    help="comma separated, solo mode only (fvg,zone,sweep,bos)")
    ap.add_argument("--order", default="fvg,zone,sweep,bos",
                    help="tie-break when several setups fire on the same bar")
    ap.add_argument("--sl-buffer", type=float, default=C.CONFIG.get("solo_sl_buffer", 0.25))
    ap.add_argument("--by-idea", action="store_true", help="extra table: one row per setup")
    ap.add_argument("--entry", choices=("retest", "market"), default=C.CONFIG.get("solo_entry", "retest"))
    ap.add_argument("--tie", choices=("pessimistic", "open"), default="pessimistic",
                    help="who wins a bar that spans both SL and TP")
    ap.add_argument("--fill-window", type=int, default=24,
                    help="bars a limit entry may wait for its retest before the idea is dropped")
    ap.add_argument("--cache", action="store_true", help="reuse fetched bars (/tmp) so you can "
                                                          "compare configs on identical data")
    ap.add_argument("--max-entry-offset", type=float, default=0.0,
                    help="drop signals whose entry sits more than N*ATR from the last close (stale limit)")
    ap.add_argument("--trend-align", action="store_true")
    ap.add_argument("--ema-gate", action="store_true",
                    help="reject ideas that fight the EMA trend (the filter as a gate)")
    ap.add_argument("--ema-lines", choices=("cross", "price"), default=C.CONFIG.get("ema_lines", "cross"))
    ap.add_argument("--footprint-idea", action="store_true",
                    help="judge the footprint flip as an idea of its own (not as a gate)")
    ap.add_argument("--footprint-gate", action="store_true",
                    help="reject ideas that fight the footprint delta read")
    ap.add_argument("--fp-strength", type=float, default=C.CONFIG.get("fp_strength", 1.4))
    ap.add_argument("--fp-blocks", type=int, default=C.CONFIG.get("fp_blocks_bars", 12),
                    help="how fresh a flow flip must be to overrule the gate (0 = never)")
    ap.add_argument("--require-sweep", action="store_true")
    ap.add_argument("--tp-liquidity", action="store_true")
    a = ap.parse_args()

    cfg = dict(C.CONFIG)
    live = [x.strip() for x in a.ideas.split(",") if x.strip()]
    cfg.update(min_conditions=a.min_conditions, signal_lookback_bars=a.lookback,
               min_rr=a.min_rr, require_trend_align=a.trend_align, mode=a.mode,
               ideas={k: (k in live) for k in D.IDEA_LABELS}, idea_order=live or list(D.IDEA_LABELS),
               solo_sl_buffer=a.sl_buffer,
               require_sweep=a.require_sweep, target_at_liquidity=a.tp_liquidity,
               ema_gate=a.ema_gate, ema_lines=a.ema_lines, footprint_gate=a.footprint_gate,
               footprint=bool(a.footprint_gate) or bool(a.footprint_idea),
               fp_strength=a.fp_strength, fp_blocks_bars=a.fp_blocks,
               max_entry_offset_atr=a.max_entry_offset, solo_entry=a.entry)
    import datetime as dt
    t1 = dt.datetime.now(dt.timezone.utc).timestamp()
    t0 = t1 - a.days * 86400

    names = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    tfs = [s.strip() for s in a.timeframes.split(",") if s.strip()]
    rows = []
    print(f"\n  replay · mode={a.mode} ideas={','.join(live) or '-'} order={','.join(live)} "
          f"sl_buffer={a.sl_buffer} · min_conditions={a.min_conditions} lookback={a.lookback} "
          f"min_rr={a.min_rr} entry={a.entry} fill_window={a.fill_window} tie={a.tie} "
          f"trend_align={a.trend_align} "
          f"require_sweep={a.require_sweep} tp_at_liq={a.tp_liquidity} "
          f"ema_gate={a.ema_gate} ema_lines={a.ema_lines} footprint_gate={a.footprint_gate} "
          f"fp_strength={a.fp_strength} · window={a.days}d\n")
    print(f"  {'symbol':<9} {'tf':<5} {'bars':>6} {'events/30d':>11} {'win':>5} {'loss':>6} "
          f"{'win%':>6} {'best tags':>26}")
    print("  " + "-" * 82)
    for nm in names:
        sym = next((s for s in C.SYMBOLS if s["name"].upper() == nm), None)
        if not sym:
            print(f"  ! unknown symbol {nm}")
            continue
        for tf in tfs:
            cache = os.path.join("/tmp", f"probe_{sym['name']}_{tf}.json")
            try:
                if a.cache and os.path.exists(cache):
                    import json
                    with open(cache) as fh:
                        bars, prov = json.load(fh), "cache"
                else:
                    bars, prov = dataio.fetch(sym, tf, 4000)
                    if a.cache:
                        import json
                        with open(cache, "w") as fh:
                            json.dump(bars, fh)
            except Exception as e:                          # noqa: BLE001
                print(f"  {nm:<9} {tf:<5} data error: {str(e)[:60]}")
                continue
            ev = replay(bars, cfg, t0, t1)
            span_days = max(1, a.days)
            per30 = len(ev) / span_days * 30
            w = l = miss = 0
            for e in ev:
                o, _ = outcome(bars, e, cfg, fill_window=a.fill_window, tie=a.tie)
                w += 1 if o == "win" else 0
                l += 1 if o == "loss" else 0
                miss += 1 if o == "missed" else 0
            dec = w + l
            rate = 100.0 * w / dec if dec else float("nan")
            tagmix = {}
            for e in ev:
                tagmix[e["tags"]] = tagmix.get(e["tags"], 0) + 1
            best = sorted(tagmix.items(), key=lambda kv: -kv[1])[:1]
            best_s = (f"{best[0][0][:22]}×{best[0][1]}" if best else "—")
            rows.append((nm, tf, per30, rate, dec))
            byidea = {}
            for e in ev:
                o, _ = outcome(bars, e, cfg, fill_window=a.fill_window, tie=a.tie)
                d = byidea.setdefault(e["name"], [0, 0, 0, 0])
                d[0] += 1
                d[1] += 1 if o == "win" else 0
                d[2] += 1 if o == "loss" else 0
                d[3] += 1 if o == "missed" else 0
            if a.by_idea:
                print(f"            (per setup: filled only; missed = limit never reached)")
                for k, (tot_, w_, l_, m_) in sorted(byidea.items(), key=lambda kv: -kv[1][0]):
                    r_ = 100.0 * w_ / (w_ + l_) if (w_ + l_) else float("nan")
                    print(f"            └ {k:<6} {tot_:>4} sig · filled {w_+l_:>4} · win {w_:>4}/{w_+l_:<4} "
                          f"{(f'{r_:.0f}%' if w_+l_ else '  n/a'):>5} · missed {m_} "
                          f"({(100.0 * m_ / tot_) if tot_ else 0:.0f}%)")
            print(f"  {nm:<9} {tf:<5} {len(bars):>6} {per30:>11.1f} {w:>5} {l:>6} "
                  f"{(f'{rate:.0f}%' if dec else '  n/a'):>6} {best_s:>26}")
            if miss:
                print(f"            (of {len(ev)} events on this row: {w} win / {l} loss / {miss} never filled)")
    if rows:
        tot = sum(r[2] for r in rows)
        dec = sum(r[4] for r in rows)
        print("  " + "-" * 82)
        print(f"  TOTAL across the watch list: {tot:.0f} alerts/30 days "
              f"({tot / max(1, len(tfs)):.0f} per timeframe per month) · resolved trades: {dec}")
        print("  rule of thumb: >60 alerts/month per timeframe = too noisy to read; "
              "<3 = you will wait weeks and learn nothing")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
