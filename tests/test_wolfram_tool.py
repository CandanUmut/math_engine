"""Wolfram tool — gating, no-network behaviour, mock-based smoke."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pru_math.parser import parse
from pru_math.tools.wolfram_tool import WolframTool


def test_unavailable_when_app_id_missing(monkeypatch):
    monkeypatch.delenv("WOLFRAM_APP_ID", raising=False)
    tool = WolframTool()
    assert tool.is_available() is False
    assert tool.candidate_approaches("solve") == []
    assert tool.can_handle({"problem_type": "solve"}) == 0.0


def test_solve_with_returns_error_when_no_key(monkeypatch):
    monkeypatch.delenv("WOLFRAM_APP_ID", raising=False)
    tool = WolframTool()
    res = tool.solve_with(parse("Eq(x**2 - 4, 0)"), "wolfram.short")
    assert res.success is False
    assert "WOLFRAM_APP_ID" in (res.error or "")


def test_short_call_with_mock(monkeypatch):
    monkeypatch.setenv("WOLFRAM_APP_ID", "TEST_KEY")
    tool = WolframTool()
    assert tool.is_available()
    with patch("pru_math.tools.wolfram_tool._short", return_value="x = 2 or x = 3"):
        res = tool.solve_with(parse("Eq(x**2 - 5*x + 6, 0)"), "wolfram.short")
    assert res.success
    assert res.result_pretty == "x = 2 or x = 3"
    assert res.tool == "wolfram"


def test_full_call_with_mock(monkeypatch):
    monkeypatch.setenv("WOLFRAM_APP_ID", "TEST_KEY")
    tool = WolframTool()
    with patch("pru_math.tools.wolfram_tool._full",
               return_value="Result:\nx = 2\nx = 3"):
        res = tool.solve_with(parse("Eq(x**2 - 5*x + 6, 0)"), "wolfram.full")
    assert res.success
    assert "x = 2" in res.result_pretty
    assert "x = 3" in res.result_pretty


def test_does_not_offer_to_cross_verify(monkeypatch):
    monkeypatch.setenv("WOLFRAM_APP_ID", "TEST_KEY")
    tool = WolframTool()
    assert tool.can_cross_verify(parse("Eq(x**2 - 4, 0)")) is False
