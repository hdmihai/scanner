"""
core - nucleul (hexagonul): agentul AI si toata logica de decizie, fara I/O.

REGULA (garda: check_integrity.check_core_is_pure): nimic din core/ nu face
retea, nu citeste si nu scrie fisiere si nu importa adaptoare. Datele intra prin
porturi (ports/), iar adaptoarele (adapters/) le aduc de la burse, din fisiere
sau din Telegram. Asa logica agentului se poate testa si rula identic oriunde -
pe date reale, pe date istorice sau in testele offline.

Continut: indicatorii, evidentele, Elliott, structura pietei, lichidarile si
lichiditatea, scorarea si geometria planului, analiza per token, planurile si
calibrarea, agentul (modelul, caracteristicile, predictia) si cazul de utilizare
al scanarii.
"""
