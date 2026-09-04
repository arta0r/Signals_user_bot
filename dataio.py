"""Free data sources: Yahoo chart API (no key), Kraken public OHLC (no key),
Twelve Data time_series (free key, 800 credits/day)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config as C

YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": C.UA,
                                               "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _jload(raw) -> dict:
    if isinstance(raw, (bytes, str)):
        raw = json.loads(raw)
    return raw


# --------------------------------------------------------------------- yahoo
def yahoo_candles(ticker: str, interval: str, rng: str, limit: int):
    last_err = None
    for host in YAHOO_HOSTS:
        q = urllib.parse.urlencode({"range": rng, "interval": interval,
                                    "includePrePost": "false", "events": "div,split",
                                    "ignoreAnnotations": "true"})
        try:
            data = _jload(_get(f"https://{host}/v8/finance/chart/{urllib.parse.quote(ticker)}?{q}"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            continue
        res = (data.get("chart") or {}).get("result")
        if not res:
            last_err = RuntimeError((data.get("chart") or {}).get("error") or "empty result")
            continue
        res = res[0]
        ts = res.get("timestamp") or []
        ind = res.get("indicators", {}).get("quote", [{}])[0]
        out = []
        for k, t in enumerate(ts):
            try:
                o, h, lo, c = (ind["open"][k], ind["high"][k], ind["low"][k], ind["close"][k])
            except (IndexError, TypeError):
                continue
            if None in (o, h, lo, c):
                continue
            v = (ind.get("volume") or [None] * len(ts))[k]
            out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(lo),
                        "c": float(c), "v": float(v or 0.0)})
        if len(out) < 30:
            last_err = RuntimeError(f"only {len(out)} usable candles")
            continue
        # the newest bar is still forming on Yahoo too; we keep it and the detector drops it
        return out
    raise RuntimeError(f"yahoo({ticker}): {last_err}")


# --------------------------------------------------------------------- kraken
KRAKEN_PAIRS = {"XAUTUSD": "XAUTUSD", "BTCUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD",
                "SOLUSD": "XSOLZUSD", "XRPUSD": "XXRPZUSD", "LTCUSD": "XLTCZUSD"}


def kraken_candles(ticker: str, interval_min: int, limit: int):
    pair = KRAKEN_PAIRS.get(ticker, ticker)
    n = min(max(limit, 100), 720)
    data = _jload(_get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}"))
    if data.get("error"):
        raise RuntimeError(f"kraken: {data['error']}")
    key = [k for k in data.get("result", {}) if k != "last"]
    if not key:
        raise RuntimeError("kraken: no result key")
    rows = data["result"][key[0]]
    out = []
    for row in rows[-n:]:
        o, h, lo, c, v = (float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[6]))
        out.append({"t": int(row[0]), "o": o, "h": h, "l": lo, "c": c, "v": v})
    if len(out) < 30:
        raise RuntimeError(f"kraken: only {len(out)} candles")
    return out


# --------------------------------------------------------------------- twelvedata
def td_candles(symbol: str, interval: str, limit: int, api_key: str):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": interval,
                                "outputsize": min(max(limit, 100), 5000),
                                "order": "desc", "apikey": api_key})
    data = _jload(_get("https://api.twelvedata.com/time_series?" + q))
    if "values" not in data:
        raise RuntimeError(f"twelvedata: {data.get('message') or data.get('status')}")
    out = []
    for row in reversed(data["values"]):
        try:
            t = int(time.mktime(time.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")))
        except (ValueError, KeyError):
            t = 0
        out.append({"t": t, "o": float(row["open"]), "h": float(row["high"]),
                    "l": float(row["low"]), "c": float(row["close"]),
                    "v": float(row.get("volume") or 0.0)})
    return out


# --------------------------------------------------------------------- helpers
def resample(candles: list, factor: int):
    """Collapse N consecutive candles into one (used to build 4h from 1h)."""
    if factor <= 1:
        return candles
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i:i + factor]
        out.append({"t": chunk[0]["t"], "o": chunk[0]["o"],
                    "h": max(c["h"] for c in chunk), "l": min(c["l"] for c in chunk),
                    "c": chunk[-1]["c"], "v": sum(c["v"] for c in chunk)})
    return out


def fetch(sym: dict, tf: str, limit: int, td_key: str = ""):
    """Returns (candles, provider_used). Tries the configured provider, then Yahoo."""
    tfc = C.TIMEFRAMES[tf]
    tries = []
    if sym["provider"] == "yahoo":
        tries.append(lambda: yahoo_candles(sym["ticker"], tfc["yahoo_interval"], tfc["range"], limit))
    elif sym["provider"] == "kraken":
        tries.append(lambda: kraken_candles(sym["ticker"], tfc["kraken"], limit))
    elif sym["provider"] == "twelvedata" and td_key:
        tries.append(lambda: td_candles(sym["ticker"], tfc.get("td_interval", tf), limit, td_key))
    # universal fallback: yahoo for anything with a ticker, kraken for metals/crypto names
    if sym["provider"] != "yahoo" and sym.get("ticker_fallback"):
        tries.append(lambda: yahoo_candles(sym["ticker_fallback"], tfc["yahoo_interval"], tfc["range"], limit))
    if sym["provider"] != "kraken" and sym["name"] in KRAKEN_PAIRS:
        tries.append(lambda: kraken_candles(KRAKEN_PAIRS[sym["name"]], tfc["kraken"], limit))

    errs = []
    for f in tries:
        try:
            bars = f()
            if tfc.get("resample"):
                bars = resample(bars, tfc["resample"])
            if tf == "4h" and sym["provider"] == "kraken":
                bars = resample(bars, 4)
            return bars, sym["provider"]
        except Exception as e:                      # noqa: BLE001 - report and move on
            errs.append(f"{type(e).__name__}: {e}")
            time.sleep(0.4)
    raise RuntimeError(" | ".join(errs) or "no provider configured")
