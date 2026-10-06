"""
tests - mediul de test offline al proiectului: bursele simulate, rularea determinista
si poarta de echivalenta folosita la fiecare actualizare (apply_update.yml).

  python -m tests.equivalence --baseline <arbore vechi> --candidate <arbore nou>
      ruleaza scanarea si pasii de dupa ea pe ambele versiuni, cu aceleasi date si
      acelasi timp inghetat, si compara tot ce scriu in data/ si docs/;
  python -m tests.guards
      strica intentionat codul in copii temporare si verifica ca gărzile prind fiecare caz.

Nimic de aici nu face retea: ccxt si requests sunt inlocuite cu tests/fakes/.
"""
