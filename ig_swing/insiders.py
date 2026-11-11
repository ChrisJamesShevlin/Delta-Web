"""Insider purchases from the SEC's free bulk data, and the cluster definition from CLAUDE.md. Read-only.

The SEC publishes every Form 3, 4 and 5 transaction quarterly (insider-transactions-data-sets). A purchase
here is a Form 4 (not 4/A) non-derivative transaction with code P (open-market purchase), acquired, worth at
least MIN_TRADE_VALUE, by a director or an officer. Each quarter is reduced to those rows and cached as a small CSV
(data/ is gitignored).

Cluster (fixed in the pre-registration): at least CLUSTER_OWNERS distinct reporting owners whose qualifying filing
dates fall within WINDOW_DAYS of the window's first filing, with at least CLUSTER_VALUE in total. The signal date is
the filing date on which the condition first becomes true. After a cluster the same issuer is skipped for SKIP_DAYS.

A joint filing (several reporting owners on one Form 4) is one set of transactions, so its value is counted once.
The bulk data carries a filing date but no filing time.
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .sources import TIMEOUT_SECONDS, USER_AGENT

BULK_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
CACHE_DIR = Path("data/history/insiders")
MIN_TRADE_VALUE = 10_000.0
CLUSTER_OWNERS = 2
CLUSTER_VALUE = 100_000.0
WINDOW_DAYS = 7
SKIP_DAYS = 30
FIRST_QUARTER = (2007, 1)
COLUMNS = ["accession", "issuer_cik", "symbol", "filing_date", "owners", "value"]


def quarter_names(first: tuple[int, int] = FIRST_QUARTER, today: date | None = None) -> list[str]:
    today = today or date.today()
    out, (y, q) = [], first
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _is_insider(relationship: Any) -> bool:
    r = str(relationship).lower()
    return "director" in r or "officer" in r


def parse_bulk(data: bytes) -> pd.DataFrame:
    """Qualifying purchase rows from one quarter's zip: one row per transaction, owners as a comma-joined string."""
    z = zipfile.ZipFile(io.BytesIO(data))
    rd = lambda name, cols: pd.read_csv(z.open(name), sep="\t", dtype=str, usecols=cols, low_memory=False)
    t = rd("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
    t = t[(t["TRANS_CODE"] == "P") & (t["TRANS_ACQUIRED_DISP_CD"] == "A")].copy()
    t["value"] = pd.to_numeric(t["TRANS_SHARES"], errors="coerce") * pd.to_numeric(t["TRANS_PRICEPERSHARE"], errors="coerce")
    t = t[t["value"] >= MIN_TRADE_VALUE]
    s = rd("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERTRADINGSYMBOL"])
    s = s[s["DOCUMENT_TYPE"] == "4"]
    o = rd("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"])
    o = o[o["RPTOWNER_RELATIONSHIP"].map(_is_insider)]
    owners = o.groupby("ACCESSION_NUMBER")["RPTOWNERCIK"].agg(lambda x: ",".join(sorted(set(x)))).rename("owners").reset_index()
    df = t.merge(s, on="ACCESSION_NUMBER").merge(owners, on="ACCESSION_NUMBER")
    df["filing_date"] = pd.to_datetime(df["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    df = df.dropna(subset=["filing_date"])
    return df.rename(columns={"ACCESSION_NUMBER": "accession", "ISSUERCIK": "issuer_cik", "ISSUERTRADINGSYMBOL": "symbol"})[COLUMNS].reset_index(drop=True)


def fetch_quarter(session: Any, q: str, cache: Path = CACHE_DIR) -> pd.DataFrame | None:
    """Cached by quarter. None when the SEC has not published that quarter (yet)."""
    path = cache / f"{q}.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=["filing_date"], dtype={"issuer_cik": str, "accession": str, "owners": str})
    if (cache / f"{q}.missing").exists():
        return None
    resp = session.get(BULK_URL.format(q=q), headers={"User-Agent": USER_AGENT}, timeout=max(TIMEOUT_SECONDS, 180))
    cache.mkdir(parents=True, exist_ok=True)
    if resp.status_code == 404:
        (cache / f"{q}.missing").write_text("")
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    df = parse_bulk(resp.content)
    df.to_csv(path, index=False)
    return df


def load_purchases(session: Any, quarters: list[str], cache: Path = CACHE_DIR, pause: float = 0.4) -> tuple[pd.DataFrame, list[str]]:
    frames, notes = [], []
    for q in quarters:
        try:
            if not (cache / f"{q}.csv").exists():
                time.sleep(pause)
            df = fetch_quarter(session, q, cache)
        except Exception as exc:  # type name only
            notes.append(f"{q}: unavailable ({type(exc).__name__})")
            continue
        if df is None:
            notes.append(f"{q}: not published")
        else:
            frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return out, notes


def find_clusters(purchases: pd.DataFrame) -> pd.DataFrame:
    """Distinct clusters under the fixed definition: issuer, symbol, signal date, owners, total value."""
    out = []
    for cik, g in purchases.sort_values("filing_date").groupby("issuer_cik"):
        rows = [(r.filing_date, set(str(r.owners).split(",")), float(r.value), r.symbol) for r in g.itertuples()]
        skip_until = pd.Timestamp.min
        for start in sorted({r[0] for r in rows}):
            if start < skip_until:
                continue
            window = [r for r in rows if start <= r[0] <= start + pd.Timedelta(days=WINDOW_DAYS)]
            owners: set[str] = set()
            total = 0.0
            for d in sorted({r[0] for r in window}):
                for r in window:
                    if r[0] == d:
                        owners |= r[1]
                        total += r[2]
                if len(owners) >= CLUSTER_OWNERS and total >= CLUSTER_VALUE:
                    out.append({"issuer_cik": cik, "symbol": window[0][3], "signal_date": d, "n_owners": len(owners), "total_value": total})
                    skip_until = d + pd.Timedelta(days=SKIP_DAYS)
                    break
    cols = ["issuer_cik", "symbol", "signal_date", "n_owners", "total_value"]
    return pd.DataFrame(out, columns=cols).sort_values("signal_date").reset_index(drop=True)
