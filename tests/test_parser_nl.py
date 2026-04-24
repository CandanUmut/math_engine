"""Natural-language parser path — mocked so we don't need a running Ollama."""
from __future__ import annotations

import json
from unittest.mock import patch

from pru_math import problem_types as PT
from pru_math.parser import try_parse_natural_language


FAKE_JSON = {
    "problem_type": "integrate",
    "expression": "Integral(x**2, (x, 0, 1))",
    "target": "x",
    "extra": None,
}


def test_nl_parser_mocked():
    with patch("pru_math.parser._ollama_chat", return_value=json.dumps(FAKE_JSON)):
        parsed = try_parse_natural_language("integrate x squared from zero to one", enabled=True)
    assert parsed is not None
    assert parsed.source_format == "natural_language"
    assert parsed.problem_type == PT.INTEGRATE
    assert parsed.target_symbol.name == "x"


def test_nl_parser_strips_code_fences():
    wrapped = "```json\n" + json.dumps(FAKE_JSON) + "\n```"
    with patch("pru_math.parser._ollama_chat", return_value=wrapped):
        parsed = try_parse_natural_language("whatever", enabled=True)
    assert parsed is not None
    assert parsed.problem_type == PT.INTEGRATE


def test_nl_parser_disabled_returns_none():
    out = try_parse_natural_language("integrate x", enabled=False)
    assert out is None
