"""
Telegram message text (Persian), built here so it can be tested without Telegram.

Layout rules — the previous version looked "weak" because of exactly these:
  1. One number per line, inside <code>. Persian is RTL, so every number that shares a line
     with Persian words can jump to the other side of the line when Telegram wraps it.
  2. No long lines: a mobile Telegram bubble is ~35 Persian chars wide.
  3. Fixed field order, always the same, so you can read a message without reading it.
  4. Only the label stays bold; everything else is plain so nothing competes for attention.
"""
from __future__ import annotations

import config as C

SIDE = {"LONG": "🟢 خرید", "SHORT": "🔴 فروش"}
TREND = {"UP": "صعودی", "DOWN": "نزولی"}


def fmt(v, digits):
    return f"{v:,.{digits}f}" if isinstance(v, (int, float)) and v == v else "—"


def _hist(v):
    """MACD histograms are tiny on FX: keep ~3 significant digits, not 9 decimal places."""
    if not isinstance(v, (int, float)) or v != v:
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:,.2f}"
    return f"{v:.3g}"


def _num(label, value, digits):
    return f"{label}\n<code>{fmt(value, digits)}</code>"


def setup(sym, tf, res) -> str:
    i = res["idea"]
    d = sym.get("digits", 2)
    tags = i.get("label") or " · ".join(
        res["tags"]["bull"] if i["side"] == "LONG" else res["tags"]["bear"])
    lines = [f"<b>{sym['label']} · {tf}</b>", SIDE.get(i["side"], i["side"])]
    lines += [
        _num("🎯 ورود", i["entry"], d),
        _num("✅ خروج (TP)", i["tp"], d),
        _num("⛔ حد ضرر (SL)", i["sl"], d),
    ]
    pip = C.pip_size(sym["name"], sym.get("digits", 5))
    risk_p = abs(i["entry"] - i["sl"]) / pip if pip else 0.0
    tgt_p = abs(i["tp"] - i["entry"]) / pip if pip else 0.0
    extra = f"  ·  {risk_p:.1f} / {tgt_p:.1f} پیپ" if 0.5 <= risk_p <= 400 else ""
    lines.append(f"📐 ریسک به ریوارد  <code>{i['rr']:.2f}</code>{extra}")
    lines.append(f"🧭 EMA: {TREND.get(res['trend'], res['trend'])} · "
                 f"{'✅ هم‌سو' if aligned(res, i) else '⚠️ خلاف'}")
    if C.CONFIG.get("footprint", False):
        lines.append(f"👣 فلو: {flow_bits(res.get('footprint'))} · "
                     f"{'✅ هم‌سو' if fp_aligned(res, i) else '⚠️ خلاف'}")
    conf = i.get("confluence", 1)
    lines.append(f"🧩 ستاپ: {tags}" + (f" · {conf} شرط هم‌سو" if conf > 1 else ""))
    lines.append(f"📊 RSI  <code>{res['rsi']:.1f}</code>")
    lines.append(f"📈 MACD hist  <code>{_hist(res['macd_hist'])}</code>")
    lines.append("—\nروی کندل بسته‌شده · پیشنهاد تحلیل، نه سیگنال")
    return "\n".join(lines)


def rsi(sym, tf, res):
    rc = res["rsi_cross"]
    if rc["dir"] == "neutral":
        return None
    d = sym.get("digits", 2)
    head = "🔻 اشباع خرید" if rc["dir"] == "overbought" else "🔺 اشباع فروش"
    lines = [f"<b>{sym['label']} · {tf}</b>", f"RSI {head}"]
    lines.append(_num("مقدار RSI", rc["value"], 1))
    lines.append(_num("قیمت", res["price"], d))
    if rc["i"] is None:
        lines.append("در ناحیه است، کراسِ همین کندل نیست")
    return "\n".join(lines)


def macd(sym, tf, res):
    m = res["macd_cross"]
    if not m:
        return None
    d = sym.get("digits", 2)
    lines = [f"<b>{sym['label']} · {tf}</b>",
             "🟢 کراس MACD صعودی" if m["dir"] == "bull" else "🔴 کراس MACD نزولی",
             _num("قیمت", res["price"], d),
             f"هیستوگرام\n<code>{_hist(res['macd_hist'])}</code>",
             f"RSI  <code>{res['rsi']:.1f}</code>",
             f"EMA  {TREND.get(res['trend'], res['trend'])}"]
    return "\n".join(lines)


def trend(sym, tf, res):
    f = res.get("trend_flip") or {}
    d = sym.get("digits", 2)
    who = "EMA 21/55" if C.CONFIG.get("ema_lines", "cross") == "cross" else "قیمت / EMA 55"
    lines = [f"<b>{sym['label']} · {tf}</b>",
             "🟢 فیلتر EMA: روند صعودی" if f.get("dir") == "bull" else "🔴 فیلتر EMA: روند نزولی",
             f"کراس روی {who}",
             _num("EMA 21", res.get("ema_fast_v"), d),
             _num("EMA 55", res.get("ema_slow_v"), d),
             _num("قیمت", res["price"], d),
             "⚖️ مجاز/ممنوع است، نه سیگنال ورود"]
    return "\n".join(lines)


def footprint(sym, tf, res):
    fp = res.get("footprint") or {}
    if not fp:
        return None
    d = sym.get("digits", 2)
    state = {"buy": "🟢 فشار خرید", "sell": "🔴 فشار فروش"}.get(fp.get("state"), "⚪ تعادل")
    lines = [f"<b>{sym['label']} · {tf}</b>", f"👣 فیلتر فوترپراینت — {state}",
             f"دلتای {fp.get('bars', 0)} کندل اخیر",
             _num("سهم دلتا (٪)", fp.get("delta_pct", 0.0), 1),
             _num("شیب دلتای انباشته (کندل)", fp.get("cum_slope_v", 0.0), 2)]
    if fp.get("divergence"):
        lines.append("واگرایی: دلتا با قیمت نمی‌خواند")
    if fp.get("absorption"):
        lines.append("جذب: حجم سنگین، بدون جابه‌جایی")
    if fp.get("flip"):
        lines.append("🔁 فلو همین الان سمت عوض کرد")
    lines.append("—\nدلتا از OHLCV تخمین زده می‌شود\nحجم واقعیِ فارکس رایگان نیست")
    return "\n".join(lines)


def structure(sym, tf, res):
    evs = res["recent_bos"] + res["recent_sweep"]
    if not evs:
        return None
    d = sym.get("digits", 2)
    lines = [f"<b>{sym['label']} · {tf}</b>", "🏗 رویداد ساختاری"]
    for e in evs[:4]:
        if e.get("kind") in ("bullish", "bearish"):
            nm = ("CHoCH" if e.get("is_choch") else "BOS") + " " + \
                 ("صعودی" if e["kind"] == "bullish" else "نزولی")
        else:
            nm = "سویپ نقدینگی"
        lines.append(f"{nm}\n<code>{fmt(e['level'], d)}</code>")
    return "\n".join(lines)


RESULT = {"tp": "✅ تارگت خورد", "sl": "⛔ حد ضرر خورد",
          "expired": "⚪ نه تارگت خورد، نه حد ضرر"}


def _dur(h, bars) -> str:
    if h is None:
        return f"مدت: {bars} کندل" if bars else ""
    return f"مدت {h:.1f} ساعت"


def outcome(sym: dict, tf: str, oc: dict) -> str:
    """
    The "what happened to it" follow-up to a setup alert. Same layout rules as everything
    else here: one number per <code> line, short lines, fixed field order.
    """
    d = sym.get("digits", 2)
    idea = (oc.get("idea") or "").replace("_", "\\_")
    head = RESULT.get(oc.get("result"), "پایان معامله")
    if oc.get("amb"):
        head += " · یک کندل هر دو سطح"
    lines = [f"<b>{oc.get('label') or sym['label']} · {tf}</b>", head]
    if idea:
        lines.append(f"🧩 {idea}")
    lines += [_num("🎯 ورود", oc.get("entry"), d),
              _num("🏁 خروج", oc.get("px"), d)]
    if oc.get("result") in ("tp", "sl"):
        lines.append(_num("📏 جابه‌جایی (پیپ)", oc.get("pips"), 1))
        if oc.get("rr_done"):
            lines.append(f"📐 خروج در R  <code>{oc['rr_done']:+.2f}</code>")
    dur = _dur(oc.get("hours"), oc.get("bars"))
    if dur:
        lines.append(dur)
    lines.append("—\nنتیجه‌ی همان پیام · پیشنهاد تازه نیست")
    return "\n".join(lines)


BUILDERS = {"setup": setup, "rsi": rsi, "macd": macd, "trend": trend,
            "footprint": footprint, "structure": structure}


def for_kind(sym, tf, res, kind):
    b = BUILDERS.get(kind)
    return b(sym, tf, res) if b else None


# ---- the two alignment questions, kept in one place so chat text and gate agree ----
def aligned(res, idea) -> bool:
    if not idea:
        return True
    if idea.get("name") == "ema" and res.get("trend_flip"):
        return True          # the flip is the signal; the trend label lags one bar
    return (idea["side"] == "LONG") == (res["trend"] == "UP")


def fp_aligned(res, idea) -> bool:
    fp = res.get("footprint") or {}
    if not fp or not idea or idea.get("name") == "footprint":
        return True
    if fp.get("state", "flat") == "flat":
        return True
    return (idea["side"] == "LONG") == (fp["state"] == "buy")


def flow_bits(fp) -> str:
    if not fp:
        return "بدون داده"
    fa = {"buy": "فشار خرید", "sell": "فشار فروش", "flat": "تعادل"}
    s = fa.get(fp.get("state", "flat"), "—")
    s += f" ({fp.get('delta_pct', 0.0):+.1f}٪)"
    if fp.get("absorption"):
        s += " · جذب"
    if fp.get("divergence"):
        s += " · واگرایی"
    return s
