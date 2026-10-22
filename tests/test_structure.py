"""Structural rules: credentials, generations, compound surnames, initials, list order."""
import pytest

from whatsername import infer_name_order, parse_name, parse_names


def fml(raw, **kw):
    r = parse_name(raw, **kw)
    return r["first"], r["middle"], r["last"]


# ── credentials and generations ──

def test_trailing_credentials_become_the_suffix():
    r = parse_name("Anna Weber, MD, PhD")
    assert (r["first"], r["last"], r["suffix"]) == ("anna", "weber", "md, phd")


def test_credentials_between_surname_and_given_name_are_dropped():
    r = parse_name("Weber, MD, Anna")
    assert (r["first"], r["last"], r["suffix"]) == ("anna", "weber", None)


@pytest.mark.parametrize("raw", ["Weber, Jr., Anna", "Weber Jr, Anna"])
def test_generation_marker_next_to_the_surname_is_the_suffix(raw):
    r = parse_name(raw)
    assert (r["first"], r["last"]) == ("anna", "weber")
    assert r["suffix"].rstrip(".") == "jr"


def test_dotted_initials_are_not_degrees():
    r = parse_name("J.D. Weber")
    assert r["suffix"] is None and r["last"] == "weber"


@pytest.mark.parametrize("raw", ["MD. Ariful Islam", "MA Bankole"])
def test_degree_lookalikes_at_the_front_are_names(raw):
    assert parse_name(raw)["suffix"] is None


def test_author_list_in_one_string_takes_the_first_author():
    r = parse_name("Weber, Anna, Keller, Jonas, Meier, Paul")
    assert (r["first"], r["last"]) == ("anna", "weber")


def test_trailing_particle_stays_with_the_given_names():
    assert fml("Santos, Sergio Vieira dos") == ("sergio", "vieira dos", "santos")


# ── compound surnames ──

@pytest.mark.parametrize("raw, last", [
    ("Maria Hinojosa Aguirre", "hinojosa aguirre"),
    ("Jose Castillo Velazquez", "castillo velazquez"),
    ("Ana Julia Sartori Ramos", "sartori ramos"),
])
def test_two_surname_like_tokens_form_one_surname(raw, last):
    assert parse_name(raw)["last"] == last


@pytest.mark.parametrize("raw", ["John Michael Smith", "Anna Maria Rossi", "Peter Hans Meier"])
def test_given_name_middle_names_are_not_surnames(raw):
    assert parse_name(raw)["last"] == raw.split()[-1].lower()


# ── caps initials and pinyin ──

@pytest.mark.parametrize("raw, want", [
    ("NF Bevis", ("n", "f", "bevis")),
    ("AKM Ahsan Ullah", ("a", "k m ahsan", "ullah")),
    ("TS Rao", ("t", "s", "rao")),
    ("MA Bankole", ("m", "a", "bankole")),
])
def test_run_together_capitals_are_initials(raw, want):
    assert fml(raw) == want


@pytest.mark.parametrize("raw", ["NF BEVIS", "KIM LEE"])
def test_all_capitals_carry_no_initials_evidence(raw):
    assert parse_name(raw)["first"] == raw.split()[0].lower()


def test_pinyin_syllables_are_recognised_as_such():
    from whatsername.parser import _np_pinyin_segmentable as seg
    assert all(seg(w) for w in ("qing", "shu-hui", "zhibing", "xiaohua", "wang"))
    assert not any(seg(w) for w in ("bankole", "nikolai", "robert", "mohammed", "schmid"))


# ── lists ──

def test_first_last_list_parses_exactly_like_the_default():
    names = ["Jason Priem", "Heather Piwowar", "Richard Orr", "Anna Maria Rossi"]
    assert infer_name_order(names) == "first_last"
    assert parse_names(names) == [parse_name(n) for n in names]


def test_last_first_list_is_read_consistently():
    names = ["Priem Jason", "Piwowar Heather", "Orr Richard", "Smith Daniel"]
    assert infer_name_order(names) == "last_first"
    assert [(r["first"], r["last"]) for r in parse_names(names)] == [
        ("jason", "priem"), ("heather", "piwowar"), ("richard", "orr"), ("daniel", "smith")]


def test_surname_with_trailing_initials_list():
    names = ["Priem J.", "Piwowar H.", "Orr R."]
    assert infer_name_order(names) == "last_first"


def test_ambiguous_or_empty_lists_stay_first_last():
    assert infer_name_order([]) == "first_last"
    assert infer_name_order(["Li Wei", "Zhou Li"]) == "first_last"


def test_explicit_order_is_applied_to_every_name():
    got = parse_names(["Jason Priem", "Heather Piwowar"], name_order="last_first")
    assert [r["first"] for r in got] == ["priem", "piwowar"]


def test_unknown_order_is_rejected():
    with pytest.raises(ValueError):
        parse_names(["Jason Priem"], name_order="sideways")


# ── trailing initials, auto-order with initials, organisations ──

@pytest.mark.parametrize("raw, want", [
    ("Dharunraja S R", ("dharunraja", None, "s r")),
    ("Helen Shaji J C", ("helen", "shaji", "j c")),
])
def test_two_bare_capitals_at_the_end_are_a_surname(raw, want):
    assert fml(raw) == want


def test_a_single_trailing_initial_is_still_a_middle_initial():
    assert fml("Helen Shaji J") == ("helen", "shaji", "j")


@pytest.mark.parametrize("raw, want", [
    ("Klimanov V. V.", ("v.", "v.", "klimanov")),
    ("Priem Jason R.", ("jason", "r.", "priem")),
])
def test_auto_reads_surname_then_given_names_and_initials(raw, want):
    assert fml(raw, name_order="auto") == want


def test_auto_keeps_first_middle_initial_last():
    assert fml("Anna M. Rossi", name_order="auto") == ("anna", "m.", "rossi")


@pytest.mark.parametrize("raw", [
    "Natural History Museum", "Agencia Nacional de Investigacion", "Faculdade de Medicina",
    "Ministère de la Santé", "Biodiversity Research Group", "Stevens Foundation Trust",
])
def test_institutions_have_no_first_name(raw):
    r = parse_name(raw)
    assert r["first"] is None and r["last"]


def test_a_bracketed_role_does_not_make_a_person_an_organisation():
    r = parse_name("Stevens R.K. (Guest Editors)")
    assert "editors" not in (r["last"] or "")


def test_glued_caps_surname_is_split():
    for raw, first, last in [("GuoxingZHANG", "guoxing", "zhang"), ("GepingLI", "geping", "li")]:
        r = parse_name(raw)
        assert (r["first"], r["last"]) == (first, last)


def test_glued_pinyin_and_korean_names_are_split():
    r = parse_name("YuanWang")
    assert (r["first"], r["last"]) == ("yuan", "wang")
    r = parse_name("SungKunKim")
    assert (r["first"], r["middle"], r["last"]) == ("sung", "kun", "kim")


def test_glued_western_words_and_prefixes_stay_whole():
    for raw in ("LightMart", "McDonald", "DeSilva", "VanDyke", "GuoShao"):
        r = parse_name(raw)
        assert r["first"] is None and r["last"] == raw.lower()


def test_courtesy_titles_need_two_words_after_them():
    r = parse_name("Mr Tomer Ben-Shushan")
    assert (r["title"], r["first"], r["last"]) == ("mr.", "tomer", "ben-shushan")
    r = parse_name("Sir Ahmed")
    assert r["title"] is None


def test_leading_hyphenated_initials_split():
    for raw, first, last in [("H.-K Chung", "h.", "chung"), ("S-Y Koay", "s", "koay")]:
        r = parse_name(raw)
        assert (r["first"], r["last"]) == (first, last)
    r = parse_name("Chen L-Y")
    assert r["last"] == "chen"
