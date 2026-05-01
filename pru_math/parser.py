"""Input parser for the PRU math engine.

Three input formats are accepted, tried in order:

1. **SymPy syntax** (``sympify``). Examples: ``x**2 + 3*x - 4``,
   ``Eq(x**2 - 5*x + 6, 0)``, ``Integral(x**2, (x, 0, 1))``.
2. **LaTeX** via :func:`sympy.parsing.latex.parse_latex`. Examples:
   ``\\int_{0}^{1} x^2 dx``, ``\\sin(x)^2 + \\cos(x)^2``.
3. **Natural language** via Ollama. The model is prompted to emit a
   structured JSON object describing the problem type, SymPy expression,
   and (if applicable) the target variable / bounds. This JSON is then
   validated and sympified.

If an input can be parsed by an earlier route we do not consult the LLM.
The LLM never decides math; it only translates language into SymPy.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
    convert_xor,
)

from . import problem_types as PT
from .config import CONFIG


TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)


@dataclass
class ParsedProblem:
    """The structured result of parsing user input."""

    raw_input: str
    source_format: str           # "sympy" | "latex" | "natural_language"
    problem_type: str            # one of PT.*
    expression: sp.Basic         # canonical SymPy expression (may be Eq/Integral/etc.)
    target_symbol: sp.Symbol | None = None
    extra: dict[str, Any] | None = None   # e.g. integration bounds, series point

    def expr_repr(self) -> str:
        return sp.srepr(self.expression)

    def pretty(self) -> str:
        return sp.sstr(self.expression)


class ParseError(ValueError):
    """Raised when no parsing path succeeds."""


# --- Problem-type inference -------------------------------------------------

_NL_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (PT.INTEGRATE,     re.compile(r"\b(integrate|integral|antideriv)", re.I)),
    (PT.DIFFERENTIATE, re.compile(r"\b(differentiat|derivative|d/d\w+)", re.I)),
    (PT.LIMIT,         re.compile(r"\blimit\b", re.I)),
    (PT.SERIES,        re.compile(r"\b(taylor|maclaurin|series expansion)", re.I)),
    (PT.FACTOR,        re.compile(r"\bfactor(iz|ise|)", re.I)),
    (PT.EXPAND,        re.compile(r"\bexpand\b", re.I)),
    (PT.SIMPLIFY,      re.compile(r"\bsimplif(y|ied|ication)", re.I)),
    (PT.EVALUATE,      re.compile(r"\b(evaluate|compute the value|numerical value)", re.I)),
    (PT.SOLVE,         re.compile(r"\b(solve|find .* such that|root|zeros?)\b", re.I)),
    (PT.PROVE,         re.compile(r"\b(prove|show that|verify .* identity)", re.I)),
]


def infer_type_from_text(text: str) -> str:
    for ptype, pat in _NL_TYPE_PATTERNS:
        if pat.search(text):
            return ptype
    return PT.UNKNOWN


def infer_type_from_expr(expr: sp.Basic) -> str:
    """Infer a problem type from the wrapping SymPy object."""
    if isinstance(expr, sp.Equality):
        return PT.SOLVE
    if isinstance(expr, sp.Integral):
        return PT.INTEGRATE
    if isinstance(expr, sp.Derivative):
        return PT.DIFFERENTIATE
    if isinstance(expr, sp.Limit):
        return PT.LIMIT
    if isinstance(expr, sp.Sum):
        return PT.EVALUATE
    return PT.SIMPLIFY


# --- SymPy-syntax path ------------------------------------------------------

_NL_KEYWORD_PATTERN = re.compile(
    r"\b(solve|integrate|integral|differentiate|derivative|simplify|factor|"
    r"expand|evaluate|compute|find|prove|show|limit|series|root|zero|"
    r"what|how|the|equals?|equal|plus|minus|times|over|squared|cubed|"
    r"approaches?)\b",
    re.I,
)


def _looks_like_nl_artifact(text: str, expr: sp.Basic) -> bool:
    """Heuristic: detect when SymPy's parser ate a natural-language phrase
    by splitting words into single-letter symbol products.

    Example: "solve 2 + 5" parses to ``s*o*l*v*e*2 + 5`` because
    ``implicit_multiplication_application`` includes ``split_symbols``.
    Such results carry no operator the user typed.

    Returns True if the input contains an NL keyword (solve/integrate/etc.)
    AND the parsed expression contains a Mul of multiple single-letter
    Symbols that the user did not actually write as a product.
    """
    if not _NL_KEYWORD_PATTERN.search(text):
        return False
    # If the user typed any '*', they meant multiplication — trust the parse.
    if "*" in text:
        return False
    # Walk the expression tree looking for a Mul of >=2 single-letter Symbols.
    for node in sp.preorder_traversal(expr):
        if isinstance(node, sp.Mul):
            single_letter_syms = [
                a for a in node.args
                if isinstance(a, sp.Symbol) and len(a.name) == 1
            ]
            if len(single_letter_syms) >= 2:
                # Verify those letters appear consecutively in the input as a
                # word (so we know SymPy split it). This avoids false positives
                # on legitimate inputs like "x*y*z".
                names = [s.name for s in single_letter_syms]
                joined = "".join(sorted(names))
                # Look for any 2+ letter word in the input whose sorted letters
                # contain those single-letter symbol names.
                for word in re.findall(r"[A-Za-z]{2,}", text):
                    if all(c in word.lower() for c in joined):
                        return True
    return False


def try_parse_sympy(text: str) -> sp.Basic | None:
    """Parse as SymPy source. Returns ``None`` on failure — never raises."""
    stripped = text.strip()
    if not stripped:
        return None
    # The raw SymPy constructors (Eq, Integral, Derivative, ...) need a local
    # dict so ``sympify`` treats them as callables rather than symbols.
    local_dict = {
        name: getattr(sp, name)
        for name in ("Eq", "Integral", "Derivative", "Limit", "Sum",
                     "Matrix", "Rational", "Symbol", "Function",
                     "sin", "cos", "tan", "exp", "log", "sqrt", "pi", "E", "I", "oo")
    }
    try:
        expr = parse_expr(stripped, local_dict=local_dict,
                          transformations=TRANSFORMATIONS, evaluate=False)
    except Exception:
        # sympy can raise a wide zoo: SympifyError, TokenError, SyntaxError,
        # AttributeError (when a wrapper class like Eq rejects weird args),
        # TypeError, ValueError, and more. Any of these means "not SymPy syntax".
        return None
    # Reject natural-language artifacts: e.g. "solve 2 + 5" parsed as
    # `s*o*l*v*e*2 + 5`. Falls through to LaTeX/NL paths in `parse()`.
    if _looks_like_nl_artifact(stripped, expr):
        return None
    return expr


# --- LaTeX path -------------------------------------------------------------

def _strip_latex_wrappers(text: str) -> str:
    s = text.strip()
    # Strip common wrappers: $...$, $$...$$, \(...\), \[...\]
    for left, right in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        if s.startswith(left) and s.endswith(right) and len(s) >= len(left) + len(right):
            s = s[len(left): -len(right)].strip()
            break
    return s


def try_parse_latex(text: str) -> sp.Basic | None:
    try:
        from sympy.parsing.latex import parse_latex
    except Exception:
        return None
    candidate = _strip_latex_wrappers(text)
    if not candidate:
        return None
    # Heuristic: LaTeX usually contains a backslash command.
    if "\\" not in candidate:
        return None
    try:
        return parse_latex(candidate)
    except Exception:
        return None


# --- Natural language path (Ollama) -----------------------------------------

_OLLAMA_SYSTEM_PROMPT = """You translate a natural-language math question into a structured JSON object.

Output ONLY a single JSON object, with no surrounding prose and no code fences. Use this schema:

{
  "problem_type": one of ["solve","simplify","integrate","differentiate","factor","evaluate","expand","limit","series","prove","unknown"],
  "expression": a SymPy-parseable string. For SOLVE problems use "Eq(lhs, rhs)".
                For INTEGRATE use "Integral(f, (x, a, b))" for definite or "Integral(f, x)" for indefinite.
                For DIFFERENTIATE use "Derivative(f, x)".
                For LIMIT use "Limit(f, x, point)".
  "target": the primary variable name as a string, or null if not applicable.
  "extra": any additional metadata as a JSON object, or null.
}

Do not solve the problem. Only produce the JSON.
""".strip()


def _ollama_chat(prompt: str, *, system: str, model: str, host: str,
                 timeout: float) -> str:
    """Blocking call to Ollama's /api/chat. Returns the model's text content."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    resp = httpx.post(f"{host.rstrip('/')}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("message") or {}).get("content", "")


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first top-level JSON object from a string."""
    text = text.strip()
    # Fenced code blocks: strip them leniently.
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    # Find the first balanced {...}.
    start = text.find("{")
    if start == -1:
        raise ParseError("LLM response contained no JSON object")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start: i + 1])
    raise ParseError("LLM response had unbalanced JSON braces")


def try_parse_natural_language(
    text: str,
    *,
    enabled: bool | None = None,
    model: str | None = None,
    host: str | None = None,
    timeout: float | None = None,
) -> ParsedProblem | None:
    """Call Ollama and convert the result to a ``ParsedProblem``. Returns
    ``None`` if Ollama is disabled or unreachable, so the caller can surface
    a clean error rather than a stack trace."""
    enabled = CONFIG.ollama_enabled if enabled is None else enabled
    if not enabled:
        return None
    model = model or CONFIG.ollama_model
    host = host or CONFIG.ollama_host
    # NL parsing via Ollama is independent from tool_timeout_s — reasoning
    # models (deepseek-r1, gpt-oss) emit chain-of-thought before the JSON,
    # so the SymPy-tool budget of ~20s is too tight. Allow up to 60s by
    # default, overridable per call.
    if timeout is None:
        timeout = max(CONFIG.tool_timeout_s, 60.0)

    try:
        raw = _ollama_chat(text, system=_OLLAMA_SYSTEM_PROMPT,
                           model=model, host=host, timeout=timeout)
    except (httpx.HTTPError, OSError):
        return None

    obj = _extract_json_object(raw)
    ptype = str(obj.get("problem_type") or PT.UNKNOWN).lower()
    if ptype not in PT.ALL:
        ptype = PT.UNKNOWN
    expr_str = obj.get("expression")
    if not isinstance(expr_str, str) or not expr_str.strip():
        raise ParseError("LLM did not return an 'expression' string")
    expr = try_parse_sympy(expr_str)
    if expr is None:
        raise ParseError(f"LLM emitted unparseable expression: {expr_str!r}")

    target_name = obj.get("target")
    target = sp.Symbol(target_name) if isinstance(target_name, str) and target_name else None
    return ParsedProblem(
        raw_input=text,
        source_format="natural_language",
        problem_type=ptype,
        expression=expr,
        target_symbol=target,
        extra=obj.get("extra") if isinstance(obj.get("extra"), dict) else None,
    )


# --- Top-level entry --------------------------------------------------------

def _default_target(expr: sp.Basic) -> sp.Symbol | None:
    """Pick a reasonable default target variable for the problem."""
    if isinstance(expr, sp.Integral):
        # Integral.variables is a tuple of integration symbols.
        return expr.variables[0] if expr.variables else None
    if isinstance(expr, sp.Derivative):
        return expr.variables[0] if expr.variables else None
    if isinstance(expr, sp.Limit):
        return expr.args[1] if len(expr.args) > 1 else None
    free = sorted(expr.free_symbols, key=lambda s: s.name)
    return free[0] if len(free) == 1 else None


def parse(text: str) -> ParsedProblem:
    """Parse user input into a :class:`ParsedProblem`.

    Order: SymPy syntax → LaTeX → natural language. The first success wins.
    Raises :class:`ParseError` if nothing succeeds.
    """
    if text is None or not text.strip():
        raise ParseError("empty input")

    # 1. SymPy
    expr = try_parse_sympy(text)
    if expr is not None:
        return ParsedProblem(
            raw_input=text,
            source_format="sympy",
            problem_type=infer_type_from_expr(expr),
            expression=expr,
            target_symbol=_default_target(expr),
        )

    # 2. LaTeX
    expr = try_parse_latex(text)
    if expr is not None:
        return ParsedProblem(
            raw_input=text,
            source_format="latex",
            problem_type=infer_type_from_expr(expr),
            expression=expr,
            target_symbol=_default_target(expr),
        )

    # 3. Natural language
    nl = try_parse_natural_language(text)
    if nl is not None:
        return nl

    raise ParseError(
        "could not parse input as SymPy, LaTeX, or natural language. "
        "If this is a word problem, ensure Ollama is running and OLLAMA_ENABLED=true."
    )
