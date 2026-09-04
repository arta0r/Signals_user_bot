"""Chart renderer: candles + FVG/zone/sweep/BOS annotations + entry/SL/TP,
with RSI and MACD sub-panels. English labels (matplotlib + Persian = missing glyphs)."""
from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                    # noqa: E402
from matplotlib.patches import Rectangle                            # noqa: E402

import config as C                                                  # noqa: E402


def _fmt(v, digits):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.{digits}f}"


def draw(bars, res, sym, tf, out_png, note=None):
    note_line = None
    room = False
    r = C.RENDER
    k = min(r["candles"], len(bars))
    seg = bars[-k:]
    off = len(bars) - k
    closes = [b["c"] for b in seg]
    rng = max(max(b["h"] for b in seg) - min(b["l"] for b in seg), 1e-9)
    pad = rng * 0.10

    plt.rcParams["axes.unicode_minus"] = False
    fig, (ax, axr, axm) = plt.subplots(
        3, 1, figsize=(r["width"], r["height"]), dpi=r["dpi"], sharex=True,
        gridspec_kw={"height_ratios": [3.0, 0.85, 0.85], "hspace": 0.06})
    fig.patch.set_facecolor("#0d1117")
    for a in (ax, axr, axm):
        a.set_facecolor("#0d1117")
        for s in a.spines.values():
            s.set_color("#30363d")
        a.tick_params(colors="#8b949e", labelsize=7)
        a.grid(color="#21262d", lw=0.5, alpha=0.6)

    up, dn = "#26a69a", "#ef5350"
    for i, b in enumerate(seg):
        col = up if b["c"] >= b["o"] else dn
        ax.plot([i, i], [b["l"], b["h"]], color=col, lw=0.7, zorder=2)
        lo, hi = sorted((b["o"], b["c"]))
        ax.add_patch(Rectangle((i - 0.34, lo), 0.68, max(hi - lo, rng * 0.0006),
                              facecolor=col, edgecolor=col, zorder=3))

    x = lambda i: i - off                                              # noqa: E731

    # --- FVGs
    for g in res.get("open_gaps", []) or []:
        if x(g["i"]) < -1:
            continue
        col = "#3b82f6" if g["dir"] == "bull" else "#f97316"
        ax.add_patch(Rectangle((x(g["i"]) - 0.5, g["bottom"]), k, g["top"] - g["bottom"],
                               facecolor=col, alpha=0.16, edgecolor=col, lw=0.6, zorder=1))
        gx = x(g["i"]) + 0.4
        gha = "left"
        if gx > k - 18:
            gx, gha = k - 0.6, "right"
        ax.text(gx, g["top"], f"FVG {'+' if g['dir']=='bull' else '-'}",
                color=col, fontsize=6.5, va="bottom", ha=gha)

    # --- order blocks / zones
    for ob, name, col in ((res.get("ob_bull"), "DEMAND", "#e11d48"),
                          (res.get("ob_bear"), "SUPPLY", "#22c55e")):
        if not ob or x(ob["i"]) < -1:
            continue
        ax.add_patch(Rectangle((x(ob["i"]) - 0.5, ob["bottom"]), k,
                               max(ob["top"] - ob["bottom"], rng * 0.002),
                               facecolor=col, alpha=0.14, edgecolor="none", zorder=0))
        tx = x(ob["i"]) + 0.4
        if tx > k - 20:
            tx, ha = k - 0.6, "right"
        else:
            ha = "left"
        ax.text(tx, ob["bottom"], name, color=col, fontsize=6.5, va="top", ha=ha)

    # --- sweeps + BOS
    for s in res.get("recent_sweep", []) or []:
        if x(s["i"]) < 0:
            continue
        ax.plot([x(s["i"]) - 6, x(s["i"]) + 3], [s["level"]] * 2, ls=":", lw=1.0,
                color="#eab308", zorder=4)
        ax.plot([x(s["i"]), x(s["i"])], [s["level"],
                 max(seg[s["i"] - off]["h"], s["level"])], color="#eab308", lw=1.0)
        if x(s["i"]) > k - 16:
            ax.text(x(s["i"]) - 3.4, s["level"], "LIQ", color="#eab308", fontsize=6.2,
                    va="center", ha="right")
        else:
            ax.text(x(s["i"]) + 3.4, s["level"], "LIQ", color="#eab308", fontsize=6.2, va="center")
    for b in res.get("recent_bos", []) or []:
        if x(b["i"]) < 0:
            continue
        col = up if b["kind"] == "bullish" else dn
        right = x(b["i"]) > k - 20
        ax.annotate(("CHoCH " if b.get("is_choch") else "BOS ") + b["kind"][0].upper(),
                    xy=(x(b["i"]), b["level"]),
                    xytext=(x(b["i"]) - 5.0 if right else x(b["i"]) + 2.5, b["level"]),
                    color=col, fontsize=6.5, va="center", ha="right" if right else "left",
                    arrowprops=dict(arrowstyle="-" if not right else "->", color=col, lw=0.7))

    # --- trade idea: make ENTRY and EXIT impossible to miss
    idea = res.get("idea")
    digits = sym.get("digits", 2)
    if idea:
        long_ = idea["side"] == "LONG"
        x0 = max(0, k - int(k * 0.62))
        x1 = k - 0.4
        bot, top = (idea["sl"], idea["tp"]) if long_ else (idea["tp"], idea["sl"])
        # risk + reward boxes, right half of the chart
        ax.add_patch(Rectangle((x0, idea["entry"]), x1 - x0, idea["tp"] - idea["entry"],
                               facecolor=up, edgecolor=up, lw=1.2, alpha=0.20, zorder=4))
        ax.add_patch(Rectangle((x0, idea["sl"]), x1 - x0, idea["entry"] - idea["sl"],
                               facecolor=dn, edgecolor=dn, lw=1.2, alpha=0.18, zorder=4))
        for lvl, col in ((idea["tp"], up), (idea["sl"], dn), (idea["entry"], "#ffffff")):
            ax.plot([x0, x1], [lvl, lvl], color=col, lw=1.5,
                    ls="--" if lvl != idea["entry"] else "-", zorder=5)

        # the actual entry point: arrow from a tag into the candle that produced the signal
        i_sig = max(0, min(k - 1, idea.get("i", len(bars) - 2) - off))
        span = abs(top - bot)
        _y0, _y1 = ax.get_ylim()
        _H = r["height"] * (3.0 / (3.0 + 0.85 + 0.85)) * r["dpi"]
        min_sep = 26.0 * (_y1 - _y0) / _H
        # tag sits left of the signal candle, on the entry line: never near the axes edge
        xtext = max(0.5, i_sig - int(k * 0.34))
        # if a band is too thin, a label centred in it would sit on the entry line:
        # park the ENTRY tag on the emptier side of the two bands instead
        band_tp = abs(idea["tp"] - idea["entry"])
        band_sl = abs(idea["entry"] - idea["sl"])
        tight = min(band_tp, band_sl) < min_sep * 2.2
        ytext = idea["entry"] + (min_sep * 1.15 if (tight and band_sl < band_tp) else
                                 (-min_sep * 1.15 if tight else 0.0))
        # TP / SL tags live in the middle of their own band, so nothing can clip them
        y_tp = (idea["entry"] + idea["tp"]) / 2.0
        y_sl = (idea["entry"] + idea["sl"]) / 2.0
        ax.annotate(f"◀  ENTRY  {idea['entry']:,.{digits}f}",
                    xy=(i_sig, idea["entry"]), xytext=(xtext, ytext),
                    fontsize=10.5, fontweight="bold", color="#ffffff", zorder=7,
                    va="center", ha="right", annotation_clip=True,
                    bbox=dict(fc="#1f6feb", ec="#ffffff", lw=0.9, boxstyle="round,pad=0.45"),
                    arrowprops=dict(arrowstyle="-|>", color="#ffffff", lw=1.8,
                                    shrinkA=3, shrinkB=4,
                                    connectionstyle="arc3,rad=" + ("0.16" if long_ else "-0.16")))
        ax.scatter([i_sig], [idea["entry"]], s=150, marker="o" if long_ else "X",
                   facecolor="#ffffff", edgecolor="#1f6feb", lw=1.6, zorder=8)

        # TP / SL labels: pinned to the right edge in fixed slots (TP above ENTRY, SL
        # below) so a thin band can never stack two tags on top of each other
        y0, y1 = ax.get_ylim()
        H = r["height"] * (3.0 / (3.0 + 0.85 + 0.85)) * r["dpi"]          # px of the price panel
        min_sep = 26.0 * (y1 - y0) / H
        y_tp = idea["entry"] + min_sep
        y_sl = idea["entry"] - min_sep
        ax.text(0.995, (y_tp - y0) / (y1 - y0), f"EXIT / TAKE PROFIT  →  {idea['tp']:,.{digits}f}",
                transform=ax.transAxes, color="k", fontsize=9.5, fontweight="bold",
                va="bottom", ha="right", zorder=7,
                bbox=dict(fc=up, ec="white", lw=0.7, boxstyle="round,pad=0.3"))
        ax.text(0.995, (y_sl - y0) / (y1 - y0), f"STOP LOSS  →  {idea['sl']:,.{digits}f}",
                transform=ax.transAxes, color="k", fontsize=9.5, fontweight="bold",
                va="top", ha="right", zorder=7,
                bbox=dict(fc=dn, ec="white", lw=0.7, boxstyle="round,pad=0.3"))

        pip = C.pip_size(sym.get("name", ""), sym.get("digits", 5))
        strip_y = (idea["sl"] + idea["tp"]) / 2.0
        # keep it short: prices are already printed on the three lines, this is only the plan
        strip = f"{idea['side']}  ·  {idea.get('label') or 'setup'}  ·  risk → target"
        if pip:
            r_pp = abs(idea["entry"] - idea["sl"]) / pip
            t_pp = abs(idea["tp"] - idea["entry"]) / pip
            strip += f"   |   risk {r_pp:.1f}p  →  target {t_pp:.1f}p   |   RR {idea['rr']:.2f}"
        note_line = strip                      # printed in the title block: no collisions
        room = True

    ax.set_xlim(-1, k)
    y_lo, y_hi = min(b["l"] for b in seg) - pad, max(b["h"] for b in seg) + pad
    if room:                                     # keep the ENTRY + the two right-edge slots inside
        lo2 = min(idea["entry"], idea["sl"], idea["tp"]) - min_sep * 1.6
        hi2 = max(idea["entry"], idea["sl"], idea["tp"]) + min_sep * 1.6
        ax.set_ylim(min(y_lo, lo2), max(y_hi, hi2))
    else:
        ax.set_ylim(y_lo, y_hi)
    tags = "/".join(res["tags"]["bull"] if (res["idea"] or {}).get("side") == "LONG"
                    else res["tags"]["bear"]) or ""
    ttl = (f"{sym['label']}  ·  {tf}  ·  {_fmt(res['price'], digits)}  ·  "
           f"trend {res['trend']}  ·  ATR {_fmt(res['atr'], digits)}")
    if res["idea"]:
        ttl += f"  ·  {res['idea']['side']} ({tags})"
        if res["idea"].get("name") and res["idea"]["name"] != "combo":
            ttl += f"  ·  {res['mode'] or 'solo'} idea"
    elif tags:
        ttl += f"  ·  watch: {tags}"
    ax.set_title(ttl, color="#e6edf3", fontsize=9.5, loc="left", pad=8)
    if note_line:                                  # the plan, top-left, above the candles
        yl = ax.get_ylim()
        ax.text(0.004, 0.985, note_line, transform=ax.transAxes, color="#e6edf3",
                fontsize=8.6, va="top", ha="left", zorder=7, clip_on=False,
                bbox=dict(fc="#161b22", ec="#8b949e", lw=0.5, boxstyle="round,pad=0.3"))
    if note:
        ax.text(0.4, ax.get_ylim()[0] + pad * 0.35, note, color="#8b949e", fontsize=6.8)

    # --- RSI / MACD
    import detector as D                                            # noqa: PLC0415
    allc = [b["c"] for b in bars]
    rs = D.rsi(allc, C.CONFIG["rsi_period"])[off:]
    li, si, hi_ = D.macd(allc, C.CONFIG["macd_fast"], C.CONFIG["macd_slow"], C.CONFIG["macd_signal"])
    li, si, hi_ = li[off:], si[off:], hi_[off:]

    axr.plot(range(k), [v if not math.isnan(v) else None for v in rs], color="#a855f7", lw=1.1)
    for lvl in (C.CONFIG["rsi_hi"], C.CONFIG["rsi_lo"]):
        axr.axhline(lvl, color="#8b949e", ls=":", lw=0.7)
    axr.set_ylim(8, 92)
    axr.set_ylabel("RSI", color="#8b949e", fontsize=7)

    axm.bar(range(k), [v if not math.isnan(v) else 0 for v in hi_],
            color=[up if (not math.isnan(v) and v >= 0) else dn for v in hi_], width=0.7)
    axm.plot(range(k), [v if not math.isnan(v) else None for v in li], color="#60a5fa", lw=1.0)
    axm.plot(range(k), [v if not math.isnan(v) else None for v in si], color="#f59e0b", lw=1.0)
    axm.axhline(0, color="#8b949e", lw=0.6)
    axm.set_ylabel("MACD", color="#8b949e", fontsize=7)

    ax.set_xticks([])
    axr.set_xticks([])
    axm.set_xlabel(f"{len(seg)} bars of {tf} · closed candles only · "
                   f"generated {__import__('datetime').datetime.now(__import__('datetime').timezone.utc):%Y-%m-%d %H:%M} UTC",
                   color="#8b949e", fontsize=6.5)
    fig.savefig(out_png, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)
    return out_png
