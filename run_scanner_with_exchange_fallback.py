#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRI exchange fallback runner
============================
Fix for GitHub Actions exchange geo/IP restrictions.

The original scanner failed because the GitHub-hosted runner received:
403 Forbidden / CloudFront "block access from your country"
from Bybit.

This wrapper:
1. Tests public market access for several CCXT exchanges.
2. Selects the first exchange whose public API is reachable.
3. Patches only CONFIG["exchange_id"] (or exchange_id=...) in
   crypto_ai_scanner.py for this run.
4. Runs the existing scanner unchanged.
5. Restores the original scanner after the run.

No API keys are needed for the public market-data test.
No trading/order functions are used.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import ccxt

SCANNER = Path("crypto_ai_scanner.py")

# Preferred order for this research scanner.
# Small-cap coverage is more important than fiat on/off-ramps.
EXCHANGE_CANDIDATES = [
    "okx",
    "kucoin",
    "gateio",
    "bitget",
    "mexc",
    "kraken",
    "coinbase",
]

# Optional override from GitHub Actions:
#   CRI_EXCHANGE_ORDER="okx,kucoin,gateio,..."
if os.environ.get("CRI_EXCHANGE_ORDER"):
    EXCHANGE_CANDIDATES = [
        x.strip().lower()
        for x in os.environ["CRI_EXCHANGE_ORDER"].split(",")
        if x.strip()
    ]

EXCHANGE_RE = re.compile(
    r"""(?P<prefix>(?:["']exchange_id["']|exchange_id)\s*:\s*["']|exchange_id\s*=\s*["'])"""
    r"""(?P<value>[^"']+)"""
    r"""(?P<suffix>["'])"""
)


def test_exchange(exchange_id: str) -> tuple[bool, str]:
    """Test only public market-data access."""
    try:
        cls = getattr(ccxt, exchange_id)
        exchange = cls({
            "enableRateLimit": True,
            "timeout": 20000,
        })
        try:
            exchange.load_markets()
            if not exchange.markets:
                return False, "API reachable but no markets returned"
            return True, f"{len(exchange.markets)} markets"
        finally:
            exchange.close()
    except Exception as exc:
        msg = str(exc).replace("\n", " ")
        if len(msg) > 180:
            msg = msg[:180] + "..."
        return False, msg


def patch_exchange(source: str, exchange_id: str) -> tuple[str, int]:
    """Replace only an exchange_id assignment/config value."""
    patched, count = EXCHANGE_RE.subn(
        lambda m: m.group("prefix") + exchange_id + m.group("suffix"),
        source,
    )
    return patched, count


def main() -> int:
    if not SCANNER.exists():
        print(f"[FATAL] Missing {SCANNER}")
        return 2

    original = SCANNER.read_text(encoding="utf-8-sig")
    backup = SCANNER.with_suffix(".py.cri_backup")
    shutil.copy2(SCANNER, backup)

    selected = None
    print("=== CRI exchange connectivity test ===")

    for exchange_id in EXCHANGE_CANDIDATES:
        ok, detail = test_exchange(exchange_id)
        print(f"[{'OK' if ok else 'FAIL'}] {exchange_id}: {detail}")
        if ok:
            selected = exchange_id
            break

    if not selected:
        print("[FATAL] Niciun exchange public accesibil din runner.")
        print("Daca problema persista, proiectul poate fi mutat pe CoinGecko-only.")
        shutil.copy2(backup, SCANNER)
        return 3

    patched, count = patch_exchange(original, selected)

    if count == 0:
        print(
            "[FATAL] Nu am gasit CONFIG['exchange_id'] / exchange_id= in "
            "crypto_ai_scanner.py. Nu modific scannerul."
        )
        shutil.copy2(backup, SCANNER)
        return 4

    SCANNER.write_text(patched, encoding="utf-8")
    print(f"=== Selected exchange: {selected} ===")
    print(f"Patched {count} exchange_id occurrence(s).")
    print("Rulez scannerul existent...")

    try:
        result = subprocess.run([sys.executable, str(SCANNER)], check=False)
        return result.returncode
    finally:
        # Always restore the user's original source.
        shutil.copy2(backup, SCANNER)
        try:
            backup.unlink()
        except OSError:
            pass
        print("Scanner source restored.")

if __name__ == "__main__":
    raise SystemExit(main())
