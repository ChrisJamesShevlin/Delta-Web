"""Offline tests for ig_swing.news_view."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ig_swing import news_view
from ig_swing.news_view import InvalidView, load_view, make_view, save_view

UTC = timezone.utc
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)


def good(**kw):
    args = dict(direction="bearish", confidence="high", reasons=["Oil spike on supply fears"], now=NOW)
    args.update(kw)
    return make_view(**args)


def test_make_view_normalises_case_and_sets_expiry():
    v = good()
    assert v.direction == "BEARISH" and v.confidence == "HIGH" and v.expires_at == NOW + timedelta(hours=12)


@pytest.mark.parametrize("kw", [
    dict(direction="SIDEWAYS"), dict(confidence="CERTAIN"), dict(reasons=[]), dict(reasons=["x"] * 6),
    dict(reasons=[""]), dict(reasons=["y" * 241]), dict(reasons=[5]), dict(ttl_hours=0), dict(ttl_hours=25),
    dict(ttl_hours="soon"), dict(now=datetime(2026, 11, 11, 10, 0)),
])
def test_schema_rejects_bad_input(kw):
    with pytest.raises(InvalidView):
        good(**kw)


def test_round_trip_through_the_file(tmp_path):
    path = tmp_path / "v.json"
    save_view(good(), path)
    v, note = load_view(NOW + timedelta(hours=1), path)
    assert note == "" and v.direction == "BEARISH" and v.reasons == ("Oil spike on supply fears",)
    assert not list(tmp_path.glob(".news_view_*"))                 # no temp files left behind


def test_missing_file_is_no_view_with_a_note(tmp_path):
    v, note = load_view(NOW, tmp_path / "nope.json")
    assert v is None and "none recorded" in note


def test_expired_view_is_ignored(tmp_path):
    path = tmp_path / "v.json"
    save_view(good(), path)
    v, note = load_view(NOW + timedelta(hours=12), path)           # exactly at expiry: gone
    assert v is None and "expired" in note
    assert load_view(NOW + timedelta(hours=11, minutes=59), path)[0] is not None


def test_malformed_files_are_ignored_not_trusted(tmp_path):
    path = tmp_path / "v.json"
    for body in ["not json", "{}", json.dumps({**good().to_json(), "direction": "MOON"}),
                 json.dumps({**good().to_json(), "created_at": "2026-11-11T10:00:00"}),            # naive time
                 json.dumps({**good().to_json(), "expires_at": (NOW + timedelta(days=30)).isoformat()})]:  # TTL far beyond the cap
        path.write_text(body)
        v, note = load_view(NOW, path)
        assert v is None and ("malformed" in note), body


def test_a_view_dated_in_the_future_is_ignored(tmp_path):
    path = tmp_path / "v.json"
    save_view(make_view("BEARISH", "HIGH", ["r"], NOW + timedelta(hours=3)), path)
    assert load_view(NOW, path)[0] is None


def test_cli_set_writes_file_and_journal_and_refuses_bad_input(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert news_view.main(["set", "--direction", "BULLISH", "--confidence", "MEDIUM", "--reason", "Fed cut priced in", "--ttl", "6"]) == 0
    assert (tmp_path / "data" / "news_view.json").exists() and (tmp_path / "data" / "journal.db").exists()
    from ig_swing.journal import Journal
    ev = Journal(tmp_path / "data" / "journal.db").events()
    assert ev[0]["kind"] == "news_view" and "BULLISH" in ev[0]["detail"]
    assert news_view.main(["set", "--direction", "BULLISH", "--confidence", "MEDIUM", "--reason", "x", "--ttl", "99"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert news_view.main(["show"]) == 0
