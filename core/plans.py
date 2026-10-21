#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.plans - planurile agentului: creare, evaluare, calibrare, decizie si geometrie (NUCLEU, fara I/O).

Mutat din plan_tracker.py in Etapa 2 (arhitectura hexagonala). Operatiile pe disc -
plans.json, arhiva pe geometrii, atenuarile din self_check.json - sunt in adaptorul
de stocare (plan_tracker.py), care le furnizeaza nucleului prin MITIGATIONS_SOURCE
si ARCHIVE_INDEX_SOURCE.

plan_tracker.py (documentatia originala)
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
   realitatea. Regula se aplica la fel oricarei ambiguitati (tinta pe bara
   intrarii, TP1 si intoarcerea la intrare in aceeasi bara) - vezi regulile
   de evaluare (EVAL_RULES) de la `evaluate_plan`.
2. MODEL DE POZITIE: 50% din pozitie se inchide la TP1, restul merge la TP2,
   iar SL-ul se muta la breakeven dupa TP1. E o regula standard, si e
   declarata explicit ca sa fie reproductibila.
3. EXPIRARE: dupa MAX_BARS fara sa atinga nimic, planul se inchide la pretul
   curent (mark-to-market), nu se sterge. Un plan care nu a mers nicaieri e
   tot un rezultat din care se invata. Barele se numara pe grila timpului,
   identic in live si in backtest.

CE FACE IN PLUS FATA DE POZE
----------------------------
Pozele arata stari de plan ("TP1 HIT - CLOSED", "SL / INVALIDATED"), dar
afiseaza si procente de tip "86% confirmed" care nu par sa fie masurate din
rezultate. Aici probabilitatea afisata e CALIBRATA din planurile inchise
efectiv, cu interval de incredere Wilson, si spun explicit cand nu am destule
date ca sa pronunt un numar.
"""

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone


# PORTURI DE CITIRE. Nucleul nu citeste fisiere: adaptorul de stocare
# (plan_tracker.py) leaga aceste surse la disc. Implicit: nicio atenuare, arhiva goala.
def _nothing():
    return {}


MITIGATIONS_SOURCE = _nothing      # () -> atenuarile active (self_check.json: mitigations)
ARCHIVE_INDEX_SOURCE = _nothing    # () -> indexul arhivei pe geometrii (count, closed, total_r)


def auto_mitigations():
    """Atenuarile decise de auto-diagnostic la rularea anterioara (mod de siguranta,
    filtrul Elliott), citite prin portul MITIGATIONS_SOURCE. Lipsa lor inseamna
    comportamentul implicit."""
    try:
        return MITIGATIONS_SOURCE() or {}
    except Exception:
        return {}


MAX_BARS = 48          # cate bare las un plan deschis de la intrare (48 = 8 zile pe 4h, 2 zile pe 1h)
MIN_BUCKET_SAMPLES = 20  # sub atat, nu pronunt o probabilitate calibrata


def history_table(store, bucket_size=20):
    """Istoricul masurat per (directie, interval de scor), din planurile inchise
    ale familiei curente: numar, rata de castig si durata mediana in bare pana
    la inchidere (pentru castiguri si in general). Folosit de prognoza de pe
    grafic, ca durata si probabilitatea sa vina din trecut, nu din presupuneri."""
    raw = {}
    for p in store.get("plans") or []:
        if p.get("realized_r") is None or p.get("state") == STATE_NO_ENTRY:
            continue
        if not same_family(p.get("geometry", "v1")):
            continue
        sc, d = p.get("score_at_entry"), p.get("direction")
        if sc is None or d not in ("LONG", "SHORT") or not p.get("closed_ts"):
            continue
        bs = p.get("bar_seconds") or DEFAULT_BAR_SECONDS
        bars = max(1, int((p["closed_ts"] - p.get("created_ts", p["closed_ts"])) / bs))
        e = raw.setdefault(f"{d}:{int(sc // bucket_size) * bucket_size}",
                           {"n": 0, "w": 0, "bw": [], "ba": []})
        e["n"] += 1
        e["ba"].append(bars)
        if p["realized_r"] > 0:
            e["w"] += 1
            e["bw"].append(bars)
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    return {k: {"n": e["n"], "win_rate": round(100 * e["w"] / e["n"], 1),
                "median_bars_win": med(e["bw"]), "median_bars_all": med(e["ba"])}
            for k, e in raw.items()}


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

# FILTRUL AGENTULUI, separat de poarta de calibrare. Cele doua au dovezi diferite:
#   - poarta de calibrare (EV din rata pe interval de scor) a INRAUTATIT
#     rezultatul pe walk-forward in 2 din 3 rulari -> ramane oprita implicit;
#   - filtrul agentului (elimina treimea de jos a ordonarii) a fost masurat pe
#     3.000 de predictii in afara esantionului, pe 8.6 ani de date reale:
#     treimea de jos -0.240R/plan, treimea de sus +0.269R/plan, avantaj
#     +0.257R cu IC95 +0.158..+0.363 -> ACTIVAT implicit.
# Activarea ramane conditionata de starea ACTIVE si de ranking_edge peste prag,
# deci se opreste singur daca avantajul dispare.
USE_AGENT_FILTER = os.environ.get("USE_AGENT_FILTER", "true").lower() == "true"
# Fractiunea semnalelor filtrate care se emit totusi, pentru explorare.
AGENT_EXPLORE_RATE = float(os.environ.get("AGENT_EXPLORE_RATE", "0.15"))

# FILTRUL DE CONFLICT ELLIOTT. Masurat pe 16.318 planuri reale inchise:
#   Elliott contrazice planul (ev_elliott <= -0.1): n=5.795, R mediu +0.042,
#       IC95 -0.02..+0.11 -> niciun avantaj demonstrat, dupa costuri
#   fara conflict:                                  n=10.523, R mediu +0.112
#   diferenta +0.070R, IC95 +0.006..+0.137
# Efectul filtrului pe istoric: R pe plan +0.087 -> +0.112 (+29%), drawdown
# maxim 236R -> 202R (-14%), dar R TOTAL +1424 -> +1179 (-17%): tranzactiile
# contra Elliott aduceau totusi ceva, doar ca fara avantaj dovedit. Activ
# implicit; se opreste cu USE_ELLIOTT_FILTER=false.
USE_ELLIOTT_FILTER = os.environ.get("USE_ELLIOTT_FILTER", "true").lower() == "true"
ELLIOTT_CONFLICT_MAX = -0.1


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
FEATURE_VERSION = "f8"      # f5 = + Elliott cu numaratori concurente; f8 = + ev_analyst (verdictul analistului)


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


def _family_of(geo):
    parts = (geo or "").split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else (geo or "")


# FAMILIA DE GEOMETRIE: versiunea + timeframe, FARA semnatura de capabilitati.
#
# DEFECT DE ARHITECTURA, reparat aici: calibrarea, agentul, vecinii comparabili
# si migrarea comparau semnatura EXACTA. Scanarea live detecteaza order book si
# trades si ruleaza ca "v6-4h-obf"; backtest-ul, fara ele, produce "v6-4h-o".
# Masurat in productie: 21.445 de planuri de backtest IGNORATE de calibrarea
# live (bucket-ul 40 avea 3 planuri - de aici "NECALIBRAT INCA (N=3)" pe
# dashboard), vecinii comparabili cautati printre 46 de planuri, iar agentul -
# care ruleaza ca proces separat, fara capabilitati - nu invata NICIODATA din
# planurile live.
#
# Capabilitatile schimba ce CARACTERISTICI vede modelul, nu REZULTATUL unui
# plan: aceleasi reguli de entry, SL, TP si costuri. Exact regula aplicata deja
# la caracteristicile noi. Semnatura ramane pe plan, pentru audit; invatarea si
# calibrarea folosesc familia.
GEOMETRY_FAMILY = _family_of(GEOMETRY_VERSION)


def same_family(geo):
    """Planul apartine familiei curente (aceleasi reguli de rezultat)?"""
    return _family_of(geo) == GEOMETRY_FAMILY


def set_capabilities(caps_sig):
    """Fixeaza semnatura de capabilitati DUPA ce bursa a fost sondata.

    BUG FIX: semnatura se citea din mediu la import, adica inainte ca scanerul
    sa stie ce ofera bursa. Rezultatul: un plan creat CU order flow primea
    eticheta "v5-4h-o" - aceeasi ca planurile de backtest, care nu au order
    flow. Ar fi ajuns in aceeasi calibrare, adica exact nepotrivirea tacuta pe
    care semnatura trebuia sa o previna.
    Se apeleaza o data, la inceputul scanarii, dupa sondare.
    """
    global GEOMETRY_VERSION, GEOMETRY_FAMILY
    GEOMETRY_VERSION = _build_geometry(caps_sig)
    GEOMETRY_FAMILY = _family_of(GEOMETRY_VERSION)
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
        if same_family(p.get("geometry", "v1")):
            continue
        prev = p.get("state")
        p["state"] = STATE_EXPIRED
        p["state_detail"] = f"INCHIS AUTOMAT LA MIGRARE (geometrie {p.get('geometry', 'v1')})"
        p["migrated"] = True
        p["migrated_ts"] = time.time()
        migrated.append((p["id"], p["symbol"], p["direction"], prev))
    return migrated


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


# ============================ CREARE DE PLANURI =============================

def open_plan_for(store, symbol, direction):
    """Planul ACTIV deja urmarit pe simbol+directie (cel mai recent), sau None.

    O SINGURA regula, folosita de scanare (has_open_plan), de dashboard si de
    emailul digest. Inainte, scanarea sarea peste semnalul cu plan activ fara sa
    scrie vreo decizie, iar cardul "AI plan" afisa nivelurile semnalului curent ca
    si cum ar fi fost planul agentului (8 oct 2026: DASH SHORT entry 54.945, desi
    planul real #293318 avea entry 54.52)."""
    found = None
    for p in (store or {}).get("plans") or []:
        if (p.get("symbol") == symbol and p.get("direction") == direction
                and p.get("state") not in CLOSED_STATES
                and (found is None or (p.get("id") or 0) > (found.get("id") or 0))):
            found = p
    return found


def has_open_plan(store, symbol, direction):
    """Nu deschid un plan nou pentru acelasi simbol+directie daca deja am unul
    activ. Fara asta, un semnal persistent ar genera zeci de planuri identice
    si ar umple istoricul cu duplicate corelate."""
    return open_plan_for(store, symbol, direction) is not None


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
        # versiunea setului de caracteristici cu care s-a creat planul - auditul memoriei
        # (core/plan_audit) si reconstructia unei epoci de date recunosc dupa ea planurile
        # create de versiuni anterioare ale agentului
        "fv": FEATURE_VERSION,
        # versiunea regulilor de evaluare (EVAL_RULES) - un plan evaluat cu reguli vechi se
        # reevalueaza de la creare (scanarea), nu se amesteca in statistici cu cele noi
        "er": EVAL_RULES,
        "bar_seconds": signal.get("bar_seconds", DEFAULT_BAR_SECONDS),
    }
    store["plans"].append(plan)
    store["next_id"] += 1
    return plan


# ======================= EVALUARE BARA CU BARA ==============================
#
# REGULILE DE EVALUARE - versiunea EVAL_RULES, salvata pe fiecare plan ("er"). Aceleasi pentru
# live si backtest. O bara arata doar deschiderea, maximul, minimul si inchiderea, nu si ORDINEA
# in care s-au atins; orice ambiguitate se rezolva IMPOTRIVA planului (regula 1 din antet,
# extinsa consecvent), ca rezultatul sa fie o margine de jos, nu o promisiune:
#   A. SL si o tinta in aceeasi bara -> SL.
#   B. Bara intrarii: o tinta atinsa in aceeasi bara cu umplerea NU se numara - maximul (minimul
#      la SHORT) putea veni INAINTE de umplere. Exceptie: bara care deschide deja dincolo de
#      intrare (umplere la deschidere, deci tot ce urmeaza in bara e dupa ea).
#   C. Prima bara cu TP1 care atinge si intrarea -> a doua jumatate iese la breakeven in aceeasi
#      bara (intoarcerea la intrare putea veni DUPA TP1).
#   D. TP2 fara TP1 inregistrat: TP1 e intre intrare si TP2, deci s-a atins in aceeasi bara -
#      jumatate la TP1, jumatate la TP2. Inainte se platea toata pozitia la TP2.
#   E. Timpul se masoara pe GRILA barelor, nu numarand lumanarile primite intr-un apel:
#      asteptarea intrarii (MAX_WAIT_BARS) incepe cu bara in care s-a creat planul, durata maxima
#      (MAX_BARS) cu bara intrarii. Rezultatul e identic cand backtest-ul trimite cate o bara si
#      cand scanarea trimite toate barele. Inainte, backtest-ul nu expira niciodata un plan
#      (masurat: 1.013 planuri tinute peste 48 de bare, unul 3.452), iar live-ul le expira.
#   F. Planul live se creeaza in mijlocul barei (scanarea ruleaza la ~40 de minute dupa inchiderea
#      ei, cu planul calculat pe barele inchise). Restul barei de creare se evalueaza dintr-o bara
#      PARTIALA, construita din lumanarile de 5 minute de dupa creare (plan["first_bar"], pusa de
#      scanare cand bara s-a inchis). Inainte se sarea toata bara: live-ul masura alta strategie
#      decat backtest-ul (masurat pe 16.406 planuri: -0.047R/plan cu prima bara sarita, fata de
#      +0.059R cu ea).
# Backtest-ul creeaza planul la INCHIDEREA barei de semnal (inainte: la deschiderea ei, cu 4 ore
# prea devreme), deci bara lui de creare e chiar prima bara evaluata, intreaga.
EVAL_RULES = 2
FIRST_BAR_TF = "5m"            # lumanarile din care se construieste bara partiala de creare (live)
FIRST_BAR_SECONDS = 300
FIRST_BAR_GIVE_UP_HOURS = 24   # dupa atat timp fara lumanari de 5 minute ...
FIRST_BAR_MAX_TRIES = 6        # ... si dupa atatea scanari esuate: evaluare fara bara partiala
OUTCOME_FIELDS = ("state", "state_detail", "realized_r", "gross_r", "closed_ts", "entered_ts",
                  "tp1_hit_ts", "bars_checked")


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


def _grid(ts, bar_seconds):
    """Deschiderea barei (pe grila timeframe-ului, UTC) care contine momentul `ts`."""
    return math.floor(ts / bar_seconds + 1e-9) * bar_seconds


def creation_bar(plan):
    """(deschidere, inchidere) ale barei in care s-a creat planul."""
    bar_s = plan.get("bar_seconds") or DEFAULT_BAR_SECONDS
    a = _grid(plan["created_ts"], bar_s)
    return a, a + bar_s


def first_bar_due(plan, closed_candles):
    """True cand planul live are nevoie acum de bara partiala de creare: creat in mijlocul barei,
    bara s-a inchis (`closed_candles` = doar bare inchise) si bara partiala nu e inca stabilita."""
    if plan.get("source") == "backtest" or "first_bar" in plan:
        return False
    a, _b = creation_bar(plan)
    if plan["created_ts"] <= a + 1e-6:
        return False
    return bool(closed_candles) and closed_candles[-1][0] / 1000.0 >= a


def partial_first_bar(plan, sub_candles, sub_seconds=FIRST_BAR_SECONDS):
    """Bara partiala [creare, inchiderea barei de creare), din lumanarile mai mici incepute dupa
    creare si incheiate in bara. [] = niciuna in interval (creat in ultimele minute ale barei)."""
    a, b = creation_bar(plan)
    lo_ms, hi_ms = plan["created_ts"] * 1000.0, b * 1000.0
    rows = []
    for c in sub_candles or []:
        try:
            ts, o, h, l, cl = float(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])
        except (TypeError, ValueError, IndexError):
            continue
        if ts >= lo_ms and ts + sub_seconds * 1000 <= hi_ms and min(o, h, l, cl) > 0 and h >= l:
            rows.append((ts, o, h, l, cl, float(c[5] or 0) if len(c) > 5 else 0.0))
    if not rows:
        return []
    rows.sort()
    return [int(math.ceil(plan["created_ts"] * 1000)), rows[0][1], max(r[2] for r in rows),
            min(r[3] for r in rows), rows[-1][4], round(sum(r[5] for r in rows), 8)]


def with_first_bar(plan, candles):
    """Lumanarile pe care se evalueaza planul: bara partiala de creare (daca exista) + barele."""
    fb = plan.get("first_bar")
    return ([fb] if fb else []) + list(candles or [])


def needs_reevaluation(plan):
    """Plan live evaluat cu alte reguli decat cele curente (inclusiv cele dinaintea versionarii)."""
    return plan.get("source") != "backtest" and plan.get("er") != EVAL_RULES


def reset_outcome(plan):
    """Sterge rezultatul evaluarii (planul redevine PENDING, ca la creare) si intoarce rezultatul
    vechi, pentru jurnal. Nivelurile, decizia si caracteristicile raman neatinse."""
    old = {k: plan.get(k) for k in ("state", "realized_r", "closed_ts", "entered_ts", "tp1_hit_ts")}
    old["er"] = plan.get("er", 1)
    for k in OUTCOME_FIELDS:
        plan.pop(k, None)
    plan.update({"state": STATE_PENDING, "state_detail": "ASTEAPTA PULLBACK la zona de intrare",
                 "realized_r": None, "closed_ts": None, "bars_checked": 0})
    return old


def _close(plan, state, detail, r, bar_ts, entry, sl):
    plan["state"] = state
    plan["state_detail"] = detail
    plan["gross_r"] = round(r, 3)
    plan["realized_r"] = round(r - cost_in_r(entry, sl), 3)
    plan["closed_ts"] = bar_ts


def evaluate_plan(plan, candles):
    """Parcurge lumanarile de dupa crearea planului si determina ce s-a atins PRIMUL, dupa regulile
    de mai sus. `candles` = [[ts, open, high, low, close, volume], ...] (pentru live: cu bara
    partiala de creare in fata, vezi `with_first_bar`).

    Returneaza True daca starea planului s-a schimbat."""
    if plan["state"] in CLOSED_STATES:
        return False

    entry, sl, tp1, tp2 = plan["entry"], plan["sl"], plan["tp1"], plan["tp2"]
    direction = plan["direction"]
    is_long = direction == "LONG"
    risk = abs(entry - sl)
    if risk <= 0:
        return False
    bar_s = plan.get("bar_seconds") or DEFAULT_BAR_SECONDS
    created = plan["created_ts"]

    relevant = [c for c in candles if c[0] / 1000.0 >= created]
    if not relevant:
        return False

    # FAZA 1: planul asteapta ca pretul sa revina la zona de intrare. Asteptarea se masoara pe
    # grila barelor (regula E), deci e aceeasi in backtest (o bara pe apel) si live (toate barele).
    if plan["state"] == STATE_PENDING:
        start = _grid(created, bar_s)
        deadline = start + MAX_WAIT_BARS * bar_s
        waited = 0
        for c in relevant:
            bar_ts, high, low = c[0] / 1000.0, c[2], c[3]
            waited = int(round((_grid(bar_ts, bar_s) - start) / bar_s)) + 1
            touched = (low <= entry) if is_long else (high >= entry)
            if touched:
                plan["state"] = STATE_OPEN
                plan["state_detail"] = "INTRARE ATINSA - pozitie activa"
                plan["entered_ts"] = bar_ts
                break
            if _grid(bar_ts, bar_s) + bar_s >= deadline:
                plan["state"] = STATE_NO_ENTRY
                plan["state_detail"] = f"PRETUL NU A REVENIT in {MAX_WAIT_BARS} bare - anulat"
                plan["realized_r"] = 0.0   # niciun trade: nicio pierdere si niciun cost
                plan["gross_r"] = 0.0
                plan["closed_ts"] = bar_ts
                plan["bars_checked"] = waited
                return True
        if plan["state"] == STATE_PENDING:
            plan["bars_checked"] = waited
            return False

    # Faza de tranzactie incepe de la INTRARE, nu de la creare - si cand planul e deja OPEN dintr-o
    # evaluare anterioara (reevaluarea nu are voie sa verifice SL/TP pe barele de dinainte).
    if plan.get("entered_ts"):
        relevant = [c for c in relevant if c[0] / 1000.0 >= plan["entered_ts"]]
    if not relevant:
        return plan["state"] != STATE_PENDING

    entered = plan["entered_ts"]
    entry_bar = _grid(entered, bar_s)
    # Momentul atingerii TP1 se retine (nu doar un boolean): breakeven-ul se aplica doar barelor de
    # DUPA el, si la reevaluare (fiecare scanare), nu retroactiv.
    tp1_ts = plan.get("tp1_hit_ts")
    changed = False
    bars = 0

    for c in relevant:
        bar_ts, opn, high, low, close = c[0] / 1000.0, c[1], c[2], c[3], c[4]
        bars = int(round((_grid(bar_ts, bar_s) - entry_bar) / bar_s)) + 1

        after_tp1 = tp1_ts is not None and bar_ts > tp1_ts
        active_sl = entry if after_tp1 else sl

        if is_long:
            sl_touched = low <= active_sl
            tp1_touched = high >= tp1
            tp2_touched = high >= tp2
            at_entry = low <= entry
        else:
            sl_touched = high >= active_sl
            tp1_touched = low <= tp1
            tp2_touched = low <= tp2
            at_entry = high >= entry

        # REGULA B: pe bara intrarii, tintele nu se numara (decat la umplerea la deschidere)
        if abs(bar_ts - entered) < 1e-6 and not ((opn <= entry) if is_long else (opn >= entry)):
            tp1_touched = tp2_touched = False

        # REGULA A: SL si tinta in aceeasi bara -> SL primul
        if sl_touched:
            if after_tp1:
                # jumatate luata la TP1, restul iesit la breakeven
                _close(plan, STATE_SL, "TP1 HIT - SL LA BREAKEVEN",
                       TP1_FRACTION * _r_at(tp1, entry, sl, direction), bar_ts, entry, sl)
            else:
                _close(plan, STATE_SL, "SL HIT - INVALIDATED", -1.0, bar_ts, entry, sl)
            changed = True
            break

        r_tp1 = _r_at(tp1, entry, sl, direction)
        # REGULA C: prima bara cu TP1 care atinge si intrarea -> breakeven in aceeasi bara
        if tp1_touched and tp1_ts is None and at_entry:
            tp1_ts = bar_ts
            plan["tp1_hit_ts"] = bar_ts
            _close(plan, STATE_SL, "TP1 HIT - SL LA BREAKEVEN (aceeasi bara)",
                   TP1_FRACTION * r_tp1, bar_ts, entry, sl)
            changed = True
            break

        if tp2_touched:
            # REGULA D: TP2 trece prin TP1 - jumatate la TP1, jumatate la TP2
            if tp1_ts is None:
                tp1_ts = bar_ts
                plan["tp1_hit_ts"] = bar_ts
            r = TP1_FRACTION * r_tp1 + (1 - TP1_FRACTION) * _r_at(tp2, entry, sl, direction)
            _close(plan, STATE_TP2, "TP2 HIT - CLOSED", r, bar_ts, entry, sl)
            changed = True
            break

        if tp1_touched and tp1_ts is None:
            tp1_ts = bar_ts
            plan["tp1_hit_ts"] = bar_ts
            plan["state"] = STATE_TP1
            plan["state_detail"] = "TP1 HIT - RULEAZA SPRE TP2"
            changed = True

        # REGULA E: durata maxima pe grila, de la bara intrarii
        if bars >= MAX_BARS:
            r = _r_at(close, entry, sl, direction)
            if tp1_ts is not None:
                r = TP1_FRACTION * r_tp1 + (1 - TP1_FRACTION) * r
            _close(plan, STATE_EXPIRED, f"EXPIRAT dupa {bars} bare", r, bar_ts, entry, sl)
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
        if not same_family(p.get("geometry", "v1")):
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

    auto = auto_mitigations()
    if auto.get("safe_mode"):
        # MOD DE SIGURANTA: auto-diagnosticul a gasit niveluri de plan imposibile.
        # Nu emit planuri noi pana cand calculul e din nou coerent.
        return {"action": "SKIP", "mode": "MOD_SIGURANTA",
                "reason": auto.get("safe_reason") or "mod de siguranta activ",
                "expected_value_r": None, "calibrated_prob": cal["win_rate"] if cal else None,
                "agent_prob": (agent_pred or {}).get("probability"), "agent_used": False}

    agent_p0 = (agent_pred or {}).get("probability")
    cut0 = (agent_pred or {}).get("rank_cut")
    if (USE_AGENT_FILTER and (agent_pred or {}).get("active")
            and (agent_pred or {}).get("superior")
            and agent_p0 is not None and cut0 is not None and agent_p0 < cut0):
        # EXPLORARE: o parte din semnalele filtrate se EMIT totusi, marcate.
        # Fara asta, semnalele refuzate nu se mai urmaresc pana la rezultat, iar
        # agentul ar invata in timp doar din ce a aprobat el insusi - o bucla de
        # selectie care i-ar deforma exact evaluarea pe care se bazeaza filtrul.
        # Alegerea e determinista (hash simbol + moment), deci reproductibila.
        # cheia include probabilitatea agentului: variaza la fiecare semnal, deci
        # alegerea nu devine constanta cand simbolul sau pretul lipsesc (bug
        # prins de test - cu cheie constanta, rata de explorare iesea 100%)
        key = (f"{signal.get('symbol', '')}|{signal.get('direction', '')}|"
               f"{signal.get('price')}|{agent_p0:.8f}")
        if (int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000) < AGENT_EXPLORE_RATE * 1000:
            return {"action": "ISSUE", "mode": "EXPLORARE_FILTRU",
                    "reason": (f"semnal din treimea de jos ({agent_p0:.3f} < {cut0:.3f}), "
                               f"emis pentru explorare ({AGENT_EXPLORE_RATE:.0%} din cele "
                               f"filtrate) - pastreaza evaluarea agentului nedeformata"),
                    "expected_value_r": None,
                    "calibrated_prob": cal["win_rate"] if cal else None,
                    "agent_prob": agent_p0, "agent_used": True}
        return {"action": "SKIP", "mode": "FILTRU_AGENT",
                "reason": (f"agentul pune semnalul in treimea de jos ({agent_p0:.3f} "
                           f"< {cut0:.3f}); masurat in afara esantionului, acea "
                           f"treime pierde in medie"),
                "expected_value_r": None,
                "calibrated_prob": cal["win_rate"] if cal else None,
                "agent_prob": agent_p0, "agent_used": True}

    # REGULI DIN CERCETAREA AUTONOMA (core/research.py): doar cele ACTIVE - trecute de
    # cautare, de perioada rezervata si de confirmarea live. Fara reguli active, nimic
    # nu se schimba. O parte din semnalele excluse se emit totusi (explorare), ca efectul
    # regulii sa ramana masurat live si regula sa poata fi retrasa daca nu mai ajuta.
    from core import research as _research
    for _rule in auto.get("research_rules") or []:
        if not _research.matches(_rule, signal):
            continue
        key = f"rr|{_rule.get('id')}|{signal.get('symbol', '')}|{signal.get('price')}"
        if (int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000) < AGENT_EXPLORE_RATE * 1000:
            return {"action": "ISSUE", "mode": "EXPLORARE_REGULA",
                    "reason": f"regula din cercetare ({_rule.get('text')}) - emis pentru explorare, "
                              "ca efectul regulii sa ramana masurat live",
                    "expected_value_r": None, "calibrated_prob": cal["win_rate"] if cal else None,
                    "agent_prob": (agent_pred or {}).get("probability"), "agent_used": False}
        return {"action": "SKIP", "mode": "REGULA_CERCETARE",
                "reason": f"regula din cercetarea autonoma: {_rule.get('text')} (confirmata pe backtest, "
                          "pe perioada rezervata si pe planuri live)",
                "expected_value_r": None, "calibrated_prob": cal["win_rate"] if cal else None,
                "agent_prob": (agent_pred or {}).get("probability"), "agent_used": False}

    ec = signal.get("elliott_conflict") or {}
    if (USE_ELLIOTT_FILTER and auto.get("elliott_filter", True) and ec.get("bias") is not None
            and ec["bias"] <= ELLIOTT_CONFLICT_MAX):
        nxt = ec.get("next_entry")
        watch = f" Nivel urmarit pentru intrare: ~{nxt:.6g}." if nxt else ""
        base = (f"Elliott contrazice planul {signal.get('direction')} "
                f"({ec.get('headline') or 'structura curenta'}: {ec.get('text') or ''}).")
        # explorare, ca efectul conflictului sa ramana masurabil (altfel
        # planurile contra Elliott n-ar mai produce niciodata rezultate)
        key = f"ew|{signal.get('symbol', '')}|{signal.get('price')}|{ec['bias']:.8f}"
        if (int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000) < AGENT_EXPLORE_RATE * 1000:
            return {"action": "ISSUE", "mode": "EXPLORARE_ELLIOTT",
                    "reason": base + " Emis pentru explorare, ca efectul sa ramana masurat.",
                    "expected_value_r": None,
                    "calibrated_prob": cal["win_rate"] if cal else None,
                    "agent_prob": (agent_pred or {}).get("probability"), "agent_used": False}
        # CIFRELE MASURATE, nu constante: textul spunea mereu "+0.04R/plan, fata de +0.11R fara
        # conflict" (scrise de mana), desi auto-diagnosticul masura pe 12.10 contra Elliott -0.159R
        # si fara conflict -0.023R (ultimele 3000 de planuri). Acum motivul preia exact masuratoarea
        # care a decis filtrul (self_check.elliott_filter_decision -> mitigations).
        # Starea filtrului se spune explicit: "justificat" (IC95 pe blocuri de timp peste zero) sau
        # "zona nesigura" (diferenta pozitiva, dar IC95 include zero - filtrul ramane activ doar prin
        # histerezis, nu pentru ca ar fi dovedit).
        _ew_why = str(auto.get("elliott_filter_reason") or "")
        _ew_head, _ew_txt = _ew_why.split(": ", 1) if ": " in _ew_why else ("", "")
        _ew_state = (" Filtrul e justificat de masuratoare" if _ew_head.startswith("justificat") else
                     " Filtrul ramane activ prin histerezis - efectul nu e distinct de zero"
                     if _ew_head.startswith("zona nesigura") else " Filtrul e activ")
        hist = (f"{_ew_state} (auto-diagnostic, {_ew_txt})." if _ew_txt.startswith("ultimele")
                else _ew_state + "; masuratoarea efectului apare in auto-diagnostic.")
        return {"action": "SKIP", "mode": "ASTEAPTA_CORECTIA",
                "reason": (base + hist + watch),
                "expected_value_r": None,
                "calibrated_prob": cal["win_rate"] if cal else None,
                "agent_prob": (agent_pred or {}).get("probability"), "agent_used": False}

    if not USE_DECISION_GATE:
        # Masuram si raportam in continuare - cifrele apar in dashboard si pe
        # plan - dar nu refuzam nimic. Vezi nota de la USE_DECISION_GATE.
        return {"action": "ISSUE", "mode": "POARTA_DEZACTIVATA",
                # fara cifre scrise de mana: dovada portii (walk-forward) e masurata de backtest in
                # data/walkforward.json si evaluata de auto-audit (core/self_audit.py, parghia "poarta")
                "reason": ("poarta de calibrare dezactivata (USE_DECISION_GATE=false) - planul se emite; "
                           "dovada walk-forward a portii e evaluata in auto-audit"),
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
    # BUG FIX LATENT: probabilitatea agentului NU mai intra in calculul EV.
    # Comentariul de mai sus explica deja de ce - iesirea unei regresii
    # logistice pe date dezechilibrate nu e calibrata (mediana 0.19-0.27 unde
    # ratele reale erau 23-60%). Si totusi codul o substituia cand agentul era
    # "superior", adica exact in situatia descrisa ca fiind gresita. Era
    # inofensiv doar pentru ca poarta e dezactivata; activarea ei ar fi facut
    # agentul sa refuze semnale profitabile.
    #
    # Agentul e bun la ORDONARE (AUC), nu la probabilitati absolute. Deci il
    # folosesc ca FILTRU: EV-ul vine din calibrarea masurata, iar agentul
    # elimina doar semnalele pe care le pune in treimea de jos.
    p = cal["win_rate"] / 100.0
    source = f"calibrare ({cal['win_rate']}%, n={cal['total']})"
    agent_used = False
    rank_cut = (agent_pred or {}).get("rank_cut")
    if (USE_AGENT_FILTER and agent_active and agent_p is not None and rank_cut is not None
            and (agent_pred or {}).get("superior") and agent_p < rank_cut):
        return {"action": "SKIP", "mode": "EXPLOATARE",
                "reason": (f"agentul pune semnalul in treimea de jos "
                           f"({agent_p:.3f} < {rank_cut:.3f}); pe evaluare in afara "
                           f"esantionului, acea treime castiga mai putin"),
                "expected_value_r": None, "calibrated_prob": cal["win_rate"],
                "agent_prob": agent_p, "agent_used": True}

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

DAY_S = 86400
WEEK_S = 7 * DAY_S


def block_size(ts_list):
    """Blocul de timp pentru bootstrap: saptamana cand datele acopera cel putin o jumatate de an
    (destule blocuri), altfel ziua (live-ul are doar cateva saptamani)."""
    if not ts_list:
        return DAY_S
    return WEEK_S if (max(ts_list) - min(ts_list)) >= 26 * WEEK_S else DAY_S


def block_mean_ci(rows, block_s=None, n_boot=600, seed=13):
    """IC95 al mediei prin bootstrap pe BLOCURI de timp. `rows` = [(ts, valoare)].
    Planurile din aceeasi zi sau saptamana sunt corelate - aceeasi miscare a pietei le loveste pe
    toate -, deci tratate ca independente dau un interval prea ingust (masurat pe backtest-ul pe 30
    de simboluri: de aproximativ 2 ori mai ingust decat pe blocuri saptamanale). (None, None) cu
    mai putin de 2 blocuri."""
    import random as _r
    if block_s is None:
        block_s = block_size([t for t, _ in rows])
    by = {}
    for t, v in rows:
        b = by.setdefault(int(t // block_s), [0.0, 0])
        b[0] += v
        b[1] += 1
    blocks = list(by.values())
    k = len(blocks)
    if k < 2:
        return None, None
    rnd = _r.Random(seed)
    means = []
    for _ in range(n_boot):
        tot = cnt = 0
        for _i in range(k):
            b = blocks[rnd.randrange(k)]
            tot += b[0]
            cnt += b[1]
        means.append(tot / cnt)
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def block_diff_ci(rows_a, rows_b, block_s=None, n_boot=1000, seed=13):
    """IC95 al DIFERENTEI de medii mean(b) - mean(a), prin bootstrap pe aceleasi BLOCURI de timp
    (un bloc tras contribuie cu planurile lui din ambele grupuri, deci corelatia din aceeasi
    saptamana se pastreaza si intre grupuri). `rows_*` = [(ts, valoare)]. (None, None) cu mai
    putin de 2 blocuri."""
    import random as _r
    if block_s is None:
        block_s = block_size([t for t, _ in rows_a] + [t for t, _ in rows_b])
    by = {}
    for g, rows in ((0, rows_a), (1, rows_b)):
        for t, v in rows:
            b = by.setdefault(int(t // block_s), [0.0, 0, 0.0, 0])
            b[2 * g] += v
            b[2 * g + 1] += 1
    blocks = list(by.values())
    k = len(blocks)
    if k < 2:
        return None, None
    rnd = _r.Random(seed)
    diffs = []
    for _ in range(n_boot):
        sa = na = sb = nb = 0
        for _i in range(k):
            b = blocks[rnd.randrange(k)]
            sa, na, sb, nb = sa + b[0], na + b[1], sb + b[2], nb + b[3]
        if na and nb:
            diffs.append(sb / nb - sa / na)
    if len(diffs) < n_boot // 2:
        return None, None
    diffs.sort()
    m = len(diffs)
    return diffs[int(0.025 * m)], diffs[int(0.975 * m) - 1]


def cluster_mean_se(rows, block_s=None):
    """(medie, eroare standard) cu varianta ROBUSTA LA GRUPARE pe blocuri de timp - varianta
    analitica (fara bootstrap) a lui block_mean_ci, pentru locurile unde se testeaza sute de
    ipoteze (core.research) si bootstrap-ul ar fi prea lent. `rows` = [(ts, valoare)].
    Planurile aceluiasi bloc se misca impreuna; eroarea standard a planurilor independente
    (sd/sqrt(n)) le numara ca observatii separate si da un interval prea ingust.
    Cu mai putin de 2 blocuri: (medie, inf)."""
    n = len(rows)
    if not n:
        return 0.0, float("inf")
    if block_s is None:
        block_s = block_size([t for t, _ in rows])
    m = sum(v for _, v in rows) / n
    res = {}
    for t, v in rows:
        b = int(t // block_s)
        res[b] = res.get(b, 0.0) + (v - m)
    g = len(res)
    if g < 2:
        return m, float("inf")
    var = sum(e * e for e in res.values()) / (n * n) * g / (g - 1)
    return m, math.sqrt(var) if var > 0 else 1e-9


def cluster_diff_se(rows_a, rows_b, block_s=None):
    """(mean(b) - mean(a), eroare standard robusta la grupare) - aceleasi blocuri de timp pentru
    ambele grupuri, deci si corelatia dintre grupuri in aceeasi saptamana intra in varianta.
    Cu un grup gol sau sub 2 blocuri: (diferenta sau 0, inf)."""
    na, nb = len(rows_a), len(rows_b)
    if not na or not nb:
        return 0.0, float("inf")
    if block_s is None:
        block_s = block_size([t for t, _ in rows_a] + [t for t, _ in rows_b])
    ma = sum(v for _, v in rows_a) / na
    mb = sum(v for _, v in rows_b) / nb
    res = {}
    for rows, m, n, sign in ((rows_a, ma, na, -1.0), (rows_b, mb, nb, 1.0)):
        for t, v in rows:
            b = int(t // block_s)
            res[b] = res.get(b, 0.0) + sign * (v - m) / n
    g = len(res)
    if g < 2:
        return mb - ma, float("inf")
    var = sum(e * e for e in res.values()) * g / (g - 1)
    return mb - ma, math.sqrt(var) if var > 0 else 1e-9


def bucket_r_ci(closed, bucket_size=20):
    """IC95 al R-ului mediu pe interval de scor, prin bootstrap pe blocuri de timp (block_mean_ci;
    saptamani cand datele acopera o jumatate de an). `closed` = planurile inchise ale familiei
    curente, fara NO_ENTRY. {"40": (lo, hi), ...}. Folosit de briefing si de auto-audit (poarta de
    calibrare) - aceeasi masuratoare in ambele locuri."""
    rows = {}
    for p in closed:
        sc = p.get("score_at_entry")
        t = p.get("created_ts") or p.get("closed_ts")
        if sc is None or p.get("realized_r") is None or t is None:
            continue
        rows.setdefault(str(int(sc // bucket_size) * bucket_size), []).append((t, p["realized_r"]))
    bs = block_size([t for r in rows.values() for t, _ in r])
    out = {}
    for b, r in rows.items():
        lo, hi = block_mean_ci(r, block_s=bs, n_boot=1000)
        if lo is not None:
            out[b] = (round(lo, 3), round(hi, 3))
    return out


def _split_stats(closed):
    """Statistici pentru un subset de planuri inchise, cu incertitudinea lor:
    rata de castig cu interval Wilson, R mediu cu IC95 prin bootstrap pe blocuri de timp
    (block_mean_ci): planurile din aceeasi zi sunt corelate (aceeasi miscare de piata), iar
    eroarea standard obisnuita dadea intervale prea INGUSTE."""
    n = len(closed)
    if not n:
        return {"closed": 0}
    rs = [p["realized_r"] for p in closed]
    wins = sum(1 for r in rs if r > 0)
    lo, hi = wilson_interval(wins, n)
    m = sum(rs) / n
    c_lo, c_hi = block_mean_ci([(_ts(p), p["realized_r"]) for p in closed]) if n > 1 else (None, None)
    first = min((p.get("created_ts") or 0 for p in closed), default=0)
    return {
        "closed": n,
        "win_rate": round(100 * wins / n, 1),
        "wr_ci_low": round(100 * lo, 1), "wr_ci_high": round(100 * hi, 1),
        "avg_r": round(m, 3),
        "avg_r_ci_low": round(c_lo, 3) if c_lo is not None else None,
        "avg_r_ci_high": round(c_hi, 3) if c_hi is not None else None,
        "total_r": round(sum(rs), 2),
        "since": time.strftime("%Y-%m-%d", time.gmtime(first)) if first else None,
    }


DIVERGENCE_MIN_LIVE = 30


def _ts(p):
    v = p.get("created_ts") or 0
    return v / 1000 if v > 1e11 else v


def _boot_diff(lr, br, n_boot, seed=11):
    """Diferenta R mediu live - backtest cu IC95 bootstrap pe ZILE (fiecare parte resamplata pe
    blocuri de cate o zi: planurile aceleiasi zile sunt corelate). `lr`, `br` = [(ts, R)]."""
    import random as _r
    rnd = _r.Random(seed)

    def blocks(rows):
        by = {}
        for t, v in rows:
            b = by.setdefault(int(t // DAY_S), [0.0, 0])
            b[0] += v
            b[1] += 1
        return list(by.values())

    def draw(bl):
        tot = cnt = 0
        for _i in range(len(bl)):
            b = bl[rnd.randrange(len(bl))]
            tot += b[0]
            cnt += b[1]
        return tot / cnt
    def m(rows):
        return sum(r for _t, r in rows) / len(rows)
    lb, bb = blocks(lr), blocks(br[-5000:])
    diffs = sorted(draw(lb) - draw(bb) for _ in range(n_boot))
    lo, hi = diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot) - 1]
    status = "sub_backtest" if hi < 0 else ("peste_backtest" if lo > 0 else "in_marja")
    return {"diff": round(m(lr) - m(br), 3), "ci_low": round(lo, 3), "ci_high": round(hi, 3), "status": status,
            "live_r": round(m(lr), 3), "bt_r": round(m(br), 3)}


DIVERGENCE_MIN_WINDOW_BT = 30


def divergence(live, bt, n_boot=2000):
    """ALARMA DE DIVERGENTA: diferenta de R mediu live - backtest, cu interval de
    incredere bootstrap 95% pe DIFERENTA. Semnificativa cand intervalul exclude zero.

    COMPARATIA CORECTA E PE ACEEASI PERIOADA. Prima versiune compara planurile live
    (cateva saptamani) cu media backtest-ului pe 2018-azi - si raporta "live
    semnificativ sub backtest". Masurat pe datele din 6 oct 2026: live -0.207R/plan
    (n=92) fata de +0.087R pe tot istoricul, dar backtest-ul pe ACELEASI saptamani da
    -0.045R (n=81), in marja; short-urile au pierdut in ambele (12 din 12 live, 8 din 8
    in backtest). Diferenta venea din piata, nu din implementare - iar edge-ul
    backtest-ului s-a erodat in timp (vezi edge_by_period).

    Acum alarma decide pe fereastra comuna: planurile live create cat timp exista
    si backtest, fata de planurile de backtest din aceleasi zile. Comparatia cu tot
    istoricul ramane informativa (vs_all). Daca fereastra comuna e prea mica,
    comparatia cade pe tot istoricul, marcata ca atare (basis)."""
    lr = [(_ts(p), p["realized_r"]) for p in sorted(live, key=_ts)]
    br = [(_ts(p), p["realized_r"]) for p in sorted(bt, key=_ts)]
    if len(lr) < DIVERGENCE_MIN_LIVE or len(br) < 300:
        return {"n_live": len(lr), "status": "date_insuficiente"}
    vs_all = _boot_diff(lr, br, n_boot)
    lo_t = min(_ts(p) for p in live)
    hi_t = min(max(_ts(p) for p in live), max(_ts(p) for p in bt))
    lw = [(t, r) for t, r in lr if t <= hi_t]
    bw = [(t, r) for t, r in br if lo_t <= t <= hi_t]
    out = {"n_live": len(lr), "vs_all": vs_all, "n_live_window": len(lw), "n_bt_window": len(bw),
           "window": [time.strftime("%Y-%m-%d", time.gmtime(lo_t)), time.strftime("%Y-%m-%d", time.gmtime(hi_t))]}
    if len(lw) >= DIVERGENCE_MIN_LIVE and len(bw) >= DIVERGENCE_MIN_WINDOW_BT:
        out.update(_boot_diff(lw, bw, n_boot))
        out["basis"] = "aceeasi_perioada"
    else:
        out.update(vs_all)
        out["basis"] = "tot_istoricul"
    return out


EDGE_RECENT_DAYS = 365


def edge_by_period(bt):
    """EDGE-UL IN TIMP, masurat pe backtest: R mediu pe plan pe fiecare an si pe
    ultimele 365 de zile, cu interval de incredere 95% pe blocuri saptamanale.

    Raspunde la intrebarea pe care media pe tot istoricul o ascunde: strategia mai
    castiga ACUM? Masurat la introducere (6 oct 2026): 2018-2024 intre +0.09 si
    +0.13R/plan, 2025 +0.035R, 2026 +0.012R - edge-ul s-a erodat, iar pe ultimele
    luni nu mai e distinct de zero. Starea "neconcludent" inseamna exact asta."""
    rows = [(_ts(p), p["realized_r"]) for p in bt if isinstance(p.get("realized_r"), (int, float))]
    if len(rows) < 100:
        return {"status": "date_insuficiente"}

    def stat(vals):
        # IC95 pe blocuri saptamanale (planurile aceleiasi saptamani sunt corelate); cu sub 2 saptamani,
        # pe zile
        n = len(vals)
        mean = sum(v for _, v in vals) / n
        lo, hi = block_mean_ci(vals, WEEK_S)
        if lo is None:
            lo, hi = block_mean_ci(vals, DAY_S)
        return {"n": n, "r": round(mean, 3), "ci_low": round(lo if lo is not None else mean, 3),
                "ci_high": round(hi if hi is not None else mean, 3)}

    years = {}
    for t, r in rows:
        years.setdefault(time.strftime("%Y", time.gmtime(t)), []).append((t, r))
    last = max(t for t, _ in rows)
    recent = [(t, r) for t, r in rows if t >= last - EDGE_RECENT_DAYS * 86400]
    rec = stat(recent) if len(recent) >= 30 else None
    status = ("date_insuficiente" if rec is None else "pozitiv" if rec["ci_low"] > 0
              else "negativ" if rec["ci_high"] < 0 else "neconcludent")
    return {"by_year": {y: stat(v) for y, v in sorted(years.items()) if len(v) >= 30},
            "recent": rec, "recent_days": EDGE_RECENT_DAYS,
            "until": time.strftime("%Y-%m-%d", time.gmtime(last)), "status": status}


UNIVERSE_MIN_QV = 200000.0     # lichiditate minima la creare: mediana volumului pe bara, USDT (30 de bare)


def universe_summary(plans, watch=(), min_qv=UNIVERSE_MIN_QV):
    """Strategia curenta pe TOT universul bursei (replay-ul rapid din backtest.replay_plans, pe
    lumanarile din depozitul de memorie): R mediu pe plan cu IC95 pe blocuri saptamanale, pe
    simbolurile lichide (fara bare goale la creare), separat lista urmarita si restul, pe directii si
    pe ani. Raspunde la intrebarea pe care 30 de simboluri nu o pot transa: are strategia un
    avantaj in piata, nu doar pe lista din care invata agentul?"""
    entered = [p for p in plans if isinstance(p.get("realized_r"), (int, float)) and p.get("state") != STATE_NO_ENTRY]
    liq = [p for p in entered if (p.get("qv30") or 0) >= min_qv and not p.get("zero20")]

    def st(ps):
        if len(ps) < 30:
            return {"n": len(ps)}
        lo, hi = block_mean_ci([(_ts(p), p["realized_r"]) for p in ps], WEEK_S)
        m = sum(p["realized_r"] for p in ps) / len(ps)
        return {"n": len(ps), "r": round(m, 4), "ci_low": round(lo, 4) if lo is not None else None,
                "ci_high": round(hi, 4) if hi is not None else None,
                "win": round(100.0 * sum(1 for p in ps if p["realized_r"] > 0) / len(ps), 1)}
    watch = set(watch or ())
    return {"symbols": len({p.get("symbol") for p in plans}), "symbols_liquid": len({p.get("symbol") for p in liq}),
            "plans": len(plans), "entered": len(entered), "min_qv": min_qv,
            "all": st(entered), "liquid": st(liq),
            "watchlist": st([p for p in liq if p.get("symbol") in watch]),
            "rest": st([p for p in liq if p.get("symbol") not in watch]),
            "long": st([p for p in liq if p.get("direction") == "LONG"]),
            "short": st([p for p in liq if p.get("direction") == "SHORT"]),
            "edge": edge_by_period(liq)}


def _cal_slim(e):
    return {k: e[k] for k in ("total", "win_rate", "ci_low", "ci_high", "avg_r", "reliable")}


def calibration_by_period(store, days=EDGE_RECENT_DAYS, bucket_size=20):
    """CALIBRAREA PE ULTIMELE `days` ZILE, langa cea pe tot istoricul.

    Probabilitatea afisata pentru un semnal e rata de castig a intervalului lui de
    scor pe TOT istoricul (backtest 2018-azi + live). Edge-ul s-a erodat
    (edge_by_period), iar media pe 8 ani ascunde asta. Masurat pe data/plans.json la
    8 oct 2026, intervalul 60-79: tot istoricul 42.5%; ultimele 365 de zile 38.0%
    (IC 34.9-41.3%, n=865, R mediu -0.049R) fata de perioada ANTERIOARA ferestrei
    43.5% (IC 42.0-45.1%, n=3906, +0.135R) - intervalele nu se suprapun.

    Aceleasi reguli ca build_calibration (aceeasi functie, aplicata pe doua
    subseturi): planuri inchise, fara NO_ENTRY, familia de geometrie curenta.
    Fereastra se termina la cel mai recent plan inchis (ca edge_by_period).
    Comparatia e cu perioada ANTERIOARA ferestrei (esantioane disjuncte), nu cu
    totalul care o contine. Verdictul `vs_prior` e "sub"/"peste" doar cand
    intervalele Wilson 95% nu se suprapun - test conservator, potrivit pentru 5
    intervale comparate deodata.

    Doar pentru afisare: decide() ramane pe tot istoricul. O calibrare ponderata
    spre perioada recenta a fost RESPINSA de validarea walk-forward (Brier mai slab
    decat pe tot istoricul), deci nu inlocuieste probabilitatea folosita la decizie."""
    closed = [p for p in store.get("plans") or []
              if p.get("state") in CLOSED_STATES and p.get("state") != STATE_NO_ENTRY
              and p.get("realized_r") is not None and p.get("score_at_entry") is not None
              and same_family(p.get("geometry", "v1"))]
    if not closed:
        return {"status": "date_insuficiente"}
    last = max(_ts(p) for p in closed)
    cut = last - days * 86400
    recent = build_calibration({"plans": [p for p in closed if _ts(p) >= cut]}, bucket_size)
    prior = build_calibration({"plans": [p for p in closed if _ts(p) < cut]}, bucket_size)
    buckets = {}
    for b in sorted(recent, key=int):
        e, q = _cal_slim(recent[b]), prior.get(b)
        e["prior"] = _cal_slim(q) if q else None
        e["vs_prior"] = None
        if q and e["reliable"] and q["reliable"]:
            e["vs_prior"] = ("sub" if e["ci_high"] < q["ci_low"] else
                             "peste" if e["ci_low"] > q["ci_high"] else "in_marja")
        buckets[b] = e
    return {"days": days, "since": time.strftime("%Y-%m-%d", time.gmtime(cut)),
            "until": time.strftime("%Y-%m-%d", time.gmtime(last)), "buckets": buckets}


def summarize(store):
    plans = store["plans"]
    all_closed = [p for p in plans if p["state"] in CLOSED_STATES and p.get("realized_r") is not None]
    current_geo = [p for p in all_closed if same_family(p.get("geometry", "v1"))]
    no_entry = [p for p in current_geo if p.get("state") == STATE_NO_ENTRY]
    closed = [p for p in current_geo if p.get("state") != STATE_NO_ENTRY]
    # Planurile legacy nu mai sunt in `plans` dupa archive_stale_plans - au fost
    # mutate pe disc, in fisiere separate per geometrie. `legacy` de aici prinde
    # doar ce a ramas NEARHIVAT inca in acest apel (rar: chiar planurile pe care
    # save_plans le arhiveaza data viitoare). Restul vine din indexul de arhiva.
    legacy = [p for p in all_closed if not same_family(p.get("geometry", "v1"))]
    open_plans = [p for p in plans if p["state"] not in CLOSED_STATES]

    archived_idx = ARCHIVE_INDEX_SOURCE() or {}
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
        # SEPARAT LIVE vs BACKTEST: cifrele de mai jos (total_r, win_rate, avg_r)
        # amesteca ambele surse - in productie >99% sunt din backtest, deci descriu
        # backtest-ul, nu ce a facut sistemul live. Dashboard-ul si briefing-ul
        # le prezentau ca rezultatul sistemului, desi live era negativ.
        "live": _split_stats([p for p in closed if p.get("source") != "backtest"]),
        "backtest": _split_stats([p for p in closed if p.get("source") == "backtest"]),
        "divergence": divergence([p for p in closed if p.get("source") != "backtest"],
                                 [p for p in closed if p.get("source") == "backtest"]),
        "edge": edge_by_period([p for p in closed if p.get("source") == "backtest"]),
        "calibration_recent": calibration_by_period(store),
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
