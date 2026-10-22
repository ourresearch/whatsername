"""whatsername — figure out what someone's name actually is.

A deterministic author-name parser extracted from the OpenAlex author entity
resolution pipeline. Splits a raw name string into structured components.

    >>> from whatsername import parse_name
    >>> parse_name("John Maynard Smith")
    {'title': None, 'first': 'john', 'middle': 'maynard', 'last': 'smith',
     'suffix': None, 'nickname': None, 'confidence': 'medium'}

Two entry points:

- ``parse_name(s)``  — rich parse, returns a ``confidence`` field so callers can
  threshold and route low-confidence names to an LLM. Recommended default.
- ``parse_human_name(s)`` — the exact form OpenAlex uses internally: 6 string
  fields (empty string for missing), surname particles stripped from ``last``,
  with a HumanNameParser fallback for low-confidence cases.
"""

from .parser import (
    infer_name_order,
    parse_human_name,
    parse_human_name_hnp,
    parse_names,
)
from .parser import _np_parse_name as _np_parse_name

__all__ = ["parse_name", "parse_names", "infer_name_order", "parse_human_name", "parse_human_name_hnp"]

__version__ = "0.1.0"


def parse_name(raw_author_name, name_order="first_last"):
    """Parse a raw author-name string into structured components.

    Returns a dict with keys: ``title``, ``first``, ``middle``, ``last``,
    ``suffix``, ``nickname`` (lowercase ASCII, or ``None`` if absent) and
    ``confidence`` (``"high"`` | ``"medium"`` | ``"low"``).

    Threshold on ``confidence`` to reject uncertain parses and route them to a
    stronger model.

    ``name_order`` says how to read Latin-script names that have no comma:

    - ``"first_last"`` (default): the given name comes first, apart from the
      Chinese and Korean surname rules that have always applied.
    - ``"last_first"``: the caller knows the surname leads ("Priem Jason R.").
    - ``"auto"``: for corpora that mix "First Last" and "Last First". Two-token
      names are weighed on how given-name-like and surname-like each token
      looks, plus a lone trailing initial ("Priem J.") and an upper-cased
      leading surname ("PRIEM Jason"). It flips only on clear evidence and
      marks every flipped or unsupported parse ``confidence="medium"``.
    """
    return _np_parse_name(raw_author_name, name_order=name_order)
