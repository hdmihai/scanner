# -*- coding: utf-8 -*-
"""liquidity_structure.py - compatibilitate: modulul traieste acum in nucleu, core/liquidity_structure.py.

Arhitectura hexagonala (Etapa 2): logica pura sta in core/, fara retea si fara
fisiere. Acest fisier pastreaza `import liquidity_structure` valid pentru restul proiectului -
inlocuieste modulul cu cel din nucleu, deci orice citire SAU atribuire ajunge
in acelasi obiect (nu exista doua copii ale starii).
"""

import sys

from core import liquidity_structure as _core_module

sys.modules[__name__] = _core_module
