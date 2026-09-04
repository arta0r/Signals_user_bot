#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold/FX/Indices/Crypto signal bot -> Telegram.

Scan one cycle (this is what cron calls):
    python3 scan.py --once
Scan specific markets / timeframes:
    python3 scan.py --once --symbols XAUUSD,EURUSD --timeframes 15m,4h
Print instead of sending:
    python3 scan.py --once --dry
Discover your chat_id (send any message to your bot first):
    python3 scan.py --whoami --token ***
Run forever (for Hugging Face Spaces / a VPS):
    python3 scan.py --loop --every-minutes 10

Env / GitHub secrets: TG_TOKEN, TG_CHAT_ID, TD_API_KEY (optional), CONFIG_JSON (optional)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C            # noqa: E402
import dataio                 # noqa: E402
import footprint as FP        # noqa: E402
import detector as D          # noqa: E402
import notify                 # noqa: E402

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals_log.csv")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def load_overrides() -> dict:
    raw = _env("CONFIG_JSON") or _env("CONFIG")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"CONFIG_JSON is not valid JSON ({e}); ignoring", file=sys.stderr)
    return {}


def pick_symbols(ov: dict, arg: str | None) -> list[dict]:
    by_name = {s["name"]: s for s in C.SYMBOLS}
    if arg:
        names = [a.strip().upper() for a in arg.split(",") if a.strip()]
    elif ov.get("symbols"):
        names = [a.strip().upper() for a in ov["symbols"]]
    else:
        names = C.DEFAULT_SYMBOLS
    out = []
    for n in names:
        if n in by_name:
            out.append(by_name[n])
        else:
            print(f"  ! unknown symbol '{n}' — skipping (see config.py)", file=sys.stderr)
    return out


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"seen": {}, "digest": {}}


def save_state(path: str, st: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1)
    except Exception as e:                                   # noqa: BLE001
        print(f"  ! cannot write state: {e}", file=sys.stderr)


GIST_API = "https://api.github.com/gists"


def gist_read(gist_id: str, token: str, filename: str = "scan_seen.json") -> dict:
    """
    Seen-state across GitHub Actions runs — the runner filesystem is wiped every run, so
    without this a bar that alerted at :05 alerts again at :10, :15 ... The gist needs no
    token to READ (public gist), only to write.
    """
    if not gist_id:
        return {}
    try:
        req = urllib.request.Request(
            f"{GIST_API}/{gist_id}",
            headers={"User-Agent": "signal-bot", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        files = data.get("files") or {}
        f = files.get(filename) or (next(iter(files.values())) if files else {})
        txt = (f or {}).get("content") or ""
        return json.loads(txt) if txt.strip() else {}
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! gist read failed ({type(e).__name__}: {str(e)[:80]}) — running without history")
        return {}


def gist_write(gist_id: str, token: str, state: dict, filename: str = "scan_seen.json") -> bool:
    if not (gist_id and token):
        return False
    body = json.dumps({"files": {filename: {"content": json.dumps(state)}}}).encode()
    req = urllib.request.Request(f"{GIST_API}/{gist_id}", data=body, method="PATCH", headers={
        "User-Agent": "signal-bot", "Content-Type": "application/json",
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return True
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! gist write failed ({type(e).__name__}: {str(e)[:80]})")
        return False


def fmt(v, digits):
    return f"{v:,.{digits}f}" if isinstance(v, (int, float)) and v == v else "—"


def caption_for(sym, tf, res, kind) -> str | None:
    d = sym.get("digits", 2)
    px = fmt(res["price"], d)
    if kind == "setup" and res["idea"]:
        i = res["idea"]
        tags = i.get("label") or " · ".join(
            res["tags"]["bull"] if i["side"] == "LONG" else res["tags"]["bear"])
        pip = C.pip_size(sym["name"], sym.get("digits", 5))
        pips = f"  <b>risk {fmt(abs(i['entry'] - i['sl']) / pip, 1)} pips · target "               f"{fmt(abs(i['tp'] - i['entry']) / pip, 1)} pips</b>"
        return (
            f" <b>{sym['label']} — {i['side']}</b>  ({tf})\n"
            f"🎯 نقطه ورود: <code>{fmt(i['entry'], d)}</code>\n"
            f"✅ نقطه خروج (TP): <code>{fmt(i['tp'], d)}</code>   ⛔ حد ضرر (SL): <code>{fmt(i['sl'], d)}</code>\n"
            f"📐 RR {i['rr']:.2f}{pips}\n"
            f"🧭 {'حد سفارشی روی' if abs(i['entry'] - i['last_close']) > 1e-9 else 'قیمت فعلی'}: "
            f"فاصله ورود تا آخرین بسته {fmt(abs(i['entry'] - i['last_close']), d)}\n"
            f"📏 فاصله ورود تا خروج: {fmt(abs(i['tp'] - i['entry']), d)}\n"
            f"🧭 فیلتر EMA: روند {res['trend']} · "
            f"{'✅ هم‌سو با ستاپ' if _aligned(res, i) else '⚠️ خلاف روند EMA'}\n"
            + (f"👣 فیلتر فوترپراینت: {FP.caption_bits(res.get('footprint'))} · "
               f"{'✅ هم‌سو' if _fp_aligned(res, i) else '⚠️ خلاف فلو'}\n"
               if C.CONFIG.get("footprint", False) else "")
            + f"🧩 ستاپ: <b>{tags}</b>{' · +۱ هم‌سو' if i.get('confluence', 1) > 1 and res.get('mode') == 'solo' else ''}\n"
            f"📊 RSI {res['rsi']:.1f} · MACD hist {fmt(res['macd_hist'], d + 2)} · trend {res['trend']}\n"
            f"⚠️ پیشنهاد تحلیلی، نه سیگنال قطعی · بسته‌شدن کندل مبناست"
        )
    if kind == "rsi":
        rc = res["rsi_cross"]
        if rc["dir"] == "neutral":
            return None
        emoji = "🔻" if rc["dir"] == "overbought" else "🔺"
        state = "cross" if rc["i"] is not None else "in zone"
        return (f"{emoji} <b>{sym['label']} RSI {rc['dir'].upper()}</b> ({tf})\n"
                f"RSI = {rc['value']:.1f} ({state}) · قیمت <code>{px}</code> · trend {res['trend']}")
    if kind == "macd":
        m = res["macd_cross"]
        if not m:
            return None
        emoji = "🟢" if m["dir"] == "bull" else "🔴"
        return (f"{emoji} <b>{sym['label']} MACD cross {m['dir']}</b> ({tf})\n"
                f"قیمت <code>{px}</code> · hist {fmt(res['macd_hist'], d + 2)} · "
                f"RSI {res['rsi']:.1f} · trend {res['trend']}")
    if kind == "trend":
        f = res.get("trend_flip") or {}
        who = "EMA 21/55" if C.CONFIG.get("ema_lines", "cross") == "cross" else "قیمت/EMA 55"
        emoji = "🟢" if f.get("dir") == "bull" else "🔴"
        side = "صعودی" if f.get("dir") == "bull" else "نزولی"
        return (f"{emoji} <b>{sym['label']} — فیلتر EMA: روند {side}</b> ({tf})\n"
                f"کراس روی {who}\n"
                f"EMA21 <code>{fmt(res.get('ema_fast_v'), d)}</code> · "
                f"EMA55 <code>{fmt(res.get('ema_slow_v'), d)}</code> · قیمت <code>{px}</code>\n"
                f"⚖️ این «مجاز/ممنوع» است، نه سیگنال ورود: فقط جهتِ هم‌سو را trade کن.")
    if kind == "footprint":
        fp = res.get("footprint") or {}
        if not fp:
            return None
        fl = fp.get("flip") or {}
        emoji = "🟢" if fp.get("state") == "buy" else ("🔴" if fp.get("state") == "sell" else "⚪")
        side = {"buy": "فشار خرید", "sell": "فشار فروش"}.get(fp.get("state"), "تعادل خرید/فروش")
        extra = []
        if fp.get("divergence"):
            extra.append("دلتا با قیمت نمی‌خواند (واگرایی)")
        if fp.get("absorption"):
            extra.append("حجم سنگین بدون جابه‌جایی = جذب سفارشات")
        if fp.get("lvn"):
            extra.append("ناحیهٔ کم‌حجم نزدیک: " + " / ".join(f"{x:.2f}" for x in fp["lvn"][:2]))
        lines = [f"{emoji} <b>{sym['label']} — فیلتر فوترپراینت: {side}</b> ({tf})",
                 f"دلتای {fp.get('bars', 0)} کندل اخیر {fp.get('delta_pct', 0.0):+.1f}٪ · "
                 f"شیب دلتای انباشته {fp.get('cum_slope_v', 0.0):+.2f} کندل",
                 f"POC <code>{fmt(fp.get('poc'), d)}</code> "
                 f"({100.0 * fp.get('poc_share', 0.0):.0f}٪ حجم) · قیمت <code>{px}</code> · "
                 f"تغییر این پنجره {fp.get('price_chg_pct', 0.0):+.2f}٪"]
        lines += [f"· {x}" for x in extra]
        if fl:
            lines.append("🔁 فلو همین الان سمت عوض کرد: "
                         f"{'خرید' if fl.get('dir') == 'bull' else 'فروش'}")
        lines.append("⚠️ دقت کن: این فوترپراینتِ واقعی نیست — برای فارکس دیتای تیک/سمتِ "
                      "سفارش رایگان وجود ندارد، پس دلتا از OHLCV تخمین زده می‌شود. "
                      "فیلتر است، نه سیگنال ورود.")
        return "\n".join(lines)
    if kind == "structure":
        evs = res["recent_bos"] + res["recent_sweep"]
        if not evs:
            return None
        lines = [f"🏗 <b>{sym['label']} structure</b> ({tf}) · <code>{px}</code>"]
        for e in evs[:4]:
            if "kind" in e and e["kind"] in ("bullish", "bearish"):
                lines.append(f"{'BOS' if not e.get('is_choch') else 'CHoCH'} {e['kind']} @ {fmt(e['level'], d)}")
            else:
                lines.append(f"{e['kind']} @ {fmt(e['level'], d)}")
        return "\n".join(lines)
    return None


def _fp_aligned(res, idea) -> bool:
    """The same question the EMA filter asks, asked of the tape: does the flow back this?"""
    fp = res.get("footprint") or {}
    if not fp or not idea or idea.get("name") == "footprint":
        return True
    if fp.get("state", "flat") == "flat":
        return True
    return (idea["side"] == "LONG") == (fp["state"] == "buy")


def _aligned(res, idea) -> bool:
    """Does this idea go with the EMA trend? Shown always, enforced when ema_gate is on."""
    if not idea:
        return True
    f = res.get("trend_flip")
    if idea.get("name") == "ema" and f:
        return True          # the flip itself is the signal; the trend label is one bar behind
    return (idea["side"] == "LONG") == (res["trend"] == "UP")


def event_key(res, kind) -> str:
    if kind == "setup":
        i = res["idea"]
        return f"setup:{i.get('name', 'combo')}:{i.get('i', res['n'])}:{i['side']}"
    if kind == "rsi":
        rc = res["rsi_cross"]
        return f"rsi:{rc['i'] if rc['i'] is not None else 'zone'}:{rc['dir']}"
    if kind == "macd":
        return f"macd:{res['macd_cross']['i']}:{res['macd_cross']['dir']}"
    if kind == "structure":
        ids = sorted([e["i"] for e in res["recent_bos"] + res["recent_sweep"]])
        return f"struct:{'-'.join(map(str, ids[-4:]))}"
    if kind == "trend":
        f = res.get("trend_flip") or {}
        return f"trend:{f.get('i')}:{f.get('dir')}"
    if kind == "footprint":
        fp = res.get("footprint") or {}
        return f"fp:{fp.get('state')}:{fp.get('cum_slope_v', 0.0):+.2f}"
    return kind


def log_signal(row: dict, path: str) -> None:
    exists = os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["time", "symbol", "tf", "kind", "side",
                                               "price", "entry", "sl", "tp", "rr", "rsi", "note"])
            if not exists:
                w.writeheader()
            w.writerow(row)
    except Exception as e:                                   # noqa: BLE001
        print(f"  ! log failed: {e}", file=sys.stderr)


def scan_once(symbols, tfs, cfg, tg, dry: bool, no_chart: bool, st: dict) -> int:
    alerts = 0
    os.makedirs(OUT_DIR, exist_ok=True)
    png = os.path.join(OUT_DIR, "chart.png")
    for sym in symbols:
        for tf in tfs:
            label = f"{sym['name']:<8} {tf:<4}"
            try:
                bars, prov = dataio.fetch(sym, tf, C.CONFIG["bars"], cfg.get("td_api_key", ""))
            except Exception as e:                            # noqa: BLE001
                print(f"{label} DATA ERROR: {str(e)[:150]}")
                continue
            try:
                res = D.build_setup(bars, C.CONFIG)
            except Exception as e:                            # noqa: BLE001
                print(f"{label} CALC ERROR: {type(e).__name__}: {e}")
                continue
            if res is None:
                print(f"{label} not enough data ({len(bars)} bars)")
                continue

            print(f"{label} {fmt(res['price'], sym.get('digits',2))}  trend {res['trend']}  "
                  f"RSI {res['rsi']:.1f}  bull:{res['score']['bull']} bear:{res['score']['bear']}  [{prov}]")

            kinds = []
            if C.ALERTS["setup"] and res["idea"]:
                kinds.append("setup")
            if C.ALERTS["rsi"] and res["rsi_cross"]["dir"] != "neutral":
                kinds.append("rsi")
            if C.ALERTS["macd"] and res["macd_cross"]:
                kinds.append("macd")
            if C.ALERTS["structure"] and (res["recent_bos"] or res["recent_sweep"]):
                kinds.append("structure")
            if C.ALERTS.get("trend", True) and res.get("trend_flip"):
                kinds.append("trend")
            fpd = res.get("footprint")
            if C.ALERTS.get("footprint", False) and C.CONFIG.get("footprint", False) and \
               fpd and fpd.get("flip"):
                kinds.append("footprint")

            for kind in kinds:
                if alerts >= C.TELEGRAM["max_per_run"]:
                    print("   (flood guard reached, stopping)")
                    return alerts
                key = f"{sym['name']}|{tf}|" + event_key(res, kind)   # tf inside the key:
                # the fast (5m/15m) and slow (4h) passes must not dedup each other's alerts
                if st["seen"].get(key) == res["n"]:
                    continue
                st["seen"][key] = res["n"]
                text = caption_for(sym, tf, res, kind)
                if not text:
                    continue
                alerts += 1
                i = res["idea"] if kind == "setup" else {}
                log_signal({"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                            "symbol": sym["name"], "tf": tf, "kind": kind,
                            "side": (i or {}).get("side", ""), "price": res["price"],
                            "entry": (i or {}).get("entry", ""), "sl": (i or {}).get("sl", ""),
                            "tp": (i or {}).get("tp", ""), "rr": f"{(i or {}).get('rr', 0):.2f}",
                            "rsi": f"{res['rsi']:.1f}", "note": "|".join(res["tags"]["bull"] + res["tags"]["bear"])[:80]},
                           LOG_FILE)
                if dry or not tg.enabled:
                    print("   " + text.replace("<b>", "").replace("</b>", "")
                               .replace("<code>", "").replace("</code>", "").replace("<i>", "").replace("</i>", ""))
                    continue
                sent = False
                if kind == "setup" and not no_chart:
                    try:
                        import render
                        render.draw(bars, res, sym, tf, png)
                        tg.send_photo(png, text)
                        sent = True
                    except Exception as e:                    # noqa: BLE001
                        print(f"   chart failed ({e}); sending text")
                if not sent:
                    try:
                        tg.send(text)
                    except Exception as e:                    # noqa: BLE001
                        print(f"   send failed: {e}")
    return alerts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("solo", "combo"), default=None,
                    help="solo = each setup on its own (no confluence), combo = require >= min_conditions")
    ap.add_argument("--ema-filter", choices=("on", "off", "signal"), default=None,
                    help="on = gate ideas by EMA trend, off = ignore it, signal = EMA as its own alert")
    ap.add_argument("--footprint-filter", choices=("on", "off", "signal"), default=None,
                    help="on = gate ideas by delta flow, off = ignore it, signal = flip alerts only")
    ap.add_argument("--ideas", default=None,
                    help="solo mode: comma separated setups, e.g. fvg,zone  (fvg,zone,sweep,bos)")
    ap.add_argument("--once", action="store_true", help="one scan cycle (what cron runs)")
    ap.add_argument("--loop", action="store_true", help="keep running (for a Space/VPS)")
    ap.add_argument("--every-minutes", type=int, default=0, help="cadence for --loop")
    ap.add_argument("--symbols", default=None, help="comma separated, e.g. XAUUSD,BTCUSD")
    ap.add_argument("--timeframes", default=None, help="e.g. 5m,15m,4h")
    ap.add_argument("--dry", action="store_true", help="no telegram sending")
    ap.add_argument("--no-chart", action="store_true", help="text only")
    ap.add_argument("--no-state", action="store_true", help="alert every matching bar (testing)")
    ap.add_argument("--gist", default=os.environ.get("SCAN_GIST") or os.environ.get("XRADAR_GIST", ""),
                    help="gist id used to persist seen-state between GitHub Actions runs")
    ap.add_argument("--gist-token", default=os.environ.get("GIST_TOKEN", ""),
                    help="fine-grained PAT with Gist read/write (only needed to persist state)")
    ap.add_argument("--whoami", action="store_true", help="print chat_id from the bot's updates")
    ap.add_argument("--token", default=None)
    a = ap.parse_args()

    ov = load_overrides()
    token = a.token or ov.get("TG_TOKEN") or _env("TG_TOKEN")
    chat = ov.get("TG_CHAT_ID") or _env("TG_CHAT_ID")

    if a.whoami:
        if not token:
            print("need --token", file=sys.stderr); return 2
        data = notify.TG(token, "0").get_updates()
        for u in data.get("result", []):
            msg = u.get("message") or u.get("channel_post") or {}
            who = msg.get("chat") or {}
            print(f"chat_id candidate: {who.get('id')}  ({who.get('type')} · {who.get('first_name')})")
            return 0
        print("no updates — send a message to your bot first, then re-run")
        return 1

    tfs = ([t.strip() for t in a.timeframes.split(",")] if a.timeframes
           else ov.get("timeframes") or C.DEFAULT_TIMEFRAMES)
    for t in tfs:
        if t not in C.TIMEFRAMES:
            print(f"unknown timeframe {t} (options: {', '.join(C.TIMEFRAMES)})", file=sys.stderr)
            return 2
    symbols = pick_symbols(ov, a.symbols)
    if not symbols:
        print("nothing to scan", file=sys.stderr); return 2

    cfg = {"td_api_key": ov.get("TD_API_KEY") or _env("TD_API_KEY")}
    # detector knobs can come from CONFIG_JSON {"detector": {...}} so a GitHub user can flip
    # mode/ideas without committing code; nested dicts (ideas) merge instead of replace
    det = ov.get("detector")
    if isinstance(det, str):
        try:
            det = json.loads(det)
        except json.JSONDecodeError:
            det = None
            print("CONFIG_JSON.detector is not valid JSON; ignoring", file=sys.stderr)
    for k, v in (det or {}).items():
        if isinstance(v, dict) and isinstance(C.CONFIG.get(k), dict):
            C.CONFIG[k].update(v)
        else:
            C.CONFIG[k] = v
    if a.mode:
        C.CONFIG["mode"] = a.mode
    if a.ema_filter == "on":
        C.CONFIG["ema_gate"] = True
        C.CONFIG["ideas"] = {**C.CONFIG["ideas"], "ema": False}
    elif a.ema_filter == "off":
        C.CONFIG["ema_gate"] = False
        C.CONFIG["ideas"] = {**C.CONFIG["ideas"], "ema": False}
    elif a.ema_filter == "signal":
        C.CONFIG["ema_gate"] = False
        C.CONFIG["ideas"] = {**C.CONFIG["ideas"], "ema": True}
        C.ALERTS["trend"] = True
    if a.footprint_filter == "on":
        C.CONFIG["footprint"] = True
        C.CONFIG["footprint_gate"] = True
    elif a.footprint_filter == "off":
        C.CONFIG["footprint"] = False
        C.CONFIG["footprint_gate"] = False
    elif a.footprint_filter == "signal":
        C.CONFIG["footprint"] = True
        C.CONFIG["footprint_gate"] = False
        C.ALERTS["footprint"] = True
        C.CONFIG["ideas"] = {**C.CONFIG["ideas"], "footprint": True}
    if a.ideas:
        live = [x.strip().lower() for x in a.ideas.split(",") if x.strip()]
        unknown = [x for x in live if x not in D.IDEA_LABELS]
        if unknown:
            print(f"unknown setup(s) {unknown}; options: {', '.join(D.IDEA_LABELS)}", file=sys.stderr)
            return 2
        C.CONFIG["ideas"] = {k: (k in live) for k in D.IDEA_LABELS}
        C.CONFIG["idea_order"] = live
    tg = notify.TG(token, chat)
    if not tg.enabled and not a.dry:
        print("TG_TOKEN / TG_CHAT_ID not set — running in dry mode", file=sys.stderr)
    st = load_state(STATE_FILE)
    if a.gist and (a.no_state or not os.path.exists(STATE_FILE)):
        # Actions wipes the workspace between runs: the gist is the only memory we have
        remote = gist_read(a.gist, a.gist_token)
        if remote.get("seen"):
            st["seen"] = {**remote["seen"], **st.get("seen", {})}
            st["digest"] = remote.get("digest", st.get("digest", {}))
            print(f"   state: loaded {len(st['seen'])} seen key(s) from gist")

    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}Z] {len(symbols)} symbols × {len(tfs)} timeframes · "
          f"{','.join(tfs)} · telegram={'on' if tg.enabled else 'OFF'}")
    while True:
        n = scan_once(symbols, tfs, cfg, tg, a.dry or not tg.enabled, a.no_chart, st)
        st["count"] = int(st.get("count", 0)) + n
        st["last_run"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if not a.no_state:
            save_state(STATE_FILE, st)
            if a.gist:
                # keep the most recent keys only: insertion order is "last time seen", and a
                # fat gist just slows every run down
                items = list(st.get("seen", {}).items())[-int(C.STATE_KEEP):]
                if gist_write(a.gist, a.gist_token, {"seen": dict(items),
                                                      "count": st["count"],
                                                      "last_run": st["last_run"]}):
                    print(f"   state: saved {len(items)} key(s) to gist")
        print(f"   → {n} new alert(s) · log: {os.path.basename(LOG_FILE)}")
        if C.ALERTS.get("daily_digest", False) and not (a.dry or not tg.enabled) \
           and a.gist and st.get("last_run"):
            tg.send(f"✅ یک دور اسکن تمام شد · {st['last_run']} · "
                    f" هشدارِ این دور: {n} · شمارشِ از روی gist: {st['count']}")
        if not a.loop:
            break
        time.sleep(max(60, (a.every_minutes or 10) * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
