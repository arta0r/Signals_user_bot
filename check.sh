#!/usr/bin/env bash
# One-shot local check. No Telegram needed, no paid key needed.
set -e
cd "$(dirname "$0")/.."
python3 -m pip install -q -r requirements.txt 2>/dev/null || true

python3 -m py_compile config.py detector.py dataio.py render.py notify.py scan.py xradar.py \
                      session.py footprint.py tools/cadence_probe.py
python3 test_detector.py          # detector + solo/combo idea split
python3 test_xradar.py             # radar parsing / translation / digest
python3 test_session.py            # session-open bias read (bullish / bearish / flat)
python3 test_footprint.py          # EMA + footprint filters (delta proxy, gates, wiring)

# solo mode = every setup on its own, combo = the old confluence gate; both must run clean
python3 scan.py --once --dry --no-chart --no-state --mode solo  --ideas fvg,zone --symbols XAUUSD,EURUSD --timeframes 4h
python3 scan.py --once --dry --no-chart --no-state --mode combo --symbols XAUUSD,EURUSD --timeframes 4h

# filters: EMA gate + footprint gate must not change the default (off) behaviour
python3 scan.py --once --dry --no-chart --no-state --footprint-filter on --ema-filter on \
        --symbols XAUUSD,EURUSD --timeframes 4h

# session bias: what does the pre-open tape say, and does the report survive no data
python3 session.py --print --session london --at "$(date -u +%Y-%m-%dT%H:%M)" | head -4

# cadence smoke test on real history (network); prints alerts/month + win% per setup
python3 tools/cadence_probe.py --days 30 --cache --by-idea --mode solo --ideas fvg \
        --symbols XAUUSD --timeframes 4h || echo "(probe skipped: no market data right now)"
echo "checks done"
