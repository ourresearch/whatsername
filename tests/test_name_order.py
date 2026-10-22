"""name_order: reading corpora that mix "First Last" and "Last First"."""

import json
import os

import pytest

from whatsername import parse_name

GOLD = os.path.join(os.path.dirname(__file__), "data", "gold-standard.jsonl")


def _fl(name, **kw):
    r = parse_name(name, **kw)
    return r["first"], r["last"], r["confidence"]


# ── default and validation ──

def test_default_order_is_unchanged():
    # Documents the long-standing default: no comma means the first token is the given name.
    assert _fl("Priem Jason") == ("priem", "jason", "high")
    assert _fl("Jason Priem") == ("jason", "priem", "high")


def test_rejects_unknown_order():
    with pytest.raises(ValueError):
        parse_name("Jason Priem", name_order="surname_first")


# ── last_first: the caller knows ──

def test_last_first_swaps_two_tokens():
    assert _fl("Priem Jason", name_order="last_first") == ("jason", "priem", "high")


def test_last_first_keeps_middle_names_and_compound_surnames():
    r = parse_name("Priem Jason R.", name_order="last_first")
    assert (r["first"], r["middle"], r["last"]) == ("jason", "r.", "priem")
    r = parse_name("van der Berg Jan", name_order="last_first")
    assert (r["first"], r["last"]) == ("jan", "van der berg")


def test_last_first_leaves_comma_names_alone():
    assert parse_name("Priem, Jason", name_order="last_first") == parse_name("Priem, Jason")


# ── auto: lexicon evidence ──

def test_auto_reads_last_first():
    assert _fl("Priem Jason", name_order="auto") == ("jason", "priem", "medium")
    assert _fl("Kantola Mauri", name_order="auto")[:2] == ("mauri", "kantola")


def test_auto_keeps_first_last_and_is_confident_when_the_evidence_agrees():
    assert _fl("Jason Priem", name_order="auto") == ("jason", "priem", "high")
    assert _fl("Mauri Kantola", name_order="auto")[:2] == ("mauri", "kantola")


@pytest.mark.parametrize("name", ["Martin Thomas", "Zzyzx Qwfp"])
def test_auto_does_not_guess_without_evidence(name):
    # Both tokens are ambiguous, or neither is known: keep the order, say so.
    first, last, conf = _fl(name, name_order="auto")
    assert (first, last) == tuple(t.lower() for t in name.split())
    assert conf == "medium"


# ── auto: initials and upper-cased surnames ──

@pytest.mark.parametrize("name,expected", [
    ("Stenuit B.", ("b.", "stenuit")),
    ("Priem J", ("j", "priem")),
])
def test_auto_reads_a_trailing_initial_as_the_given_name(name, expected):
    assert _fl(name, name_order="auto")[:2] == expected


def test_auto_keeps_a_leading_initial():
    assert _fl("J. Priem", name_order="auto") == ("j.", "priem", "high")


def test_auto_keeps_given_name_like_words_before_an_initial():
    # "Stella C" is the South Indian "Given S" style, not "Last Initial".
    assert _fl("Stella C", name_order="auto")[:2] == ("stella", "c")


def test_auto_reads_an_upper_cased_leading_surname():
    assert _fl("PRIEM Jason", name_order="auto")[:2] == ("jason", "priem")
    assert _fl("Jason PRIEM", name_order="auto")[:2] == ("jason", "priem")
    assert _fl("KIM LEE", name_order="auto")[:2] == ("kim", "lee")  # both shouting: no evidence


# ── auto: Chinese surnames the default table does not cover ──

@pytest.mark.parametrize("name", ["Duan Tianxiang", "Liao Jingqiu"])
def test_pinyin_surname_first_without_a_comma(name):
    """A pinyin surname followed by a token that spells pinyin syllables is surname-first, in every mode."""
    surname, given = name.lower().split()
    assert _fl(name, name_order="auto")[:2] == (given, surname)
    assert _fl(name)[:2] == (given, surname)


@pytest.mark.parametrize("name", ["LU Qing", "WU Shu-hui", "HE Jian-ling", "OU Zhibing"])
def test_short_pinyin_surnames_lead_when_the_rest_is_pinyin(name):
    surname, given = name.lower().split()
    assert _fl(name)[:2] == (given, surname)


@pytest.mark.parametrize("name", ["Lu Anna", "Ho Nikolai"])
def test_pinyin_surname_rule_needs_pinyin_syllables_after_it(name):
    first, last = name.lower().split()
    assert _fl(name)[:2] == (first, last)


@pytest.mark.parametrize("name", ["Zhang Wei", "Wang Fang", "Kim Minjun", "Hongji Zhang"])
def test_auto_leaves_the_existing_cjk_rules_alone(name):
    got, want = parse_name(name, name_order="auto"), parse_name(name)
    assert (got["first"], got["last"]) == (want["first"], want["last"])


@pytest.mark.parametrize("name", ["Priem, Jason", "Jason M. Priem", "Dr. Jason Priem", "Jason Priem Jr"])
def test_auto_does_not_touch_other_shapes(name):
    got, want = parse_name(name, name_order="auto"), parse_name(name)
    assert {k: v for k, v in got.items() if k != "confidence"} == {k: v for k, v in want.items() if k != "confidence"}


# ── benchmark gates on the project's gold set ──

def _rows():
    with open(GOLD, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _plain_two_token(row):
    raw = row["raw_author_name"]
    return (len(raw.split()) == 2 and "," not in raw and raw.isascii() and not row.get("middle")
            and row.get("first") and row.get("last") and not row.get("title") and not row.get("suffix"))


def _ordered_rows():
    """Plain two-token rows the default parser already gets right, so what is left is order."""
    out = []
    for row in _rows():
        if _plain_two_token(row):
            got = parse_name(row["raw_author_name"])
            if (got["first"], got["last"]) == (row["first"], row["last"]):
                out.append(row)
    return out


def _accuracy(rows, swapped, name_order):
    """Share of rows recovered when ``swapped(i)`` rows are written "Last First"."""
    ok = 0
    for i, row in enumerate(rows):
        a, b = row["raw_author_name"].split()
        got = parse_name(f"{b} {a}" if swapped(i) else f"{a} {b}", name_order=name_order)
        ok += (got["first"], got["last"]) == (row["first"], row["last"])
    return ok / len(rows)


@pytest.fixture(scope="module")
def ordered_rows():
    rows = _ordered_rows()
    assert len(rows) > 3000
    return rows


def test_auto_barely_costs_a_pure_first_last_corpus(ordered_rows):
    # No dictionary knows every name, so a few correct names flip. Keep that rate visible.
    assert _accuracy(ordered_rows, lambda i: False, "auto") >= 0.994


def test_auto_pays_off_on_a_mixed_order_corpus(ordered_rows):
    """Half the names rewritten as "Last First". The default already flips pinyin surnames."""
    mixed = lambda i: i % 2 == 1  # noqa: E731
    baseline = _accuracy(ordered_rows, mixed, "first_last")
    auto = _accuracy(ordered_rows, mixed, "auto")
    assert baseline < 0.65
    assert auto >= 0.80


def test_auto_recovers_most_last_first_names(ordered_rows):
    everything = lambda i: True  # noqa: E731
    baseline = _accuracy(ordered_rows, everything, "first_last")
    auto = _accuracy(ordered_rows, everything, "auto")
    assert baseline < 0.25
    assert auto >= 0.60


def test_auto_does_not_hurt_real_two_token_names():
    """On the corpus as it is (about 2% Last First) auto must be at least as good as the default."""
    rows = [r for r in _rows() if r.get("last") and r["raw_author_name"].isascii()
            and "," not in r["raw_author_name"] and len(r["raw_author_name"].split()) == 2]

    def acc(order):
        ok = 0
        for r in rows:
            got = parse_name(r["raw_author_name"], name_order=order)
            ok += (got["first"], got["last"]) == (r["first"], r["last"])
        return ok / len(rows)

    assert acc("auto") >= acc("first_last")
