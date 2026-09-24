#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_tracker.py
================
Fundatia agentului: planuri numerotate, cu ciclu de viata urmarit in timp si
R REALIZAT masurat bara cu bara.

DE CE E ASTA FUNDATIA
---------------------
Pana acum proiectul genera un plan (entry/SL/TP1/TP2) la fiecare scanare si
il uita imediat. Nimic nu verifica vreodata daca TP1 a fost atins sau daca
SL a fost lovit. Consecinta: `expected_r` era o cifra decorativa, iar
ponderile adaptive si agentul invatau din "unde era pretul la ora 24", nu
din "a functionat tranzactia".

Aici masor corect: descarc lumanarile de la crearea planului incoace si
parcurg bara cu bara ca sa vad ce s-a atins PRIMUL.

REGULI EXPLICITE (ca sa nu-mi umflu rezultatele)
------------------------------------------------
1. AMBIGUITATE IN ACEEASI BARA: daca o bara atinge si SL si TP, presupun ca
   SL a venit primul. Fara date de tick nu pot sti ordinea, deci aleg mereu
   varianta defavorabila mie. Altfel as raporta rezultate mai bune decat
   realitatea.
2. MODEL DE POZITIE: 50% din pozitie se inchide la TP1, restul merge la TP2,
   iar SL-ul se muta la breakeven dupa TP1. E o regula standard, si e
   declarata explicit ca sa fie reproductibila.
3. EXPIRARE: dupa MAX_BARS fara sa atinga nimic, planul se inchide la pretul
   curent (mark-to-market), nu se sterge. Un plan care nu a mers nicaieri e
   tot un rezultat din care se invata.

CE FACE IN PLUS FATA DE POZE
----------------------------
Pozele arata stari de plan ("TP1 HIT - CLOSED", "SL / INVALIDATED"), dar
afiseaza si procente de tip "86% confirmed" care nu par sa fie masurate din
rezultate. Aici probabilitatea afisata e CALIBRATA din planurile inchise
efectiv, cu interval de incredere Wilson, si spun explicit cand nu am destule
date ca sa pronunt un numar.
"""

import json
import math
import os
import time
from datetime import datetime, timezone

DATA_DIR = "data"
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
ARCHIVE_INDEX_FILE = os.path.join(ARCHIVE_DIR, "_index.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")

MAX_BARS = 48          # cate lumanari las un plan deschis (48 = 2 zile pe 1h)
MIN_BUCKET_SAMPLES = 20  # sub atat, nu pronunt o probabilitate calibrata

def _archive_filename(geometry):
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in geometry) or "unknown"
    if len(safe) > 100:
        # Nume de fisier limitat: gasit de testul de proprietati cu o geometrie
        # de 300 de caractere, care arunca "File name too long" pe Linux (limita
        # tipica 255 pentru intreaga cale). SCAN_TIMEFRAME e citit din mediu -
        # o valoare custom neobisnuit de lunga nu trebuie sa poata crapa
        # arhivarea intregii rulari. Hash-ul pastreaza unicitatea.
        import hashlib
        h = hashlib.sha256(geometry.encode("utf-8")).hexdigest()[:16]
        safe = safe[:80] + "-" + h
    return os.path.join(ARCHIVE_DIR, f"{safe}.json")


def archive_stale_plans(store):
    """Muta DEFINITIV planurile din geometrii vechi in fisiere de arhiva
    separate, unul per geometrie. plans.json ramane cu DOAR geometria curenta.

    DE CE ASTA, SI NU DOAR O GARDA DE DIMENSIUNE
    ---------------------------------------------
    Verificat pe date reale: 73.020 de planuri acumulate din 9 geometrii
    anterioare (proiectul a schimbat geometria de 9 ori pana acum), plus
    13.077 noi intr-o singura rulare de backtest - 86.097 in total, 100.83 MB,
    respins de GitHub. O garda care doar TAIE cele mai vechi ID-uri cand se
    depaseste un prag are un defect serios: daca se ruleaza backtest de doua
    ori pe ACEEASI geometrie curenta, planurile din prima rulare au ID mai mic
    decat cele din a doua. Daca totalul depaseste pragul, taierea le-ar elimina
    pe cele din prima rulare - planuri din geometria ACTIVA, nu doar istoric
    mort - stricand direct calibrarea, nu doar arhiva.

    O geometrie veche e INCHISA definitiv: nu mai primeste NICIODATA planuri
    noi dupa ce geometria curenta se schimba. Deci fiecare fisier de arhiva
    are dimensiune FINITA garantat, iar plans.json ramane mereu mic - oricat
    de multe schimbari de geometrie mai vin.

    Fisierele de arhiva individuale raman disponibile pe disc pentru audit;
    doar nu mai sunt pe calea critica de citire/scriere la fiecare rulare.
    Un index mic (_index.json) tine count si R total per geometrie arhivata,
    ca summarize() sa poata raporta legacy_total_r fara sa recitesca totul.
    """
    # BUG FIX CRITIC: pastrez toata FAMILIA versiune+timeframe, nu semnatura exacta.
    #
    # Scanarea live sondeaza bursa, detecteaza capabilitati si fixeaza geometria
    # la v6-4h-obf prin set_capabilities(). Dar ai_agent.py ruleaza ca proces
    # SEPARAT, fara SCAN_CAPS in mediu, deci GEOMETRY_VERSION e v6-4h-o acolo.
    # Rezultatul masurat: scanerul crea 7 planuri, ai_agent le arhiva pe toate,
    # si plans.json ramanea gol dupa fiecare rulare. Pierdere totala de date
    # live, tacuta, la fiecare ora.
    #
    # Familia = acelasi numar de versiune si acelasi timeframe. Calibrarea si
    # agentul filtreaza in continuare pe semnatura EXACTA, deci separarea pe
    # capabilitati ramane intacta - doar ca datele nu mai sunt distruse de un
    # proces care nu stie ce capabilitati a detectat alt proces.
    parts = GEOMETRY_VERSION.split("-")
    family = "-".join(parts[:2]) + "-" if len(parts) >= 2 else GEOMETRY_VERSION

    plans = store.get("plans") or []
    keep, by_geo = [], {}
    for p in plans:
        geo = p.get("geometry", "v1")
        (keep if geo.startswith(family) else by_geo.setdefault(geo, [])).append(p)

    if not by_geo:
        return 0

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    index = load_json(ARCHIVE_INDEX_FILE, {})
    moved = 0
    for geo, geo_plans in by_geo.items():
        path = _archive_filename(geo)
        existing = load_json(path, {"plans": []})
        existing_ids = {p.get("id") for p in existing["plans"]}
        fresh = [p for p in geo_plans if p.get("id") not in existing_ids]
        if fresh:
            existing["plans"].extend(fresh)
            save_json(path, existing)
            moved += len(fresh)
        closed = [p for p in existing["plans"] if p.get("realized_r") is not None
                 and p.get("state") != STATE_NO_ENTRY]
        index[geo] = {
            "count": len(existing["plans"]), "closed": len(closed),
            "total_r": round(sum(p["realized_r"] for p in closed), 2),
        }

    store["plans"] = keep
    save_json(ARCHIVE_INDEX_FILE, index)
    return moved


# POARTA DE DECIZIE: implicit DEZACTIVATA, pe baza de dovezi.
#
# A fost construita ca sa refuze intervalele de scor cu valoare asteptata
# negativa. In-sample parea sa functioneze (ducea -0.171R la +0.041R). Dar
# walk-forward-ul, pe ferestre nevazute, a masurat altceva, de trei ori:
#
#   1h, 14 simboluri:  cu poarta -0.0614R | fara -0.0355R  -> pierde 0.0259R
#   4h, 14 simboluri:  cu poarta +0.0364R | fara +0.0343R  -> castiga 0.0021R
#   4h, 54 simboluri:  cu poarta -0.0046R | fara +0.0232R  -> pierde 0.0278R
#
# Pe cel mai mare esantion (12.864 planuri emise), poarta transforma un sistem
# semnificativ pozitiv intr-unul plat. Cauza: calibrarea invata praguri de pe
# ferestrele trecute, iar ele nu se transfera - clasica potrivire pe trecut.
#
# Codul ramane, pentru ca infrastructura de calibrare e folosita si la afisare,
# si pentru ca pe alt set de date poarta ar putea deveni utila. Dar nu mai
# filtreaza nimic implicit. Pune True doar daca ai dovezi walk-forward proprii.
USE_DECISION_GATE = os.environ.get("USE_DECISION_GATE", "false").lower() == "true"

# Versiunea geometriei planului. Cand regulile de plasare a TP1/TP2 se schimba,
# rezultatele vechi devin necomparabile: descriu o structura care nu mai exista.
# Calibrarea foloseste doar planuri din versiunea curenta.
# v1 -> v2: TP1 nu mai poate fi sub 1R (v1 producea planuri cu asteptare
# negativa prin constructie: 21 din 47 aveau TP1 sub 1R, unul la 0.00R).
# Timeframe-ul face parte din versiunea geometriei: un plan de pe 4h are alta
# distributie de rezultate decat unul de pe 1h (miscari mai ample la acelasi
# cost de tranzactie). Amestecarea lor in calibrare ar media doua sisteme
# diferite - aceeasi eroare pe care am evitat-o la schimbarile de geometrie.
# Asa, trecerea de la 1h la 4h separa automat datele, fara interventie manuala.
# Structura de piata si Elliott au adaugat CARACTERISTICI, nu reguli noi de
# plan. Sunt urmarite de FEATURE_VERSION - planurile raman comparabile si
# agentul nu se reseteaza.
#
# v6: HARTA DE LICHIDARI. S-a adaugat evidenta `liq_magnet`, deci vectorul de
# caracteristici a trecut de la 16 la 17. Planurile v5 nu o au, deci un model
# antrenat pe ele nu e comparabil - resetul e obligatoriu.
#
# v5: STRAT DE EVIDENTE. Caracteristicile agentului au trecut de la 6 numere
# abstracte la 16, din care 11 sunt evidente orientate dupa directia planului.
# `is_long` a fost scos. Planurile v4 nu au evidente, deci un model antrenat pe
# ele nu poate fi comparat cu unul antrenat pe v5 - resetul e obligatoriu, nu
# optional. Datele v4 raman in fisier ca urma auditabila.
# Semnatura include si CAPABILITATILE active. Un plan creat cu order flow are
# alt vector de caracteristici decat unul fara. Fara separare, agentul ar invata
# din amandoua ca si cum ar fi acelasi sistem - aceeasi eroare pe care
# versionarea o previne la schimbarile de timeframe si de geometrie.
# Backtest-ul ruleaza mereu cu "o" (doar OHLCV); scanarea live poate avea "obf".
# DOUA VERSIUNI SEPARATE, pentru doua lucruri diferite
# ----------------------------------------------------
# GEOMETRY_VERSION descrie REGULILE care determina REZULTATUL unui plan: entry
# pe pullback, SL la 1.5 ATR, costuri incluse, bara neinchisa eliminata. Cand
# acestea se schimba, acelasi setup produce alt R - datele vechi sunt genuin
# incomparabile si resetul agentului e obligatoriu.
#
# FEATURE_VERSION descrie doar ce CARACTERISTICI vede modelul. Cand adaug o
# evidenta noua, rezultatul planului ramane EXACT acelasi; se schimba doar
# vectorul de intrare.
#
# Le-am tratat la fel pana acum, si asta a costat: masurat pe istoricul real,
# 72.837 de planuri aruncate la resetari, din care ~40.000 pentru schimbari de
# tip "am adaugat un indicator". Agentul repornea de la zero exact cand se
# apropia de pragul de activare. De asta nu-l trecea niciodata.
FEATURE_VERSION = "f6"      # f5 = + Elliott cu numaratori concurente


def _build_geometry(caps_sig=None):
    # Semnatura de capabilitati ramane in geometrie: ea schimba ce EXISTA in
    # date, nu doar cum e privit. Un plan fara order flow chiar are alt continut.
    # REVENIT la v6, intentionat. Urcasem la v7 cand am adaugat
    # market_structure - adica o schimbare de CARACTERISTICI, exact situatia in
    # care tocmai demonstrasem ca geometria NU trebuie urcata. Rezultatele
    # planurilor v6 nu s-au schimbat: aceleasi reguli de entry, SL, TP, costuri.
    # Urcarea ar fi aruncat cele 13.086 de planuri deja masurate.
    return ("v6-" + os.environ.get("SCAN_TIMEFRAME", "1h")
            + "-" + (caps_sig or os.environ.get("SCAN_CAPS", "o")))


GEOMETRY_VERSION = _build_geometry()


def set_capabilities(caps_sig):
    """Fixeaza semnatura de capabilitati DUPA ce bursa a fost sondata.

    BUG FIX: semnatura se citea din mediu la import, adica inainte ca scanerul
    sa stie ce ofera bursa. Rezultatul: un plan creat CU order flow primea
    eticheta "v5-4h-o" - aceeasi ca planurile de backtest, care nu au order
    flow. Ar fi ajuns in aceeasi calibrare, adica exact nepotrivirea tacuta pe
    care semnatura trebuia sa o previna.
    Se apeleaza o data, la inceputul scanarii, dupa sondare.
    """
    global GEOMETRY_VERSION
    GEOMETRY_VERSION = _build_geometry(caps_sig)
    os.environ["SCAN_CAPS"] = caps_sig or "o"
    return GEOMETRY_VERSION
# v3 -> v4: doua schimbari care fac rezultatele necomparabile cu cele anterioare.
#   1. Scanarea nu mai foloseste lumanarea curenta, neinchisa. Cron-ul e :07 dar
#      rulari reale au fost masurate intre :09 si :59, deci bara era prinsa intre
#      8% si 92% formata - acelasi setup dadea scoruri diferite doar dupa cat de
#      tarziu pornea jobul. In plus, backtest.py foloseste doar bare inchise, deci
#      pana acum cele doua nu testau acelasi lucru.
#   2. R-ul raportat include acum costul dus-intors (0.1%). Inainte era brut, deci
#      supraestima performanta: pe cele 51 de planuri intrate, ~4.6R diferenta.
#
# v2 -> v3: INTRARE PE PULLBACK, nu la piata.
# Diagnostic pe 27 de planuri v2 inchise: LONG castiga 15.4%, SHORT 14.3% -
# ambele directii pierdeau la fel, deci nu era regim de piata, ci moment de
# intrare. Logica de semnal (RSI 45-75 + trend ascendent pentru LONG) intra
# DUPA miscare, la un maxim local; simetric pentru SHORT. SL-ul se atingea in
# 3.6h median, 6 din 22 in prima ora.
# Acum planul asteapta revenirea pretului la zona de intrare. Daca nu revine in
# MAX_WAIT_BARS, expira FARA pierdere - exact tranzactiile care fugeau.
TP1_FRACTION = 0.5     # cat din pozitie se inchide la TP1

# Cost dus-intors (taxe + spread) ca procent din pret. R-ul raportat era BRUT,
# deci supraestima performanta reala. backtest.py il modela deja; aici lipsea,
# ceea ce facea ca cele doua sa raporteze marimi diferite sub acelasi nume.
# Costul in R depinde de marimea stopului: un stop strans e penalizat mai tare,
# fiindcă R se imparte la un risc mai mic.
ROUNDTRIP_COST_PCT = 0.001

STATE_PENDING = "PENDING"     # asteapta revenirea pretului la zona de intrare
STATE_NO_ENTRY = "NO_ENTRY"   # pretul nu a revenit - plan anulat, FARA pierdere
STATE_OPEN = "OPEN"
STATE_TP1 = "TP1_HIT"
STATE_TP2 = "TP2_HIT"
STATE_SL = "SL_HIT"
STATE_EXPIRED = "EXPIRED"
CLOSED_STATES = (STATE_TP2, STATE_SL, STATE_EXPIRED, STATE_NO_ENTRY)

MAX_WAIT_BARS = 8      # cate bare astept revenirea la zona de intrare
DEFAULT_BAR_SECONDS = 3600   # 1h; se salveaza pe fiecare plan la creare


# ============================== PERSISTENTA ================================

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def auto_migrate_geometry(store):
    """Inchide automat planurile deschise generate cu o geometrie veche.

    DE CE AUTOMAT SI NU CA PAS MANUAL:
    `has_open_plan` refuza sa deschida un plan nou pentru o combinatie
    simbol+directie care are deja unul activ. Planurile ramase din geometria
    veche ar bloca astfel simbolurile respective pana s-ar inchide singure -
    intarziind degeaba colectarea de date bune. Facand-o automat, nu depinde
    de un pas manual pe care e usor sa-l uiti sau sa-l rulezi gresit.

    Nu le atribui un R: geometria lor nu mai e comparabila, deci un rezultat
    ar fi contorizat undeva unde nu-si are locul. Raman vizibile in istoric,
    cu numerotarea intacta.

    Idempotent: dupa o rulare nu mai gaseste nimic de migrat.
    """
    migrated = []
    for p in store.get("plans", []):
        if p.get("state") in CLOSED_STATES:
            continue
        if p.get("geometry", "v1") == GEOMETRY_VERSION:
            continue
        prev = p.get("state")
        p["state"] = STATE_EXPIRED
        p["state_detail"] = f"INCHIS AUTOMAT LA MIGRARE (geometrie {p.get('geometry', 'v1')})"
        p["migrated"] = True
        p["migrated_ts"] = time.time()
        migrated.append((p["id"], p["symbol"], p["direction"], prev))
    return migrated


def load_plans():
    return load_json(PLANS_FILE, {"next_id": 1, "plans": []})


# Cate planuri RECENTE pastreaza lista de evidente cu etichete text.
# Restul pastreaza doar `components`, unde evidentele sunt deja numere - exact
# ce citeste agentul. Lista cu etichete e strict pentru afisare, iar dashboard-ul
# arata un singur plan. Masurat: cu evidente pe toate cele 27.000 de planuri,
# fisierul ajunge la 101 MB si GitHub respinge push-ul la 100 MB.
KEEP_EVIDENCE_ON = 100

# Prag de avertizare. GitHub respinge fisierele peste 100 MB si avertizeaza
# peste 50. Verific INAINTE de scriere, nu dupa push - altfel afli dupa 18
# minute de backtest ca munca nu se poate salva.
SIZE_WARN_MB = 45
SIZE_FAIL_MB = 90


def _strip_display_fields(store):
    """Scoate campurile de afisare de pe planurile vechi.

    `evidence`, `fusion` si `neighbors` nu sunt citite de agent: el foloseste
    `components`, unde aceleasi evidente sunt deja numere. Le pastrez doar pe
    cele mai recente KEEP_EVIDENCE_ON planuri, pentru dashboard.
    """
    plans = store.get("plans") or []
    if len(plans) <= KEEP_EVIDENCE_ON:
        return 0
    keep_ids = {p.get("id") for p in sorted(plans, key=lambda x: x.get("id", 0),
                                            reverse=True)[:KEEP_EVIDENCE_ON]}
    stripped = 0
    for p in plans:
        if p.get("id") in keep_ids:
            continue
        for field in ("evidence", "fusion", "neighbors"):
            if field in p:
                del p[field]
                stripped = stripped or 1
    return stripped


def save_plans(store):
    # ARHIVAREA vine INAINTEA gardei de dimensiune, nu dupa: o geometrie veche e
    # inchisa definitiv, deci arhivarea completa e mereu sigura si mereu de
    # dimensiune finita. Taierea oarba de mai jos ramane doar ca plasa de
    # siguranta pentru cazul (rar) in care GEOMETRIA CURENTA singura ar
    # depasi pragul - caz in care nu exista alta solutie decat sa astepti mai
    # putine planuri per rulare sau sa muti memoria pe un fisier separat.
    archived = archive_stale_plans(store)
    if archived:
        print(f"Arhivate {archived} planuri din geometrii vechi in {ARCHIVE_DIR}/ "
              f"(plans.json pastreaza doar geometria curenta: {GEOMETRY_VERSION}).")
    _strip_display_fields(store)
    # separators compacte: `indent=2` aproape dubleaza dimensiunea pe fisiere
    # cu zeci de mii de inregistrari, fara niciun castig - nimeni nu citeste
    # plans.json cu ochiul.
    payload = json.dumps(store, separators=(",", ":"))
    mb = len(payload.encode("utf-8")) / 1024 / 1024

    # Daca depaseste pragul, TAI cele mai vechi planuri in loc sa esuez.
    # A arunca o exceptie ar insemna sa pierd toata munca rularii - inclusiv
    # 18 minute de backtest. Planurile vechi au fost deja invatate de agent
    # (marcate `agent_trained`); pierderea lor costa ceva istoric la vecini si
    # calibrare, dar infinit mai putin decat pierderea intregii rulari.
    if mb > SIZE_FAIL_MB:
        plans = sorted(store.get("plans") or [], key=lambda p: p.get("id", 0))
        before = len(plans)
        while plans and mb > SIZE_FAIL_MB * 0.8:
            drop = max(1, len(plans) // 20)          # taie 5% odata
            plans = plans[drop:]
            store["plans"] = plans
            payload = json.dumps(store, separators=(",", ":"))
            mb = len(payload.encode("utf-8")) / 1024 / 1024
        print(f"[!] plans.json depasea {SIZE_FAIL_MB} MB. Am taiat cele mai vechi "
              f"{before - len(plans)} planuri; raman {len(plans)} ({mb:.1f} MB).")
        print("    Agentul invatase deja din ele. Pentru mai mult istoric, "
              "mareste SIZE_FAIL_MB sau muta memoria intr-un fisier separat.")
    elif mb > SIZE_WARN_MB:
        print(f"[!] plans.json: {mb:.1f} MB - se apropie de limita GitHub de 100 MB.")
    os.makedirs(os.path.dirname(PLANS_FILE) or ".", exist_ok=True)
    with open(PLANS_FILE, "w") as f:
        f.write(payload)


# ============================ CREARE DE PLANURI =============================

def has_open_plan(store, symbol, direction):
    """Nu deschid un plan nou pentru acelasi simbol+directie daca deja am unul
    activ. Fara asta, un semnal persistent ar genera zeci de planuri identice
    si ar umple istoricul cu duplicate corelate."""
    return any(
        p["symbol"] == symbol and p["direction"] == direction
        and p["state"] not in CLOSED_STATES
        for p in store["plans"]
    )


def create_plan(store, signal, plan_levels, decision):
    """Inregistreaza un plan nou, numerotat (PLAN #N, ca in poze).

    Returneaza None daca nivelurile lipsesc sau sunt degenerate - nu arunc
    exceptie, pentru ca un singur simbol problematic nu are voie sa opreasca
    intreaga scanare.
    """
    if not plan_levels:
        return None
    entry, sl = plan_levels.get("entry"), plan_levels.get("sl")
    if entry is None or sl is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    plan = {
        "id": store["next_id"],
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "created_ts": time.time(),
        "created_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "entry": entry,
        "signal_price": plan_levels.get("signal_price"),  # BUG FIX: se calcula
        # in compute_trade_plan dar nu era copiat aici, deci se pierdea. E
        # necesar ca sa pot diagnostica cat de des se atinge zona de pullback.
        "sl": sl,
        "tp1": plan_levels["tp1"],
        "tp2": plan_levels["tp2"],
        "risk": round(risk, 8),
        "planned_r_tp2": round(abs(plan_levels["tp2"] - entry) / risk, 2),
        "state": STATE_PENDING,
        "state_detail": "ASTEAPTA PULLBACK la zona de intrare",
        "realized_r": None,
        "closed_ts": None,
        "bars_checked": 0,
        # context la momentul deciziei - ca sa pot invata ce fel de setup merge
        "score_at_entry": signal.get("risk_adjusted"),
        "components": signal.get("components"),
        "persistence_at_entry": signal.get("persistence"),
        "decision": decision,
        # Lista de evidente si rezumatul lor, exact ce se afiseaza in dashboard.
        # Salvate pe plan, nu recalculate: dashboard-ul citeste din memoria
        # agentului, nu face propriul calcul paralel care ar putea diverge.
        "evidence": signal.get("evidence") or [],
        "fusion": signal.get("fusion"),
        "neighbors": signal.get("neighbors"),
        "geometry": GEOMETRY_VERSION,
        "bar_seconds": signal.get("bar_seconds", DEFAULT_BAR_SECONDS),
    }
    store["plans"].append(plan)
    store["next_id"] += 1
    return plan


# ======================= EVALUARE BARA CU BARA ==============================

def cost_in_r(entry, sl):
    """Costul tranzactiei exprimat in R. Zero daca nu s-a intrat in piata."""
    if not entry or entry <= 0:
        return 0.0
    risk_pct = abs(entry - sl) / entry
    return ROUNDTRIP_COST_PCT / risk_pct if risk_pct > 0 else 0.0


def _r_at(price, entry, sl, direction):
    """Cati R fata de intrare, cu semn (pozitiv = in favoare)."""
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    move = (price - entry) if direction == "LONG" else (entry - price)
    return move / risk


def evaluate_plan(plan, candles):
    """Parcurge lumanarile de dupa crearea planului si determina ce s-a atins
    PRIMUL. `candles` = [[ts, open, high, low, close, volume], ...].

    Returneaza True daca starea planului s-a schimbat."""
    if plan["state"] in CLOSED_STATES:
        return False

    entry, sl, tp1, tp2 = plan["entry"], plan["sl"], plan["tp1"], plan["tp2"]
    direction = plan["direction"]
    is_long = direction == "LONG"
    risk = abs(entry - sl)
    if risk <= 0:
        return False

    relevant = [c for c in candles if c[0] / 1000.0 >= plan["created_ts"]]
    if not relevant:
        return False

    # FAZA 1: planul asteapta ca pretul sa revina la zona de intrare
    if plan["state"] == STATE_PENDING:
        # BUG FIX: asteptarea se masoara in TIMP, nu numarand barele primite in
        # apelul curent. Scanarea live trimite toate lumanarile de la creare, deci
        # numaratoarea iesea corecta; backtest-ul trimite cate UNA, deci contorul
        # ramanea mereu 1 si NO_ENTRY nu se declansa niciodata - backtest-ul nu
        # modela deloc expirarea pullback-ului. Criteriul de timp e identic in
        # ambele moduri si e si idempotent la reevaluari suprapuse.
        bar_seconds = plan.get("bar_seconds") or DEFAULT_BAR_SECONDS
        deadline = plan["created_ts"] + MAX_WAIT_BARS * bar_seconds
        waited = 0
        for c in relevant:
            waited += 1
            bar_ts, high, low = c[0] / 1000.0, c[2], c[3]
            touched = (low <= entry) if is_long else (high >= entry)
            if touched:
                plan["state"] = STATE_OPEN
                plan["state_detail"] = "INTRARE ATINSA - pozitie activa"
                plan["entered_ts"] = bar_ts
                break
            if bar_ts >= deadline:
                plan["state"] = STATE_NO_ENTRY
                plan["state_detail"] = f"PRETUL NU A REVENIT in {MAX_WAIT_BARS} bare - anulat"
                plan["realized_r"] = 0.0   # niciun trade: nicio pierdere si niciun cost
                plan["gross_r"] = 0.0
                plan["closed_ts"] = bar_ts
                return True
        if plan["state"] == STATE_PENDING:
            plan["bars_checked"] = waited
            return False

    # BUG FIX (idempotenta, gasit la testul de 2000 de variante):
    # faza de tranzactie incepe de la INTRARE, nu de la creare. Filtrul asta
    # trebuie sa se aplice SI cand planul e deja OPEN dintr-o evaluare
    # anterioara - atunci blocul PENDING de mai sus e sarit complet. Fara el,
    # reevaluarea verifica SL si TP pe barele de DINAINTE de intrare: la un
    # LONG care mai intai a urcat la TP1 si abia apoi a coborat la zona de
    # intrare, a doua trecere inregistra fals TP1, apoi iesirea la breakeven.
    if plan.get("entered_ts"):
        relevant = [c for c in relevant if c[0] / 1000.0 >= plan["entered_ts"]]
    if not relevant:
        return plan["state"] != STATE_PENDING

    # BUG FIX (idempotenta): retin MOMENTUL cand s-a atins TP1, nu doar un
    # boolean. Fara asta, la reevaluarea planului - se intampla la FIECARE
    # scanare, pe aceleasi lumanari - SL-ul mutat la breakeven se aplica
    # RETROACTIV si barelor de dinainte de TP1. Cum entry-ul e langa pretul
    # curent, aproape orice plan coboara sub entry la un moment dat inainte de
    # TP1, deci toti castigatorii s-ar fi inchis fals la +0.5R in loc sa ruleze
    # spre TP2 - si toate datele de invatare ar fi fost falsificate in jos.
    tp1_ts = plan.get("tp1_hit_ts")
    changed = False
    bars = 0

    for c in relevant:
        bars += 1
        bar_ts, high, low, close = c[0] / 1000.0, c[2], c[3], c[4]

        # breakeven-ul se aplica DOAR barelor de dupa cea in care s-a atins TP1
        after_tp1 = tp1_ts is not None and bar_ts > tp1_ts
        active_sl = entry if after_tp1 else sl

        if is_long:
            sl_touched = low <= active_sl
            tp1_touched = high >= tp1
            tp2_touched = high >= tp2
        else:
            sl_touched = high >= active_sl
            tp1_touched = low <= tp1
            tp2_touched = low <= tp2

        # REGULA 1: ambiguitate in aceeasi bara -> presupun SL primul
        if sl_touched:
            if after_tp1:
                # jumatate luata la TP1, restul iesit la breakeven
                r = TP1_FRACTION * _r_at(tp1, entry, sl, direction)
                plan["state_detail"] = "TP1 HIT - SL LA BREAKEVEN"
            else:
                r = -1.0
                plan["state_detail"] = "SL HIT - INVALIDATED"
            plan["state"] = STATE_SL
            plan["gross_r"] = round(r, 3)
            plan["realized_r"] = round(r - cost_in_r(entry, sl), 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

        if tp2_touched:
            r_tp1 = _r_at(tp1, entry, sl, direction)
            r_tp2 = _r_at(tp2, entry, sl, direction)
            r = (TP1_FRACTION * r_tp1 + (1 - TP1_FRACTION) * r_tp2
                 if tp1_ts is not None else r_tp2)
            plan["state"] = STATE_TP2
            plan["state_detail"] = "TP2 HIT - CLOSED"
            plan["gross_r"] = round(r, 3)
            plan["realized_r"] = round(r - cost_in_r(entry, sl), 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

        if tp1_touched and tp1_ts is None:
            tp1_ts = bar_ts
            plan["tp1_hit_ts"] = bar_ts
            plan["state"] = STATE_TP1
            plan["state_detail"] = "TP1 HIT - RULEAZA SPRE TP2"
            changed = True

        if bars >= MAX_BARS:
            r = _r_at(close, entry, sl, direction)
            if tp1_ts is not None:
                r = TP1_FRACTION * _r_at(tp1, entry, sl, direction) + (1 - TP1_FRACTION) * r
            plan["state"] = STATE_EXPIRED
            plan["state_detail"] = f"EXPIRAT dupa {bars} bare"
            plan["gross_r"] = round(r, 3)
            plan["realized_r"] = round(r - cost_in_r(entry, sl), 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

    plan["bars_checked"] = bars
    return changed


# ===================== CALIBRARE DIN REZULTATE REALE ========================

def wilson_interval(successes, total, z=1.96):
    """Interval de incredere Wilson - onest si la esantioane mici, spre
    deosebire de intervalul normal care da rezultate absurde acolo."""
    if total <= 0:
        return (0.0, 1.0)
    # Aparare: un `successes` peste `total` (date corupte, contor desincronizat)
    # ar da p > 1, iar p*(1-p) negativ sub radical - math domain error, care ar
    # opri intreg workflow-ul. Marginile se limiteaza in loc sa crape.
    successes = max(0, min(successes, total))
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_calibration(store, bucket_size=20):
    """Rata REALA de succes pe intervale de scor, calculata din planurile
    inchise. Asta inlocuieste formula `50 + scor * 0.35`, care nu era o
    probabilitate ci scorul rescalat."""
    buckets = {}
    for p in store["plans"]:
        if p["state"] not in CLOSED_STATES or p.get("realized_r") is None:
            continue
        if p.get("geometry", "v1") != GEOMETRY_VERSION:
            continue  # geometrie veche: rezultatele nu sunt comparabile
        if p.get("state") == STATE_NO_ENTRY:
            continue  # pretul nu a revenit la intrare: nicio tranzactie, deci
            # niciun rezultat de calibrat. Le lasam inauntru ar fi insemnat sa le
            # numaram ca pierderi (realized_r = 0.0 nu e > 0), coborand artificial
            # rata de succes si invatand agentul ca acele configuratii esueaza.
        score = p.get("score_at_entry")
        if score is None:
            continue
        b = int(score // bucket_size) * bucket_size
        entry = buckets.setdefault(b, {"wins": 0, "total": 0, "sum_r": 0.0,
                                       "sum_win_r": 0.0, "sum_loss_r": 0.0,
                                       "wins_n": 0, "losses_n": 0})
        entry["total"] += 1
        entry["sum_r"] += p["realized_r"]
        if p["realized_r"] > 0:
            entry["wins"] += 1
            entry["wins_n"] += 1
            entry["sum_win_r"] += p["realized_r"]
        else:
            entry["losses_n"] += 1
            entry["sum_loss_r"] += abs(p["realized_r"])

    calibration = {}
    for b, e in buckets.items():
        lo, hi = wilson_interval(e["wins"], e["total"])
        calibration[str(b)] = {
            "total": e["total"],
            "win_rate": round(100 * e["wins"] / e["total"], 1),
            "ci_low": round(100 * lo, 1),
            "ci_high": round(100 * hi, 1),
            "avg_r": round(e["sum_r"] / e["total"], 3),
            "avg_win_r": round(e["sum_win_r"] / e["wins_n"], 3) if e["wins_n"] else None,
            "avg_loss_r": round(e["sum_loss_r"] / e["losses_n"], 3) if e["losses_n"] else None,
            "reliable": e["total"] >= MIN_BUCKET_SAMPLES,
        }
    return calibration


def calibrated_probability(calibration, score, bucket_size=20):
    """Probabilitatea masurata pentru scorul asta. Returneaza None daca nu am
    destule date - prefer sa spun "nu stiu" decat sa inventez un numar."""
    b = str(int(score // bucket_size) * bucket_size)
    entry = calibration.get(b)
    if not entry or not entry["reliable"]:
        return None
    return entry


# ========================= POARTA DE DECIZIE ================================

def decide(calibration, signal, agent_pred=None, bucket_size=20):
    """Agentul decide singur daca merita deschis un plan, pe baza istoricului
    lui de rezultate - nu pe baza unei formule fixe.

    Politica: EXPLOREAZA cand nu stie, EXPLOATEAZA cand stie.
      - fara date suficiente -> deschide planul, marcat ca EXPLORARE
        (are nevoie de date ca sa invete; a refuza tot ar insemna sa nu
        invete niciodata nimic)
      - cu date -> calculeaza valoarea asteptata in R si deschide doar daca
        e pozitiva

    ROLUL AGENTULUI (`agent_pred` din ai_agent.predict_for_signal):
    Calibrarea stie doar in ce interval de scor cade semnalul. Agentul vede
    toate caracteristicile (trend, momentum, volatilitate, volum, directie,
    persistenta), deci e mai fin. Cand agentul e ACTIVE - adica a demonstrat
    pe date ca bate baseline-ul - probabilitatea lui inlocuieste rata bruta
    de bucket in calculul valorii asteptate. Marimile de castig/pierdere in R
    raman cele MASURATE din planuri inchise; agentul estimeaza doar sansa,
    nu inventeaza si magnitudini.
    Cand agentul e in SHADOW, predictia lui e doar inregistrata pe plan, ca
    sa se poata verifica ulterior daca ar fi ajutat.
    """
    score = signal.get("risk_adjusted", 0)
    cal = calibrated_probability(calibration, score, bucket_size)

    if not USE_DECISION_GATE:
        # Masuram si raportam in continuare - cifrele apar in dashboard si pe
        # plan - dar nu refuzam nimic. Vezi nota de la USE_DECISION_GATE.
        return {"action": "ISSUE", "mode": "POARTA_DEZACTIVATA",
                "reason": ("poarta dezactivata: pe walk-forward a inrautatit "
                           "rezultatul in 2 din 3 rulari"),
                "expected_value_r": None,
                "calibrated_prob": cal["win_rate"] if cal else None,
                "agent_prob": (agent_pred or {}).get("probability"),
                "agent_used": False}
    agent_p = (agent_pred or {}).get("probability")
    agent_active = bool((agent_pred or {}).get("active"))

    if cal is None:
        return {"action": "ISSUE", "mode": "EXPLORARE",
                "reason": f"inca nu am destule planuri inchise la scor ~{score} "
                          f"(prag {MIN_BUCKET_SAMPLES}) - deschid ca sa invat",
                "expected_value_r": None, "calibrated_prob": None,
                "agent_prob": agent_p, "agent_used": False}

    avg_win = cal["avg_win_r"] if cal["avg_win_r"] is not None else 1.0
    avg_loss = cal["avg_loss_r"] if cal["avg_loss_r"] is not None else 1.0

    # BUG FIX: agentul NU mai inlocuieste probabilitatea in calculul EV.
    # Iesirea unei regresii logistice antrenate pe date dezechilibrate nu e o
    # probabilitate calibrata: masurat pe 4226 de planuri, agentul dadea median
    # 0.19-0.27 acolo unde ratele reale erau 23%-60%. Substituind-o, poarta
    # refuza pana si intervalul de scor cu +0.406R - singurul profitabil.
    # In plus, agentul s-a dovedit ca ordoneaza mai PROST decat scorul brut
    # (AUC 0.594 vs 0.606), deci nu are ce imbunatati deocamdata.
    # Probabilitatea vine acum mereu din calibrare, care e masurata direct din
    # rezultate. Agentul e inregistrat pe plan si va conta abia cand va dovedi
    # ordonare superioara - vezi `agent_superior` din ai_agent.
    p = cal["win_rate"] / 100.0
    source = f"calibrare ({cal['win_rate']}%, n={cal['total']})"
    agent_used = False
    if agent_active and agent_p is not None and (agent_pred or {}).get("superior"):
        p = agent_p
        source = f"agent AI ({100*agent_p:.0f}%, ordonare dovedita superioara)"
        agent_used = True

    ev = p * avg_win - (1 - p) * avg_loss
    base = {"expected_value_r": round(ev, 3), "calibrated_prob": cal["win_rate"],
            "agent_prob": agent_p, "agent_used": agent_used}

    if ev <= 0:
        return {"action": "SKIP", "mode": "EXPLOATARE",
                "reason": f"valoare asteptata negativa ({ev:+.2f}R) la scor ~{score}, "
                          f"sansa din {source}", **base}

    return {"action": "ISSUE", "mode": "EXPLOATARE",
            "reason": f"valoare asteptata {ev:+.2f}R la scor ~{score}, sansa din {source} "
                      f"(IC calibrare {cal['ci_low']}-{cal['ci_high']}%)", **base}


# ============================== RAPORTARE ===================================

def summarize(store):
    plans = store["plans"]
    all_closed = [p for p in plans if p["state"] in CLOSED_STATES and p.get("realized_r") is not None]
    current_geo = [p for p in all_closed if p.get("geometry", "v1") == GEOMETRY_VERSION]
    no_entry = [p for p in current_geo if p.get("state") == STATE_NO_ENTRY]
    closed = [p for p in current_geo if p.get("state") != STATE_NO_ENTRY]
    # Planurile legacy nu mai sunt in `plans` dupa archive_stale_plans - au fost
    # mutate pe disc, in fisiere separate per geometrie. `legacy` de aici prinde
    # doar ce a ramas NEARHIVAT inca in acest apel (rar: chiar planurile pe care
    # save_plans le arhiveaza data viitoare). Restul vine din indexul de arhiva.
    legacy = [p for p in all_closed if p.get("geometry", "v1") != GEOMETRY_VERSION]
    open_plans = [p for p in plans if p["state"] not in CLOSED_STATES]

    archived_idx = load_json(ARCHIVE_INDEX_FILE, {})
    archived_closed = sum(v.get("closed", 0) for v in archived_idx.values())
    archived_r = sum(v.get("total_r", 0.0) for v in archived_idx.values())

    total_r = sum(p["realized_r"] for p in closed)
    wins = [p for p in closed if p["realized_r"] > 0]
    losses = [p for p in closed if p["realized_r"] <= 0]

    by_state = {}
    for p in plans:
        by_state[p["state"]] = by_state.get(p["state"], 0) + 1

    profit_factor = None
    if losses:
        gross_win = sum(p["realized_r"] for p in wins)
        gross_loss = abs(sum(p["realized_r"] for p in losses))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    return {
        "no_entry": len(no_entry),
        "no_entry_pct": round(100 * len(no_entry) / len(current_geo), 1) if current_geo else None,
        "legacy_closed": len(legacy) + archived_closed,
        "legacy_total_r": round(sum(p["realized_r"] for p in legacy) + archived_r, 2)
                          if (legacy or archived_closed) else None,
        "geometry": GEOMETRY_VERSION,
        "total_plans": len(plans),
        "open": len(open_plans),
        "closed": len(closed),
        "by_state": by_state,
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / len(closed), 3) if closed else None,
        "profit_factor": profit_factor,
        "best": max((p["realized_r"] for p in closed), default=None),
        "worst": min((p["realized_r"] for p in closed), default=None),
    }


def print_summary(store):
    s = summarize(store)
    print(f"Planuri: {s['total_plans']} total, {s['open']} deschise, {s['closed']} inchise")
    if s["closed"]:
        print(f"  Rata de succes: {s['win_rate']}%")
        print(f"  R total: {s['total_r']:+.2f}R  |  R mediu/plan: {s['avg_r']:+.3f}R")
        if s["profit_factor"] is not None:
            print(f"  Profit factor: {s['profit_factor']}")
        print(f"  Cel mai bun: {s['best']:+.2f}R  |  cel mai slab: {s['worst']:+.2f}R")
    print(f"  Stari: {s['by_state']}")
