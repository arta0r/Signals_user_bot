#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X (Twitter) gold-opinion radar -> Persian digest on Telegram.

WHY THIS LOOKS LIKE IT
----------------------
The official X API has no free tier in 2026 (pay-per-use, ~$0.005 per post read) and Nitter
is dead, so "search X" for free means going through an index of public posts. Google News'
public RSS supports `site:x.com`, which returns real posts with their text in the title.
That is the primary source here. You can add plain RSS/Atom sources too (see config).

  python3 xradar.py --once --dry        # one pass, print, no Telegram
  python3 xradar.py --once              # one pass + send digest
  python3 xradar.py --loop --every-minutes 120
  python3 xradar.py --query '"gold price" analysis' --when 1d --max 40
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C          # noqa: E402
import notify               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "xradar_seen.json")

DEFAULT_CFG = {
    "queries": [
        'site:x.com (XAUUSD OR "gold price" OR "$GC" OR "gold analysis")',
        'site:x.com gold (forecast OR outlook OR "breakout" OR "correction") $ OR ounce',
    ],
    "extra_rss": [],                       # e.g. ["https://www.investing.com/rss/news_11.rss"]
    "when": "2d",                          # Google News time window
    "max_items": 60,
    "keep_score_ge": 2,                    # min |sentiment score| to include
    "max_translate": 12,                   # translation is the polite-limit part
    "max_per_message": 10,
    "translate": True,
    "require_gold_word": True,
}

LEX_BULL = {
    "breakout": 3, "all-time high": 3, "ath": 3, "record high": 3, "bullish": 2, "rally": 2,
    "surge": 2, "soar": 2, "jump": 1, "bounce": 1, "rebound": 2, "recovery": 1, "target": 1,
    "buy": 1, "long": 1, "accumulate": 2, "accumulation": 2, "safe haven": 2, "hedge": 1,
    "rate cut": 2, "rate cuts": 2, "dovish": 2, "weaker dollar": 2, "dollar weakness": 2,
    "upside": 2, "higher": 1, "support held": 2, "buyers": 1, "short squeeze": 3,
    "de-escalation": 1, "easing inflation": 2, "geopolitical risk": 1,
}
LEX_BEAR = {
    "bearish": 2, "correction": 2, "selloff": 3, "sell-off": 3, "selling": 1, "crash": 3,
    "dump": 2, "plunge": 2, "tumble": 2, "slump": 2, "drop": 1, "falls": 1, "break below": 3,
    "rejected": 2, "rejection": 2, "resistance held": 2, "short": 1, "profit taking": 2,
    "profit-taking": 2, "hawkish": 2, "rate hike": 2, "sticky inflation": 2, "higher yields": 1,
    "strong dollar": 2, "dollar strength": 2, "downside": 2, "distribution": 2, "bubble": 2,
    "overbought": 2, "euphoria": 2, "head and shoulders": 2, "double top": 2, "lower high": 1,
}
GOLD_WORDS = ("gold", "xau", "xauusd", "$gc", "ounce", "bullion", "precious metal",
              "soना", "طلای", "suvarna", "सोना")
NOISE = ("sports", "crypto pump dump signal group", "giveaway", "nft", "casino", "onlyfans")

TAG_RE = re.compile(r"<[^>]+>")
CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
ITEM_RE = re.compile(r"<(?:item|entry)\b.*?</(?:item|entry)>", re.S)


def _field(blob: str, *names) -> str:
    for n in names:
        m = re.search(rf"<{n}[^>]*>(.*?)</{n}>", blob, re.S | re.I)
        if m:
            v = CDATA.sub(r"\1", m.group(1))
            v = html.unescape(TAG_RE.sub(" ", v)).strip()
            return re.sub(r"\s+", " ", v)
    return ""


DESC_A = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def parse_feed(xml_text: str, source_tag: str = "news") -> list[dict]:
    """Works for Google News RSS (site:x.com) and plain RSS/Atom.
    Google News gives no direct tweet URL, so `link` is the Google redirect and `guid`
    is what we deduplicate on; a text fingerprint catches reposted copies of the same post."""
    out = []
    for raw in ITEM_RE.findall(xml_text):
        # Google News escapes the whole <description>, so unescape once and parse the markup
        blob = html.unescape(raw)
        if "<description>" in blob and "<a" not in blob:
            blob = raw                                   # unescaping created no tags: keep raw
        title = _field(blob, "title")
        link = _field(blob, "link", "id")
        guid = _field(blob, "guid") or link
        pub = _field(blob, "pubDate", "published", "updated", "date")
        src = _field(blob, "source") or source_tag
        if not (title and link):
            continue
        # Google News puts the real post text inside <description><a ...>TEXT</a>&nbsp;<font>OUTLET</font>
        m = DESC_A.search(blob)
        text = ""
        if m:
            link = html.unescape(m.group(1))
            text = re.sub(r"\s*&?nbsp;.*$", "", m.group(2))
            text = html.unescape(TAG_RE.sub(" ", text))
            text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = title
        # strip the trailing " - x.com" / " | @handle" that feeds append
        for pat in (r"\s+-\s+[\w. ]{2,20}$", r"\s+\|\s+@?[A-Za-z0-9_]{2,15}$",
                    r"\s+\|\s+[A-Za-z0-9 ._]{2,24}$"):
            mm = re.search(pat, text)
            if mm and mm.start() > 20:
                text = text[:mm.start()].strip()
        h = re.search(r"(?:^|\s)@([A-Za-z0-9_]{2,15})\b", text)
        handle = h.group(1) if h else ""
        when = _parse_dt(pub)
        fp = re.sub(r"[^a-z0-9\u0600-\u06ff ]+", "", text.lower())[:90].split()
        fp = " ".join(fp[:14])
        out.append({"title": title, "text": text, "handle": handle, "link": link,
                    "guid": guid, "fingerprint": fp, "source": src,
                    "when": when.isoformat() if when else pub})
    return out


def _parse_dt(s: str):
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            d = dt.datetime.strptime(s.strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def score(text: str) -> tuple[int, str, list[str]]:
    low = text.lower()
    s = sum(w for k, w in LEX_BULL.items() if k in low) - sum(w for k, w in LEX_BEAR.items() if k in low)
    hits = [k for k in list(LEX_BULL) + list(LEX_BEAR) if k in low]
    verdict = "bullish" if s >= 2 else ("bearish" if s <= -2 else ("mild-bull" if s > 0 else
              ("mild-bear" if s < 0 else "neutral")))
    return s, verdict, hits[:4]


def relevant(item: dict, cfg: dict) -> bool:
    low = (item["text"] + " " + item["source"]).lower()
    if cfg["require_gold_word"] and not any(g in low for g in GOLD_WORDS):
        return False
    if any(n in low for n in NOISE):
        return False
    return True


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": C.UA,
                                               "Accept": "application/rss+xml,application/xml,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def gnews_rss(query: str, when: str, max_items: int) -> str:
    q = query if "when:" in query else f"{query} when:{when}"
    u = ("https://news.google.com/rss/search?" +
         urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    return fetch(u)


# ------------------------------------------------------------------ translation
def translate_fa(text: str) -> str:
    """MyMemory (free, 200 verified) with Google's gtx endpoint as a secondary attempt.
    Both are unofficial: on any error we return '' and the digest keeps the original."""
    text = re.sub(r"https?://\S+", "", text).strip()
    if len(text) < 12:
        return ""
    payload = text[:480]
    try:
        u = ("https://api.mymemory.translated.net/get?" +
             urllib.parse.urlencode({"q": payload, "langpair": "en|fa", "de": "user@example.com"}))
        data = json.loads(fetch(u, timeout=25))
        out = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        if out and "MYMEMORY WARNING" not in out.upper():
            return _fix_fa(out)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        u = ("https://translate.googleapis.com/translate_a/single?" +
             urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "fa", "dt": "t",
                                     "q": payload}))
        data = json.loads(fetch(u, timeout=20))
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        return _fix_fa(out)
    except Exception:                                        # noqa: BLE001
        return ""


LRO, PDF = "\u202a", "\u202c"


def _fix_fa(s: str) -> str:
    """MyMemory escapes Persian as \\u06xx and sometimes leaves Latin numbers in an RTL run."""
    s = s.strip().strip('"')
    if "\\u" in s:
        try:
            s = json.loads('"' + s.replace('"', '\\"') + '"')
        except Exception:                                    # noqa: BLE001
            pass
    s = re.sub(r"\s+([،؛؛\.])", r"\1", s)
    return s.strip()


# ------------------------------------------------------------------ persisted state
GIST_API = "https://api.github.com/gists"


def gist_read(gist_id: str, token: str) -> dict:
    """State across GitHub Actions runs (the runner filesystem is wiped every run)."""
    try:
        req = urllib.request.Request(f"{GIST_API}/{gist_id}",
                                     headers={"User-Agent": C.UA, "Accept": "application/vnd.github+json"})
        data = json.loads(fetch(f"{GIST_API}/{gist_id}", timeout=25))
        for f in (data.get("files") or {}).values():
            txt = f.get("content") or ""
            if txt:
                return json.loads(txt)
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! gist read failed ({type(e).__name__}); running without history")
    return {}


def gist_write(gist_id: str, token: str, seen: dict) -> bool:
    if not token:
        return False
    body = json.dumps({"description": "xradar seen-state",
                       "files": {"xradar_seen.json": {"content": json.dumps(seen)}}}).encode()
    req = urllib.request.Request(f"{GIST_API}/{gist_id}", data=body, method="PATCH", headers={
        "User-Agent": C.UA, "Content-Type": "application/json",
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return True
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! gist write failed ({type(e).__name__}: {str(e)[:100]})")
        return False


# ------------------------------------------------------------------ digests
def build_digest(items: list[dict], cfg: dict, do_translate: bool = True) -> str:
    buckets = {"bullish": [], "mild-bull": [], "neutral": [], "mild-bear": [], "bearish": []}
    for it in items:
        buckets.setdefault(it["verdict"], buckets["neutral"]).append(it)
    head = (f"🛰 <b>رادار نظرات طلا در ایکس</b>\n"
            f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC · {len(items)} پست جدید\n")
    label = {"bullish": "🟢 موافق صعود (bullish)", "mild-bull": "🟢 کمی صعودی",
             "neutral": "⚪ بدون جهت واضح", "mild-bear": "🔴 کمی نزولی",
             "bearish": "🔴 مخالف (bearish)"}
    order = ["bullish", "mild-bull", "mild-bear", "bearish", "neutral"]
    body, shown = [], 0
    for k in order:
        rows = buckets.get(k) or []
        if not rows:
            continue
        body.append(f"\n<b>{label.get(k,k)}</b>")
        for r in rows[: max(1, cfg["max_per_message"] // 2) if k != "neutral" else 3]:
            if shown >= cfg["max_per_message"]:
                break
            shown += 1
            who = ("@" + r["handle"]) if r.get("handle") else ""
            who = (who + " · " if who else "") + (r.get("source") or "x.com")
            raw = r["text"]
            raw = raw if len(raw) <= 190 else raw[:188] + "…"
            line = f"• {LRO}{who}{PDF}: {LRO}{raw}{PDF}"
            if r.get("fa"):
                line += f"\n   {_arrow(r['fa'])}"
            why = "، ".join(r.get("hits") or [])
            if why:
                line += f"\n   <i>({LRO}{why}{PDF})</i>"
            line += f'\n   <a href="{r["link"]}">لینک</a>'
            body.append(line)
    if not shown:
        return head + "\nهیچ پست تازه‌ای با امتیاز کافی پیدا نشد."
    tail = ("\n\n⚠️ این‌ها نظر دیگران است، نه تحلیل ربات. ترجمه ماشینی است؛ "
            "قبل از هر تصمیم خودت متن اصلی را بخوان.")
    return head + "\n".join(body) + tail


def arrow(t: str) -> str:
    return f"{LRO}{t}{PDF}"


def _arrow(t: str) -> str:
    return f"↩︎ {LRO}{t}{PDF}"


def scan(cfg: dict, seen: dict, translate: bool) -> tuple[list[dict], int]:
    fresh, parsed_total = [], 0
    for q in cfg["queries"]:
        try:
            rows = parse_feed(gnews_rss(q, cfg["when"], cfg["max_items"]))
        except Exception as e:                                # noqa: BLE001
            print(f"  ! query failed: {type(e).__name__}: {str(e)[:120]}")
            rows = []
        for it in rows:
            parsed_total += 1
            keys = [it.get("guid") or "", it.get("fingerprint") or ""]
            keys = [k for k in keys if k]
            if not keys or any(k in seen for k in keys) or not relevant(it, cfg):
                continue
            for k in keys:
                seen[k] = dt.datetime.now(dt.timezone.utc).isoformat()
            s, verdict, hits = score(it["text"])
            if abs(s) < cfg["keep_score_ge"] and verdict == "neutral":
                continue
            it.update({"score": s, "verdict": verdict, "hits": hits})
            fresh.append(it)
        if len(fresh) >= cfg["max_items"]:
            break
    for u in cfg.get("extra_rss") or []:
        try:
            for it in parse_feed(fetch(u), source_tag="rss"):
                keys = [k for k in (it.get("guid") or "", it.get("fingerprint") or "") if k]
                if not keys or any(k in seen for k in keys) or not relevant(it, cfg):
                    continue
                for k in keys:
                    seen[k] = "1"
                s, verdict, hits = score(it["text"])
                it.update({"score": s, "verdict": verdict, "hits": hits})
                fresh.append(it)
        except Exception as e:                                # noqa: BLE001
            print(f"  ! rss {u}: {str(e)[:100]}")

    fresh = fresh[: cfg["max_items"]]
    fresh.sort(key=lambda r: -abs(r["score"]))
    if translate and cfg["translate"]:
        for it in fresh[: cfg["max_translate"]]:
            it["fa"] = translate_fa(it["text"])
        if len(fresh) > cfg["max_translate"]:
            print(f"  (translation limited to {cfg['max_translate']} items this run; "
                  f"{len(fresh) - cfg['max_translate']} left untranslated)")
    return fresh, parsed_total


def prune(seen: dict, days: int = 14) -> dict:
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    keep = {}
    for k, v in seen.items():
        try:
            if dt.datetime.fromisoformat(v) >= cut:
                keep[k] = v
        except Exception:                                    # noqa: BLE001
            keep[k] = v if isinstance(v, str) else "1"
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--every-minutes", type=int, default=120)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-translate", action="store_true")
    ap.add_argument("--query", action="append", default=None)
    ap.add_argument("--when", default=None)
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--min-score", type=int, default=None)
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--out", default=None, help="write the digest to a file too")
    ap.add_argument("--gist", default=os.environ.get("XRADAR_GIST", ""),
                    help="gist id used to persist seen-state between GitHub Actions runs")
    ap.add_argument("--gist-token", default=os.environ.get("GIST_TOKEN", ""),
                    help="fine-grained PAT with Gist:read/write (only needed for --gist)")
    a = ap.parse_args()

    cfg = dict(DEFAULT_CFG)
    ov = os.environ.get("XRADAR_JSON")
    if ov:
        try:
            cfg.update(json.loads(ov))
        except json.JSONDecodeError as e:
            print(f"XRADAR_JSON invalid: {e}", file=sys.stderr)
    if a.query:
        cfg["queries"] = a.query
    if a.when:
        cfg["when"] = a.when
    if a.max:
        cfg["max_items"] = a.max
    if a.min_score is not None:
        cfg["keep_score_ge"] = a.min_score

    token = os.environ.get("TG_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    tg = notify.TG(token, chat)

    while True:
        if a.no_state:
            seen = {}
        elif a.gist:
            seen = gist_read(a.gist, a.gist_token)
            print(f"  gist state: {len(seen)} entries")
        else:
            seen = _load_json(STATE, {})
        fresh, total = scan(cfg, seen, translate=not a.no_translate)
        msg = build_digest(fresh, cfg, do_translate=not a.no_translate)
        plain = re.sub(r"<[^>]+>", "", msg).replace(LRO, "").replace(PDF, "")
        print(plain)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(plain)
        if not a.no_state:
            _dump_json(STATE, prune(seen))
            if a.gist and not a.dry:
                ok = gist_write(a.gist, a.gist_token, prune(seen))
                print("  gist state saved" if ok else "  gist not saved (needs --gist-token)")
        if not a.dry and tg.enabled and fresh:
            try:
                tg.send(msg)
            except Exception as e:                            # noqa: BLE001
                print(f"send failed: {e}", file=sys.stderr)
        elif not tg.enabled and not a.dry:
            print("(TG_TOKEN/TG_CHAT_ID missing → printed only)")
        print(f"   {len(fresh)} fresh / {total} parsed")
        if not a.loop:
            break
        import time
        time.sleep(max(300, a.every_minutes * 60))
    return 0


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _dump_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception as e:                                    # noqa: BLE001
        print(f"state write failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
