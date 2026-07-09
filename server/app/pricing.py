"""Apex Cam cloud — shared pricing. The wallet is in 'live seconds' (sold at the
live rate). Each mode burns its own rate; packages top the wallet up.

Costs (Decart, 1 credit=$0.01) vs our sell — margins baked in:
  live    cost $0.02/s  sell $0.03/s  ($1.80/min)
  video   cost $0.04/s  sell $0.06/s  ($3.60/min)   -> 2.0x wallet-sec/real-sec
  restyle cost $0.01/s  sell $0.02/s  ($1.20/min)   -> 0.667x
  photo   cost $0.02    sell $0.40                  -> ~13.33 wallet-sec/photo
"""
import os

LIVE_USD_PER_SEC = 0.03                      # wallet is priced at the live rate
MODE_RATE = {"live": 1.0, "video": 2.0, "restyle": 2.0 / 3.0}
IMAGE_COST_SECONDS = round(0.40 / LIVE_USD_PER_SEC, 2)   # ~13.33 wallet-sec/photo

# Minute packages the app sells (wallet minutes).
PACKAGES = [5, 12, 15, 25, 30, 50, 100, 160, 375, 1000]

# Currency: charge NGN (buffered), display USD. Keep in sync with the app.
CURRENCY = os.environ.get("APEXCAM_PAY_CURRENCY", "NGN")
NGN_PER_USD = float(os.environ.get("APEXCAM_NGN_PER_USD", "1600"))


def usd_price(minutes: float) -> float:
    return round(minutes * 60 * LIVE_USD_PER_SEC, 2)


def charge_amount(minutes: float) -> float:
    usd = usd_price(minutes)
    return float(round(usd * NGN_PER_USD)) if CURRENCY == "NGN" else usd
