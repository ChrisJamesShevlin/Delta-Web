"""Offline tests for ig_swing.backup: export then restore gives back the same journal."""

from __future__ import annotations

import sqlite3

import pytest

from ig_swing import backup
from ig_swing.journal import Journal


def test_export_then_restore_round_trips(tmp_path):
    j = Journal(tmp_path / "a.db")
    j.record_event("2026-11-12T10:00:00+00:00", "idea", '{"market": "Gold", "thesis": "café £"}')
    j.record_equity("2026-11-12", 50.0, "live")
    counts = backup.export_journal(tmp_path / "a.db", tmp_path / "out")
    assert counts["events"] == 1 and counts["equity_daily"] == 1
    restored = backup.restore_journal(tmp_path / "out", tmp_path / "b.db")
    assert restored == counts
    rows = sqlite3.connect(tmp_path / "b.db").execute("SELECT kind, detail FROM events").fetchall()
    assert rows == [("idea", '{"market": "Gold", "thesis": "café £"}')]


def test_restore_refuses_a_journal_that_has_data(tmp_path):
    j = Journal(tmp_path / "a.db")
    j.record_event("2026-11-12T10:00:00+00:00", "idea", "{}")
    backup.export_journal(tmp_path / "a.db", tmp_path / "out")
    with pytest.raises(SystemExit):
        backup.restore_journal(tmp_path / "out", tmp_path / "a.db")


def test_commit_without_a_remote_stays_local(tmp_path, monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)
    Journal(tmp_path / "a.db")
    backup.export_journal(tmp_path / "a.db", tmp_path / "out")
    assert backup.commit(tmp_path / "out") == "committed locally"
    assert backup.commit(tmp_path / "out", push=True) == "no changes since the last backup"
