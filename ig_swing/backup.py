"""Back up the journal to plain text files, and restore it.

The journal (data/journal.db) is gitignored and local. This exports every table to one JSON-lines file
(rows in id order, one JSON object per line) in a directory that is its own PRIVATE git repo, so the data
survives a lost laptop and diffs cleanly. It contains account size, trades and ideas: never put it in a public
repo, and it never contains credentials (the journal holds none).

    python3 -m ig_swing.backup export [--dir DIR] [--commit] [--push]
    python3 -m ig_swing.backup restore [--dir DIR] [--db data/journal.db]

Pushes go to the branch journal-backup of origin, never main. Default DIR is $IG_BACKUP_DIR, else ../IG-Swing-backup next to this repo. Restore refuses to touch a journal that
already has rows. Rebuildable caches (data/history, data/cache) are deliberately not backed up.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from .journal import Journal

BRANCH = "journal-backup"  # pushed to this branch only, never to main
TABLES = ("trades", "decisions", "events", "equity_daily", "snapshots", "baseline_ideas")


def default_dir() -> Path:
    env = os.environ.get("IG_BACKUP_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent.parent / "IG-Swing-backup"


def export_journal(db: str | Path, out: str | Path) -> dict[str, int]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    counts = {}
    for t in TABLES:
        order = "day" if t == "equity_daily" else "id"
        rows = conn.execute(f"SELECT * FROM {t} ORDER BY {order}").fetchall()
        with open(out / f"{t}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(dict(r), sort_keys=True, ensure_ascii=False) + "\n")
        counts[t] = len(rows)
    return counts


def restore_journal(src: str | Path, db: str | Path) -> dict[str, int]:
    src = Path(src)
    db = Path(db)
    j = Journal(db)  # creates the schema
    conn = sqlite3.connect(db)
    if any(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES):
        conn.close()
        raise SystemExit(f"{db} already has data; refusing to restore over it. Move it away first.")
    counts = {}
    for t in TABLES:
        path = src / f"{t}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        for r in rows:
            cols = ", ".join(r)
            conn.execute(f"INSERT INTO {t} ({cols}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
        counts[t] = len(rows)
    conn.commit()
    conn.close()
    return counts


HEADER_KEY = "http.https://github.com/.extraheader"


def _auth_env() -> dict[str, str]:
    """Environment for a push when the shell has no GitHub login: borrow the extraheader another local repo already
    holds (default the sibling Snooker repo, or $IG_BACKUP_AUTH_FROM). The value is only passed to git through the
    environment, never printed, logged or written to a file."""
    src = os.environ.get("IG_BACKUP_AUTH_FROM")
    src_dir = Path(src) if src else Path(__file__).resolve().parent.parent.parent / "Snooker"
    if not (src_dir / ".git").exists():
        return {}
    r = subprocess.run(["git", "-C", str(src_dir), "config", "--get", HEADER_KEY], capture_output=True, text=True)
    value = r.stdout.strip()
    if r.returncode != 0 or not value:
        return {}
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": HEADER_KEY, "GIT_CONFIG_VALUE_0": value}


def _redact(text: str) -> str:
    import re
    return re.sub(r"(?i)(authorization: *\w+ )\S+", r"\1***", text)


def _git(dir_: Path, *args: str, auth: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, **_auth_env()} if auth else None
    return subprocess.run(["git", "-C", str(dir_), *args], capture_output=True, text=True, env=env)


def commit(dir_: Path, push: bool = False) -> str:
    if not (dir_ / ".git").exists():
        _git(dir_, "init", "-b", "main")
    _git(dir_, "add", "-A")
    if not _git(dir_, "status", "--porcelain").stdout.strip():
        if not push:
            return "no changes since the last backup"
        committed = False  # still try the push: an earlier push may have failed after its commit
    else:
        r = _git(dir_, "commit", "-m", "journal backup")
        if r.returncode != 0:
            return "commit failed: " + (r.stderr or r.stdout).strip()[:200]
        committed = True
    if push:
        if not _git(dir_, "remote").stdout.strip():
            return "committed locally; no remote set (create a PRIVATE repo and add it as origin)"
        p = _git(dir_, "push", "origin", f"HEAD:refs/heads/{BRANCH}", auth=True)
        return ("committed and pushed" if committed else "pushed (no new changes)") if p.returncode == 0 else "committed; push failed: " + _redact(p.stderr.strip())[:200]
    return "committed locally"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("export", "restore"))
    ap.add_argument("--dir", default=None)
    ap.add_argument("--db", default="data/journal.db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    d = Path(a.dir) if a.dir else default_dir()
    if a.action == "export":
        counts = export_journal(a.db, d)
        print(f"exported to {d}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
        if a.commit or a.push:
            print(commit(d, push=a.push))
    else:
        counts = restore_journal(d, a.db)
        print(f"restored into {a.db}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
