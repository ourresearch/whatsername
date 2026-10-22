"""Rebuild src/whatsername/data/name_evidence.json.

The file backs ``parse_name(..., name_order="auto")``. It maps lowercase ASCII
name tokens to a signed score: how much more a token looks like a *given name*
(positive) than like a *surname* (negative). The score is
``ln((g + EPS) / (s + EPS))`` where ``g`` and ``s`` are rough uses per million
people, rounded to the nearest 0.5 and stored as integers (``SCALE`` per unit).
Tokens with a score close to zero are left out, so an unknown token is neutral.

Sources (all freely redistributable):

* US Census 1990 first names, male and female: given-name frequency
  (US Government work, public domain).
* US Census 2010 surnames: surname frequency (public domain).
* Wikidata English labels of given-name items (CC0), fetched in batches and
  checked against Wikidata's own item count so a timeout cannot silently
  truncate the list.
* Faker's person-name lists for ~80 locales (MIT; ``pip install faker``).
  A token that Faker lists as both a given name and a surname is ignored.

It also stores ``pinyin_surnames``: the most common Mandarin surnames, minus any
that are mostly used as given names elsewhere. ``auto`` mode uses them to read
"Duan Tianxiang" as surname-first.

Run from the repository root (needs network access and ``faker``):

    PYTHONPATH=src python scripts/build_name_evidence.py [--cache DIR]
"""

import argparse
import csv
import gzip
import io
import json
import math
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

from whatsername.parser import _np_clean

OUT = Path(__file__).resolve().parent.parent / "src" / "whatsername" / "data" / "name_evidence.json"
UA = "whatsername-build/0.1 (https://github.com/ourresearch/whatsername)"

SCALE = 2             # stored integer = round(score * SCALE)
EPS = 2.0             # per million; keeps tokens with no evidence at 0
WIKIDATA_PPM = 20.0   # Wikidata lists names without frequencies
FAKER_PPM = 20.0
MIN_ABS_SCORE = 1.0   # drop tokens whose evidence is weaker than this
BATCH = 3000          # Wikidata's per-query overhead dominates, so go big

SPARQL = "https://query.wikidata.org/sparql"
# male given name, female given name, unisex given name, given name
GIVEN_NAME_CLASSES = ["Q12308941", "Q11879590", "Q3409032", "Q202444"]
CENSUS_2010 = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
CENSUS_1990 = "https://www2.census.gov/topics/genealogy/1990surnames/"

# Most common Mandarin surnames in pinyin. Dropped at build time when the
# evidence says the token is mostly a given name.
PINYIN_SURNAME_CANDIDATES = """
wang li zhang liu chen yang zhao huang zhou wu xu sun hu zhu gao lin he guo ma luo liang song zheng xie han tang
feng yu dong xiao cheng cao yuan deng fu shen zeng peng lv su lu jiang cai jia ding wei xue ye yan pan du dai xia
zhong tian ren fan fang shi yao tan liao zou xiong jin hao kong bai cui kang mao qiu qin gu hou shao meng long wan
duan lei qian yin yi chang niu wen qiao lai gong pang ge lan shang ruan min xing ji nie ou ouyang situ zhuge
shangguan murong guan kuang tong xi mo mu pei qi qu rao sha shan shu tao tu xin xiang yue zhan zhi zhuang zuo cen
chu di fei geng gan hong huo kuai lian lou nong pu rong sang tai wo xun you zang zha
""".split()


def _open(url, data=None, accept=None, timeout=240, attempts=5):
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if accept:
        headers["Accept"] = accept
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return gzip.decompress(body) if resp.headers.get("Content-Encoding") == "gzip" else body
        except OSError:  # timeouts, resets and HTTP errors (urllib.error.URLError is an OSError)
            if attempt == attempts:
                raise
            time.sleep(8 * attempt)


class Fetcher:
    """HTTP GET/POST with an optional on-disk cache so reruns do not hit the servers."""

    def __init__(self, cache):
        self.cache = Path(cache) if cache else None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, name, url, **kw):
        path = self.cache / name if self.cache else None
        if path and path.exists():
            return path.read_bytes()
        body = _open(url, **kw)
        if path:
            path.write_bytes(body)
        return body


def _sparql_rows(fetcher, name, query):
    data = urllib.parse.urlencode({"query": query}).encode()
    text = fetcher.get(name, SPARQL, data=data, accept="text/csv").decode("utf-8")
    rows = csv.reader(io.StringIO(text))
    next(rows, None)
    return [r for r in rows if r]


def wikidata_given_names(fetcher):
    """English labels of given-name items. Raises if the item list is incomplete."""
    names = set()
    for cls in GIVEN_NAME_CLASSES:
        expected = int(_sparql_rows(fetcher, f"wd-{cls}-count.csv",
                                    f"SELECT (COUNT(*) AS ?c) WHERE {{ ?i wdt:P31 wd:{cls} }}")[0][0])
        ids = [r[0].rsplit("/", 1)[-1]
               for r in _sparql_rows(fetcher, f"wd-{cls}-ids.csv", f"SELECT ?i WHERE {{ ?i wdt:P31 wd:{cls} }}")]
        if len(ids) != expected:
            raise RuntimeError(f"{cls}: got {len(ids)} of {expected} items; Wikidata truncated the result")
        for k in range(0, len(ids), BATCH):
            values = " ".join(f"wd:{i}" for i in ids[k:k + BATCH])
            query = (f'SELECT ?l WHERE {{ VALUES ?i {{ {values} }} '
                     f'?i rdfs:label ?l . FILTER(LANG(?l) = "en") }}')
            for row in _sparql_rows(fetcher, f"wd-{cls}-labels-b{BATCH}-{k:06d}.csv", query):
                token = _np_clean(row[0])
                if token and " " not in token and token.isascii():
                    names.add(token)
            time.sleep(0.5)
    return names


def census_surnames_2010(fetcher):
    """token -> uses per million."""
    archive = zipfile.ZipFile(io.BytesIO(fetcher.get("census-2010.zip", CENSUS_2010)))
    ppm = {}
    with archive.open("Names_2010Census.csv") as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
            try:
                ppm[row["name"].lower()] = float(row["prop100k"]) * 10
            except ValueError:  # suppressed cells
                continue
    return ppm


def census_first_names_1990(fetcher):
    """token -> uses per million, male and female combined."""
    ppm = {}
    for name in ("dist.male.first", "dist.female.first"):
        for line in fetcher.get(f"census-1990-{name}", CENSUS_1990 + name).decode("ascii").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                ppm[parts[0].lower()] = ppm.get(parts[0].lower(), 0.0) + float(parts[1]) * 10000  # percent -> ppm
    return ppm


def faker_names():
    """(given, surname) sets from Faker's locale providers, ambiguous tokens removed."""
    import importlib
    import pkgutil

    import faker.providers.person as person_pkg

    given_attrs = ("first_names", "first_names_male", "first_names_female", "first_names_unisex",
                   "first_romanized_names", "romanized_first_names")
    surname_attrs = ("last_names", "last_names_male", "last_names_female",
                     "last_romanized_names", "romanized_last_names")

    def tokens(provider, attrs):
        out = set()
        for attr in attrs:
            value = getattr(provider, attr, None)
            if isinstance(value, dict):
                value = value.keys()
            if isinstance(value, (list, tuple, set, frozenset, type({}.keys()))):
                out.update(v for v in value if isinstance(v, str))
        return out

    given, surname = set(), set()
    for mod in pkgutil.iter_modules(person_pkg.__path__):
        provider = importlib.import_module(f"faker.providers.person.{mod.name}").Provider
        given |= {t for t in map(_np_clean, tokens(provider, given_attrs)) if t}
        surname |= {t for t in map(_np_clean, tokens(provider, surname_attrs)) if t}
    ok = lambda t: " " not in t and t.isascii() and t.isalpha()  # noqa: E731
    given, surname = {t for t in given if ok(t)}, {t for t in surname if ok(t)}
    both = given & surname
    return given - both, surname - both


def compute(wikidata, surnames, first_names, faker_given, faker_surname):
    tokens = set(wikidata) | set(surnames) | set(first_names) | faker_given | faker_surname
    scores = {}
    for t in tokens:
        if not t.isalpha() or len(t) < 2:
            continue
        g = first_names.get(t, 0.0) + (WIKIDATA_PPM if t in wikidata else 0.0) + (FAKER_PPM if t in faker_given else 0.0)
        s = surnames.get(t, 0.0) + (FAKER_PPM if t in faker_surname else 0.0)
        scores[t] = math.log((g + EPS) / (s + EPS))
    return scores


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", help="directory for raw downloads, so reruns are offline")
    args = ap.parse_args()
    fetcher = Fetcher(args.cache)

    wikidata = wikidata_given_names(fetcher)
    surnames = census_surnames_2010(fetcher)
    first_names = census_first_names_1990(fetcher)
    fg, fs = faker_names()
    scores = compute(wikidata, surnames, first_names, fg, fs)

    levels = {}
    for token, score in scores.items():
        if abs(score) >= MIN_ABS_SCORE:
            levels.setdefault(round(score * SCALE), []).append(token)
    pinyin = sorted(t for t in PINYIN_SURNAME_CANDIDATES if scores.get(t, 0.0) <= 0.0)

    payload = {
        "built": date.today().isoformat(),
        "scale": SCALE,
        "levels": {str(k): " ".join(sorted(v)) for k, v in sorted(levels.items())},
        "pinyin_surnames": pinyin,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    n = sum(len(v) for v in levels.values())
    print(f"{OUT}: {n} scored tokens ({OUT.stat().st_size // 1024} KB), {len(pinyin)} pinyin surnames")


if __name__ == "__main__":
    main()
