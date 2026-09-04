"""All tunables live here so GitHub Actions users never have to touch code.

You can also override symbols/timeframes at runtime with a secret named CONFIG_JSON,
e.g. {"symbols": ["gold","btc"], "timeframes": ["15m"]}
"""

# ------------------------------------------------------------------ symbols
# provider: yahoo (no key needed) | kraken (no key) | twelvedata (needs a key)
# "pip" is what we quote risk in. Gold/indices are not "0.0001 = 1 pip" instruments,
# so it is declared per symbol instead of guessed from the name.
SYMBOLS = [
    {"name": "XAUUSD", "pip": 0.1, "label": "GOLD / XAUUSD", "provider": "yahoo", "ticker": "GC=F",
     "digits": 2, "note": "COMEX gold futures on Yahoo; XAUUSD=X is not offered there"},
    {"name": "XAUUSD.K", "pip": 0.1, "label": "GOLD (TetherGold proxy)", "provider": "kraken", "ticker": "XAUTUSD",
     "digits": 1, "note": "24/7 spot proxy; enable if Yahoo data is rate-limited"},
    {"name": "EURUSD", "pip": 0.0001,   "label": "EURUSD",  "provider": "yahoo", "ticker": "EURUSD=X", "digits": 5},
    {"name": "GBPUSD", "pip": 0.0001,   "label": "GBPUSD",  "provider": "yahoo", "ticker": "GBPUSD=X", "digits": 5},
    {"name": "USDJPY", "pip": 0.01,   "label": "USDJPY",  "provider": "yahoo", "ticker": "JPY=X",    "digits": 3},
    {"name": "AUDUSD", "pip": 0.0001,   "label": "AUDUSD",  "provider": "yahoo", "ticker": "AUDUSD=X", "digits": 5},
    {"name": "USDCAD", "pip": 0.0001,   "label": "USDCAD",  "provider": "yahoo", "ticker": "CAD=X",    "digits": 5},
    {"name": "DXY", "pip": 0.01,      "label": "DXY (cash)", "provider": "yahoo", "ticker": "DX-Y.NYB", "digits": 3, "invert": True},
    {"name": "SPX500", "pip": 1.0,   "label": "S&P 500 (ES)", "provider": "yahoo", "ticker": "ES=F",  "digits": 1},
    {"name": "US30", "pip": 1.0,     "label": "Dow Jones (YM)", "provider": "yahoo", "ticker": "YM=F", "digits": 1},
    {"name": "NAS100", "pip": 1.0,   "label": "Nasdaq 100 (NQ)", "provider": "yahoo", "ticker": "NQ=F", "digits": 1},
    {"name": "BTCUSD", "pip": 1.0,   "label": "BITCOIN", "provider": "yahoo", "ticker": "BTC-USD", "digits": 0},
    {"name": "ETHUSD", "pip": 0.1,   "label": "ETHEREUM", "provider": "yahoo", "ticker": "ETH-USD", "digits": 1},
    {"name": "SOLUSD", "pip": 0.01,   "label": "SOLANA",   "provider": "yahoo", "ticker": "SOL-USD", "digits": 2},
]

# Direction convention for the session-bias read: True = the symbol RISING means gold
# would rather go UP (EURUSD, SPX500, BTC...); False = it rising means pressure on gold
# (USDJPY, USDCAD, DXY). Anything not listed is treated as True.
GOLD_UP = {"XAUUSD": True, "XAUUSD.K": True, "EURUSD": True, "GBPUSD": True, "AUDUSD": True,
           "USDJPY": False, "USDCAD": False, "DXY": False, "SPX500": True, "US30": True,
           "NAS100": True, "BTCUSD": True, "ETHUSD": True, "SOLUSD": True}

# Only these are scanned per run; keep it small or you will rate-limit yourself.
DEFAULT_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "DXY",
                   "SPX500", "US30", "NAS100", "BTCUSD", "ETHUSD"]

# ------------------------------------------------------------------ timeframes
TIMEFRAMES = {
    "5m":  {"yahoo_interval": "5m",  "range": "7d",  "kraken": 5},
    "15m": {"yahoo_interval": "15m", "range": "60d", "kraken": 15},
    "1h":  {"yahoo_interval": "1h",  "range": "730d", "kraken": 60},
    "4h":  {"yahoo_interval": "1h",  "range": "730d", "kraken": 240, "resample": 4},
}
DEFAULT_TIMEFRAMES = ["15m", "4h"]   # measured: M5 on gold is mostly noise (330-450 alerts/mo/symbol, win% below breakeven)

# ------------------------------------------------------------------ detector
CONFIG = {
    "min_bars": 90,
    "analysis_bars": 320,       # bars actually analysed; keeps replay/backtest cost linear
    "bars": 260,              # candles requested per symbol/timeframe
    "drop_forming": True,     # NEVER decide on the forming candle (kills repaint)

    "ema_fast": 21,
    "ema_slow": 55,
    "rsi_period": 14,
    "rsi_hi": 70.0,
    "rsi_lo": 30.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "atr_period": 14,

    "swing_left": 3,
    "swing_right": 3,
    "sweep_lookback": 40,
    "signal_lookback_bars": 2,   # a structure event counts as "fresh" for 2 closed bars
    "min_conditions": 2,         # setup alert needs >= 2 of: FVG / zone / sweep / BOS
                                 # 3 is NOT measurably better: it cut volume ~4x and left win% flat
    "fvg_fill_tol": 0.35,        # gap counts as used once 35% is filled
    "sl_atr": 0.5,               # stop beyond the swept level, in ATRs
    "min_rr": 1.6,               # reject the idea if reward/risk is worse than this
    "mode": "solo",              # "solo" = each setup is its OWN idea/SL/TP (no confluence needed)
                                 # "combo" = old style: >= min_conditions of the four must agree
    "ideas": {                   # which setups are live in solo mode (combo ignores this).
                                 # measured 90d on H4 (win% = TP before SL, market fills):
        "fvg":   True,           #   fvg 25-35% · bos 19-33% · sweep 10-29% -> fvg/zone are the
        "zone":  True,           #   two that at least hold up; disable the rest to cut noise
        "sweep": False,          # liquidity sweep -> entry at the sweep close, stop beyond the wick
        "bos":   False,          # BOS/CHoCH -> entry at the breaking close, stop at the last extreme
        "footprint": False,      # footprint read as an idea of its own (see footprint.py)
        "ema":   False,          # EMA trend on its own (see ema_lines below). Measured: it is a
                                 # filter, not an edge -> keep it OFF as a signal, ON as a gate
    },
    # --- the EMA filter, separate from everything else -------------------------
    "ema_lines": "cross",        # "cross" = EMA21 crossing EMA55; "price" = close crossing EMA55
    "ema_gate": False,           # True = no idea against the EMA trend (per-side, all setups)
    "ema_gate_bars": 12,         # ...unless a flip happened in the last N bars (fresh = allowed)
    # --- the footprint filter, its own separate thing -------------------------
    "footprint": False,          # True = the delta/absorption read is live (line + gate + alert)
    "footprint_gate": False,     # True: an idea against the flow gets rejected
    "fp_window": 20,             # bars the delta / volume profile are measured over
    "fp_strength": 1.4,          # |cum delta slope| in "average bar volumes" to call a side
    "fp_absorb_vol": 1.6,        # volume >= N x median counts as heavy (absorption candidate)
    "fp_absorb_body": 0.4,       # ...and body or range <= N x median => the move was absorbed
    "fp_blocks_bars": 12,        # a flow flip inside N bars overrules the gate
    "fp_edge": 0.72,             # ...and the flip must happen at an edge of the window range
    "fp_require_div": True,      # a flip only counts with divergence or absorption behind it
    "fp_require_edge": True,     # ...and at the top/bottom of the window (mid-range flips = noise)
    "idea_order": ["fvg", "zone", "sweep", "bos"],   # when several fire on the same bar, this one wins
    "solo_entry": "market",      # "market" = entry at the signal-bar close (measured: retest
                                 # limit fills are fine but 30%+ never reach the level at all)
                                 # "retest" = limit at the level, needs a fill, can be missed
    "solo_sl_buffer": 0.25,      # stop distance beyond the level, in ATRs (solo mode)
    "max_entry_offset_atr": 0.75,# >0: drop an idea whose limit entry sits further than this
                                 # many ATRs from the last close (kills stale retest ideas)
    "require_sweep": False,      # combo only: True = no sweep, no idea
    "target_at_liquidity": False,# True = TP at the swept level (both modes). Measured 90d/5:
                                 # it rarely changes anything, because TP = max(liquidity,
                                 # entry + min_rr*risk) already ignores far/near liquidity
    "require_trend_align": False,  # True = never issue a LONG against the EMA trend (fewer, cleaner)
}

# ------------------------------------------------------------------ alerts
STATE_KEEP = 800          # how many "already alerted" keys to remember in the gist

ALERTS = {
    "setup": True,      # the enabled setups, one alert per idea -> entry/SL/TP with chart
    "rsi": True,        # RSI crossing above 70 / below 30
    "macd": True,       # MACD(12,26,9) line/signal cross
    "structure": False,  # every bare BOS/CHoCH and every sweep, even without a trade plan
    "footprint": False,  # the footprint read alone: ping when the flow changes side
    "trend": True,       # the EMA filter alone: ping when EMA21/55 (or close/EMA55) flips
    "daily_digest": False,  # a short "cycle finished" note (needs SCAN_GIST to count alerts)
}

RENDER = {
    "candles": 110,
    "width": 11.0,
    "height": 6.4,
    "dpi": 110,
}

TELEGRAM = {
    "disable_rich_preview": True,
    "max_per_run": 8,           # flood guard
}

# --------------------------------------------------------------- session bias
# When each session opens (UTC) and what to read before it opens. GitHub Actions
# cron fires a few minutes after these times; session.py tolerates that.
SESSIONS = [
    {"name": "sydney",   "label_fa": "سیدنی (آسیای صبح)",      "utc_hour": 22, "utc_minute": 0,
     "symbols": ["XAUUSD", "AUDUSD", "BTCUSD"]},
    {"name": "tokyo",    "label_fa": "توکیو (آسیا)",            "utc_hour": 0,  "utc_minute": 0,
     "symbols": ["XAUUSD", "AUDUSD", "USDJPY", "BTCUSD"]},
    {"name": "london",   "label_fa": "لندن (اروپا)",            "utc_hour": 7,  "utc_minute": 0,
     "symbols": ["XAUUSD", "EURUSD", "GBPUSD", "DXY"]},
    {"name": "newyork",  "label_fa": "نیویورک (آمریکا)",        "utc_hour": 13, "utc_minute": 30,
     "symbols": ["XAUUSD", "EURUSD", "USDJPY", "DXY", "SPX500", "NAS100"]},
]
# A rising DXY / USDJPY is pressure on gold, so those count with a negative weight in the
# session-bias aggregate. GOLD_UP above is the single source of truth for that.

# ------------------------------------------------------------------ session bias knobs
BIAS = {
    "ema_filter": True,          # the session read also reports (and can require) EMA agreement
    "footprint": False,          # ...and the delta/absorption read (own line per symbol)
    "footprint_filter": False,   # strong flow against the side -> session read goes flat
    "pre_session_hours": 12,     # read the 12 hours before the open
    "bias_timeframe": "1h",      # hourly candles for that read (free on Yahoo)
    "symbols": ["XAUUSD", "EURUSD", "USDJPY", "DXY", "SPX500", "BTCUSD"],
}


def pip_size(symbol_name, digits=5):
    """Pip size from config first, name heuristic as the fallback (never a silent 0.0001)."""
    for entry in SYMBOLS:
        if entry["name"].upper() == str(symbol_name).upper():
            return float(entry.get("pip", 0.0001))
    name = str(symbol_name).upper()
    if "JPY" in name:
        return 0.01
    if any(x in name for x in ("XAU", "GOLD", "SOL", "BTC", "ETH", "SPX", "US30", "NAS", "DXY")):
        return 0.1 if "XAU" in name or "GOLD" in name else 1.0
    return 0.0001 if int(digits) >= 5 else 0.01


SESSION_BIAS_SYMBOLS = BIAS["symbols"]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
