# -*- coding: utf-8 -*-
"""core.config - configurarea scanarii: CONFIG (watchlist, timeframe, praguri), cheile documentelor
salvate si constantele modulelor per bursa. Citeste doar variabile de mediu (timeframe-ul),
niciun fisier.
"""

import os


# ============================== CONFIG ==============================
CONFIG = {
    # "gateio" a fost redenumit "gate" in ccxt - verificat contra ccxt 4.5.78, unde
    # `gateio` nu mai exista. connect_exchange sarea peste el gracios, deci nu
    # crapa, dar aveai practic 4 rezerve in loc de 5, tacut.
    "exchange_fallback": ["okx", "kucoin", "gate", "mexc", "kraken"],
    # Incearca pe rand, primul care raspunde e folosit - vezi connect_exchange().
    # Kraken primul (serveste SUA, nu are motiv sa geo-blocheze); restul sunt
    # rezerve. NU mai modifica sursa fisierului la runtime (spre deosebire de
    # run_scanner_with_exchange_fallback.py, care trebuie sters - vezi nota
    # din raspuns).
    "quotes": ["USDT", "USDC", "USD"],
    # Mai multe monede de cotare, nu doar USDT: pe Kraken aproape totul e cotat
    # in USD (doar 44 perechi USDT din 1440 de piete), motiv pentru care prima
    # rulare reala a gasit doar 34 de simboluri. Deduplicate pe simbolul de baza.
    "min_universe": 120,       # sub atat, incerc urmatorul exchange din lista
                               # (se ajusteaza automat la marimea watchlist-ului)
    "universe_size": 200,      # cate simboluri intra efectiv in scanare (scor + persistenta)
    "coingecko_scope": 200,    # doar proiectele din top N CoinGecko dupa market cap sunt eligibile

    # WATCHLIST: daca e completata, scaneaza DOAR aceste simboluri si ignora
    # complet top-200 CoinGecko. Lasa lista goala ca sa revii la scanarea larga.
    # Aliasurile acopera redenumirile: GRAM e Toncoin redenumit (iunie 2026), dar
    # unele burse pot inca lista perechea ca TON/USDT. Se ia primul alias gasit.
    # WATCHLIST in trepte. Cele 7 initiale erau toate mid-cap alts cu profil
    # asemanator - date omogene. Treapta 1 adauga capete diferite de spectru:
    # BTC/ETH au volatilitate mult mai mica, DOGE mult mai mare. Diversitatea
    # de ATR conteaza mai mult decat numarul brut de simboluri, pentru ca da
    # regimuri diferite in ACEEASI perioada calendaristica.
    # WATCHLIST: cei 18 propusi de tine in discutiile anterioare (JASMY scos -
    # nu exista ca pereche pe OKX), plus 10 alesi pe rezultate MASURATE pe 5 ani,
    # nu pe intuitie: BTC si ETH pentru capatul de volatilitate mica, DOGE pentru
    # cel mare, iar THETA (+123R), ATOM (+79R), MANA (+76R), GRT (+70R),
    # FLOW (+67R), SOL (+40R), XRP (+30R) pentru ca au dat cele mai bune
    # rezultate in backtest si au istoric complet.
    #
    # Lista mai scurta inseamna mai putine planuri pe zi, deci acumulare mai
    # lenta - dar si mai putin zgomot de la simboluri cu 50 de zile de istoric.
    "watchlist": [
        # propusi de tine
        "ADA", "ALGO", "AVAX", "AXS", "CHZ", "DASH", "DOT", "EGLD", "FET",
        "GRAM", "LINK", "NEAR", "POL", "SEI", "SUI", "TRX", "ZEC", "ZEN",
        # adaugati pe rezultate masurate
        "BTC", "ETH", "SOL", "XRP", "DOGE", "ATOM", "THETA", "GRT", "MANA", "FLOW",
        # Doi din top 100 cu cel mai mare multiplu pana la ATH: ambii ~99.7%
        # sub maxim, deci peste 300x pe criteriul cerut. I-am ales dintre cei
        # profund scazuti pentru ca se disting de proiectele moarte - ICP are a
        # treia cea mai mare activitate de dezvoltare din crypto, iar Filecoin
        # conduce industria la commit-uri zilnice pe GitHub.
        #
        # CAVEAT, pentru ca nu vreau sa para altceva decat e: distanta fata de
        # ATH NU e o masura a valorii. Maximul a fost un pret care a existat o
        # clipa in conditii specifice, nu o valoare la care activul are dreptul
        # sa revina. Backtest-ul va spune daca produc planuri bune; criteriul
        # ATH spune doar ca au spatiu, nu ca il vor parcurge.
        "ICP", "FIL",
    ],
    # Redenumiri de care sunt sigur. Backtest-ul alege aliasul cu istoricul
    # cel mai adanc, nu pe cel cu volumul mai mare.
    "aliases": {"POL": ["POL", "MATIC"], "EGLD": ["EGLD", "ERD"]},
    # Configurabil din mediu, ca sa poata fi schimbat din workflow fara sa
    # editezi codul. Backtest-ul citeste acelasi CONFIG, deci scanarea si
    # backtest-ul raman mereu pe acelasi timeframe.
    "timeframe": os.environ.get("SCAN_TIMEFRAME", "1h"),
    # 260, nu 200: EMA 200 are nevoie de 200 de bare, iar bara neinchisa se
    # elimina - la 200 ramaneau 199 si Golden/Death Cross ar fi lipsit MEREU,
    # tacut. Prins la verificarea panoului de structura, unde cross-ul aparea
    # gol in productie desi codul era corect.
    "candles": 260,
    "lookahead_hours": 24,     # dupa cate ore evaluam daca un semnal a "nimerit"
    "hit_threshold_atr": 0.5,  # miscare minima (in ATR-uri) ca sa conteze "hit"
    # Plafonul REAL de volum: se deschid planuri doar pentru top N pe directie,
    # deci un univers de 200 ar da tot 10 planuri/scanare cu N=5.
    "top_n_per_direction": 8,
    "chart_candles": 80,       # cate lumanari pastram pentru graficul din dashboard
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "PUNE_AICI_TOKEN_DE_LA_BOTFATHER"),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "PUNE_AICI_CHAT_ID_UL_TAU"),
    "data_dir": "data",
}


HISTORY_FILE = os.path.join(CONFIG["data_dir"], "scan_history.json")


WEIGHTS_FILE = os.path.join(CONFIG["data_dir"], "weights.json")


WEIGHTS_HISTORY_FILE = os.path.join(CONFIG["data_dir"], "weights_history.json")


# POLITICA PONDERILOR DE SCOR, scrisa la reconstructia memoriei (data_reset.py): cu "frozen",
# scanarea foloseste exact ponderile cu care s-a generat backtest-ul, iar ajustarea euristica
# (core/learning.evaluate_and_learn) ramane doar diagnostic. Fara fisier: comportamentul vechi.
SCORING_POLICY_FILE = os.path.join(CONFIG["data_dir"], "scoring_policy.json")


CHART_FILE = os.path.join(CONFIG["data_dir"], "latest_chart.json")


DETAILS_FILE = os.path.join(CONFIG["data_dir"], "latest_details.json")


EXCHANGES_FILE = os.path.join(CONFIG["data_dir"], "exchanges.json")


EXCHANGE_SCANS_FILE = os.path.join(CONFIG["data_dir"], "exchange_scans.json")


# MODULELE PER BURSA. Fiecare bursa conectata primeste ANALIZA COMPLETA - scor,
# detalii pe token (grafic, Elliott, structura, lichidare), evidente si decizia pe
# care ar lua-o agentul - prin adaptorul ei din adapters/exchanges/.
#
# INVATAREA ramane doar pe bursa activa: acelasi token are practic acelasi pret pe
# toate bursele (arbitrajul le aliniaza), iar cinci planuri aproape identice ar
# numara fiecare rezultat de cinci ori - AUC-ul si confirmarea live ar deveni
# artificial de bune. Propunerile de pe celelalte burse se calculeaza si se
# afiseaza, marcate "neantrenat".
SECONDARY_SCAN_LIMIT = 30      # = marimea watchlist-ului; plafonul simbolurilor per bursa


EXCHANGE_TIME_BUDGET = 360     # secunde per bursa secundara; bursele ruleaza IN PARALEL,
                               # deci scanarea creste cu cea mai lenta (Kraken: 1 cerere/s)


                               # deci scanarea creste cu cea mai lenta (Kraken: 1 cerere/s)
EXCHANGE_HISTORY_KEEP = 60     # scanari pastrate per bursa, pentru persistenta semnalelor


EXCHANGES_DIR = os.path.join(CONFIG["data_dir"], "exchanges")


# ORDER BOOK-UL FIECARUI SEMNAL. Pana acum se descarca o singura carte - a celui mai
# bun candidat - si se folosea pentru TOATE semnalele: zidurile de lichiditate si
# dezechilibrul cartii afisate la fiecare token veneau de la alt token (aceeasi clasa
# de eroare ca "NEAR sub nor"). Deciziile nu erau afectate - ev_book_imbalance e
# exclusa din agent - dar afisarea si memoria planurilor da. Costa cel mult 15
# apeluri in plus pe scanare.
PER_SIGNAL_ORDER_BOOK = True


CHART_BARS_MAX = 170      # plafonul ferestrei adaptive per token


CHART_BARS = 90           # lumanari per token. 120 dadea ~2.4 MB de pagina
                          # la 30 de tokenuri; 90 pastreaza structura vizibila.


                          # la 30 de tokenuri; 90 pastreaza structura vizibila.
SPARKLINE_BARS = 40   # cate preturi de inchidere pastrez pentru graficul mic


DEFAULT_WEIGHTS = {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0}


def timeframe_seconds():
    """Durata unei lumanari, in secunde, derivata din CONFIG['timeframe'].
    Necesara ca expirarea pullback-ului sa fie masurata in timp, nu in numarul
    de bare primite intr-un apel - vezi nota din plan_tracker.evaluate_plan."""
    tf = CONFIG.get("timeframe", "1h")
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        return int(tf[:-1]) * units[tf[-1]]
    except (ValueError, KeyError):
        return 3600
