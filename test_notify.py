#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline test for the Telegram transport. No network.

Regression: `TG.send()` called `self._post(...)`, but `_post` is a module-level function.
Every send (text and photo) therefore died with `AttributeError: 'TG' object has no
attribute '_post'` — and because scan.py catches it, the bot reported "0 new alerts" style
success while nothing ever reached Telegram. This test actually executes the send path.
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import notify                                  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestTG(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def fake_urlopen(req, timeout=None):
            self.sent.append(req)
            return FakeResp(json.dumps({"ok": True, "result": {}}).encode())

        self.patcher = mock.patch("urllib.request.urlopen", fake_urlopen)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_send_builds_a_real_request(self):
        tg = notify.TG("123:ABC", "-10042")
        self.assertTrue(tg.enabled)
        tg.send("hello <b>world</b>")
        self.assertEqual(len(self.sent), 1)
        req = self.sent[0]
        self.assertIn("api.telegram.org/bot123:ABC/sendMessage", req.full_url)
        body = req.data.decode("utf-8", "replace")
        self.assertIn('name="chat_id"', body)
        self.assertIn("-10042", body)
        self.assertIn("hello <b>world</b>", body)
        self.assertIn("parse_mode", body)
        self.assertIn("multipart/form-data", req.headers["Content-type"])

    def test_send_photo_uploads_the_png(self):
        path = os.path.join(os.path.dirname(__file__), "_tmp_chart.png")
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        try:
            notify.TG("123:ABC", "42").send_photo(path, "caption")
        finally:
            os.remove(path)
        body = self.sent[0].data
        self.assertIn(b'filename="chart.png"', body)
        self.assertIn(b"\x89PNG", body)                     # the bytes actually went out
        self.assertIn(b"caption", body)

    def test_disabled_without_credentials(self):
        self.assertFalse(notify.TG("", "").enabled)

    def test_error_from_telegram_is_raised(self):
        def fake_urlopen(req, timeout=None):
            return FakeResp(json.dumps({"ok": False, "description": "chat not found"}).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(RuntimeError) as cm:
                notify.TG("1:2", "3").send("x")
        self.assertIn("chat not found", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
