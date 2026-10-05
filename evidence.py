# -*- coding: utf-8 -*-
"""evidence.py - compatibilitate: modulul traieste acum in nucleu, core/evidence.py.

Arhitectura hexagonala (Etapa 2): logica pura sta in core/, fara retea si fara
fisiere. Acest fisier pastreaza `import evidence` valid pentru restul proiectului -
inlocuieste modulul cu cel din nucleu, deci orice citire SAU atribuire ajunge
in acelasi obiect (nu exista doua copii ale starii).
"""

import sys

from core import evidence as _core_module

sys.modules[__name__] = _core_module
