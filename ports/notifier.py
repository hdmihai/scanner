# -*- coding: utf-8 -*-
"""ports.notifier - portul notificarilor: rezumatul scanarii trimis in afara sistemului."""

from typing import Protocol


class Notifier(Protocol):
    def __call__(self, token, chat_id, text):
        """Trimite textul. Un esec se raporteaza, nu opreste scanarea."""
