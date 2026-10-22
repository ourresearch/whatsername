"""whatsername — deterministic author-name parser.

Parses a raw author-name string into structured components
(title, first, middle, last, suffix, nickname) with a confidence score.
Handles Latin, Chinese, Japanese, Korean, Cyrillic, and Arabic/Persian scripts.

Extracted from the OpenAlex author entity resolution pipeline (AER v4).
Licensed GPL-3.0-or-later (see LICENSE) — required by the GPL `unidecode` dependency.
"""

import unicodedata
import re
import json
from importlib import resources

from nameparser import HumanName
from unidecode import unidecode as _unidecode

try:
    from pykakasi import kakasi as _kakasi
except ImportError:
    _kakasi = None

_kks = None  # lazily initialized

try:
    from korean_romanizer.romanizer import Romanizer as _Romanizer
except ImportError:
    _Romanizer = None

try:
    from transliterate import translit as _translit
except ImportError:
    _translit = None


# ══════════════════════════════════════════════════════════════════════════════
# HNP fallback parser (original nameparser-based, used when confidence is low)
# ══════════════════════════════════════════════════════════════════════════════

# -- CJK support for HNP fallback --

COMPOUND_SURNAMES = {
    '欧阳', '太史', '端木', '上官', '司马', '东方', '独孤', '南宫', '万俟',
    '闻人', '夏侯', '诸葛', '尉迟', '公羊', '赫连', '澹台', '皇甫', '宗政',
    '濮阳', '公冶', '太叔', '申屠', '公孙', '慕容', '仲孙', '钟离', '长孙',
    '宇文', '司徒', '鲜于', '司空', '闾丘', '子车', '亓官', '司寇', '巫马',
    '公西', '颛孙', '壤驷', '公良', '漆雕', '乐正', '宰父', '谷梁', '拓跋',
    '夹谷', '轩辕', '令狐', '段干', '百里', '呼延', '东郭', '南门', '羊舌',
    '微生', '公户', '公玉', '公仪', '梁丘', '公仲', '公上', '公门', '公山',
    '公坚', '左丘', '公伯', '西门', '公祖', '第五', '公乘', '贯丘', '公皙',
    '南荣', '东里', '东宫', '仲长', '子书', '子桑', '即墨', '达奚', '褚师',
    '歐陽', '司馬', '東方', '獨孤', '南宮', '諸葛', '尉遲', '赫連', '澹臺',
    '皇甫', '濮陽', '慕容', '鍾離', '長孫', '宇文', '鮮于', '閭丘', '顓孫',
    '漆雕', '樂正', '穀梁', '拓跋', '夾谷', '軒轅', '段幹', '東郭', '南門',
    '羊舌', '梁丘', '左丘', '西門', '東里', '東宮', '仲長'
}

def _is_cjk_char(char):
    cp = ord(char)
    return (
        (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or
        (0x20000 <= cp <= 0x2A6DF) or (0xF900 <= cp <= 0xFAFF) or
        (0x2F800 <= cp <= 0x2FA1F)
    )

def _is_all_cjk(s):
    chars = s.replace(' ', '')
    return len(chars) > 0 and all(_is_cjk_char(c) for c in chars)

def _split_chinese_name(name):
    if len(name) >= 2 and name[:2] in COMPOUND_SURNAMES:
        return name[:2], name[2:]
    return name[0], name[1:]

def _hnp_parse_name(name_string):
    name_string = name_string.strip()
    if _is_all_cjk(name_string) and ' ' not in name_string:
        surname, given = _split_chinese_name(name_string)
        result = HumanName()
        result.last = surname
        result.first = given
        return result
    return HumanName(name_string)

def _hnp_clean(s):
    if s is None or s == '':
        return ''
    normalized = unicodedata.normalize('NFD', s)
    without_diacritics = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    cleaned = without_diacritics.lower().replace('.', '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def parse_human_name_hnp(name):
    """Original HNP-based parser, used as fallback for low-confidence names."""
    if name is None or not isinstance(name, str) or name.strip() == '':
        return {'title': '', 'first': '', 'middle': '', 'last': '', 'suffix': '', 'nickname': ''}
    try:
        parsed = _hnp_parse_name(name)
        return {
            'title': _hnp_clean(parsed.title),
            'first': _hnp_clean(parsed.first),
            'middle': _hnp_clean(parsed.middle),
            'last': _hnp_clean(parsed.last),
            'suffix': _hnp_clean(parsed.suffix),
            'nickname': _hnp_clean(parsed.nickname)
        }
    except Exception:
        return {'title': '', 'first': '', 'middle': '', 'last': '', 'suffix': '', 'nickname': ''}


# ══════════════════════════════════════════════════════════════════════════════
# New deterministic parser (aer-python-name-parser v2.2)
# ══════════════════════════════════════════════════════════════════════════════

# ── Script detection ──

def _np_is_cjk(c):
    cp = ord(c)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2A6DF or
            0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF)

def _np_is_hangul(c):
    cp = ord(c)
    return (0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F)

def _np_is_cyrillic(c):
    cp = ord(c)
    return 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F

def _np_is_arabic(c):
    cp = ord(c)
    return (0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or
            0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF)

def _np_has_cjk(s):
    return any(_np_is_cjk(c) for c in s)

def _np_has_hangul(s):
    return any(_np_is_hangul(c) for c in s)

def _np_has_cyrillic(s):
    return any(_np_is_cyrillic(c) for c in s)

def _np_has_arabic(s):
    return any(_np_is_arabic(c) for c in s)

def _np_has_latin_alpha(s):
    return any(c.isalpha() and ord(c) < 128 for c in s)

# ── Normalization ──

def _np_to_ascii(s):
    if s is None:
        return None
    s = s.replace('\u2010', '-').replace('\u2011', '-').replace('\u2012', '-')
    s = s.replace('\u2013', '-').replace('\u2014', '-').replace('\u2015', '-')
    s = s.replace('\u00ad', '-')
    s = s.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '')
    s = s.replace('ø', 'o').replace('Ø', 'O')
    s = s.replace('æ', 'ae').replace('Æ', 'AE')
    s = s.replace('ß', 'ss')
    s = s.replace('đ', 'd').replace('Đ', 'D')
    s = s.replace('ł', 'l').replace('Ł', 'L')
    s = s.replace('ð', 'd').replace('Ð', 'D')
    s = s.replace('þ', 'th').replace('Þ', 'Th')
    s = s.replace('ı', 'i').replace('İ', 'i')
    s = unicodedata.normalize('NFKD', s)
    result = []
    for c in s:
        if c == '-':
            result.append('-')
        elif c == '.':
            result.append('.')
        elif c in ("'", '\u2019', '\u2018', '\u02bc'):
            continue
        elif unicodedata.category(c) == 'Mn':
            continue
        elif ord(c) < 128:
            result.append(c)
    return ''.join(result).lower().strip()

def _np_clean(s):
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    result = _np_to_ascii(s)
    if result is None:
        return None
    result = result.replace(',', '').replace(';', '')
    result = re.sub(r'\s+', ' ', result).strip()
    if not result:
        return None
    return result

# ── Constants ──

_NP_COMPOUND_PREFIXES = frozenset([
    'de', 'del', 'della', 'di', 'da', 'das', 'do', 'dos',
    'van', 'von', 'der', 'den', 'het', 'la', 'le', 'les', 'el',
    'al', 'al-', 'bin', 'ibn', 'abu', 'e', 'i',
])

# ── Particle strip for matching key ──
#
# Strips leading surname particles (de, da, van der, etc.) from parsed_name.last
# so matching treats "de Oliveira" and "Oliveira" as the same surname. Raw
# strings stay untouched — only the matching/blocking key changes. See
# oxjobs/working/split-authors-name-parser/PLAN-surname-particle-parser-fix.md.

_STRIP_PARTICLES_SINGLE = frozenset({
    'de', 'da', 'do', 'dos', 'das', 'del',
    'van', 'von', 'zu',
})

_STRIP_PARTICLES_TWO = frozenset({
    ('de', 'la'), ('de', 'las'), ('de', 'los'),
    ('van', 'de'), ('van', 'der'), ('van', 'den'),
})

def _strip_surname_particles(last):
    if not last:
        return last
    tokens = last.split()
    if len(tokens) < 2:
        return last
    if len(tokens) >= 3 and (tokens[0], tokens[1]) in _STRIP_PARTICLES_TWO:
        return ' '.join(tokens[2:])
    if tokens[0] in _STRIP_PARTICLES_SINGLE:
        return ' '.join(tokens[1:])
    return last

_NP_TITLE_PATTERNS = [
    (re.compile(r'^prof\.?\s+dr\.?\s+', re.IGNORECASE), 'prof. dr.'),
    (re.compile(r'^prof\.?\s+', re.IGNORECASE), 'prof.'),
    (re.compile(r'^dr\.?\s+', re.IGNORECASE), 'dr.'),
    (re.compile(r'^ir\.?\s+', re.IGNORECASE), 'ir.'),
    (re.compile(r'^dra\.?\s+', re.IGNORECASE), 'dra.'),
    (re.compile(r'^ing\.?\s+', re.IGNORECASE), 'ing.'),
]

# Courtesy titles that are also ordinary words or names ("Sir", "Miss"): taken only when two or
# more words follow, so "Sir Ahmed" stays a name.
_NP_COURTESY_TITLES = re.compile(r'^(mr|mrs|ms|miss|sir|dame|rev)\.?\s+', re.IGNORECASE)

_NP_ORG_KEYWORDS = [
    'institute', 'university', 'ministry', 'society', 'department',
    'academy', 'foundation', 'laboratory', 'board', 'commission',
    'journal', 'program studi', 'pontificia', 'escuela', 'ministerio',
    'editorial', 'office', 'council', 'committee', 'association',
    'corporation', 'company', 'group', 'center', 'centre',
]

# Whole-word nouns that name a body of people (museum, agency, school, society, ministry...) in the
# languages that dominate the corpus. Matched on accent-folded tokens of names with two or more
# words, so a surname that happens to equal one of them still parses as a person when alone.
_NP_ORG_WORDS = frozenset("""
    museum museo musee museu museums agency agencia agence project projeto proyecto projet
    collaboration consortium network initiative collaborative hospital clinic clinica klinik
    school escola escuela college faculty faculdade facultad fakultet fakulti library biblioteca bureau
    zentrum centro institut instituto istituto institution universidad universidade universite
    universitat universiteit akademie academia sociedad sociedade societe gesellschaft servicio servico
    departamento dipartimento abteilung ministere ministerium ministerio ministerie kommission comision
    comissao programa programme observatory observatorio laboratorio laboratoire laboratories
    federation authority administration directorate secretariat comite revista zeitschrift editores
    editors biodiversity natural history research national cancer
""".split())

# ── Post-nominal credentials ──
# Curated for OpenAlex's multilingual corpus. nameparser's SUFFIX_ACRONYMS
# (609 entries) is deliberately NOT used — it collides heavily with surnames
# in our data: Mba (Igbo), Rai (Indian), Mai (Vietnamese), Abd (Arabic/Malay),
# Cfp, Mfa, Cha, Chi, Sa, Ra, Pt, Ot, and many more.
#
# This list covers the common academic/medical/professional credentials that
# actually appear in author names, excluding anything that overlaps with
# known surname patterns. Short (<=2 char) entries rely on the all-caps /
# comma-before guard in _np_strip_credentials to avoid eating surnames.

_NP_CREDENTIAL_ACRONYMS = frozenset([
    # Doctoral
    'phd', 'dphil', 'drph', 'scd', 'edd', 'psyd',
    'pharmd', 'mbbs', 'mbchb', 'dvm', 'dds', 'dmd', 'dnp', 'dpt',
    # Masters (unambiguous only)
    'msc', 'mph', 'mscn', 'msn', 'mpp', 'mhs',
    # Law
    'llm', 'jd',
    # Social work / clinical
    'lcsw',
    # Medical/board fellowships
    'facs', 'facp', 'facog', 'faap', 'fccp', 'faan',
    'frcp', 'frcs', 'frcpc', 'frcog', 'mrcp', 'mrcs',
    'fcps',
    # Nursing
    'rn', 'crna', 'fnp',
    # Short (<=2 char) — require comma or all-caps via the stripper's guard
    'md', 'do',
])

# Credentials accepted ONLY in dotted form (M.S., M.A., M.B.A., M.F.A.).
# The internal dots disambiguate them from bare-letter forms which collide
# with surnames (Ma, Mba) or initials (MS, MA).
_NP_DOTTED_ONLY_CREDS = frozenset(['ms', 'ma', 'mba', 'mfa', 'ba', 'bs'])

# Generational markers — excluded from the credential stripper so the existing
# suffix regex handles them (preserving original casing for "Jr." etc.).
_NP_GENERATIONAL = frozenset(['jr', 'sr', 'ii', 'iii', 'iv', 'v', 'esq'])

_CRED_RE = re.compile(r'(,\s*|\s+)([A-Za-z][A-Za-z.]*\.?)\s*$')
_NP_GENERATIONAL_RE = re.compile(r'^(?:Jr\.?|Junior|Júnior|Sr\.?|III|IV|II|Esq\.?)$', re.IGNORECASE)
_NP_GENERATIONAL_TAIL = re.compile(r'\s+(Jr\.?|Sr\.?|III|IV|II|Esq\.?)$', re.IGNORECASE)  # not 'Júnior': that is part of the surname before a comma
_NP_INITIALS_RUN = re.compile(r'^(?:[A-Za-z]\.){2,}$')  # 'J.D.', 'H.G.G.M.': a person's initials, usually

def _np_acronym_shape(s):
    """Cred-shape heuristic for unknown acronyms (MBA, MFA, FRACP, FAHMS, ...).
    All-caps and mixed-case both 2-6 chars; mixed-case requires >=2 uppercase
    (PhD, MSc, PharmD). All-caps given names (JAMES, JOHN) are protected by
    the strip's anchor requirement: an unknown acronym is only stripped if
    a whitelisted credential exists in the same comma-list.
    """
    L = len(s)
    if L < 2 or L > 6 or not s[0].isupper():
        return False
    if s.isupper():
        return True
    caps = sum(1 for c in s if c.isupper())
    return caps >= 2


def _np_mid_is_credential_token(tok):
    """Mid-stream cred check — used inside mixed comma segments.
    Rejects mixed-case 'Md.' (name token in 'Alam Md. Mahbub').
    Accepts all-caps 'MBA' / 'MD' / 'DVM' and dotted 'M.D.' / 'M.B.A.'.
    """
    raw_no_dot = tok.replace('.', '')
    norm = raw_no_dot.lower()
    has_internal_dots = '.' in tok[:-1]
    is_short = len(norm) <= 2
    is_wl_base = (
        norm in _NP_CREDENTIAL_ACRONYMS
        or (has_internal_dots and norm in _NP_DOTTED_ONLY_CREDS)
        or (raw_no_dot.isupper() and norm in _NP_DOTTED_ONLY_CREDS)
    )
    if not is_wl_base:
        return False
    if is_short:
        return has_internal_dots or raw_no_dot.isupper()
    return True


def _np_segment_is_credential(seg_tokens):
    """Whole-segment cred check — used to DROP a comma segment.
    Looser than mid-stream: a bare 'Md' alone between commas is a credential
    (real names don't appear as a single token 'Md' between commas).
    """
    for tok in seg_tokens:
        raw_no_dot = tok.replace('.', '')
        norm = raw_no_dot.lower()
        has_internal_dots = '.' in tok[:-1]
        is_wl_base = (
            norm in _NP_CREDENTIAL_ACRONYMS
            or (has_internal_dots and norm in _NP_DOTTED_ONLY_CREDS)
            or (raw_no_dot.isupper() and norm in _NP_DOTTED_ONLY_CREDS)
        )
        if not is_wl_base:
            return False
    return True


def _np_leading_cred_is_name(tok):
    """'MD', 'MA', 'MS', 'DO' lead a name far more often as an abbreviated given name or as
    initials ('MD. Ariful Islam', 'MA Bankole') than as a degree. 'RN', 'PhD' and the like
    stay credentials."""
    norm = tok.replace('.', '').lower()
    return norm in _NP_DOTTED_ONLY_CREDS or norm in ('md', 'do')


def _np_drop_credential_segments(r):
    """Drop comma segments that are purely credentials; also strip cred tokens
    inside mixed segments (mid-stream) when >=2 name tokens survive.

    Returns ``(name, credentials)``. ``credentials`` are the lowercase, dot-free credentials
    that trail the whole name ('Markus Meyer, MD, PhD' -> ['md', 'phd']), so the caller can
    report them as the suffix. Credentials sitting between the surname and the given name
    ('Davatzikos, PhD, Christos') or inside a segment are dropped without being reported.

    Fixes:
      - 'Davatzikos, PhD, Christos'      -> drops PhD segment
      - 'DeLong, Md, Facp, Douglas M.'   -> drops Md and Facp segments
      - 'Matsuura DVM Yoshiharu'         -> drops DVM mid-stream
      - 'John M. Harris Jr MD MBA'       -> drops MD, MBA mid-stream (all-caps)
      - 'Prof. Dr Smith, MD, PhD'        -> drops MD, PhD segments
    Preserves:
      - 'Alam Md. Mahbub'                -> 'Md.' mixed case kept as name
      - 'Dang V Do' / 'Ron Do'           -> 'Do' short + mixed case kept
      - 'MD. Ariful Islam' / 'MA Bankole' -> a short leading token is a name, not a degree
    """
    if not r:
        return r, []
    segments = [s.strip() for s in r.split(',')]
    kept = []
    cred_marks = []  # (index in kept after which it was dropped, credentials of that segment)
    for seg in segments:
        if not seg:
            continue
        tokens = seg.split()
        if not tokens:
            continue
        if _np_segment_is_credential(tokens):
            cred_marks.append((len(kept), [t.replace('.', '').lower() for t in tokens]))
            continue
        joined = ''.join(tokens).replace('.', '')  # 'Ph. D.' -> 'PhD'
        if len(tokens) == 2 and len(joined) >= 3 and _np_segment_is_credential([joined]):
            cred_marks.append((len(kept), [joined.lower()]))
            continue
        lead_is_name = _np_leading_cred_is_name(tokens[0]) and len(tokens) >= 2
        non_cred = [t for i, t in enumerate(tokens)
                    if not _np_mid_is_credential_token(t) or (i == 0 and lead_is_name)]
        first_was_cred = _np_mid_is_credential_token(tokens[0]) and not lead_is_name
        if 2 <= len(non_cred) < len(tokens):
            # Standard mid-stream strip — >=2 name tokens survive.
            seg = ' '.join(non_cred)
        elif len(non_cred) == 1 and first_was_cred and len(tokens) >= 2:
            # Credential-prefix pattern ('Phd Chiara', 'Msc Maricela', etc.).
            # No one is actually named 'PhD' / 'MSc' — safe to drop leading
            # creds even if only 1 name token remains.
            seg = ' '.join(non_cred)
        kept.append(seg)
    trailing = [c for at, creds in cred_marks if at >= len(kept) for c in creds]
    return ', '.join(kept), trailing


def _np_strip_credentials(r, existing_suffix):
    """Strip trailing credentials and return (stripped_name, updated_suffix).

    Two-phase algorithm:
      Phase 1 (probe): walk right-to-left over trailing comma/space-separated
        tokens. Collect candidates that are EITHER whitelisted credentials
        OR comma-adjacent acronym-shaped tokens (MBA, MFA, XYZ). Don't
        modify r yet.
      Phase 2 (commit): find the deepest whitelisted credential in the
        candidate list. Commit all candidates up to and including that one.
        If no whitelisted credential was found, commit nothing — we won't
        strip unknowns on their own.

    This means:
      - "Derek Banyard, MD, MS, MBA" -> all three stripped (MD whitelisted,
        MS and MBA accepted via acronym shape in comma list)
      - "Smith, JOHN" -> JOHN not stripped (no whitelisted cred to anchor)
      - "Smith, FACS, XYZ" -> both stripped (FACS whitelisted, XYZ accepted)

    Short (<=2 char) whitelisted entries still require comma-before or
    all-caps to avoid eating surnames like "Ma" or "Do".
    """
    candidates = []  # list of (remainder_after_strip, norm, is_whitelisted)
    probe = r
    for _ in range(8):
        m = _CRED_RE.search(probe)
        if not m:
            break
        sep = m.group(1)
        token = m.group(2)
        raw_no_dot = token.replace('.', '')
        norm = raw_no_dot.lower()

        if norm in _NP_GENERATIONAL:
            break

        has_internal_dot = '.' in token[:-1]  # dot before final position
        is_wl = norm in _NP_CREDENTIAL_ACRONYMS or (
            has_internal_dot and norm in _NP_DOTTED_ONLY_CREDS
        )
        # Dotted shape-like tokens of length >=4 (e.g. B.Tech., M.Tech.) count as
        # whitelisted for anchor purposes — internal dots are a strong cred signal.
        initials_run = bool(_NP_INITIALS_RUN.match(token))
        if not is_wl and has_internal_dot and _np_acronym_shape(raw_no_dot) and len(raw_no_dot) >= 4 \
                and not initials_run:
            is_wl = True
        if is_wl and initials_run and norm != 'md' \
                and ' ' not in probe[:m.start()].rstrip(', \t').strip():
            break  # 'Lawrence, J.D.': stripping it would leave a surname with no given name
        if is_wl:
            # Short (<=2 char) creds need a guard to avoid eating surnames
            # (Ma, Do, Ha, Sa). Exempted when:
            #   - norm == 'md' (not a surname in any language we see)
            #   - token has internal dots (M.S., M.A. — dots disambiguate)
            if len(norm) <= 2 and norm != 'md' and not has_internal_dot:
                has_comma = ',' in sep
                all_caps = raw_no_dot.isupper()
                if not (has_comma or all_caps):
                    break
        elif _np_acronym_shape(raw_no_dot):
            pass  # accept as shape-like; commit anchor still requires a whitelisted cred
        else:
            break

        remainder = probe[:m.start()].rstrip(', \t').strip()
        candidates.append((remainder, norm, is_wl))
        probe = remainder

    last_wl = -1
    for i, (_, _, is_wl) in enumerate(candidates):
        if is_wl:
            last_wl = i
    if last_wl < 0:
        return r, existing_suffix

    committed = candidates[:last_wl + 1]
    creds = [c[1] for c in committed][::-1]  # reverse: natural reading order
    r = committed[-1][0]
    combined = ', '.join(creds)
    if existing_suffix:
        return r, f"{existing_suffix}, {combined}"
    return r, combined



# ── Cyrillic/Latin look-alikes ──

_LATIN_TO_CYRILLIC = {
    'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'H': 'Н', 'K': 'К',
    'M': 'М', 'O': 'О', 'P': 'Р', 'T': 'Т', 'X': 'Х',
    'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о', 'p': 'р', 'x': 'х',
}
_CYRILLIC_TO_LATIN = {v: k for k, v in _LATIN_TO_CYRILLIC.items()}

def _np_fix_latin_lookalikes(s):
    return ''.join(_LATIN_TO_CYRILLIC.get(c, c) for c in s)

def _np_fix_cyrillic_lookalikes(s):
    return ''.join(_CYRILLIC_TO_LATIN.get(c, c) for c in s)

# ── CJK surname data (inline) ──
# Loaded from Databricks census tables — too large to inline fully,
# so we load from the catalog at import time if available, else use empty sets.

def _load_surname_set(filename):
    with resources.files(__package__).joinpath("data", filename).open(encoding="utf-8") as f:
        return set(json.load(f))

try:
    _NP_CHINESE_SURNAMES = _load_surname_set("chinese_surnames.json")
except Exception:
    _NP_CHINESE_SURNAMES = set()

try:
    _NP_JAPANESE_SURNAMES = _load_surname_set("japanese_surnames.json")
except Exception:
    _NP_JAPANESE_SURNAMES = set()

# ── Korean surname data ──

_NP_KOREAN_SURNAME_MAP = {
    '김': 'kim', '이': 'lee', '박': 'park', '최': 'choi', '정': 'jeong',
    '강': 'kang', '조': 'jo', '윤': 'yun', '장': 'jang', '임': 'lim',
    '한': 'han', '오': 'oh', '서': 'seo', '신': 'shin', '권': 'kwon',
    '황': 'hwang', '안': 'ahn', '송': 'song', '전': 'jeon', '홍': 'hong',
    '유': 'yu', '고': 'go', '문': 'moon', '양': 'yang', '손': 'son',
    '배': 'bae', '백': 'baek', '허': 'heo', '남': 'nam', '심': 'sim',
    '노': 'noh', '하': 'ha', '곽': 'kwak', '성': 'seong', '차': 'cha',
    '주': 'ju', '우': 'woo', '구': 'gu', '민': 'min', '진': 'jin',
    '지': 'ji', '엄': 'eom', '채': 'chae', '류': 'ryu',
}

_NP_KOREAN_ROMANIZED_SURNAMES = frozenset([
    'kim', 'lee', 'park', 'choi', 'jung', 'jeong', 'kang', 'cho', 'jo',
    'yoon', 'yun', 'jang', 'chang', 'lim', 'im', 'han', 'oh', 'seo',
    'shin', 'kwon', 'hwang', 'ahn', 'an', 'song', 'jeon', 'chun', 'hong',
    'yu', 'yoo', 'ko', 'go', 'moon', 'yang', 'son', 'bae', 'baek',
    'heo', 'hur', 'nam', 'sim', 'noh', 'roh', 'ha', 'kwak', 'seong',
    'sung', 'cha', 'joo', 'ju', 'woo', 'koo', 'ku', 'gu', 'min',
    'jin', 'ji', 'eom', 'um', 'chae', 'ryu', 'yoo', 'hyun', 'chung',
    'shin', 'sohn', 'shim', 'paik', 'pae', 'byun', 'byeon',
])

# Conservative Chinese pinyin surnames (48) used for 2-token Latin order detection.
# Excludes pinyin that collides with Western given names / English words:
#   ma, he, lin, han, lu, bai, mu, bo, yu, wu, jin, cai, wen, shi
_NP_CHINESE_ROMANIZED_SURNAMES = frozenset([
    'wang', 'li', 'zhang', 'liu', 'chen', 'yang', 'huang', 'zhao', 'zhou',
    'xu', 'sun', 'zhu', 'hu', 'guo', 'gao', 'luo',
    'zheng', 'liang', 'xie', 'song', 'tang', 'feng', 'deng', 'cao', 'peng',
    'zeng', 'xiao', 'tian', 'pan', 'yuan', 'jiang', 'dong', 'jia', 'ding',
    'shen', 'wei', 'meng', 'qian', 'yan', 'dai', 'du',
    'fu', 'zhong', 'kong', 'cui', 'qiu', 'lei', 'yao',
])

# ── Pinyin syllables ──
# Standard Mandarin syllables without tone marks, written out by initial. A token
# that splits cleanly into one to four of them ("jianling", "shu-hui", "zhibing")
# reads as romanised Chinese; "bankole" and "nikolai" do not.

_PINYIN_BY_INITIAL = {
    '': 'a ai an ang ao e ei en eng er o ou',
    'b': 'a ai an ang ao ei en eng i ian iao ie in ing o u',
    'p': 'a ai an ang ao ei en eng i ian iao ie in ing o ou u',
    'm': 'a ai an ang ao e ei en eng i ian iao ie in ing iu o ou u',
    'f': 'a an ang ei en eng o ou u',
    'd': 'a ai an ang ao e ei en eng i ia ian iao ie ing iu ong ou u uan ui un uo',
    't': 'a ai an ang ao e eng i ian iao ie ing ong ou u uan ui un uo',
    'n': 'a ai an ang ao e ei en eng i ian iang iao ie in ing iu ong ou u uan uo v ve',
    'l': 'a ai an ang ao e ei eng i ia ian iang iao ie in ing iu ong ou u uan un uo v ve',
    'g': 'a ai an ang ao e ei en eng ong ou u ua uai uan uang ui un uo',
    'k': 'a ai an ang ao e en eng ong ou u ua uai uan uang ui un uo',
    'h': 'a ai an ang ao e ei en eng ong ou u ua uai uan uang ui un uo',
    'j': 'i ia ian iang iao ie in ing iong iu u uan ue un',
    'q': 'i ia ian iang iao ie in ing iong iu u uan ue un',
    'x': 'i ia ian iang iao ie in ing iong iu u uan ue un',
    'zh': 'a ai an ang ao e ei en eng i ong ou u ua uai uan uang ui un uo',
    'ch': 'a ai an ang ao e en eng i ong ou u ua uai uan uang ui un uo',
    'sh': 'a ai an ang ao e ei en eng i ou u ua uai uan uang ui un uo',
    'r': 'an ang ao e en eng i ong ou u uan ui un uo',
    'z': 'a ai an ang ao e ei en eng i ong ou u uan ui un uo',
    'c': 'a ai an ang ao e en eng i ong ou u uan ui un uo',
    's': 'a ai an ang ao e en eng i ong ou u uan ui un uo',
    'y': 'a an ang ao e i in ing ong ou u uan ue un',
    'w': 'a ai an ang ei en eng o u',
}
_NP_PINYIN_SYLLABLES = frozenset(
    initial + final for initial, finals in _PINYIN_BY_INITIAL.items() for final in finals.split()
)
_NP_PINYIN_MAX_SYLLABLES = 4
_PINYIN_GIVEN_GUARD = 5.0  # "Lu Anna": a clearly Western given name that happens to spell pinyin

def _np_pinyin_segmentable(token):
    """True when ``token`` splits into 1-4 pinyin syllables (hyphens and apostrophes are free breaks)."""
    word = re.sub(r"[-'\s]", '', token.lower())
    if not word.isalpha() or not word.isascii():
        return False
    # best[i] = fewest syllables that spell word[:i]
    best = [0] + [99] * len(word)
    for i in range(1, len(word) + 1):
        for j in range(max(0, i - 6), i):
            if best[j] < 99 and word[j:i] in _NP_PINYIN_SYLLABLES:
                best[i] = min(best[i], best[j] + 1)
    return best[-1] <= _NP_PINYIN_MAX_SYLLABLES

# ── Name-order inference ──
# ``name_order="auto"`` weighs how given-name-like and surname-like each token
# looks. The evidence is only loaded on first use. See scripts/build_name_evidence.py.

_NAME_ORDERS = ("first_last", "last_first", "auto")
_AUTO_MARGIN = 3.0         # evidence (nats) required to read "Last First" from the lexicon alone
_AUTO_INITIAL_GUARD = 1.0  # "Word J": stay "First Last" if Word is at least this given-name-like
_AUTO_CONFIDENT = 1.0      # evidence in favour of "First Last" needed to report 'high'
_NP_INITIAL_TOKEN = re.compile(r'^[A-Za-z]\.?$')
_evidence_cache = None

def _name_evidence():
    """Return ``(scores, pinyin_surnames)``. ``scores`` maps a lowercase ASCII
    token to a number that is positive when the token is mostly a given name and
    negative when it is mostly a surname; unknown tokens score 0."""
    global _evidence_cache
    if _evidence_cache is None:
        with resources.files(__package__).joinpath("data", "name_evidence.json").open(encoding="utf-8") as f:
            data = json.load(f)
        scale = data["scale"]
        scores = {}
        for level, tokens in data["levels"].items():
            value = int(level) / scale
            for token in tokens.split():
                scores[token] = value
        _evidence_cache = (scores, frozenset(data["pinyin_surnames"]))
    return _evidence_cache

def _evidence_key(token):
    cleaned = _np_clean(token)
    return re.sub(r'[^a-z]', '', cleaned.lower()) if cleaned else ''

def _auto_order(t0, t1):
    """Decide the order of two name tokens.

    Returns ``(swap, confident)``. ``swap`` is True when they read as "Last First".
    Flips are deliberately conservative: a wrong flip costs a name that was
    already right, so weak evidence keeps the default order and is reported as
    ``confident=False`` instead.
    """
    scores, pinyin = _name_evidence()
    a, b = _evidence_key(t0), _evidence_key(t1)
    if a in _NP_CHINESE_ROMANIZED_SURNAMES or a in _NP_KOREAN_ROMANIZED_SURNAMES \
            or b in _NP_CHINESE_ROMANIZED_SURNAMES or b in _NP_KOREAN_ROMANIZED_SURNAMES:
        return False, False  # the CJK rules above own these; they declined, so stay ambiguous
    t0_initial, t1_initial = bool(_NP_INITIAL_TOKEN.match(t0)), bool(_NP_INITIAL_TOKEN.match(t1))
    if t0_initial and not t1_initial:
        return False, True   # "J. Priem"
    if t1_initial and not t0_initial and len(a) >= 2:
        # "Priem J.": a lone letter is almost never a surname. The exception is
        # the South Indian "Given S" style, so keep given-name-like words as they are.
        if scores.get(a, 0.0) < _AUTO_INITIAL_GUARD:
            return True, False
        return False, False
    if len(a) < 2 or len(b) < 2:
        return False, False
    if len(t0) >= 3 and t0.isupper() and not t1.isupper():
        return True, False   # "PRIEM Jason": the upper-cased surname convention
    if (a in pinyin) != (b in pinyin):
        return a in pinyin, False
    margin = scores.get(b, 0.0) - scores.get(a, 0.0)
    if margin > _AUTO_MARGIN:
        return True, False
    return False, -margin >= _AUTO_CONFIDENT

_CAPS_INITIALS = re.compile(r'^[A-Z]{2,3}$')

def _np_split_caps_initials(tokens):
    """"NF Bevis" -> ["N", "F", "Bevis"]: a run of two or three capitals in front of a
    mixed-case word is a person's initials, run together (the PubMed style), not a word."""
    if len(tokens) < 2 or len(tokens) > 5 or not _CAPS_INITIALS.match(tokens[0]):
        return tokens
    if all(t.isupper() for t in tokens):
        return tokens  # shouting: "NF BEVIS" carries no case evidence
    rest = tokens[1:]
    if any(t.isupper() and len(t) > 1 for t in rest):
        return tokens
    head = _evidence_key(tokens[0])
    scores, pinyin = _name_evidence()
    if head in pinyin or head in _NP_CHINESE_ROMANIZED_SURNAMES or head in _NP_KOREAN_ROMANIZED_SURNAMES:
        # "MA Bankole" are initials; "MA Youzhi" is a Chinese name written surname-first
        if len(rest) == 1 and _np_pinyin_segmentable(rest[0]) and scores.get(_evidence_key(rest[0]), 0.0) < _PINYIN_GIVEN_GUARD:
            return tokens
    return list(tokens[0]) + rest

def _np_auto_reorder_with_initials(tokens):
    """Return ``(first, middle, last)`` when ``tokens`` read "Surname Given [initials]", else None."""
    t0 = tokens[0]
    if len(_evidence_key(t0)) < 2 or _NP_INITIAL_TOKEN.match(t0) or t0.lower() in _NP_COMPOUND_PREFIXES:
        return None
    scores = _name_evidence()[0]
    rest = tokens[1:]
    dotted = [bool(re.fullmatch(r'[A-Z]\.', t)) for t in rest]
    if len(rest) >= 2 and all(dotted):
        # "Klimanov V. V.": every later token is a dotted initial
        if scores.get(_evidence_key(t0), 0.0) < _AUTO_INITIAL_GUARD:
            return rest[0], ' '.join(rest[1:]), t0
        return None
    if len(tokens) == 3 and _NP_INITIAL_TOKEN.match(tokens[2]) and not _NP_INITIAL_TOKEN.match(tokens[1]):
        # "Smith John P.": judge the two words like a plain two-token name
        if _auto_order(t0, tokens[1])[0]:
            return tokens[1], tokens[2], t0
    return None

# ── List-level order inference ──
# Authors of one paper or one record usually share a convention, so the names
# of a list vote together. Each name contributes a signed number: positive when
# it reads "Last First", negative when it reads "First Last", zero when it has
# no opinion. A list is read "Last First" only when the weight of that
# evidence clearly outweighs the opposite, which keeps correctly ordered lists
# on the default parse.

_LIST_VOTE_CLIP = 4.0   # one name never counts for more than this
_LIST_VOTE_CJK = 2.0    # a CJK-family surname next to a non-surname
_LIST_MARGIN = 4.0      # net evidence needed before a list is read "Last First"

# ── Compound (double) surnames ──
# "Maria Garcia Lopez", "Juan Carlos Perez Gomez": two surname-like tokens at the
# end of a name usually form one surname. Decided from the lexicon scores of the
# last three tokens; the cut-offs were chosen on half of the gold set and checked
# on the other half.

_BIN_STRONG = 2.0   # at least this given-name-like: 'G'
_BIN_SURNAME = -3.0  # at most this surname-like: 'S'

def _score_bin(token):
    score = _name_evidence()[0].get(_evidence_key(token), 0.0)
    if score >= _BIN_STRONG:
        return 'G'
    if score > 0:
        return 'g'
    if score == 0:
        return '0'
    return 's' if score > _BIN_SURNAME else 'S'

def _np_has_double_surname(tokens):
    """True when the last two of ``tokens`` read as one compound surname."""
    if len(tokens) < 3:
        return False
    tail = tokens[-3:]
    if len(tokens[0].rstrip('.')) < 2:
        return False
    for tok in tail:
        plain = _np_clean(tok)
        if not plain or len(plain) < 3 or not re.fullmatch(r"[a-z]+(?:[-'][a-z]+)*", plain):
            return False
    if any(t.lower().rstrip('.') in _NP_COMPOUND_PREFIXES for t in tokens):
        return False
    before, penult, final = (_score_bin(t) for t in tail)
    if len(tokens) > 3:
        # "Maria Isabel Garcia Lopez": the token before the pair must be given-name-like too
        if _score_bin(tokens[0]) != 'G' or before != 'G':
            return False
        return (penult == 'S' and final in 'Ss0g') or (penult in '0s' and final == 'S')
    if penult != 'S':
        return False
    # "Maria Garcia Lopez": a given-name-like first token, or an unknown one when the last is a clear surname
    if final == 'S' and before in 'G0s':
        return True
    # Accents point to Iberian naming, where a second surname is the norm.
    return not ''.join(tokens).isascii() and before == 'G' and final in 'Ss0g'

def _order_signal(raw):
    if ',' in raw or not raw.isascii():
        return 0.0
    tokens = raw.split()
    if len(tokens) < 2 or len(tokens) > 3:
        return 0.0
    if len(tokens) == 3 and not _NP_INITIAL_TOKEN.match(tokens[1]):
        return 0.0  # "Anna Maria Rossi": too ambiguous to vote
    t0, t1 = tokens[0], tokens[-1]
    scores, pinyin = _name_evidence()
    a, b = _evidence_key(t0), _evidence_key(t1)
    cjk_surnames = _NP_CHINESE_ROMANIZED_SURNAMES | _NP_KOREAN_ROMANIZED_SURNAMES | pinyin
    a_cjk, b_cjk = a in cjk_surnames, b in cjk_surnames
    if a_cjk != b_cjk and len(a) >= 2 and len(b) >= 2 and not (t0.isupper() and t1.isupper()):
        return _LIST_VOTE_CJK if a_cjk else -_LIST_VOTE_CJK
    if a_cjk and b_cjk:
        return 0.0
    t0_initial, t1_initial = bool(_NP_INITIAL_TOKEN.match(t0)), bool(_NP_INITIAL_TOKEN.match(t1))
    if t0_initial and not t1_initial:
        return -_LIST_VOTE_CLIP
    if t1_initial and not t0_initial and len(a) >= 2:
        return 2.0 if scores.get(a, 0.0) < _AUTO_INITIAL_GUARD else -1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    if len(t0) >= 3 and t0.isupper() and not t1.isupper():
        return 2.0
    return max(-_LIST_VOTE_CLIP, min(_LIST_VOTE_CLIP, scores.get(b, 0.0) - scores.get(a, 0.0)))

def infer_name_order(names):
    """Return ``"first_last"`` or ``"last_first"`` for a list of names that share a convention.

    The default is ``"first_last"``; the list is read ``"last_first"`` only when
    its names, taken together, lean clearly that way.
    """
    signals = [_order_signal(n) for n in names if n]
    last_first = sum(s for s in signals if s > 0)
    first_last = -sum(s for s in signals if s < 0)
    return 'last_first' if last_first - first_last > _LIST_MARGIN else 'first_last'

def parse_names(names, name_order='auto'):
    """Parse the names of one record (e.g. one paper's authors).

    ``name_order="auto"`` infers one order for the whole list with
    :func:`infer_name_order`, so a list written "First Last" parses exactly like
    :func:`parse_name`, and a list written "Last First" is read consistently.
    ``"first_last"`` and ``"last_first"`` are applied to every name.
    """
    names = list(names)
    if name_order not in _NAME_ORDERS:
        raise ValueError(f"name_order must be one of {_NAME_ORDERS}, got {name_order!r}")
    order = infer_name_order(names) if name_order == 'auto' else name_order
    return [_np_parse_name(n, name_order=order) for n in names]

# ── Cyrillic patronymic endings ──

_NP_PATRONYMIC_ENDINGS = (
    'ович', 'евич', 'ивич', 'овна', 'евна', 'ивна',
    'овича', 'евича', 'івна', 'іївна', 'ївна', 'ійович',
)

# ── Output helper ──

_GLUE_BOUNDARY = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_GLUE_PREFIXES = frozenset(['mc', 'mac', 'de', 'di', 'da', 'del', 'la', 'le', 'van', 'von', 'o', 'd', 'st',
                            'al', 'el', 'ben', 'bin', 'ter', 'ten', 'der', 'den', 'du', 'dos', 'das'])


def _np_split_glued(token):
    """Split a single glued token ("YuanWang", "GuoxingZHANG", "SungKunKim").

    Returns ``(first, middle, last)`` or None. Only a camel-case or lower-to-CAPS
    boundary is used, never a guess inside a lowercase run, and the two-word case
    needs a clear pinyin surname at the end."""
    if not (token.isascii() and token.isalpha()):
        return None
    parts = _GLUE_BOUNDARY.split(token)
    if not 2 <= len(parts) <= 3 or any(len(p) < 2 for p in parts):
        return None
    if parts[0].lower() in _GLUE_PREFIXES:
        return None
    keys = [p.lower() for p in parts]
    if len(parts) == 2:
        a, b = parts
        if b.isupper() and not a.isupper():
            return a, None, b
        if a.isupper() or b.isupper():
            return None
        scores = _name_evidence()[0]
        sa, sb = scores.get(keys[0], 0.0), scores.get(keys[1], 0.0)
        # Only the pinyin case is decisive: a clear surname closing a pinyin-shaped word.
        # Western pairs ("LightMart", "VojnovicMilan") are left whole; no word list can
        # tell a brand from a name, and a split that is wrong costs more than none.
        if keys[1] in _NP_CHINESE_ROMANIZED_SURNAMES and sb <= -2.0 and sa - sb >= 3.0 \
                and keys[0] not in _NP_KOREAN_ROMANIZED_SURNAMES and _np_pinyin_segmentable(keys[0]):
            return a, None, b
        return None
    if any(p.isupper() for p in parts):
        return None
    if keys[2] in _NP_KOREAN_ROMANIZED_SURNAMES and keys[1] not in _NP_KOREAN_ROMANIZED_SURNAMES:
        return parts[0], parts[1], parts[2]
    return None


def _np_out(title=None, first=None, middle=None, last=None, suffix=None,
            nickname=None, confidence='high'):
    return {
        "title": title, "first": first, "middle": middle,
        "last": last, "suffix": suffix, "nickname": nickname,
        "confidence": confidence,
    }

# ── CJK parsers ──

def _np_is_likely_japanese(name):
    chars = name.replace(' ', '')
    if not chars:
        return False
    for length in [2, 3, 1]:
        if length <= len(chars):
            candidate = chars[:length]
            if candidate in _NP_JAPANESE_SURNAMES:
                if length >= 2:
                    return True
                if candidate not in _NP_CHINESE_SURNAMES:
                    return True
    return False

def _np_pykakasi_romanize(text):
    global _kks
    if _kks is None:
        if _kakasi is None:
            return text
        _kks = _kakasi()
    results = _kks.convert(text)
    rom = ''.join(r['hepburn'] for r in results).strip()
    rom = rom.replace('uu', 'u').replace('ou', 'o')
    return rom

def _np_parse_cjk(raw):
    r = raw.strip()
    has_kana = any(0x3040 <= ord(c) <= 0x30FF for c in r)
    if has_kana:
        return _np_parse_japanese(r)
    if _np_is_likely_japanese(r):
        return _np_parse_japanese(r)
    return _np_parse_chinese(r)

def _np_parse_japanese(raw):
    global _kks
    if _kks is None:
        if _kakasi is None:
            return _np_out(confidence='low')
        _kks = _kakasi()
    r = raw.strip()
    if ' ' in r:
        parts = r.split(None, 1)
        p1_is = parts[0] in _NP_JAPANESE_SURNAMES
        p2_is = parts[1] in _NP_JAPANESE_SURNAMES
        if p2_is and not p1_is:
            family_part, given_part = parts[1], parts[0]
        else:
            family_part, given_part = parts[0], parts[1]
        family_rom = _np_pykakasi_romanize(family_part)
        given_rom = _np_pykakasi_romanize(given_part)
        conf = 'medium' if (p1_is or p2_is) else 'low'
        return _np_out(first=_np_clean(given_rom), last=_np_clean(family_rom), confidence=conf)
    else:
        results = _kks.convert(r)
        romanized = [x['hepburn'] for x in results if x['hepburn'].strip()]
        if len(romanized) >= 2:
            return _np_out(first=_np_clean(' '.join(romanized[1:])),
                           last=_np_clean(romanized[0]), confidence='medium')
        elif len(romanized) == 1:
            return _np_out(last=_np_clean(romanized[0]), confidence='low')
        return _np_out(confidence='low')

def _np_parse_chinese(raw):
    r = raw.strip()
    pinyin = _unidecode(r).strip()
    pinyin = re.sub(r'\s+', ' ', pinyin).strip()
    tokens = pinyin.split()
    if not tokens:
        return _np_out(confidence='low')
    surname = _np_clean(tokens[0])
    given = _np_clean(''.join(tokens[1:])) if len(tokens) > 1 else None
    return _np_out(first=given, last=surname, confidence='medium')

def _np_safe_korean_romanize(text):
    """Romanize Korean text, falling back to unidecode if korean_romanizer crashes."""
    if not text:
        return None
    if _Romanizer is not None:
        try:
            return _Romanizer(text).romanize().strip()
        except (KeyError, Exception):
            pass
    return _unidecode(text).strip()

def _np_parse_hangul(raw):
    r = raw.strip()
    chars = list(r.replace(' ', ''))
    if not chars:
        return _np_out()
    surname_char = chars[0]
    given_chars = ''.join(chars[1:])
    surname_rom = _NP_KOREAN_SURNAME_MAP.get(surname_char)
    if surname_rom is None:
        surname_rom = _np_safe_korean_romanize(surname_char)
    given_rom = _np_safe_korean_romanize(given_chars) if given_chars else None
    return _np_out(first=_np_clean(given_rom), last=_np_clean(surname_rom), confidence='medium')

def _np_parse_cyrillic(raw):
    r = raw.strip()
    tokens = r.split()
    if len(tokens) == 3:
        last_tok = tokens[-1]
        if any(last_tok.endswith(end) for end in _NP_PATRONYMIC_ENDINGS):
            return _np_out(first=_np_clean(_unidecode(tokens[1])),
                           middle=_np_clean(_unidecode(tokens[2])),
                           last=_np_clean(_unidecode(tokens[0])), confidence='medium')
        mid_tok = tokens[1]
        if any(mid_tok.endswith(end) for end in _NP_PATRONYMIC_ENDINGS):
            return _np_out(first=_np_clean(_unidecode(tokens[0])),
                           middle=_np_clean(_unidecode(tokens[1])),
                           last=_np_clean(_unidecode(tokens[2])), confidence='medium')
    romanized = _unidecode(r)
    result = _np_parse_latin(romanized)
    if result['confidence'] == 'high':
        result['confidence'] = 'medium'
    return result

_NP_ARABIC_MAP = {
    'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'a',
    'ب': 'b', 'ت': 't', 'ث': 'th',
    'ج': 'j', 'ح': 'h', 'خ': 'kh',
    'د': 'd', 'ذ': 'dh',
    'ر': 'r', 'ز': 'z',
    'س': 's', 'ش': 'sh',
    'ص': 's', 'ض': 'd',
    'ط': 't', 'ظ': 'z',
    'ع': 'a', 'غ': 'gh',
    'ف': 'f', 'ق': 'q',
    'ك': 'k', 'ک': 'k',
    'ل': 'l', 'م': 'm', 'ن': 'n',
    'ه': 'h', 'ة': 'a',
    'و': 'w', 'ي': 'y', 'ى': 'a',
    'ئ': 'i', 'ؤ': 'u',
    'ء': '',
    'پ': 'p', 'چ': 'ch', 'ژ': 'zh', 'گ': 'g',
    'ی': 'y',
    '\u064e': 'a', '\u064f': 'u', '\u0650': 'i',
    '\u0651': '', '\u0652': '',
}

def _np_arabic_romanize(text):
    result = []
    for c in text:
        if c in _NP_ARABIC_MAP:
            result.append(_NP_ARABIC_MAP[c])
        elif c == ' ':
            result.append(' ')
        elif c in ('-', '/'):
            result.append(' ')
        elif ord(c) < 128:
            result.append(c)
    rom = ''.join(result)
    rom = re.sub(r'\s+', ' ', rom).strip()
    return rom

def _np_parse_arabic(raw):
    r = raw.strip()
    romanized = _np_arabic_romanize(r)
    if not romanized or not any(c.isalpha() for c in romanized):
        return _np_out(confidence='low')
    result = _np_parse_latin(romanized)
    result['confidence'] = 'medium'
    return result

# ── Latin parser ──

def _np_is_author_list(segs):
    """True when comma segments read as several people ("Verena Menz, Mona Semsch, Florian
    Mosbach") rather than "Surname, Given, extra". Needs three or more multi-word segments, and a
    first segment that does not start with a surname particle ("Van Der Berg, Jan, Antwerp")."""
    head = segs[:3]
    if len(head) < 3 or not all(len(seg.split()) >= 2 for seg in head):
        return False
    return segs[0].split()[0].lower().rstrip('.') not in _NP_COMPOUND_PREFIXES



def _np_parse_latin(name, name_order='first_last'):
    r = name.strip()
    r = re.sub(r'\s+\d{4}-\s*$', '', r)
    r = re.sub(r';\s*id_orcid\s+[\d-]+', '', r)
    r = re.sub(r'id_orcid\s+[\d-]+', '', r)
    r = re.sub(r'(\w)\d+([,\s])', r'\1\2', r)
    r = re.sub(r'(\w)\d+$', r'\1', r)
    r = r.replace('\u2010', '-').replace('\u2011', '-').replace('\u2012', '-')
    r = r.replace('\u2013', '-').replace('\u2014', '-').replace('\u2015', '-')
    r = re.sub(r'-\s+', '-', r)
    r = re.sub(r'\s+-', '-', r)
    r = re.sub(r'\s*-\s*$', '', r)
    r = re.sub(r'^\s*-\s*', '', r)
    r = re.sub(r'\s+', ' ', r).strip()

    # Affiliations in brackets ("Kulesza, Joel A. [Los Alamos National Laboratory]") are not part
    # of the name, and neither are life dates ("Huber, Kaspar (1825 - 1882), Kupferstecher").
    r = re.sub(r'\s*\[[^\]]*\]', '', r)
    r, _life_dates = re.subn(r'\s*\(\s*\d{3,4}\s*-\s*\d{0,4}\s*\)', '', r)

    # "First Last; First Last; ..." keeps the first author. Done before the organisation test
    # so an affiliation riding along in a later part cannot turn a person into an organisation.
    if ';' in r:
        parts = r.split(';')
        if len(parts) >= 2 and len(parts[0].strip().split()) >= 2 and len(parts[1].strip().split()) >= 2:
            r = parts[0].strip()

    # Drop comma segments that are pure credentials + mid-stream cred tokens
    # before any first/last role assignment.
    r, _segment_creds = _np_drop_credential_segments(r)

    # Strip credentials BEFORE the dot-split, so trailing 'M.D.' / 'Ph.D.' /
    # 'M.S.' tokens are matched in compact form (the dot-split below would
    # otherwise turn 'M.D.' into 'M. D.' which the strip can't reassemble).
    r, _early_cred_suffix = _np_strip_credentials(r, None)
    if _segment_creds:
        _early_cred_suffix = ', '.join(([_early_cred_suffix] if _early_cred_suffix else []) + _segment_creds)

    r = re.sub(r'\.([A-Z])', r'. \1', r)

    # Bracketed text ("(Guest Editors)", "(IJMACR)") says nothing about whether the rest is a person.
    r_org = re.sub(r'\s+', ' ', re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', r)).strip()
    r_lower = r_org.lower()
    if any(kw in r_lower for kw in _NP_ORG_KEYWORDS) or (
            len(r_org.split()) >= 2
            and any(w in _NP_ORG_WORDS for w in re.findall(r'[a-z]+', _np_to_ascii(r_lower)))):
        return _np_out(last=_np_clean(r_org))

    et_al_match = re.search(r'\bet\s+al\.?\s*$', r, re.IGNORECASE)
    if et_al_match:
        r = r[:et_al_match.start()].strip()
    r = r.replace(';', '').strip()
    r = re.sub(r'\s+', ' ', r)

    nickname = None
    nick_match = re.search(r'\s*[\(\[](.*?)[\)\]]', r)
    if nick_match:
        nickname = _np_clean(nick_match.group(1))
        r = r[:nick_match.start()] + r[nick_match.end():]
        r = re.sub(r'\s+', ' ', r).strip()

    title = None
    for pattern, title_val in _NP_TITLE_PATTERNS:
        m = pattern.match(r)
        if m:
            title = title_val
            r = r[m.end():].strip()
            break
    else:
        m = _NP_COURTESY_TITLES.match(r)
        if m and len(r[m.end():].split()) >= 2:
            title = m.group(1).lower() + '.'
            r = r[m.end():].strip()

    suffix = None
    suffix_match = re.search(r',?\s+(Jr\.?|Junior|Júnior|Sr\.?|III|IV|II|Esq\.?)$', r, re.IGNORECASE)
    if suffix_match:
        suffix = _np_clean(suffix_match.group(1))
        r = r[:suffix_match.start()].strip()

    r, suffix = _np_strip_credentials(r, suffix)
    if _early_cred_suffix:
        suffix = f"{suffix}, {_early_cred_suffix}" if suffix else _early_cred_suffix

    # Re-run generational suffix regex in case a generational marker sat
    # between name and credentials (e.g. "Smith Jr., MD" -> strip MD -> Jr. now at end).
    if suffix_match is None or (suffix and ',' in suffix):
        suffix_match2 = re.search(r',?\s+(Jr\.?|Junior|Júnior|Sr\.?|III|IV|II|Esq\.?)$', r, re.IGNORECASE)
        if suffix_match2:
            gen = _np_clean(suffix_match2.group(1))
            r = r[:suffix_match2.start()].strip()
            suffix = f"{gen}, {suffix}" if suffix else gen

    if ',' in r:
        segs = [seg.strip() for seg in r.split(',') if seg.strip()]
        if len(segs) >= 3 and _NP_GENERATIONAL_RE.match(segs[1]):
            # "Farrell, Jr., Robert E": the generation marker sits between surname and given name.
            gen = _np_clean(segs.pop(1))
            suffix = f"{gen}, {suffix}" if suffix else gen
        elif len(segs) >= 2:
            tail = _NP_GENERATIONAL_TAIL.search(segs[0])
            if tail and len(segs[0].split()) >= 2:
                # "Knodler Jr, Michael"
                gen = _np_clean(tail.group(1))
                suffix = f"{gen}, {suffix}" if suffix else gen
                segs[0] = segs[0][:tail.start()].strip()
        if len(segs) >= 3 and _np_is_author_list(segs):
            r = segs[0]  # several people in one string: parse the first
        elif len(segs) >= 2:
            last_part = segs[0]
            first_part = segs[1] if _life_dates else ', '.join(segs[1:])
            ftokens = first_part.split()
            first = _np_clean(ftokens[0])
            middle = _np_clean(' '.join(ftokens[1:])) if len(ftokens) > 1 else None
            return _np_out(title=title, first=first, middle=middle, last=_np_clean(last_part),
                           suffix=suffix, nickname=nickname, confidence='high')
        else:
            r = segs[0] if segs else ''

    r = re.sub(r'\b([A-Z])\.-([A-Z])\.', r'\1. \2.', r)
    r = re.sub(r'^([A-Z])(\.?)-([A-Z])\b(\.?)(?=\s)', r'\1\2 \3\4', r)
    tokens = r.split()

    if len(tokens) == 0:
        return _np_out(last=_np_clean(r), suffix=suffix, nickname=nickname)
    if len(tokens) == 1:
        glued = _np_split_glued(tokens[0])
        if glued:
            return _np_out(title=title, first=_np_clean(glued[0]),
                           middle=_np_clean(glued[1]) if glued[1] else None,
                           last=_np_clean(glued[2]), suffix=suffix, nickname=nickname,
                           confidence='medium')
        return _np_out(title=title, last=_np_clean(tokens[0]), suffix=suffix,
                       nickname=nickname, confidence='high')

    tokens = _np_split_caps_initials(tokens)

    if name_order == 'last_first':
        # The caller says the surname leads ("Priem Jason R."): no guessing, and a
        # leading compound surname ("van der Berg Jan") stays together.
        i = 0
        while i < len(tokens) - 2 and tokens[i].lower().rstrip('.') in _NP_COMPOUND_PREFIXES:
            i += 1
        rest = tokens[i + 1:]
        return _np_out(title=title, first=_np_clean(rest[0]),
                       middle=_np_clean(' '.join(rest[1:])) if len(rest) > 1 else None,
                       last=_np_clean(' '.join(tokens[:i + 1])), suffix=suffix, nickname=nickname,
                       confidence='high')

    if len(tokens) == 2:
        t0_lower = tokens[0].lower().rstrip('.')
        t1_lower = tokens[1].lower().rstrip('.')
        if t0_lower in _NP_KOREAN_ROMANIZED_SURNAMES and '-' in tokens[1]:
            return _np_out(title=title, first=_np_clean(tokens[1]), last=_np_clean(tokens[0]),
                           suffix=suffix, nickname=nickname, confidence='high')
        if t1_lower in _NP_KOREAN_ROMANIZED_SURNAMES and '-' in tokens[0]:
            return _np_out(title=title, first=_np_clean(tokens[0]), last=_np_clean(tokens[1]),
                           suffix=suffix, nickname=nickname, confidence='high')
        # Chinese pinyin surname order detection (conservative — unambiguous surnames only).
        # Asymmetric: flip only when exactly one token is a known pinyin surname,
        # so "Li Yang" / "Yang Li" (both surnames) stays at the default and remains ambiguous.
        if t0_lower in _NP_CHINESE_ROMANIZED_SURNAMES and t1_lower not in _NP_CHINESE_ROMANIZED_SURNAMES:
            return _np_out(title=title, first=_np_clean(tokens[1]), last=_np_clean(tokens[0]),
                           suffix=suffix, nickname=nickname, confidence='high')
        if t1_lower in _NP_CHINESE_ROMANIZED_SURNAMES and t0_lower not in _NP_CHINESE_ROMANIZED_SURNAMES:
            return _np_out(title=title, first=_np_clean(tokens[0]), last=_np_clean(tokens[1]),
                           suffix=suffix, nickname=nickname, confidence='high')
        # Short pinyin surnames that double as ordinary words ("Lu", "He", "Wu", "Ou") are left out of
        # the set above, but "LU Qing" is still surname-first: the second token spells pinyin
        # syllables and is not a surname itself, while the first one is.
        a_key, b_key = _evidence_key(tokens[0]), _evidence_key(tokens[1])
        if (a_key in _name_evidence()[1] and b_key not in _name_evidence()[1] and len(b_key) >= 2
                and a_key not in _NP_KOREAN_ROMANIZED_SURNAMES and b_key not in _NP_KOREAN_ROMANIZED_SURNAMES
                and _name_evidence()[0].get(b_key, 0.0) < _PINYIN_GIVEN_GUARD
                and _np_pinyin_segmentable(tokens[1])):
            return _np_out(title=title, first=_np_clean(tokens[1]), last=_np_clean(tokens[0]),
                           suffix=suffix, nickname=nickname, confidence='medium')
        if name_order == 'auto':
            # Without a comma the order is a guess. Flipped parses are inferences,
            # so they are never 'high'; neither is a parse with no evidence behind it.
            swap, confident = _auto_order(tokens[0], tokens[1])
            if swap:
                return _np_out(title=title, first=_np_clean(tokens[1]), last=_np_clean(tokens[0]),
                               suffix=suffix, nickname=nickname, confidence='medium')
            return _np_out(title=title, first=_np_clean(tokens[0]), last=_np_clean(tokens[1]),
                           suffix=suffix, nickname=nickname, confidence='high' if confident else 'medium')
        return _np_out(title=title, first=_np_clean(tokens[0]), last=_np_clean(tokens[1]),
                       suffix=suffix, nickname=nickname, confidence='high')

    if name_order == 'auto' and len(tokens) >= 3:
        # "Klimanov V. V." / "Smith John P.": a surname followed by given names and initials.
        reordered = _np_auto_reorder_with_initials(tokens)
        if reordered:
            first, middle, last = reordered
            return _np_out(title=title, first=_np_clean(first), middle=_np_clean(middle) if middle else None,
                           last=_np_clean(last), suffix=suffix, nickname=nickname, confidence='medium')

    # "Dharunraja S R": South Indian style, where the surname is itself initials. Two or more
    # bare capitals at the end, after a real word, read as one surname.
    run = 0
    while run < len(tokens) - 1 and re.fullmatch(r'[A-Z]', tokens[-1 - run]):
        run += 1
    if run >= 2 and len(tokens[-run - 1].rstrip('.')) >= 2 and tokens[-run - 1].lower() not in _NP_COMPOUND_PREFIXES:
        head = tokens[:-run]
        return _np_out(title=title, first=_np_clean(head[0]),
                       middle=_np_clean(' '.join(head[1:])) if len(head) > 1 else None,
                       last=_np_clean(' '.join(tokens[-run:])), suffix=suffix, nickname=nickname,
                       confidence='medium')

    surname_start = len(tokens) - 1
    found_prefix = False
    while surname_start > 1:
        prev_tok = tokens[surname_start - 1]
        prev_stripped = prev_tok.rstrip('.')
        if len(prev_stripped) == 1 and prev_stripped[0].isupper():
            break
        if prev_stripped.lower() in _NP_COMPOUND_PREFIXES:
            surname_start -= 1
            found_prefix = True
        else:
            break

    if surname_start == len(tokens) - 1 and _np_has_double_surname(tokens):
        surname_start -= 1
    conf = 'high' if found_prefix else 'medium'
    first = _np_clean(tokens[0])
    if surname_start == 1:
        return _np_out(title=title, first=first, last=_np_clean(' '.join(tokens[1:])),
                       suffix=suffix, nickname=nickname, confidence=conf)
    middle = _np_clean(' '.join(tokens[1:surname_start]))
    last = _np_clean(' '.join(tokens[surname_start:]))
    return _np_out(title=title, first=first, middle=middle, last=last,
                   suffix=suffix, nickname=nickname, confidence=conf)

# ── Main entry point ──

def _np_parse_name(raw_author_name, name_order='first_last'):
    """New deterministic parser. Returns dict with confidence field."""
    if name_order not in _NAME_ORDERS:
        raise ValueError(f"name_order must be one of {_NAME_ORDERS}, got {name_order!r}")
    if not raw_author_name or not raw_author_name.strip():
        return _np_out()
    r = raw_author_name.strip()

    latin_alpha_count = sum(1 for c in r if c.isalpha() and ord(c) < 128)
    nonlatin_alpha_count = sum(1 for c in r if c.isalpha() and ord(c) >= 128)
    mostly_nonlatin = nonlatin_alpha_count > latin_alpha_count * 3

    if not _np_has_latin_alpha(r) or mostly_nonlatin:
        fixed = _np_fix_latin_lookalikes(r) if mostly_nonlatin else r
        if _np_has_cjk(fixed):
            return _np_parse_cjk(fixed)
        if _np_has_hangul(fixed):
            return _np_parse_hangul(fixed)
        if _np_has_cyrillic(fixed):
            return _np_parse_cyrillic(fixed)
        if _np_has_arabic(fixed):
            return _np_parse_arabic(fixed)
        return _np_out(last=_np_clean(fixed))

    if _np_has_cjk(r) or _np_has_hangul(r):
        latin_part = ''.join(c if (ord(c) < 128 or c in '-. ') else ' ' for c in r).strip()
        latin_part = re.sub(r'\s+', ' ', latin_part).strip()
        if latin_part:
            return _np_parse_latin(latin_part)
        return _np_out(confidence='low')

    if _np_has_cyrillic(r):
        fixed = _np_fix_cyrillic_lookalikes(r)
        if _np_has_latin_alpha(fixed) and not _np_has_cyrillic(fixed):
            return _np_parse_latin(fixed)
        latin_part = ''.join(c if (ord(c) < 128 or c in '-. ') else ' ' for c in fixed).strip()
        latin_part = re.sub(r'\s+', ' ', latin_part).strip()
        if latin_part:
            return _np_parse_latin(latin_part)
        return _np_out(confidence='low')

    if _np_has_arabic(r):
        latin_part = ''.join(c if (ord(c) < 128 or c in '-. ') else ' ' for c in r).strip()
        latin_part = re.sub(r'\s+', ' ', latin_part).strip()
        if latin_part:
            return _np_parse_latin(latin_part)
        return _np_out(confidence='low')

    return _np_parse_latin(r, name_order=name_order)


# ══════════════════════════════════════════════════════════════════════════════
# Router: new parser with HNP fallback for low confidence
# ══════════════════════════════════════════════════════════════════════════════

def parse_human_name(name):
    """
    Parse a name using the new deterministic parser (v2.2).
    Falls back to HNP when confidence is low.
    Returns dict with: title, first, middle, last, suffix, nickname.
    Values are strings (empty string for missing, matching original convention).
    """
    result = _np_parse_name(name)
    if result['confidence'] == 'low':
        result = parse_human_name_hnp(name)

    last = _strip_surname_particles(result.get('last') or '')

    return {
        'title': result.get('title') or '',
        'first': result.get('first') or '',
        'middle': result.get('middle') or '',
        'last': last,
        'suffix': result.get('suffix') or '',
        'nickname': result.get('nickname') or '',
    }




# ── CLI ──

def _cli():
    """Parse a name from argv, or JSONL/plain names from stdin."""
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(_np_parse_name(' '.join(sys.argv[1:])), ensure_ascii=False, indent=2))
        return
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            rec = json.loads(raw_line)
            raw = rec.get('raw_author_name', raw_line)
        except json.JSONDecodeError:
            raw = raw_line
        result = _np_parse_name(raw)
        result['raw_author_name'] = raw
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    _cli()
