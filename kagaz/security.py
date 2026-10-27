"""Small guards that keep the public agents safe to leave online.

Anyone on Agentverse can message our agents, so:
- RateLimiter stops one sender from flooding us (and burning the ASI:One key).
- clip() caps how much text one message can push into the pipeline.
- safe_fetch() only downloads public https URLs, with a size cap (no SSRF).
- PinGuard checks the expert login PIN in constant time and locks out guessing.
"""

import hmac
import ipaddress
import os
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

MAX_TEXT_CHARS = int(os.getenv("KAGAZ_MAX_TEXT_CHARS", "6000"))
PER_MINUTE = int(os.getenv("KAGAZ_RATE_PER_MINUTE", "15"))
PER_DAY = int(os.getenv("KAGAZ_RATE_PER_DAY", "300"))

SLOW_DOWN = ("You're sending messages very fast. Please wait a minute and try again. 🙏")


class RateLimiter:
    """Sliding-window limits per sender, kept in memory (reset on restart)."""

    def __init__(self, per_minute: int = PER_MINUTE, per_day: int = PER_DAY):
        self.per_minute = per_minute
        self.per_day = per_day
        self.hits: dict[str, deque] = defaultdict(deque)
        self.warned: dict[str, float] = {}

    def allow(self, sender: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        q = self.hits[sender]
        while q and now - q[0] > 86400:
            q.popleft()
        last_minute = sum(1 for t in q if now - t <= 60)
        if last_minute >= self.per_minute or len(q) >= self.per_day:
            return False
        q.append(now)
        if len(self.hits) > 50000:  # forget idle senders so memory stays small
            for s in [s for s, d in self.hits.items() if not d or now - d[-1] > 3600]:
                self.hits.pop(s, None)
        return True

    def should_warn(self, sender: str, now: float | None = None) -> bool:
        """Tell a limited sender once per minute, not on every message."""
        now = time.time() if now is None else now
        if now - self.warned.get(sender, 0) < 60:
            return False
        self.warned[sender] = now
        return True


def clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = (text or "").replace("\x00", "")
    return text if len(text) <= limit else text[:limit]


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def safe_fetch(url: str, max_bytes: int, timeout: int = 15) -> tuple[bytes, str]:
    """GET a public https URL, refusing private/internal hosts and big bodies."""
    import requests

    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("only public https links are allowed")
    if not _public_host(parts.hostname):
        raise ValueError("host is not a public address")
    with requests.get(url, timeout=timeout, stream=True, allow_redirects=False) as resp:
        resp.raise_for_status()
        if int(resp.headers.get("content-length") or 0) > max_bytes:
            raise ValueError("file too large")
        data = b""
        for chunk in resp.iter_content(64 * 1024):
            data += chunk
            if len(data) > max_bytes:
                raise ValueError("file too large")
        return data, resp.headers.get("content-type", "")


class PinGuard:
    """Constant-time PIN check; 5 wrong tries locks that sender out for 15 minutes."""

    def __init__(self, max_tries: int = 5, lock_seconds: int = 900):
        self.max_tries = max_tries
        self.lock_seconds = lock_seconds
        self.fails: dict[str, list[float]] = defaultdict(list)

    def locked(self, sender: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        recent = [t for t in self.fails[sender] if now - t < self.lock_seconds]
        self.fails[sender] = recent
        return len(recent) >= self.max_tries

    def check(self, sender: str, given: str, real: str, now: float | None = None) -> bool:
        if not real or self.locked(sender, now):
            return False
        ok = hmac.compare_digest(given.encode(), real.encode())
        if not ok:
            self.fails[sender].append(time.time() if now is None else now)
        return ok
