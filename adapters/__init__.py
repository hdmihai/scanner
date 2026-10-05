"""
adapters - adaptoarele proiectului (arhitectura hexagonala, in constructie).

Un adaptor leaga nucleul de lumea exterioara printr-un port. Etapa 1 muta aici
bursele: adapters/exchanges/ are cate un modul per bursa. Urmeaza stocarea,
prezentarea si notificarile, iar nucleul (agent, scorare, evidente, Elliott,
structura, planuri) ramane logica pura, fara retea si fara fisiere.
"""
