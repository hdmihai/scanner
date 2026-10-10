# -*- coding: utf-8 -*-
"""adapters.memory.local_store - depozitul de memorie intr-un dosar (portul ports.memory.MemoryStore).

Folosit de teste si ca rezerva cand release-urile nu sunt disponibile. Structura:
<radacina>/<tag set de date>/<partitie>.parquet si <radacina>/<tag>/index.json."""

import json
import os

from adapters.memory import parquet
from core import memory as M


class LocalStore:
    def __init__(self, root):
        self.root = root

    def _dir(self, dataset):
        d = os.path.join(self.root, M.DATASETS.get(dataset, dataset))
        os.makedirs(d, exist_ok=True)
        return d

    def index(self, dataset):
        p = os.path.join(self._dir(dataset), "index.json")
        if not os.path.exists(p):
            return {}
        with open(p) as f:
            return json.load(f)

    def save_index(self, dataset, index):
        p = os.path.join(self._dir(dataset), "index.json")
        with open(p + ".tmp", "w") as f:
            json.dump(index, f, indent=1, sort_keys=True)
        os.replace(p + ".tmp", p)

    def read(self, dataset, name):
        return parquet.read(self.local_path(dataset, name))

    def write(self, dataset, name, rows, schema):
        return parquet.write(os.path.join(self._dir(dataset), name), rows, schema)

    def put(self, dataset, name, path):
        """Publica un fisier Parquet deja scris (ex. rezultatul unei uniri DuckDB)."""
        dest = os.path.join(self._dir(dataset), name)
        if os.path.abspath(path) != os.path.abspath(dest):
            import shutil
            shutil.copyfile(path, dest)
        return os.path.getsize(dest)

    def cache_path(self, dataset, name):
        return os.path.join(self._dir(dataset), name + ".work")

    def local_path(self, dataset, name):
        p = os.path.join(self._dir(dataset), name)
        return p if os.path.exists(p) else None

    def remote_url(self, dataset, name):
        return None

    def names(self, dataset):
        return {n for n in os.listdir(self._dir(dataset)) if n.endswith(".parquet")}
