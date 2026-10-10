# -*- coding: utf-8 -*-
"""ports.memory - portul depozitului de memorie (seturi de date partitionate, vezi core/memory.py).

Un set de date are partitii (fisiere Parquet, numite dupa core.memory.*_partition) si un index
{nume partitie: {rows, hash, bytes, min_ts, max_ts, ...}}. Implementari: adapters/memory/local_store.py
(un dosar) si adapters/memory/release_store.py (release-uri GitHub).
"""

from typing import Protocol


class MemoryStore(Protocol):
    def index(self, dataset):
        """Indexul setului de date ({} daca nu exista inca)."""

    def save_index(self, dataset, index):
        """Scrie indexul (dupa partitii, ca un index sa nu arate niciodata o partitie inexistenta)."""

    def read(self, dataset, name):
        """Randurile unei partitii (lista de dict-uri; [] daca lipseste)."""

    def write(self, dataset, name, rows, schema):
        """Scrie (inlocuieste) o partitie; intoarce marimea in octeti."""

    def put(self, dataset, name, path):
        """Publica un fisier Parquet deja scris; intoarce marimea."""

    def cache_path(self, dataset, name):
        """O cale de lucru locala pentru a construi o partitie inainte de publicare."""

    def local_path(self, dataset, name):
        """Calea locala a partitiei (descarcata la nevoie), pentru interogari DuckDB; None daca lipseste."""

    def remote_url(self, dataset, name):
        """URL-ul public al partitiei (pentru interogari HTTP cu cereri partiale), sau None."""

    def names(self, dataset):
        """Partitiile care exista efectiv (pentru a re-urca ce lipseste, chiar daca indexul o listeaza)."""
