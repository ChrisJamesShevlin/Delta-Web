"""Offline tests for ig_swing.insiders and ig_swing.insider_test."""

from __future__ import annotations

import io
import math
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ig_swing import insider_test as it
from ig_swing import insiders as ins


def make_zip(trans, subs, owners):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD\n"
                   + "\n".join("\t".join(map(str, r)) for r in trans))
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tISSUERTRADINGSYMBOL\n"
                   + "\n".join("\t".join(map(str, r)) for r in subs))
        z.writestr("REPORTINGOWNER.tsv", "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNER_RELATIONSHIP\n" + "\n".join("\t".join(map(str, r)) for r in owners))
    return buf.getvalue()


def test_parse_bulk_keeps_only_qualifying_purchases():
    trans = [("A1", 1, "P", 1000, 20, "A"),        # 20k purchase by a director: kept
             ("A2", 1, "S", 1000, 20, "D"),        # a sale: dropped
             ("A3", 1, "P", 100, 20, "A"),         # 2k: under the minimum
             ("A4", 1, "P", 1000, 20, "A"),        # a 4/A amendment: dropped
             ("A5", 1, "P", 1000, 20, "A"),        # bought only by a 10% owner: dropped
             ("A6", 1, "P", 2000, 10, "A"),        # joint filing by two insiders: one row, value counted once
             ("A7", 1, "P", 1000, 20, "D")]        # disposed, not acquired: dropped
    subs = [("A1", "05-MAR-2007", "4", "111", "AAA"), ("A2", "05-MAR-2007", "4", "111", "AAA"), ("A3", "05-MAR-2007", "4", "111", "AAA"),
            ("A4", "05-MAR-2007", "4/A", "111", "AAA"), ("A5", "05-MAR-2007", "4", "111", "AAA"), ("A6", "06-MAR-2007", "4", "222", "BBB"),
            ("A7", "05-MAR-2007", "4", "111", "AAA")]
    owners = [("A1", "c1", "Director"), ("A2", "c1", "Officer"), ("A3", "c1", "Officer"), ("A4", "c1", "Officer"), ("A5", "c9", "TenPercentOwner"),
              ("A6", "c2", "Director,Officer"), ("A6", "c3", "Officer"), ("A7", "c1", "Director")]
    df = ins.parse_bulk(make_zip(trans, subs, owners))
    assert sorted(df["accession"]) == ["A1", "A6"]
    a6 = df[df["accession"] == "A6"].iloc[0]
    assert a6["value"] == 20000 and a6["owners"] == "c2,c3" and a6["filing_date"] == pd.Timestamp("2007-03-06") and a6["symbol"] == "BBB"


def purchases(rows):
    return pd.DataFrame([(f"a{i}", cik, "SYM", pd.Timestamp(d), o, v) for i, (cik, d, o, v) in enumerate(rows)], columns=ins.COLUMNS)


def test_cluster_signal_date_is_when_the_condition_first_becomes_true():
    p = purchases([("X", "2020-03-02", "a", 60000), ("X", "2020-03-04", "b", 50000)])
    c = ins.find_clusters(p)
    assert len(c) == 1 and c.iloc[0]["signal_date"] == pd.Timestamp("2020-03-04") and c.iloc[0]["n_owners"] == 2 and c.iloc[0]["total_value"] == 110000
    late = ins.find_clusters(purchases([("X", "2020-03-02", "a", 150000), ("X", "2020-03-07", "b", 10000)]))
    assert late.iloc[0]["signal_date"] == pd.Timestamp("2020-03-07")                      # one big buy alone is not a cluster until a second insider joins


def test_what_is_not_a_cluster():
    assert ins.find_clusters(purchases([("X", "2020-03-02", "a", 80000), ("X", "2020-03-03", "a", 80000)])).empty            # one insider twice
    assert ins.find_clusters(purchases([("X", "2020-03-02", "a", 40000), ("X", "2020-03-03", "b", 40000)])).empty            # under $100k
    assert ins.find_clusters(purchases([("X", "2020-03-02", "a", 60000), ("X", "2020-03-12", "b", 60000)])).empty            # 10 days apart


def test_skip_window_after_a_cluster_and_a_new_one_afterwards():
    p = purchases([("X", "2020-03-02", "a", 60000), ("X", "2020-03-03", "b", 60000),        # cluster, signal 3 March, skip until 2 April
                   ("X", "2020-03-10", "c", 60000), ("X", "2020-03-11", "d", 60000),        # inside the skip window
                   ("X", "2020-05-04", "e", 60000), ("X", "2020-05-05", "f", 60000),        # a second cluster
                   ("Y", "2020-03-02", "a", 60000), ("Y", "2020-03-03", "b", 60000)])       # another issuer is independent
    c = ins.find_clusters(p)
    assert sorted(zip(c["issuer_cik"], c["signal_date"])) == [("X", pd.Timestamp("2020-03-03")), ("X", pd.Timestamp("2020-05-05")), ("Y", pd.Timestamp("2020-03-03"))]


def test_quarter_names_run_to_the_current_quarter():
    q = ins.quarter_names(today=date(2026, 11, 11))
    assert q[0] == "2007q1" and q[-1] == "2026q4" and len(q) == (2026 - 2007) * 4 + 4 and len(set(q)) == len(q)


def test_missing_quarter_is_remembered_and_never_crashes(tmp_path):
    class R:
        status_code = 404
        content = b""

    class S:
        calls = 0
        def get(self, *a, **k):
            S.calls += 1
            return R()

    df, notes = ins.load_purchases(S(), ["2099q1"], tmp_path, pause=0)
    assert df.empty and notes == ["2099q1: not published"]
    ins.load_purchases(S(), ["2099q1"], tmp_path, pause=0)
    assert S.calls == 1                                                                      # the 404 was cached


# ---- the test module -------------------------------------------------------


def stock_px(n=500, price=20.0, volume=1_000_000.0, start="2020-01-01"):
    d = pd.bdate_range(start, periods=n)
    c = np.full(n, price)
    return pd.DataFrame({"timestamp": d, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": volume, "raw_close": c})


SIGNAL = pd.Timestamp("2021-06-15")      # a Tuesday, session 380-ish of the series below


def trio(stock_df, bench_df=None):
    b = make_b = bench_df if bench_df is not None else stock_px(n=len(stock_df))
    return it.make_stock(stock_df), it.make_stock(b), it.make_stock(b)


def test_yahoo_symbol_mapping():
    assert it.yahoo_symbol("brk.b") == "BRK-B" and it.yahoo_symbol("AAPL") == "AAPL"
    for bad in ("", "N/A", "NONE", "ABC DEF", None, float("nan"), "TOOLONGSYMBOL"):
        assert it.yahoo_symbol(bad) is None


def test_primary_return_is_market_adjusted_by_hand():
    px = stock_px()
    i = int(pd.DatetimeIndex(px["timestamp"]).searchsorted(SIGNAL, side="right")) - 1
    e = i + 1
    px.loc[e + 39, ["close", "high"]] = [24.0, 24.5]                                          # +20% from the entry open of 20
    bench = stock_px()
    bench.loc[e:, ["open", "high", "low", "close"]] = 100.0
    bench.loc[e + 39, ["close", "high"]] = [110.0, 111.0]                                      # +10% for the benchmark over the same dates
    res = it.evaluate("T", *trio(px, bench), SIGNAL, 2, 150000.0)
    assert isinstance(res, it.InsiderEvent) and res.entry_date == pd.DatetimeIndex(px["timestamp"])[e]
    assert res.adj_primary == pytest.approx(0.20 - 0.10) and res.n_owners == 2 and res.total_value == 150000.0


def test_costs_are_the_pre_registered_ones():
    res = it.evaluate("T", *trio(stock_px()), SIGNAL)
    assert res.net_pct == pytest.approx(res.traded_pct - 0.0055 - 0.062 * res.cal_days / 365)
    assert res.net_r == pytest.approx(res.net_pct / res.stop_pct)


def test_guards_name_each_reason():
    assert it.evaluate("T", *trio(stock_px()), pd.Timestamp("2019-01-01")) == "gap"                      # before the series starts
    short = stock_px(n=500)
    assert it.evaluate("T", *trio(short), pd.Timestamp("2020-06-01")) == "history_short"                # under 250 sessions of history
    assert it.evaluate("T", *trio(stock_px(price=3.0)), SIGNAL) == "filtered"                           # under $5
    assert it.evaluate("T", *trio(stock_px(volume=1000.0)), SIGNAL) == "filtered"                       # under $5M a day
    assert it.evaluate("T", *trio(stock_px()), pd.Timestamp("2021-06-19")) is not None                    # a Saturday signal is handled


def test_ended_early_is_told_apart_from_incomplete():
    bench = stock_px(n=800)
    stopped = stock_px(n=400)                                                                          # trading stops long before the benchmark's end
    assert it.evaluate("T", *trio(stopped, bench), SIGNAL) == "ended_early"
    recent_bench = stock_px(n=420)
    recent_sig = pd.DatetimeIndex(recent_bench["timestamp"])[-10]
    assert it.evaluate("T", *trio(stock_px(n=420), recent_bench), recent_sig) == "incomplete"            # the event is simply too recent


def test_break_even_for_missing_events_by_hand():
    assert it.break_even_missing(0.05, 80, 20) == pytest.approx(-0.20)
    assert math.isnan(it.break_even_missing(0.05, 80, 0))


def test_run_and_report_end_to_end(tmp_path):
    prices = {"AAA": stock_px(), "IWM": stock_px(), "SPY": stock_px()}
    clusters = pd.DataFrame([{"issuer_cik": "1", "symbol": "AAA", "signal_date": SIGNAL, "n_owners": 2, "total_value": 150000.0},
                             {"issuer_cik": "2", "symbol": "GONE", "signal_date": SIGNAL, "n_owners": 3, "total_value": 300000.0},
                             {"issuer_cik": "3", "symbol": "N/A", "signal_date": SIGNAL, "n_owners": 2, "total_value": 120000.0}])
    events, coverage = it.run(clusters, prices, {"GONE"})
    assert len(events) == 1 and coverage[2021] == {"ok": 1, "unpriced": 2}
    text = it.format_report(events, coverage, 3)
    for needle in ("UPPER BOUND", "Coverage by year", "PRIMARY", "Break-even for the missing", "TRADED", "Reading A", "Reading B", "upper bound"):
        assert needle in text


def test_pre_registered_constants():
    assert (it.PRICE_MIN, it.DOLLAR_VOLUME_MIN, it.HISTORY_MIN, it.PRIMARY, it.SECONDARY) == (5.0, 5_000_000.0, 250, 40, (20, 120))
    assert (it.SPREAD, it.FIN_LONG, it.STOP_ATR, it.Z_BAR, it.BENCH) == (0.0055, 0.062, 2.0, 2.5, "IWM")
    assert it.HOLDOUT_START == pd.Timestamp("2017-01-01") and it.EVENT_START == pd.Timestamp("2007-01-01")
    assert (ins.MIN_TRADE_VALUE, ins.CLUSTER_OWNERS, ins.CLUSTER_VALUE, ins.WINDOW_DAYS, ins.SKIP_DAYS) == (10_000.0, 2, 100_000.0, 7, 30)
