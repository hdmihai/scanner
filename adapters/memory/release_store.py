# -*- coding: utf-8 -*-
"""adapters.memory.release_store - depozitul de memorie in release-urile GitHub ale repo-ului.

Gratuit si fara cheie noua: workflow-ul are deja GITHUB_TOKEN (permisiunea contents: write).
Limite GitHub (docs "About releases"): fiecare fisier < 2 GiB, cel mult 1000 de fisiere pe release,
fara limita de marime totala sau de trafic. Un set de date = un release (core.memory.DATASETS);
partitiile sunt anuale, deci un set de date are zeci de fisiere, nu mii.

Release-urile repo-ului public sunt publice: oricine le poate descarca, inclusiv DuckDB prin HTTP.
Memoria contine planuri, caracteristici si lumanari de piata - nimic secret (fara chei, fara solduri).

Scrierea unei partitii: Parquet local -> sterge fisierul vechi cu acelasi nume -> urca noul fisier.
Indexul (index.json) se urca ULTIMUL, deci nu arata niciodata o partitie care nu exista.
"""

import http.client
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from adapters.memory import parquet
from core import memory as M

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class ReleaseError(RuntimeError):
    pass


class ReleaseStore:
    def __init__(self, repo, token, cache_dir=None, api=None):
        self.repo, self.token = repo, token
        self.api = (api or API).rstrip("/")
        self.cache = cache_dir or os.path.join(tempfile.gettempdir(), "memory-cache")
        self._rel = {}

    # ------------------------------------------------------------ HTTP
    def _req(self, method, url, data=None, headers=None, raw=False, retries=3):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "scanner-memory"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        for k in range(retries):
            try:
                req = urllib.request.Request(url, data=data, headers=h, method=method)
                with urllib.request.urlopen(req, timeout=300) as resp:
                    body = resp.read()
                    return body if raw else (json.loads(body.decode()) if body else None)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (500, 502, 503, 504) and k < retries - 1:
                    time.sleep(2 ** k)
                    continue
                raise ReleaseError(f"{method} {url.split('?')[0]}: HTTP {e.code} {e.read()[:200]!r}")
            except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as e:
                if k < retries - 1:
                    time.sleep(2 ** k)
                    continue
                raise ReleaseError(f"{method} {url.split('?')[0]}: {e}")

    def _release(self, dataset, create=True):
        tag = M.DATASETS.get(dataset, dataset)
        if tag in self._rel:
            return self._rel[tag]
        rel = self._req("GET", f"{self.api}/repos/{self.repo}/releases/tags/{tag}")
        if rel is None and create:
            rel = self._req("POST", f"{self.api}/repos/{self.repo}/releases", data=json.dumps({
                "tag_name": tag, "name": f"Memoria agentului: {dataset}", "prerelease": True,
                "body": ("Depozitul de memorie al agentului (core/memory.py): partitii Parquet scrise automat de "
                         "memory_sync.py. Nu edita manual.")}).encode(),
                headers={"Content-Type": "application/json"})
        if rel is not None:
            self._rel[tag] = rel
        return rel

    def _assets(self, dataset):
        rel = self._release(dataset, create=False)
        if not rel:
            return {}
        out, page = {}, 1
        while True:
            got = self._req("GET", f"{self.api}/repos/{self.repo}/releases/{rel['id']}/assets?per_page=100&page={page}") or []
            for a in got:
                out[a["name"]] = a
            if len(got) < 100:
                return out
            page += 1

    def _download(self, asset, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        body = self._req("GET", asset["url"], headers={"Accept": "application/octet-stream"}, raw=True)
        with open(dest + ".part", "wb") as f:
            f.write(body or b"")
        os.replace(dest + ".part", dest)
        return dest

    def _upload(self, dataset, name, path, content_type):
        """Inlocuire fara fereastra de pierdere: urc intai sub un nume temporar, abia apoi sterg vechiul
        fisier si redenumesc noul. O rulare oprita la jumatate lasa cel mult un fisier .new in plus."""
        rel = self._release(dataset, create=True)
        assets = self._assets(dataset)
        tmp_name = name + ".new"
        if tmp_name in assets:
            self._req("DELETE", f"{self.api}/repos/{self.repo}/releases/assets/{assets[tmp_name]['id']}")
        up = rel["upload_url"].split("{")[0] + "?" + urllib.parse.urlencode({"name": tmp_name})
        with open(path, "rb") as f:
            data = f.read()
        new = self._req("POST", up, data=data, headers={"Content-Type": content_type})
        if not new or "id" not in new:
            raise ReleaseError(f"incarcarea {name} nu a intors un fisier")
        if name in assets:
            self._req("DELETE", f"{self.api}/repos/{self.repo}/releases/assets/{assets[name]['id']}")
        return self._req("PATCH", f"{self.api}/repos/{self.repo}/releases/assets/{new['id']}",
                         data=json.dumps({"name": name}).encode(), headers={"Content-Type": "application/json"})

    def names(self, dataset):
        return {n for n in self._assets(dataset) if n.endswith(".parquet")}

    # ------------------------------------------------------------ portul MemoryStore
    def index(self, dataset):
        a = self._assets(dataset).get("index.json")
        if not a:
            return {}
        p = self._download(a, os.path.join(self.cache, M.DATASETS.get(dataset, dataset), "index.json"))
        with open(p) as f:
            return json.load(f)

    def save_index(self, dataset, index):
        d = os.path.join(self.cache, M.DATASETS.get(dataset, dataset))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "index.json")
        with open(p, "w") as f:
            json.dump(index, f, indent=1, sort_keys=True)
        self._upload(dataset, "index.json", p, "application/json")

    def local_path(self, dataset, name):
        p = os.path.join(self.cache, M.DATASETS.get(dataset, dataset), name)
        if os.path.exists(p):
            return p
        a = self._assets(dataset).get(name)
        return self._download(a, p) if a else None

    def read(self, dataset, name):
        return parquet.read(self.local_path(dataset, name))

    def write(self, dataset, name, rows, schema):
        p = os.path.join(self.cache, M.DATASETS.get(dataset, dataset), name)
        n = parquet.write(p, rows, schema)
        self._upload(dataset, name, p, "application/octet-stream")
        return n

    def put(self, dataset, name, path):
        """Publica un fisier Parquet deja scris; copia locala devine cache-ul partitiei."""
        dest = os.path.join(self.cache, M.DATASETS.get(dataset, dataset), name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.abspath(path) != os.path.abspath(dest):
            import shutil
            shutil.copyfile(path, dest)
        self._upload(dataset, name, dest, "application/octet-stream")
        return os.path.getsize(dest)

    def cache_path(self, dataset, name):
        d = os.path.join(self.cache, M.DATASETS.get(dataset, dataset))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name + ".work")

    def remote_url(self, dataset, name):
        a = self._assets(dataset).get(name)
        return a.get("browser_download_url") if a else None
