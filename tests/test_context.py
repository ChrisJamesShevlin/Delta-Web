"""Offline tests for ig_swing.context. Every number is chosen to sit clearly either side of a threshold."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ig_swing.config import LIVE_TEST
from ig_swing.context import ContextRules, assess, plan_context_reductions, stress_headlines
from ig_swing.ig_client import Position
from ig_swing.inputs import CrossAssetInput
from ig_swing.news import Headline, select_headlines
from ig_swing.risk import EntryProposal, KillSwitch, RiskEngine, AccountState

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)


def inp(name, pct):
    return CrossAssetInput(name, "e", "TRADEABLE", pct, None, None, None)


def calm(vix=0.5, oil=0.2, gold=0.1):
    return [inp("VIX", vix), inp("Oil (US crude)", oil), inp("Gold", gold)]


def heads(*titles):
    return [Headline(t, NOW, "t") for t in titles]


def test_calm_backdrop_changes_nothing():
    v = assess("BUY", calm(), [])
    assert v.vetoes == () and v.size_factor == 1.0 and v.drivers == ()


def test_vix_spike_vetoes_longs_not_shorts():
    assert assess("BUY", calm(vix=12), []).vetoes and "vix_spike" in assess("BUY", calm(vix=12), []).drivers
    assert assess("SELL", calm(vix=12), []).vetoes == ()
    assert assess("BUY", calm(vix=9.9), []).vetoes == ()      # just under the threshold


def test_vix_collapse_vetoes_shorts_not_longs():
    assert assess("SELL", calm(vix=-11), []).vetoes
    assert assess("BUY", calm(vix=-11), []).vetoes == ()


def test_oil_spike_vetoes_longs_only():
    assert "oil_spike" in assess("BUY", calm(oil=3.5), []).drivers
    assert assess("SELL", calm(oil=3.5), []).vetoes == ()
    assert assess("BUY", calm(oil=-5), []).vetoes == ()        # an oil fall is not a rule


def test_missing_vix_or_oil_vetoes_everything_and_is_not_a_driver():
    for d in ("BUY", "SELL"):
        v = assess(d, [inp("Oil (US crude)", 0.1)], [])
        assert v.vetoes and "VIX" in v.vetoes[0] and v.drivers == ()
    no_pct = [CrossAssetInput("VIX", "e", "x", None, None, None, None), inp("Oil (US crude)", 0.1)]
    assert assess("BUY", no_pct, []).vetoes


def test_context_not_loaded_vetoes():
    assert assess("BUY", None, []).vetoes and assess("BUY", calm(), None).vetoes


def test_gold_never_vetoes_it_only_adds_a_note():
    v = assess("BUY", calm(vix=2, gold=2.5), [])
    assert v.vetoes == () and any("flight to safety" in n for n in v.notes)


def test_stress_headlines_halve_longs_only_at_the_count():
    two = heads("Stocks plunge as banks tumble", "Markets in turmoil")
    three = two + heads("War fears hit Wall Street")
    assert assess("BUY", calm(), two).size_factor == 1.0
    v = assess("BUY", calm(), three)
    assert v.size_factor == 0.5 and "headline_stress" in v.drivers
    assert assess("SELL", calm(), three).size_factor == 1.0


def test_stress_matching_is_whole_word():
    assert stress_headlines(heads("Warren Buffett buys shares", "Stocks crash on Fed", "Trade war escalates")) == heads("Stocks crash on Fed", "Trade war escalates")


def test_stress_words_survive_the_relevance_filter():
    sel = select_headlines(heads("Markets tumble as war widens", "Wall Street crash fears"), NOW)
    assert len(sel) >= 1


def position(direction="BUY", size=0.04):
    return Position("D1", "IX", "US 500", direction, size, 7600.0, 7500.0, None, None, None, None)


def test_reductions_propose_halving_a_long_when_the_vix_spikes():
    out = plan_context_reductions([position()], calm(vix=15), [], 0.01)
    assert len(out) == 1 and out[0].close_size == 0.02 and "VIX" in out[0].reason


def test_reductions_ignore_the_missing_data_veto_and_small_positions():
    assert plan_context_reductions([position()], [inp("Oil (US crude)", 0.1)], [], 0.01) == []   # absence is not evidence
    assert plan_context_reductions([position(size=0.01)], calm(vix=15), [], 0.01) == []          # cannot halve the minimum
    assert plan_context_reductions([position()], None, None, 0.01) == []
    assert plan_context_reductions([position("SELL")], calm(vix=15), [], 0.01) == []             # a short likes a VIX spike


def test_engine_applies_vetoes_and_halving(tmp_path):
    engine = RiskEngine(LIVE_TEST, KillSwitch(tmp_path / "ks.json"))
    acct = AccountState(30.0, 30.0, 30.0)
    full = engine.evaluate_entry(EntryProposal("IX", "BUY", 10.0, 0.01, 1.0), acct, [], [], NOW)
    half = engine.evaluate_entry(EntryProposal("IX", "BUY", 10.0, 0.01, 1.0, size_factor=0.5), acct, [], [], NOW)
    assert full.allowed and half.allowed and half.size < full.size
    veto = engine.evaluate_entry(EntryProposal("IX", "BUY", 10.0, 0.01, 1.0, vetoes=("x",)), acct, [], [], NOW)
    assert not veto.allowed and "context veto: x" in veto.reasons
    # a factor above 1 can never enlarge a trade
    big = engine.evaluate_entry(EntryProposal("IX", "BUY", 10.0, 0.01, 1.0, size_factor=5.0), acct, [], [], NOW)
    assert big.size == full.size
