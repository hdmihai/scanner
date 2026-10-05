# -*- coding: utf-8 -*-
"""adapters.compat - fatadele de compatibilitate dintre modulele vechi si nucleu.

Arhitectura hexagonala muta logica in core/, dar workflow-urile, backtest-ul,
auto-diagnosticul si dashboard-ul folosesc in continuare `import plan_tracker`,
`import ai_agent`, `import crypto_ai_scanner`. O fatada pastreaza acele nume
valide FARA a copia starea nucleului:

  - citirea unui nume care nu e definit in fatada se face, la momentul citirii,
    din modulul nucleului (stare vie - ex. GEOMETRY_VERSION dupa set_capabilities);
  - atribuirea unui nume care traieste in nucleu se face IN nucleu (backtest-ul
    seteaza plan_tracker.TP1_FRACTION si crypto_ai_scanner.PULLBACK_ATR pentru
    variante - fara asta, setarea ar fi ramas in fatada, ignorata in tacere).

Numele definite in fatada (adaptoarele de fisiere: PLANS_FILE, load_plans, ...)
raman ale ei; auto-verificarea le poate redirectiona catre un dosar temporar.
"""

import sys
import types


def bind(module_name, *cores):
    """Transforma modulul `module_name` in fatada peste modulele `cores` (in ordine)."""
    mod = sys.modules[module_name]

    class _Facade(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__") and name.endswith("__"):
                # numele speciale (__path__, __class__, ...) nu se imprumuta din nucleu
                raise AttributeError(name)
            for c in cores:
                if hasattr(c, name):
                    return getattr(c, name)
            raise AttributeError(f"module '{module_name}' has no attribute '{name}'")

        def __setattr__(self, name, value):
            # numele speciale raman ale fatadei: importlib.reload re-executa modulul si
            # reatribuie __class__ - redirectionat in nucleu, ar fi transformat nucleul
            # insusi in fatada (recursivitate infinita)
            if not (name.startswith("__") and name.endswith("__")) and name not in self.__dict__:
                # in TOATE modulele nucleului care au numele: o constanta importata si
                # in alt modul (ex. RSI_LONG) ramane sincronizata peste tot
                owners = [c for c in cores if hasattr(c, name)]
                if owners:
                    for c in owners:
                        setattr(c, name, value)
                    return
            super().__setattr__(name, value)

        def __dir__(self):
            names = set(self.__dict__)
            for c in cores:
                names |= set(dir(c))
            return sorted(names)

    mod.__class__ = _Facade
    return mod
