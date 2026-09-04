#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the X gold-opinion radar. No network, no Telegram."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xradar as X                                          # noqa: E402

GNEWS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>q</title>
 <item>
  <title>Gold breakout above 4500, buyers in control - x.com</title>
  <link>https://news.google.com/rss/articles/AAA?oc=5</link>
  <guid isPermaLink="false">AAA</guid>
  <pubDate>Thu, 03 Sep 2026 20:46:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/AAA?oc=5" target="_blank"&gt;Gold
   breakout above 4500, buyers in control - @GoldAnalystX&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font
   color="#6f6f6f"&gt;x.com&lt;/font&gt;</description>
  <source url="https://x.com">x.com</source>
 </item>
 <item>
  <title>same post republished - x.com</title>
  <link>https://news.google.com/rss/articles/BBB?oc=5</link>
  <guid isPermaLink="false">BBB</guid>
  <pubDate>Thu, 03 Sep 2026 20:47:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/BBB?oc=5"&gt;Gold breakout above
   4500, buyers in control - @GoldAnalystX&lt;/a&gt;</description>
  <source url="https://x.com">x.com</source>
 </item>
 <item>
  <title>Manchester United win - x.com</title>
  <link>https://news.google.com/rss/articles/CCC?oc=5</link>
  <guid isPermaLink="false">CCC</guid>
  <pubDate>Thu, 03 Sep 2026 20:48:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/CCC?oc=5"&gt;Manchester United win
   3-0, fans celebrate&lt;/a&gt;</description>
  <source url="https://x.com">x.com</source>
 </item>
 <item>
  <title>Gold correction continues, sellers target 4200 - x.com</title>
  <link>https://news.google.com/rss/articles/DDD?oc=5</link>
  <guid isPermaLink="false">DDD</guid>
  <pubDate>Thu, 03 Sep 2026 20:49:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/DDD?oc=5"&gt;Gold correction
   continues, sellers target 4200&lt;/a&gt;</description>
  <source url="https://x.com">x.com</source>
 </item>
</channel></rss>"""


class TestParse(unittest.TestCase):
    def test_fields_and_handle(self):
        rows = X.parse_feed(GNEWS)
        self.assertEqual(len(rows), 4)
        r = rows[0]
        self.assertEqual(r["handle"], "GoldAnalystX")
        self.assertIn("Gold breakout above 4500", r["text"])
        self.assertNotIn("x.com", r["text"])               # the outlet suffix is trimmed
        self.assertNotIn("\n", r["text"])                  # whitespace is collapsed
        self.assertEqual(r["source"], "x.com")
        self.assertTrue(r["guid"])
        self.assertTrue(r["when"].startswith("2026-09-03T20:46"))
        self.assertEqual(r["guid"], "AAA")

    def test_fingerprint_identifies_a_republished_post(self):
        rows = X.parse_feed(GNEWS)
        self.assertEqual(rows[0]["fingerprint"], rows[1]["fingerprint"])
        self.assertNotEqual(rows[0]["fingerprint"], rows[2]["fingerprint"])

    def test_relevance_filter_drops_non_gold(self):
        rows = X.parse_feed(GNEWS)
        cfg = dict(X.DEFAULT_CFG)
        keep = [r for r in rows if X.relevant(r, cfg)]
        self.assertNotIn("Manchester United win 3-0, fans celebrate",
                         [k["text"] for k in keep])
        self.assertEqual(len(keep), 3)


class TestSentiment(unittest.TestCase):
    def test_bull_and_bear(self):
        s, v, hits = X.score("Gold: breakout above ATH, buyers in control, rate cuts coming")
        self.assertGreater(s, 0)
        self.assertEqual(v, "bullish")
        self.assertTrue(hits)
        s2, v2, _ = X.score("Gold correction continues, sellers in charge, bearish rejection at resistance")
        self.assertLess(s2, 0)
        self.assertEqual(v2, "bearish")

    def test_neutral_when_no_signal_words(self):
        s, v, hits = X.score("Gold price is 4500 today, trading in a range")
        self.assertEqual((s, v), (0, "neutral"))
        self.assertEqual(hits, [])

    def test_mixed_text_does_not_crash(self):
        for t in ("", "gold", "BREAKOUT !!!!!!!!!!!! " * 50, "طلا breakout رالی"):
            s, v, h = X.score(t)
            self.assertIsInstance(s, int)
            self.assertIn(v, ("bullish", "bearish", "mild-bull", "mild-bear", "neutral"))


class TestScanAndDigest(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(X.DEFAULT_CFG)
        self.cfg.update(translate=False, max_items=50, keep_score_ge=1)

    def test_scan_dedupes_and_scores(self):
        seen = {}
        rows = X.parse_feed(GNEWS)
        fresh = []
        for it in rows:                                     # replay scan()'s core loop
            keys = [k for k in (it["guid"], it["fingerprint"]) if k]
            if any(k in seen for k in keys) or not X.relevant(it, self.cfg):
                continue
            for k in keys:
                seen[k] = "1"
            s, v, hits = X.score(it["text"])
            if v == "neutral" and abs(s) < self.cfg["keep_score_ge"]:
                continue
            it.update({"score": s, "verdict": v, "hits": hits})
            fresh.append(it)
        self.assertEqual(len(fresh), 2, [r["text"] for r in fresh])
        self.assertTrue(all(r["verdict"] in ("bearish", "mild-bear") for r in fresh
                            if r["score"] < 0), [r["verdict"] for r in fresh])
        self.assertIn("bullish", [r["verdict"] for r in fresh])

    def test_digest_persian_and_balanced_html(self):
        rows = X.parse_feed(GNEWS)
        items = []
        for it in rows:
            s, v, h = X.score(it["text"])
            if v == "neutral":
                continue
            it.update({"score": s, "verdict": v, "hits": h, "fa": "طلا بالای ۴۵۰ شکست"})
            items.append(it)
        msg = X.build_digest(items, self.cfg, do_translate=False)
        self.assertIn("رادار نظرات طلا", msg)
        for tag in ("b", "i", "a"):
            self.assertEqual(msg.count(f"<{tag}"), msg.count(f"</{tag}>"), tag)
        self.assertIn("لینک", msg)
        self.assertEqual(msg.count("شکست"), len(items), "translation should be shown per item")

    def test_empty_digest_is_honest(self):
        msg = X.build_digest([], self.cfg)
        self.assertIn("پست تازه‌ای", msg)

    def test_unicode_escape_from_mymemory_is_decoded(self):
        raw = "\\u0637\\u0644\\u0627 \\u0645\\u06cc\\u200c\\u0634\\u06a9\\u0646\\u062f"
        out = X._fix_fa(raw)
        self.assertTrue(out.startswith("طلا"), out)
        self.assertNotIn("\\u", out)                       # escapes must be gone
        self.assertIn("\u200c", out)                          # ZWNJ preserved

    def test_urls_are_stripped_before_translation(self):
        self.assertEqual(X.translate_fa("https://t.co/abc"), "")      # too short after stripping

    def test_prune_drops_old_keys(self):
        seen = {"a": "2020-01-01T00:00:00+00:00", "b": "2999-01-01T00:00:00+00:00", "c": "junk"}
        kept = X.prune(seen)
        self.assertNotIn("a", kept)
        self.assertIn("b", kept)
        self.assertIn("c", kept)                                        # unknown format is kept, not lost


if __name__ == "__main__":
    unittest.main(verbosity=2)
