# -*- coding: utf-8 -*-
"""ports.store - portul documentelor salvate (istoric, ponderi, detalii, module per bursa).

Cheile sunt caile documentelor relative la radacina proiectului (ex.
data/scan_history.json): pentru nucleu sunt doar identificatori; adaptorul
JSON le interpreteaza ca fisiere, cu scriere atomica.
"""

from typing import Protocol


class DocumentStore(Protocol):
    def load(self, key, default):
        """Documentul, sau `default` daca nu exista."""

    def save(self, key, data):
        """Scriere atomica, lizibila (indentata)."""

    def save_compact(self, key, data):
        """Scriere atomica, compacta - pentru documentele rescrise la fiecare scanare."""


class PlanStore(Protocol):
    def load_plans(self): ...

    def save_plans(self, store): ...


class AgentStore(Protocol):
    def load_agent(self):
        """(model, stare) - modelul agentului si starea lui de invatare."""
