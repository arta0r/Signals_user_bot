"""
Signal lifecycle: fire once, then report the outcome.

A trade plan (entry/SL/TP) is remembered in the same gist the bot already uses for its
"already alerted" memory, so between two GitHub Actions runs it knows what is still open.
On every later scan the finished candles after the signal are walked in order: whichever
level is touched first wins. Nothing is guessed from the current price alone, because a
run every 2 hours can skip over a take-profit that the next candle erased.

Bar format is whatever dataio returns: {"t": epoch, "o":, "h":, "l":, "c":, "v":}.
"""
from __future__ import annotations


def snapshot(sym_name: str, tf: str, i: dict, res: dict, include_forming: bool = False) -> dict | None:
    """
    Remember one trade plan. `n_at` is the index of the last CLOSED bar at signal time, so
    only candles printed after it can close the trade.
    """
    entry, sl, tp = i.get("entry"), i.get("sl"), i.get("tp")
    if not i.get("side") or not _ok(entry) or not _ok(sl) or not _ok(tp):
        return None
    return {
        "side": i["side"],
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "rr": float(i.get("rr", 0.0) or 0.0),
        # index of the last closed bar; the timestamp below is what survives a series that
        # rolled over between two runs, this is the cheap fast path
        "n_at": _anchor(res) - (1 if include_forming else 0),
        "t": _last_time(res),
        "idea": i.get("label", "") or "",
    }


def _start_index(open_pos: dict, data: list) -> int:
    """
    Index of the first bar that may close the trade. `n_at` is the cheap answer, but state
    that was written by an older run (or after the series rolled over) has no usable index,
    so fall back to the timestamp of the signal bar and search for it.
    """
    n_at = open_pos.get("n_at")
    t0 = open_pos.get("t")
    if isinstance(n_at, int) and 0 <= n_at < len(data) - 1:
        ts = (data[n_at] or {}).get("t")
        # the index is only trusted when it still points at the signal bar; a long gap
        # between two runs rolls the series over and every stored index shifts
        if t0 is None or ts is None or abs(float(ts) - float(t0)) <= 4 * 86_400:
            return n_at + 1
    if t0 is not None and data:
        t0 = float(t0)
        for j, bar in enumerate(data):
            ts = (bar or {}).get("t")
            if ts is None:
                continue
            if float(ts) > t0:
                return j
            if int(float(ts)) == int(t0):
                return j + 1
        return len(data)          # every bar we have is older than the signal: nothing to read
    return max(0, len(data) - 1)


def _ok(v) -> bool:
    return isinstance(v, (int, float)) and v == v and v > 0


def _is_full(res: dict) -> bool:
    """True when scan.py handed over the whole fetched series, not just the window."""
    return len(res.get("bars") or []) > len(res.get("data") or [])


def _series(res: dict) -> list:
    """The bar list the follow-up must read: the fetched series, or the detector window."""
    return (res.get("bars") if _is_full(res) else None) or res.get("data") or []


def _anchor(res: dict) -> int:
    """
    Index of the newest CLOSED bar inside _series(res).

    build_setup slices its analysis window out of the caller's list and reports "last"
    relative to that slice, so when scan.py hands over the whole fetched series (res["bars"])
    the window offset has to be added back — measured: without it the follow-up reads the
    wrong candle, with it the anchor lands exactly on the bar the alert was printed for.
    A window-only res (tests, replay) is already absolute. drop_forming=False keeps the
    printing candle inside the series; the caller says so with include_forming and resolve
    steps back over it.
    """
    data = _series(res)
    if not data:
        return 0
    last = int(res.get("last", len(data) - 1))
    if _is_full(res):
        last += int(res.get("window_start", 0) or 0)
    return max(0, min(len(data) - 1, last))


def _last_index(res: dict) -> int:
    return _anchor(res)


def _last_time(res: dict):
    data = _series(res)
    try:
        return data[_anchor(res)].get("t")
    except Exception:                                             # noqa: BLE001
        return None


def resolve(open_pos: dict, res: dict, touch: bool = True, expire_hours: float = 72.0,
            pip: float = 0.0, include_forming: bool = False, pessimistic: bool = True) -> dict | None:
    """
    Walk the bars that closed after the signal and return the outcome, or None while the
    trade is still alive.

      touch=True  -> a candle that merely wicked the level counts (what happens on a chart)
      touch=False -> only candle closes count (quieter, later)
    """
    if not open_pos:
        return None
    side = open_pos.get("side")
    entry, sl, tp = open_pos.get("entry"), open_pos.get("sl"), open_pos.get("tp")
    if side not in ("LONG", "SHORT") or not _ok(entry) or not _ok(sl) or not _ok(tp):
        return None
    data = _series(res)
    if not data:
        return None
    # walk to the newest bar we were given, not to the anchor: on a live fetch the anchor is
    # the last CLOSED bar and the bar after it is the one that has just closed since the
    # signal; and if the stored index was unusable (series rolled over between runs) the
    # timestamp search already gave us the right start, so read everything after it
    last = len(data) - 1
    start = _start_index(open_pos, data)
    if include_forming:          # the newest bar is still printing -> not usable
        last -= 1
    if last < start:
        return None              # nothing new has closed since the signal
    now = _last_time(res)

    for j in range(start, min(last, len(data) - 1) + 1):   # clamped, never out of range
        bar = data[j] or {}
        hi, lo, cl = bar.get("h"), bar.get("l"), bar.get("c")
        if not _ok(cl):
            continue
        hit_tp = (hi >= tp if side == "LONG" else lo <= tp) if touch else (
            cl >= tp if side == "LONG" else cl <= tp)
        hit_sl = (lo <= sl if side == "LONG" else hi >= sl) if touch else (
            cl <= sl if side == "LONG" else cl >= sl)
        amb = bool(hit_tp and hit_sl)
        if amb:
            # one candle wicked through both levels; inside a bar nobody knows the order,
            # so the pessimistic read (stopped out) is the default and it is configurable
            if pessimistic:
                hit_tp = False
            else:
                hit_sl = False
        if hit_tp or hit_sl:
            move = (cl - entry) if side == "LONG" else (entry - cl)
            return {
                "result": "tp" if hit_tp else "sl",
                "px": cl,
                "t": bar.get("t"),
                "pips": (move / pip) if pip else 0.0,
                "rr_done": _rr_done(side, entry, sl, cl),
                "bars": j - start + 1,
                "hours": _hours(open_pos.get("t"), bar.get("t")),
                "idea": open_pos.get("idea", "") or "",
                "age_h": _hours(open_pos.get("t"), now),
                "amb": amb,
            }

    age = _hours(open_pos.get("t"), now)
    if expire_hours and age is not None and age > expire_hours:
        return {"result": "expired", "px": data[min(last, len(data) - 1)].get("c") if data else None,
                "t": now, "pips": 0.0, "rr_done": 0.0, "bars": 0, "hours": age,
                "idea": open_pos.get("idea", "") or "", "age_h": age}
    return None


def _rr_done(side: str, entry: float, sl: float, px: float) -> float:
    risk = abs(entry - sl)
    if not risk:
        return 0.0
    move = (px - entry) if side == "LONG" else (entry - px)
    return move / risk


def _hours(a, b) -> float | None:
    try:
        if a is None or b is None:
            return None
        return max(0.0, (float(b) - float(a)) / 3600.0)
    except Exception:                                             # noqa: BLE001
        return None


def prune(open_pos: dict, keep: int = 12) -> dict:
    """Never let the gist grow: drop the oldest entries if the cap is exceeded."""
    items = list(open_pos.items())
    return dict(items[-keep:]) if len(items) > keep else open_pos
