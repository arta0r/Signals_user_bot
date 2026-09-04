# Gold · FX · Indices · Crypto → Telegram signal bot

A free, keyless market scanner that runs on **GitHub Actions cron**, detects ICT-style setups
(FVG + liquidity sweep + order block + BOS/CHoCH), plus RSI 70/30 and MACD(12,26,9) crosses,
and pushes an annotated chart image with **ENTRY / SL / TP** into your Telegram.

No paid data, no broker account, no VPS, no server to keep alive. Your PC can be off.

```
telegram-signal-bot/
├── scan.py             CLI: one cycle (--once), loop mode (--loop), --dry, --whoami
├── detector.py         indicators + FVG / sweep / BOS / order-block logic (pure, testable)
├── dataio.py           Yahoo chart API (no key) + Kraken + Twelve Data, with fallback
├── render.py           dark-theme chart: candles, zones, ENTRY/SL/TP, RSI + MACD panels
├── notify.py           Telegram sendMessage / sendPhoto (stdlib only, handles 429)
├── config.py           ⭐ all tunables: symbols, timeframes, thresholds, flags
├── session.py          bullish/bearish read of the session that is about to open
├── footprint.py        delta / absorption / volume-profile read (OHLCV proxy, see filters)
├── test_detector.py    29 offline unit tests (no network, no Telegram)
├── test_footprint.py   21 tests for the two filters · test_session.py 22 · test_xradar.py 12
├── .github/workflows/scan.yml      cron */5 (5m+15m) and */2h (4h)
├── .github/workflows/hourly.yml    hourly structure watch
└── Dockerfile          optional: run on Hugging Face Spaces instead of Actions
```

''Session bias'' → `session.py --post` at each open; signals → `scan.py`.

## Markets covered (config.py → SYMBOLS)

| name | source | note |
|---|---|---|
| `XAUUSD` | Yahoo `GC=F` | COMEX gold futures. Yahoo has no `XAUUSD=X`; futures track spot within a few dollars. Set `ticker: "XAUTUSD", provider: "kraken"` if you prefer a 24/7 spot proxy |
| `EURUSD GBPUSD USDJPY AUDUSD USDCAD` | Yahoo | FX spot |
| `DXY` | Yahoo `DX-Y.NYB` | dollar index |
| `SPX500 US30 NAS100` | Yahoo `ES=F YM=F NQ=F` | index futures (23h/day), the closest free proxy to the cash indices |
| `BTCUSD ETHUSD SOLUSD` | Yahoo `*-USD` | crypto, also available via Kraken fallback |
| any FX/CFD with real quotes | Twelve Data | `provider: "twelvedata"` + your free `TD_API_KEY` secret |

Default watch list: `XAUUSD, EURUSD, GBPUSD, USDJPY, DXY, SPX500, US30, NAS100, BTCUSD, ETHUSD` (10 symbols).
Timeframes: `5m`, `15m`, `4h` (your "15 ساعته" was read as **15m** — change it in one line, see below).

## Setup (10 minutes)

### 1. Bot
1. Telegram → search **@BotFather** → `/newbot` → copy the token.
2. Send your new bot any message (e.g. `hi`). This is required, or it cannot message you.
3. Get your chat id:
   ```bash
   python3 scan.py --whoami --token ***
   ```

### 2. Repo
Create a **public** repo named `signal-bot`, then:
```bash
git init -q && git add -A && git commit -qm "signal bot"
git branch -M main
git remote add origin https://github.com/<you>/signal-bot.git
git push -u origin main
```

### 3. Secrets — Settings → Secrets and variables → Actions
| name | value |
|---|---|
| `TG_TOKEN` | bot token |
| `TG_CHAT_ID` | your chat id |
| `TD_API_KEY` | optional (only for Twelve Data symbols) |
| `CONFIG_JSON` | optional, e.g. `{"symbols": ["XAUUSD","BTCUSD"], "timeframes": ["15m","4h"]}` |

**Why public:** on a public repo GitHub runs cron every 5 minutes reliably. On a private repo
schedules are typically throttled to ~15 min and can drift 30–60 min. Secrets stay private in
both cases; only your detection logic is visible. If you must keep the code private, use the
Dockerfile on Hugging Face Spaces instead (free, no card, exact cron).

### 4. Run it now
Actions → **scan (signals -> telegram)** → *Run workflow*. Then check the log; each scan prints
`XAUUSD 15m 4,527.50 trend UP RSI 55.2 bull:0 bear:0 [yahoo]` and alerts are pushed if found.

### Local check before pushing
```bash
python3 -m pip install -r requirements.txt
bash scripts/check.sh          # all 4 suites + a live scan + a session read, one command
python3 test_detector.py                                   # 29 tests, must be OK
python3 scan.py --once --dry                     # config defaults
python3 scan.py --once --dry --mode solo --ideas fvg,zone   # try one setup at a time
python3 scan.py --once --dry --mode combo --symbols XAUUSD,SPX500 --timeframes 15m
```

## What counts as a signal

Four setups are detected, and **each one can stand on its own** (`CONFIG["mode"]`):

| key | what it sees | its own entry / stop / target |
|---|---|---|
| `fvg` | 3-candle fair value gap, still unfilled (<35%) | mid of the gap / outside the gap ± `solo_sl_buffer`·ATR / opposite liquidity, never closer than `min_rr` |
| `zone` | order block (last opposite-body candle before the impulse) | mid of the zone / outside the zone / same |
| `sweep` | wick through a prior swing that closes back inside | close of the sweep candle / beyond the swept wick / same |
| `bos` | close beyond the last confirmed swing (BOS, or CHoCH if it flips trend) | close of the breaking bar / 25-bar extreme / same |

* `mode = "solo"` (default) — one enabled setup is enough; no confluence required. Enable
  or disable each in `CONFIG["ideas"]`. `idea_order` breaks ties on the same bar.
* `mode = "combo"` — the old behaviour: the alert needs `min_conditions` of the four tags
  at once and uses the shared swept-level stop.
* `solo_entry`: `"market"` trades the signal-bar close (always fills; measured win% 25–35%),
  `"retest"` places the entry at the level itself (prettier risk, but 8–35% of signals are
  never filled — `max_entry_offset_atr` drops stale ones).

Indicators run alongside: RSI crossing 70/30, MACD(12,26,9) line/signal cross, EMA21/55 trend.
Only **closed** candles are ever used (the last, still-forming bar is dropped), so a signal
never repaints. Every alert names the setup that produced it, in the caption and on the chart.

## Knobs you will actually turn (config.py)

| key | default | effect |
|---|---|---|
| `DEFAULT_TIMEFRAMES` | `["15m","4h"]` | `"5m"` was measured out: 330–450 msgs/month/symbol, win% below breakeven |
| `mode` | `"solo"` | `"combo"` = require `min_conditions` of the four tags at once |
| `ideas` | fvg+zone on, sweep+bos off | flip per setup on/off; measured H4 90d win%: fvg 25–35, bos 19–33 (gold 44), sweep 10–29 |
| `solo_entry` | `"market"` | `"retest"` = limit at the level (prettier risk, 8–35% never fill) |
| `min_conditions` | `2` | combo only; `3` cut volume ~4× without lifting win% |
| `signal_lookback_bars` | `2` | how fresh a structure event must be |
| `require_trend_align` | `False` | `True` = never a LONG against the EMA 21/55 pair. Measured: 14 scans gave **6 ideas** off, **1** on (5 gated) |
| `rsi_hi / rsi_lo` | `70 / 30` | your RSI request |
| `ALERTS["structure"]` | `False` | `True` = also ping every bare sweep/BOS (loud) |
| `TELEGRAM["max_per_run"]` | `8` | flood guard |
| `swing_left/right` | `3/3` | bigger = fewer, more significant swings |

## Session bias — `session.py`

At every session open the workflow runs `python3 session.py --post`. It reads the 12 hours
before the open (1h candles, free Yahoo data) and scores five checks per symbol: EMA21/55
alignment, position inside the pre-session range, RSI side of 50, MACD histogram sign, and a
range breakout. `>2` = bullish, `<-2` = bearish, in between = **flat** (and "flat" is a real
answer, not a coin flip). USD-strength symbols (DXY, USDJPY, USDCAD) count with a negative
weight, because a rising DXY is pressure on gold — see `GOLD_UP` in `config.py`.

| knob | where | note |
|---|---|---|
| session opens | `config.py → SESSIONS` | UTC; flip New York to `utc_hour: 14` for US winter time |
| symbols per session | `SESSIONS[i]["symbols"]` | what that session usually reacts to |
| look-back / timeframe | `config.py → BIAS` | `pre_session_hours: 12`, `bias_timeframe: "1h"` |
| aggregate list | `BIAS["symbols"]` | the six used for the headline verdict |
| flat band | `session.py → FLAT_BAND` | raise it to only speak when the tape is emphatic |

Try it offline first: `python3 session.py --print --session london --at 2026-09-04T07:00`.
It is a read of momentum before the bell, **not** a forecast — it cannot know a CPI print is
15 minutes away. Use it as a filter (only long on a bullish read), not as an entry; entries
still come from `scan.py`.

## Two separate filters — `ema_gate` and `footprint.py`

Both are **filters**, not setups: off by default, and switching them on never changes what
`scan.py` sends unless you ask.

```bash
python3 scan.py --once --dry --ema-filter on        # reject ideas against EMA21/55
python3 scan.py --once --dry --ema-filter signal     # EMA21/55 cross as its own setup
python3 scan.py --once --dry --footprint-filter on   # gate + 👣 line from the delta read
python3 scan.py --once --dry --footprint-filter signal  # ping when the flow changes side
python3 session.py --print --footprint veto           # flow line in the session read (+ veto)
```

**EMA gate — measured (45d, 4h, market entries, RR 1.6, XAU/EUR/BTC/SPX):** alerts per 30
days **241 → 125** and win rate 28/32/23/44% → **33/33/29/36%**. Roughly half the noise for
a better mix; this is the one worth turning on.

**Footprint — what it actually is:** a real footprint needs every print with its bid/ask
side, and no free feed gives that for FX/CFD. So `footprint.py` rebuilds the *read* from
OHLCV: bar delta, cumulative-delta slope over the last 3 bars (in average bar volumes),
absorption (heavy volume, no displacement), and a POC/LVN profile — time-at-price instead
of volume-at-price when the symbol has no volume data at all, which the caption says out
loud. Measured on the same window: as a gate it changed almost nothing (**241 → 239**
alerts/30d, win rate flat), and as a signal it was pure noise (1463 alerts/30d at 20-33%).
Reason: a fresh FVG signal bar is by construction on the same side as the flow, so a
flow gate has nothing to reject; and a delta proxy from candles re-says what price already
said. Keep it for the 👣 line, and let the EMA gate do the filtering.

## Honest limitations

- **Yahoo is unofficial and free.** It occasionally answers `429`; the run then logs a DATA ERROR
  and recovers on the next tick. Twelve Data (free key) or Kraken is the fallback path.
- **Gold via `GC=F` is futures, not spot XAUUSD.** Levels sit a few dollars off your broker, and
  futures pause overnight (60–75 min/day) so the timestamp of bars differs from your MT5 chart.
- `ES=F / YM=F` are futures too; the cash open (09:30 ET) will not match a CFD "O" price.
- Cron is *at least* every N minutes, not exactly: GitHub can delay schedules by a few minutes.
- The bot **does not trade**. It has no order access by design.
- A detector that fires 6 signals per 14 scans is not an edge. Read `out/` PNGs, log your own
  results (the workflow uploads `signals_log.csv` as an artifact for 30 days) and judge it after
  ~50 signals before believing anything.

## X (Twitter) gold-opinion radar — `xradar.py`

Reads what people are saying about gold on X, scores it bullish/bearish, machine-translates the
post into Persian and sends **one digest message** (not one message per post).

```bash
python3 xradar.py --once --dry --no-translate      # look, no Telegram
python3 xradar.py --once                            # + translate + send
python3 xradar.py --query 'site:x.com "gold price" outlook' --when 1d --min-score 3
```

**Why it does not use the X API:** there is no free tier in 2026 — pay-per-use, roughly **$0.005 per
post read** (≈$10 per 1,000 posts) [see notes], Nitter is gone (`nitter.net` returns HTTP 41), and
`x.com/search.rss` serves a JS challenge instead of XML. Instead this reads **Google News' public
RSS with `site:x.com`**, which indexes recent public posts — free, keyless, and stable enough for a
digest. Consequences you should accept: it is not real-time (hours of lag), you cannot pin a random
list of accounts, and only posts Google has indexed show up. To follow specific people, add their
own RSS/blog to `extra_rss`, or paste a paid API key later and swap `scan()`'s source.

| knob (config in `DEFAULT_CFG` / `XRADAR_JSON` secret) | default | effect |
|---|---|---|
| `queries` | 2 x.com queries | add `"site:x.com @SomeAnalyst"` to target a handle |
| `when` | `2d` | Google time window; use `3h` for a tighter loop |
| `keep_score_ge` | `2` | drop noise; `4+` = only strong opinions |
| `max_translate` | `12` | translation is the rate-limited step |
| `max_per_message` | `10` | digest size |

Translation uses MyMemory (works, 200 OK) with Google's `gtx` endpoint as backup; both are unofficial,
quality is "understand the idea", not "publication grade" — the original text is always shown above
its translation. If every item shows `(translation unavailable)`, both providers rate-limited you;
re-run later or lower `max_translate`.

Avoiding repeats across GitHub Actions runs: the runner filesystem is wiped every run, so pass
`--gist <gist id>` + a fine-grained PAT (secrets `XRADAR_GIST`, `GIST_TOKEN`) — that is the one
clean way to keep the seen-list on Actions. Without it, the workflow runs `--no-state` and may
re-report a post once.

## What the chart image shows

Every alert image is annotated so the trade plan reads without opening the caption:
a blue **◀ ENTRY** tag with an arrow landing exactly on the candle that produced the signal,
**EXIT / TAKE PROFIT → price** and **STOP LOSS → price** pinned to the right edge in fixed
slots (so two tags never stack on a thin band), and a summary strip
`LONG · FVG (gap) · risk → target | risk 35.6p → target 92.2p | RR 2.59`. The setup that fired
is named in the title (`· solo idea`), and the caption adds `+N aligned` when other setups
happen to agree on the same bar. FVG boxes, the order block (DEMAND/SUPPLY), `LIQ` sweep lines
and `BOS/CHoCH` markers stay on the price panel; RSI (with 70/30 guides) and MACD(12,26,9) get
their own panels. Persian is kept out of the image on purpose — matplotlib ships no Persian
glyphs — so the chart is ASCII and the caption carries the Persian text.

| file | what it shows |
|---|---|
| `docs/example_LONG_setup_annotated.png` | gold 4h `LONG fvg`, RR 5.01, real Yahoo data |
| `docs/example_SHORT_setup_annotated.png` | gold 15m `SHORT fvg`, two conditions on the same bar |
| `docs/example_XAUUSD_4h_watch.png` | one condition met but no trade plan → tags only, no arrows |
| `docs/solo_bull.png` / `docs/solo_bear.png` | the same layout rendered from the offline test fixtures |

## Two behaviours to know before you judge it
- `TELEGRAM["max_per_run"]: 8` is a per-run flood guard. If a cold start has more than 8 pending
  signals, the rest are dropped (and re-appear only if still valid). Raise it or run with
  `--timeframes 4h` alone to drain a backlog.
- Each symbol × timeframe is judged independently, so you can get a `USDJPY 5m SHORT` and a
  `USDJPY 15m LONG` in the same run. That is honest per-timeframe output; if you want
  "higher timeframe must agree or stay silent", set `require_trend_align: True` and scan one
  timeframe at a time, or gate on the 4h `res["trend"]` before trusting a 5m idea.

## Files worth reading
`test_detector.py` (what is actually guaranteed), `detector.py` (the rules), `config.py` (everything else).
