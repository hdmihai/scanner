# -*- coding: utf-8 -*-
"""indicators.py - compatibilitate: modulul traieste acum in nucleu, core/indicators.py.

Arhitectura hexagonala (Etapa 2): logica pura sta in core/, fara retea si fara
fisiere. Acest fisier pastreaza `import indicators` valid pentru restul proiectului -
inlocuieste modulul cu cel din nucleu, deci orice citire SAU atribuire ajunge
in acelasi obiect (nu exista doua copii ale starii).
"""

import sys

from core import indicators as _core_module

sys.modules[__name__] = _core_module
