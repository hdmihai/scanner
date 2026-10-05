# -*- coding: utf-8 -*-
"""adapters.storage.json_store - documentele ca fisiere JSON, cu scriere atomica (portul ports.store.DocumentStore)."""

import json
import os


class JsonStore:
    """Cheile sunt cai de fisiere relative la radacina proiectului (data/...)."""

    def load(self, path, default):
        if not os.path.exists(path):
            return default
        with open(path, "r") as f:
            return json.load(f)

    def save(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # SCRIERE ATOMICA: fisier temporar, golit pe disc, apoi inlocuire intr-un singur
        # pas. Un job oprit in timpul scrierii (timeout, anulare) lasa intact fisierul
        # vechi - pasul de commit din workflow ruleaza cu if: always() si ar fi urcat
        # un JSON trunchiat, oprind toate scanarile urmatoare.
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def save_compact(self, path, data):
        """Ca save (scriere atomica), dar fara indentare: datele modulelor per bursa se
        rescriu la fiecare scanare, iar forma compacta tine istoricul git mic."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
