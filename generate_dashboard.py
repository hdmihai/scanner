#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_dashboard.py - punctul de intrare, pastrat pentru scan.yml si metadata.yml.

Toata logica e in pachetul dashboard/ (vezi dashboard/__init__.py). Iesirea ramane
docs/index.html - acelasi URL ca pana acum.
"""

from dashboard.build import main
from dashboard.page import build_html  # noqa: F401  (compatibilitate)

if __name__ == "__main__":
    main()
