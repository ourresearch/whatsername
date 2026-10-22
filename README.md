# whatsername

**Figure out what someone's name actually is.**

`whatsername` is a deterministic Python parser that splits a raw author-name
string into structured components — title, first, middle, last, suffix,
nickname — across Latin, Chinese, Japanese, Korean, Cyrillic, and Arabic /
Persian scripts. It's the name parser extracted from the
[OpenAlex](https://openalex.org) author entity resolution pipeline.

It is **deterministic and fast** (no network, no model weights), and every parse
comes with a **confidence score** so you can route the hard cases to a human or
an LLM.

```python
from whatsername import parse_name

parse_name("John Maynard Smith")
# {'first': 'john', 'middle': 'maynard', 'last': 'smith', 'confidence': 'medium', ...}
# (3 tokens, no surname prefix -> the boundary is a guess, hence 'medium')

parse_name("Smith, John M.")             # comma form -> surname is explicit
# {'first': 'john', 'middle': 'm.', 'last': 'smith', 'confidence': 'high', ...}

parse_name("John M. Harris Jr MD")       # post-nominal credentials removed
# {'first': 'john', 'middle': 'm.', 'last': 'harris', 'suffix': 'jr', ...}

parse_name("张伟")                        # Chinese: surname-first
# {'first': 'wei', 'last': 'zhang', 'confidence': 'medium', ...}
```

## Install

```bash
pip install whatsername
```

For better romanization of Japanese, Korean, and Cyrillic names, install the
optional extra (the parser still works without it, at lower confidence):

```bash
pip install "whatsername[cjk]"
```

## What you get back

`parse_name(s)` returns a dict. All string values are lowercase ASCII (diacritics
removed, apostrophes dropped, hyphens and initials' periods preserved), or `None`.

| key | example | notes |
|-----|---------|-------|
| `title` | `dr.` | recognized academic/professional titles |
| `first` | `john` | given name |
| `middle` | `maynard` | middle name(s) / patronymic |
| `last` | `smith` | family name (compound prefixes like `van der` kept) |
| `suffix` | `jr., phd` | generational + post-nominal credentials |
| `nickname` | `jack` | text found in `(...)` / `[...]` |
| `confidence` | `high` | `high` \| `medium` \| `low` — **threshold on this** |

**Confidence** is the whole point of routing:

- `high` — comma-delimited (`Last, First`), a clear compound surname prefix, or a
  simple 1–2 token Latin name.
- `medium` — 3+ token Latin with no prefix (surname boundary is a guess), or a
  CJK/Korean name resolved via the surname tables.
- `low` — Arabic/Persian (short vowels aren't written), ambiguous CJK, or
  Cyrillic transliteration. Good candidates to send to an LLM.

### The OpenAlex-internal form

`parse_human_name(s)` returns the exact 6-field form OpenAlex uses for author
matching: empty strings instead of `None`, surname particles stripped from
`last` (so "de Oliveira" and "Oliveira" match), and a
[`nameparser`](https://pypi.org/project/nameparser/) `HumanName` fallback for
low-confidence Latin names.

## Accuracy

Benchmarked against the public
[human-name-parser-gold-standard](https://github.com/ourresearch/human-name-parser-gold-standard)
(15,309 OpenAlex author names):

| metric | accuracy |
|--------|----------|
| **Full match** (all 6 fields exact) | **88.8%** |
| `last` (family name) | 90.6% |
| `last` (surname particles stripped, i.e. matching-relevant) | 91.4% |
| `first` | 94.3% |
| `middle` | 94.7% |
| `title` / `suffix` / `nickname` | ≥99.5% |

Run it yourself:

```bash
pip install pytest
pytest tests/test_benchmark.py -s
```

The largest error sources are inherent and hard: compound-surname boundaries,
name-order disambiguation in romanized CJK names, and unusual scripts. That's
what the `confidence` field is for.

> **About the benchmark.** The gold standard is **LLM-annotated** — each name was
> parsed by Claude Opus 4.6, not labeled by hand. It's a strong reference set but
> not infallible, especially on the same hard cases the parser struggles with
> (compound surnames, CJK name order, rare scripts). Treat the accuracy numbers
> as indicative, not gospel.

### Mixed "First Last" / "Last First" corpora

Without a comma, `parse_name` reads the first token of a two-token Latin name as
the given name. `name_order` changes that:

```python
parse_name("Priem Jason")                         # first='priem', last='jason'  (default)
parse_name("Priem Jason", name_order="last_first")# first='jason', last='priem'  (you know the order)
parse_name("Priem Jason", name_order="auto")      # first='jason', last='priem', confidence='medium'
parse_name("Priem J.",    name_order="auto")      # first='j.',    last='priem'
parse_name("PRIEM Jason", name_order="auto")      # first='jason', last='priem'
```

`"last_first"` is for sources whose convention you know; a leading compound
surname stays together ("van der Berg Jan"). `"auto"` is for corpora that mix both
orders. It flips a two-token name only on clear evidence:

- a trailing lone initial ("Priem J.") unless the word before it is given-name-like
  (South Indian "Dhivya S" style),
- an upper-cased leading surname ("PRIEM Jason"),
- a larger table of Mandarin surnames than the default rules use ("Duan Tianxiang"),
- otherwise, how much more given-name-like the second token is than the first,
  from per-token frequencies of given names and surnames (margin 3 nats).

Flipped parses and parses with no evidence behind them get `confidence="medium"`.
Comma forms, three or more tokens, and the existing Chinese and Korean rules are
not reinterpreted.

Measured on the gold standard's plain two-token names (7,700 that the default
already parses correctly), with those names written in different orders:

| names written as | default | `auto` |
|---|---|---|
| "First Last" (as they are) | 100% | 99.8% |
| half "Last First" | 60.8% | 80.7% |
| all "Last First" | 22.0% | 61.0% |

On the gold standard as it is (about 2% of two-token names are "Last First"),
two-token accuracy goes from 97.3% to 97.7%, so `auto` does not cost a
"First Last" corpus. The remaining false flips are names whose tokens are both
common given names and surnames ("Michel David"), and the South Indian family-initial
style. The margin is `_AUTO_MARGIN` in `parser.py`: 2.0 recovers 75.5% of all-swapped
names at 99.0% for correctly ordered ones.

### Inferring the order of a whole list

One name rarely carries enough evidence, but a list from one source usually shares a
convention. `infer_name_order(names)` sums signed per-name signals (leading initial,
trailing initial, CJK surname position, upper-cased leading surname, given-name versus
surname scores) over the plain two-token and "First M. Last" names, and reports
`"last_first"` only when the evidence exceeds a margin of 4; otherwise `"first_last"`.
`parse_names(names, name_order="auto")` applies it and then parses every name in that order.
On the gold standard's two-token names, lists written "First Last" stay at 100%, and
lists written "Last First" are parsed correctly about 94-95% of the time.

```python
from whatsername import infer_name_order, parse_names
infer_name_order(["Priem Jason", "Piwowar Heather", "Smith J."])   # 'last_first'
parse_names(["Priem Jason", "Smith J."])                            # parsed as Last First
```

### Structure rules that are tuned on the corpus

These are heuristics measured on the gold standard, not language rules, so you may
want to know they exist:

- *Double surnames.* In three- and four-token names a final pair that scores as two surnames
  ("Fernanda Araujo Baião") is kept together as the last name. For three tokens this needs
  accented letters (an Iberian cue) unless the token scores match the mined pattern.
- *Short pinyin surnames.* "LU Qing" reads surname-first when the second token is pinyin-
  segmentable and not a strong given name (`_PINYIN_GIVEN_GUARD`, 5.0); "Lu Anna" stays.
- *Glued names.* "GuoxingZHANG", "YuanWang" and "SungKunKim" are split at the case boundary;
  Western pairs ("LightMart") are left whole.
- *Initials and organisations.* Fused capitals ("NF Bevis"), trailing initials ("Lee A B"),
  courtesy titles with two or more words after them, and organisation vocabulary
  (museum, agency, faculty...) are handled before the person rules.

Not covered, by design: per-script work for CJK polyphones, Arabic and Persian name order,
and a larger surname table. Adding Wikidata family names (550,000 labels) improved mixed lists
by about 3 points but lowered correctly ordered names and grew the data file about 5x, so it is not used.

The evidence (`src/whatsername/data/name_evidence.json`) is built by
`scripts/build_name_evidence.py` from US Census 1990 first names and 2010
surnames (public domain), Wikidata given-name items (CC0) and Faker's person-name
lists (MIT). Names missing from all of them (many non-Western names) get no
evidence and keep the default order.

## Why is this GPL-licensed?

`whatsername` depends on [`Unidecode`](https://pypi.org/project/Unidecode/) for
transliteration (Chinese pinyin, Cyrillic, Arabic fallback), and Unidecode is
licensed under the **GPL**. A project that depends on a GPL library must itself
be GPL-compatible, so `whatsername` is released under the **GPL-3.0-or-later**.
If you need a permissively-licensed name parser, you'll want a different library
(or one that doesn't transliterate non-Latin scripts).

## Credits

Built by [OpenAlex](https://openalex.org) / OurResearch as part of the author
entity resolution (AER) work. The deterministic parser and its surname
gazetteers come from the OpenAlex data pipeline; the benchmark was generated with
Claude Opus 4.6. See the
[gold standard repo](https://github.com/ourresearch/human-name-parser-gold-standard)
for the labeling protocol and reproduction harness.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
