"""Telegram sending: sendMessage + sendPhoto (multipart, stdlib only)."""
from __future__ import annotations

import json
import mimetypes
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import config as C


def _post(url: str, fields: dict, files: dict | None = None, retries: int = 3):
    boundary = "----gfs" + secrets.token_hex(12)
    body = bytearray()
    for k, v in (fields or {}).items():
        if v is None:
            continue
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += str(v).encode("utf-8") + b"\r\n"
    for k, (fname, blob, ctype) in (files or {}).items():
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                 f"Content-Type: {ctype or mimetypes.guess_type(fname)[0] or 'image/png'}\r\n\r\n").encode()
        body += blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=bytes(body), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": C.UA,
    })
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if not data.get("ok", True):
                raise RuntimeError(f"telegram: {data.get('description')}")
            return data
        except urllib.error.HTTPError as e:
            raw = e.read()[:400].decode("utf-8", "replace")
            last = f"HTTP {e.code} {raw}"
            if e.code == 429:                                  # flood control
                try:
                    wait = int(json.loads(raw).get("parameters", {}).get("retry_after", 5))
                except Exception:
                    wait = 5
                time.sleep(min(wait + 1, 40))
                continue
            if e.code >= 500:
                time.sleep(2 + 3 * attempt)
                continue
            break
        except Exception as e:                                  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"telegram send failed: {last}")


class TG:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat = token, str(chat_id)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def _api(self, method: str):
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send(self, text: str, html: bool = True):
        fields = {"chat_id": self.chat, "text": text[:4090],
                  "disable_web_page_preview": "true"}
        if html:
            fields["parse_mode"] = "HTML"
        if C.TELEGRAM["disable_rich_preview"]:
            fields["disable_notification"] = "false"
        return self._post(self._api("sendMessage"), fields)

    def send_photo(self, png_path: str, caption: str):
        with open(png_path, "rb") as fh:
            blob = fh.read()
        fields = {"chat_id": self.chat, "caption": caption[:1020], "parse_mode": "HTML"}
        return self._post(self._api("sendPhoto"), fields,
                          files={"photo": ("chart.png", blob, "image/png")})

    def get_updates(self):
        """Used by `--whoami` to discover your chat_id."""
        req = urllib.request.Request(self._api("getUpdates") + "?limit=1",
                                     headers={"User-Agent": C.UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
