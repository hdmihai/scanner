"""selfrepair - reparare automata de cod pentru erorile de logica gasite de self_check.py.

Fluxul: self_check.py (la fiecare scanare) gaseste o EROARE de logica -> selfrepair.repair
cere unui model gratuit (Gemini, apoi Ollama local) o corectura a functiilor implicate ->
poarta de validare (fisiere permise, sintaxa 3.11, check_integrity, patch mic) -> Pull
Request. Merge-ul ramane al tau; scanarea urmatoare confirma pe date reale ca eroarea
a disparut (self_check o raporteaza din nou daca nu).
"""
