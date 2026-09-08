#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_py311.py
===============
Verifica daca sursele sunt valide pe Python 3.11 - versiunea folosita de
workflow - chiar daca sunt scrise/testate pe 3.12.

DE CE EXISTA
------------
PEP 701 (Python 3.12) a permis ghilimele identice imbricate in f-string-uri:

    f"{d["cheie"]}"          <- valid din 3.12, EROARE DE SINTAXA pe 3.11

Un fisier scris pe 3.12 trece `python -m py_compile` local si pica in CI cu
"SyntaxError: f-string: unmatched '['". S-a intamplat exact asa: generate_dashboard.py
compila local si oprea workflow-ul la fiecare rulare.

`ast.parse(..., feature_version=(3,11))` NU prinde asta - restrictia e la nivel de
tokenizer, nu de gramatica. De aceea verificarea se face pe fluxul de tokeni.

CUM FUNCTIONEAZA
----------------
Tokenizer-ul din 3.12 emite FSTRING_START / FSTRING_MIDDLE / FSTRING_END. Cat timp
suntem intr-un f-string, orice token STRING care incepe cu ACELASI caracter de
ghilimea ca delimitatorul f-string-ului ar fi rupt literalul pe 3.11.

Se raporteaza si backslash-urile din interiorul expresiilor {...}, interzise si ele
inainte de 3.12.

RULARE
------
    python3 check_py311.py                # toate fisierele .py din folderul curent
    python3 check_py311.py fisier.py ...  # fisiere anume

Cod de iesire 1 daca gaseste probleme, ca sa poata fi folosit intr-un workflow.
"""

import io
import sys
import tokenize
from pathlib import Path


def check_source(path):
    """Returneaza lista de (linie, mesaj) cu tipare care pica pe Python 3.11."""
    problems = []
    try:
        src = Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        return [(0, f"nu am putut citi fisierul: {exc}")]

    if not hasattr(tokenize, "FSTRING_START"):
        # Rulam pe Python < 3.12: tokenizer-ul vede f-string-ul ca un singur
        # token STRING, deci daca sursa se tokenizeaza, e deja compatibila.
        try:
            list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, SyntaxError) as exc:
            problems.append((getattr(exc, "lineno", 0), f"tokenizare esuata: {exc}"))
        return problems

    fstring_stack = []   # caracterele de ghilimea ale f-string-urilor deschise
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.FSTRING_START:
                # tok.string arata ca f" / rf' / F""" etc. Ultimul caracter e
                # delimitatorul; pentru triple, tot acelasi caracter.
                quote = tok.string[-1]
                fstring_stack.append(quote)
            elif tok.type == tokenize.FSTRING_END:
                if fstring_stack:
                    fstring_stack.pop()
            elif fstring_stack and tok.type == tokenize.STRING:
                inner = tok.string.lstrip("rRbBuUfF")
                if inner and inner[0] == fstring_stack[-1]:
                    problems.append((
                        tok.start[0],
                        f"ghilimea {fstring_stack[-1]!r} reutilizata in expresia unui "
                        f"f-string delimitat cu acelasi caracter - valid doar din "
                        f"Python 3.12 (PEP 701). Foloseste .format() sau ghilimele diferite."))
            elif fstring_stack and "\\" in tok.string and tok.type == tokenize.OP:
                problems.append((tok.start[0],
                                 "backslash in expresia unui f-string - interzis pe 3.11"))
    except (tokenize.TokenError, SyntaxError) as exc:
        problems.append((getattr(exc, "lineno", 0), f"tokenizare esuata: {exc}"))

    return problems


def main():
    targets = sys.argv[1:]
    if not targets:
        targets = sorted(str(p) for p in Path(".").glob("*.py"))
    if not targets:
        print("Niciun fisier de verificat.")
        return 0

    total = 0
    for path in targets:
        problems = check_source(path)
        if problems:
            total += len(problems)
            print(f"\n{path}:")
            for line, msg in problems:
                print(f"  linia {line}: {msg}")

    print()
    if total:
        print(f"INCOMPATIBIL cu Python 3.11: {total} problema(e) in {len(targets)} fisiere.")
        print("Workflow-ul ruleaza pe 3.11 - reparai inainte de a urca.")
        return 1

    print(f"OK: toate cele {len(targets)} fisiere sunt valide si pe Python 3.11.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
