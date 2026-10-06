# -*- coding: utf-8 -*-
"""tests.runner - ruleaza un pas al workflow-ului cu timpul inghetat si cu bursele si reteaua simulate.

Uz: python tests/runner.py <repo> <frozen_ts> <set_de_date.json> <script.py> [argumente]

Pentru crypto_ai_scanner.py, altseason-ul (CoinGecko) e inlocuit cu starea deja salvata;
pentru orice alt script, scriptul ruleaza neschimbat, ca pas din scan.yml. Timpul e
inghetat in time si datetime, deci doua rulari pe aceleasi date scriu aceleasi fisiere.
"""

import datetime as _dt
import json
import os
import runpy
import sys
import time as _t

repo, FROZEN, dataset, script, extra = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5:]
_og, _ol, _os = _t.gmtime, _t.localtime, _t.strftime
_t.time = lambda: FROZEN
_t.gmtime = lambda s=None: _og(FROZEN if s is None else s)
_t.localtime = lambda s=None: _ol(FROZEN if s is None else s)
_t.strftime = lambda fmt, tt=None: _os(fmt, _ol(FROZEN) if tt is None else tt)
_t.sleep = lambda s: None


class FrozenDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(FROZEN, tz)

    @classmethod
    def utcnow(cls):
        return cls.utcfromtimestamp(FROZEN)


_dt.datetime = FrozenDT
os.environ["TESTS_DATASET"] = os.path.abspath(dataset)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY", "GITHUB_TOKEN", "SMTP_PASSWORD",
          "EMAIL_PASSWORD", "GMAIL_APP_PASSWORD"):
    os.environ.pop(k, None)
fakes = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakes")
os.chdir(repo)
sys.path[:0] = [fakes, os.path.abspath(repo)]
sys.argv = [script] + extra
if script == "crypto_ai_scanner.py":
    import altseason
    try:
        with open("data/altseason.json") as f:
            _alt = json.load(f)
    except Exception:
        _alt = None
    altseason.update = lambda market, results: _alt
    import crypto_ai_scanner
    crypto_ai_scanner.main()
else:
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        if e.code not in (None, 0):
            raise
