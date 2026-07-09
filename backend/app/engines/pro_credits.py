"""Apex Pro — prepaid minutes ledger + per-second meter.

This is the integrity core of the paid tier: it holds the user's balance (in
SECONDS, for exactness), burns one second per second while Pro is LIVE, blocks
going live with an empty balance, and **auto-stops the Pro call the instant the
balance hits zero** so we never stream more than the customer paid for.

Balance persists to data/pro_credits.json so it survives restarts. Purchases add
minutes here (payment is wired later; the add path already exists). All access is
lock-guarded; the meter runs on its own thread.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from app.core.logging import get_logger

log = get_logger(__name__)

CREDITS_FILE = Path("data") / "pro_credits.json"


class ProCredits:
    def __init__(self) -> None:
        self._seconds: float = 0.0
        self._lock = threading.Lock()
        self._meter: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_empty: Callable[[], None] | None = None
        self._is_active: Callable[[], bool] | None = None
        self._load()

    # --- persistence ---------------------------------------------------------
    def _load(self) -> None:
        try:
            if CREDITS_FILE.exists():
                self._seconds = float(json.loads(CREDITS_FILE.read_text()).get("seconds", 0))
        except Exception as exc:
            log.debug("credits load skipped: %s", exc)

    def _save(self) -> None:
        try:
            CREDITS_FILE.parent.mkdir(parents=True, exist_ok=True)
            CREDITS_FILE.write_text(json.dumps({"seconds": round(self._seconds, 2)}))
        except Exception as exc:
            log.debug("credits save skipped: %s", exc)

    # --- balance -------------------------------------------------------------
    @property
    def remaining_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._seconds)

    @property
    def remaining_minutes(self) -> float:
        return round(self.remaining_seconds / 60.0, 2)

    def has_credit(self) -> bool:
        return self.remaining_seconds > 0.5

    def add_minutes(self, minutes: float) -> float:
        """Add purchased minutes to the balance (payment ties in here later)."""
        with self._lock:
            self._seconds += max(0.0, float(minutes)) * 60.0
            self._save()
        return self.remaining_minutes

    def set_seconds(self, seconds: float) -> None:
        """Directly set the balance (used for testing / admin)."""
        with self._lock:
            self._seconds = max(0.0, float(seconds))
            self._save()

    def deduct(self, seconds: float) -> bool:
        """Take a fixed amount off the balance up-front (photo = one shot; video/
        restyle jobs = the clip length x their rate). Returns False WITHOUT
        deducting if the balance can't cover it, so a job never runs unpaid.

        The wallet is in 'live seconds' (sold at the live rate). Other modes cost
        more/less per real second, so callers pass the wallet-seconds already
        scaled by the mode's rate (see MODE_RATE)."""
        seconds = max(0.0, float(seconds))
        with self._lock:
            if self._seconds < seconds:
                return False
            self._seconds -= seconds
            self._save()
        return True

    # --- metering ------------------------------------------------------------
    def start(self, on_empty: Callable[[], None],
              is_active: Callable[[], bool] | None = None) -> bool:
        """Begin burning one second per second WHILE ``is_active`` is true (so the
        customer is only charged while the cloud is actually transforming, not
        while it's connecting). Returns False (and does NOT start) if there's no
        credit — that's how GO LIVE is blocked at zero minutes."""
        if not self.has_credit():
            return False
        self._on_empty = on_empty
        self._is_active = is_active
        if self._meter and self._meter.is_alive():
            return True
        self._stop.clear()
        self._meter = threading.Thread(target=self._loop, daemon=True)
        self._meter.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._save()

    def _loop(self) -> None:
        log.info("Pro meter started (%.1f min left)", self.remaining_minutes)
        last = time.monotonic()
        saved = 0.0
        while not self._stop.is_set():
            time.sleep(0.25)
            now = time.monotonic()
            dt = now - last
            last = now
            # Only charge while the cloud is actually transforming (fairness):
            # connecting/idle time doesn't burn the customer's minutes.
            if self._is_active is not None and not self._is_active():
                continue
            empty = False
            with self._lock:
                self._seconds -= dt
                saved += dt
                if saved >= 5.0:      # persist every ~5s, not every tick
                    self._save()
                    saved = 0.0
                if self._seconds <= 0:
                    self._seconds = 0.0
                    self._save()
                    empty = True
            if empty:
                log.info("Pro balance exhausted -> stopping the call")
                cb = self._on_empty
                self._stop.set()
                if cb:
                    try:
                        cb()
                    except Exception:
                        log.exception("on_empty callback failed")
                break
        with self._lock:
            self._save()
        log.info("Pro meter stopped (%.1f min left)", self.remaining_minutes)


# How many WALLET seconds each mode costs, per real second (or per image). The
# wallet is priced at the LIVE rate ($0.03/s); a mode that sells for more burns
# proportionally more wallet-seconds. Image is a flat per-photo charge.
#   live   $0.03/s -> 1.0x     video   $0.06/s -> 2.0x
#   restyle $0.02/s -> 0.667x  image   $0.40   -> 0.40/0.03 = 13.33s
MODE_RATE = {"live": 1.0, "video": 2.0, "restyle": 2.0 / 3.0}
IMAGE_COST_SECONDS = round(0.40 / 0.03, 2)   # ~13.33 wallet-seconds per photo


# Singleton
pro_credits = ProCredits()
