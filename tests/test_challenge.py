"""Offline tests for ig_swing.challenge. Every number is hand-checkable."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from ig_swing import challenge as ch

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


def test_kelly_by_hand():
    assert ch.kelly_fraction(0.45, 2.0, cost_r=0.0) == pytest.approx(0.175)                  # 0.45 - 0.55 / 2
    b = (2.0 - 0.05) / 1.05
    assert ch.kelly_fraction(0.45, 2.0) == pytest.approx(0.45 - 0.55 / b)
    assert ch.kelly_fraction(0.33, 2.0, cost_r=0.0) == pytest.approx(0.0, abs=0.0051)         # no edge: bet nothing
    assert ch.kelly_fraction(0.30, 2.0) == 0.0 and ch.kelly_fraction(0.0, 2.0) == 0.0 and ch.kelly_fraction(0.5, 0.0) == 0.0


def test_one_eighth_kelly_absorbs_a_ten_point_overestimate():
    claimed = ch.kelly_fraction(0.45, 2.0, cost_r=0.0)                                         # 17.5%
    true = ch.kelly_fraction(0.35, 2.0, cost_r=0.0)                                            # 2.5%
    assert claimed == pytest.approx(0.175) and true == pytest.approx(0.025)
    assert ch.KELLY_FRACTION * claimed == pytest.approx(0.021875) and ch.KELLY_FRACTION * claimed < 1.0 * true   # inside the true Kelly bet


def test_suggested_risk_is_an_eighth_and_capped_until_calibrated():
    f = ch.kelly_fraction(0.45, 2.0)
    assert ch.suggested_risk_pct(0.45, 2.0) == pytest.approx(100 * 0.125 * f)
    assert ch.suggested_risk_pct(0.9, 3.0) == ch.CAP_UNTIL_CALIBRATED                          # a huge claimed edge is capped
    assert ch.suggested_risk_pct(0.9, 3.0, calibrated=True) == pytest.approx(min(ch.CAP_CALIBRATED, 100 * 0.125 * ch.kelly_fraction(0.9, 3.0)))
    assert ch.suggested_risk_pct(0.30, 2.0) == 0.0


def idea(**kw):
    base = dict(idea_id=1, now=NOW, market="EUR/USD", direction="BUY", entry=11000.0, spread_pts=2.0, stop_pts=100.0, target_pts=200.0,
                p_win=0.4, playbook="news_thesis", confidence="MEDIUM", thesis="Dollar weakens on soft data", horizon_days=5)
    base.update(kw)
    return ch.make_idea(**base)


@pytest.mark.parametrize("kw", [dict(market="  "), dict(direction="HOLD"), dict(stop_pts=0), dict(target_pts=-1), dict(p_win=0.99), dict(p_win=0.01),
                                dict(playbook="vibes"), dict(confidence="CERTAIN"), dict(thesis=""), dict(thesis="x" * 301), dict(horizon_days=0), dict(horizon_days=31)])
def test_idea_validation(kw):
    with pytest.raises(ch.InvalidIdea):
        idea(**kw)


def bars(rows, start="2026-11-03"):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.bdate_range(start, periods=len(rows)))
    return df


FLAT = (11000.0, 11010.0, 10990.0, 11000.0)


def test_target_stop_gap_and_time_exits_by_hand():
    i = idea()                                                                     # entry 11000, stop 10900, target 11200, spread 2 (half 1)
    t = ch.score_idea(i, bars([FLAT, (11000.0, 11250.0, 10995.0, 11200.0)]))
    assert t["reason"] == "target" and t["r"] == pytest.approx((11200 - 11000) / 100)
    s = ch.score_idea(i, bars([FLAT, (11000.0, 11005.0, 10850.0, 10900.0)]))
    assert s["reason"] == "stop" and s["r"] == pytest.approx(-1.0)
    both = ch.score_idea(i, bars([(11000.0, 11250.0, 10850.0, 11000.0)]))
    assert both["reason"] == "stop"                                                 # a stop and a target in one bar count as a stop
    gap = ch.score_idea(i, bars([(10800.0, 10810.0, 10790.0, 10800.0)]))
    assert gap["reason"] == "stop_gap" and gap["r"] == pytest.approx((10799 - 11000) / 100)
    time = ch.score_idea(idea(horizon_days=2), bars([FLAT, (11000.0, 11010.0, 10990.0, 11050.0), FLAT]))
    assert time["reason"] == "time" and time["r"] == pytest.approx((11050 - 1 - 11000) / 100)       # exits at the close less half the spread


def test_short_mirrors_the_long_and_unfinished_ideas_return_none():
    i = idea(direction="SELL", entry=11000.0)                                       # stop 11100, target 10800
    t = ch.score_idea(i, bars([(11000.0, 11005.0, 10750.0, 10800.0)]))
    assert t["reason"] == "target" and t["r"] == pytest.approx(2.0)
    assert ch.score_idea(i, bars([FLAT])) is None                                   # horizon is 5 days: one quiet bar is not enough
    assert ch.score_idea(i, bars([FLAT] * 3, start="2026-10-20")) is None            # bars all before the idea: nothing to score


def test_bars_on_the_day_of_the_idea_are_ignored():
    i = idea()
    wild_same_day = bars([(11000.0, 11500.0, 10000.0, 11000.0)], start="2026-11-02")  # the day the idea was written: not scored
    assert ch.score_idea(i, wild_same_day) is None


def test_calibration_flags_overconfidence():
    ideas = []
    for k in range(40):
        i = idea(idea_id=k + 1, p_win=0.45)
        ideas.append((i, {"r": 2.0 if k < 14 else -1.0}))                           # 14 of 40 win: 35%
    cal = ch.calibration(ideas)
    assert cal["n"] == 40 and cal["predicted"] == pytest.approx(0.45) and cal["actual"] == pytest.approx(0.35)
    assert not cal["calibrated"] or abs(cal["predicted"] - cal["actual"]) <= ch.CALIBRATION_TOLERANCE + 1e-9
    honest = [(idea(idea_id=k + 1, p_win=0.35), {"r": 2.0 if k < 14 else -1.0}) for k in range(40)]
    assert ch.calibration(honest)["calibrated"]                                      # stated 35%, actual 35%, and 40 ideas
    assert not ch.calibration(honest[:10])["calibrated"]                             # too few ideas to trust


def test_measure_and_report_state_sample_size_and_the_cap():
    ideas = [idea(idea_id=1), idea(idea_id=2, playbook="trend")]
    b = {"EUR/USD": bars([FLAT, (11000.0, 11250.0, 10995.0, 11200.0)])}
    m = ch.measure(ideas, lambda market: b[market])
    assert m["total"] == 2 and len(m["finished"]) == 2
    text = ch.format_report(m)
    for needle in ("n=2", "playbook news_thesis", "Probability check", "Kelly cap", "held at 5%", "under 30: noise"):
        assert needle in text


def test_stake_hint_by_hand():
    stake, note = ch.stake_hint(45.57, 10.0, 40.0, 11093.0, 3.33)                    # 10% = 4.557 over 40 points = 0.1139, limited by margin
    assert stake == pytest.approx(0.07) and note == "margin-limited"
    assert ch.stake_hint(45.57, 10.0, 400.0, 11093.0, 3.33) == (0.01, "")           # wide stop: the minimum stake
    assert ch.stake_hint(1.0, 10.0, 400.0, 11093.0, 3.33)[1] == "below the minimum stake"


def test_loading_ideas_ignores_other_events_and_garbage():
    import json
    events = [{"kind": "news_view", "detail": "{}"}, {"kind": ch.KIND, "detail": json.dumps(idea())}, {"kind": ch.KIND, "detail": "garbage"}]
    assert [i["id"] for i in ch.load_ideas(events)] == [1]


def test_every_market_has_a_class_and_the_mix_is_not_just_currencies():
    assert set(ch.ASSET_CLASS) == set(ch.ALL_MARKETS) and set(ch.ASSET_CLASS.values()) == set(ch.CLASS_ORDER)
    counts = {c: sum(1 for v in ch.ASSET_CLASS.values() if v == c) for c in ch.CLASS_ORDER}
    assert counts == {"Stock indices": 8, "US shares": 9, "Metals": 2, "Energy": 3, "Currencies": 10}


def test_briefing_text_contains_the_pieces():
    rows = [{"market": "EUR/USD", "price": 11093.3, "day_pct": 0.1, "atr": 60.0, "atr_pct": 0.54, "range_pos": 0.4, "long_pct": 65.0, "stake": 0.07,
             "note": "margin-limited", "spread_pct": 0.008, "sent_id": "EURUSD"}]
    text = ch.format_briefing(NOW, rows, 45.57, 0, ["x: unavailable"], ["  Fri 13 Nov PPI [HIGH]"], ["  09:00 Fed holds"])
    for needle in ("Equity £45.57", "Currencies (1)", "EUR/USD", "65% long", "£0.07/pt margin-limited", "Fits the live account at 10% risk (1): EUR/USD", "Shadow book only", "PPI", "Fed holds", "No order has been placed"):
        assert needle in text


def test_constants():
    assert ch.KELLY_FRACTION == 0.125 and ch.CAP_UNTIL_CALIBRATED == 5.0 and ch.CAP_CALIBRATED == 10.0 and ch.CALIBRATION_MIN == 30
    assert len(ch.ALL_MARKETS) == 32 and len({v[0] for v in ch.ALL_MARKETS.values()}) == 32 and all(v[2] > 0 for v in ch.ALL_MARKETS.values())


def test_research_depth_and_sources_are_recorded_and_validated():
    i = idea(research="web", sources=["https://www.federalreserve.gov/newsevents/pressreleases/x.htm", "  ", "BLS CPI release"])
    assert i["research"] == "web" and i["sources"] == ["https://www.federalreserve.gov/newsevents/pressreleases/x.htm", "BLS CPI release"]
    assert idea()["research"] == "headlines" and idea()["sources"] == []
    for bad in (dict(research="deep"), dict(sources=["s"] * 7), dict(sources=["x" * 301])):
        with pytest.raises(ch.InvalidIdea):
            idea(**bad)


def test_the_report_splits_results_by_research_depth_and_treats_old_ideas_as_headlines():
    old = idea(idea_id=1)
    old.pop("research")                                                              # recorded before the field existed
    deep = idea(idea_id=2, research="web")
    b = {"EUR/USD": bars([FLAT, (11000.0, 11250.0, 10995.0, 11200.0)])}
    text = ch.format_report(ch.measure([old, deep], lambda m: b[m]))
    assert "research headlines: n=1" in text and "research web: n=1" in text


def test_batched_snapshots_and_sentiment_take_one_call_each_and_survive_failure():
    calls = []

    class C:
        def _call(self, method, path, version, params=None, context=""):
            calls.append(path)
            if path == "/markets":
                return {"marketDetails": [{"instrument": {"epic": "E1"}, "snapshot": {"bid": 10.0, "offer": 11.0, "percentageChange": 1.5, "netChange": 0.2, "marketStatus": "TRADEABLE"}},
                                          {"instrument": {"epic": "E2"}, "snapshot": {"bid": None, "offer": None}}]}
            return {"clientSentiments": [{"marketId": "EURUSD", "longPositionPercentage": 64.0}]}

    snaps = ch.batch_snapshots(C(), ["E1", "E2"])
    assert list(snaps) == ["E1"] and snaps["E1"].offer == 11.0 and snaps["E1"].pct_change == 1.5
    assert ch.batch_sentiment(C(), ["EURUSD"]) == {"EURUSD": 64.0}
    assert calls == ["/markets", "/clientsentiment"]

    class Broken:
        def _call(self, *a, **k):
            raise RuntimeError("no")

    assert ch.batch_sentiment(Broken(), ["EURUSD"]) == {}


def test_more_than_fifty_markets_are_split_into_batches():
    calls = []

    class C:
        def _call(self, method, path, version, params=None, context=""):
            calls.append(len(params["epics"].split(",")))
            return {"marketDetails": []}

    ch.batch_snapshots(C(), [f"E{i}" for i in range(120)])
    assert calls == [50, 50, 20]


def test_every_check_covers_all_markets_including_the_users_named_ones():
    assert ch.MARKETS is ch.ALL_MARKETS and len(ch.MARKETS) == 32
    for name in ("US 500", "Germany 40", "FTSE 100", "Japan 225", "Brent crude", "Gold", "US Tech 100", "Apple", "Meta"):
        assert name in ch.MARKETS
    assert {ch.ASSET_CLASS[k] for k in ch.MARKETS} == {"Stock indices", "US shares", "Metals", "Energy", "Currencies"}   # not just currencies


def test_promotional_headlines_are_dropped_and_real_ones_kept():
    from datetime import datetime, timezone
    from ig_swing.news import Headline
    now = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    titles = ["Wall Street Analysts Believe Howmet (HWM) Could Rally 36%: Here's How to Trade - Zacks", "History Says 1 Month Has Been Reliable - The Motley Fool",
              "CTO Realty's Series A Preferred Stock Crosses Above 8% Yield Territory", "Nayax Q3 2026 Earnings Call Transcript",
              "Oil Futures Slide as Trump Says New Strikes on Iran Unlikely - WSJ", "Rebounding AI stocks lift Wall Street"]
    kept = [h.title for h in ch.drop_promotional([Headline(t, now, "x") for t in titles])]
    assert kept == ["Oil Futures Slide as Trump Says New Strikes on Iran Unlikely - WSJ", "Rebounding AI stocks lift Wall Street"]


def test_build_rows_covers_only_the_markets_it_is_given():
    import numpy as np

    class C:
        def _call(self, method, path, version, params=None, context=""):
            epics = params["epics"].split(",")
            return {"marketDetails": [{"instrument": {"epic": e}, "snapshot": {"bid": 99.0, "offer": 101.0, "percentageChange": 0.5, "netChange": 0.5, "marketStatus": "TRADEABLE"}} for e in epics]}

        def get_prices(self, epic, res, n):
            c = np.linspace(90, 100, 30)
            return pd.DataFrame({"timestamp": pd.bdate_range("2026-10-01", periods=30), "open": c, "high": c + 1, "low": c - 1, "close": c})

    rows, notes = ch.build_rows(C(), 45.57, NOW, lambda df, now: df)
    assert [r["market"] for r in rows] == list(ch.MARKETS) and len(rows) == 32 and notes == []
    share = next(r for r in rows if r["market"] == "Apple")
    assert share["atr"] is None and "no bars" in share["note"]                                   # shares are snapshot-only in the briefing
    two = {k: ch.MARKETS[k] for k in ("Gold", "US 500")}
    rows_two, _ = ch.build_rows(C(), 45.57, NOW, lambda df, now: df, two)
    assert [r["market"] for r in rows_two] == ["Gold", "US 500"]


class SearchClient:
    """A fake IG client with a search and a batch market call."""

    def __init__(self, hits, details):
        self.hits, self.details = hits, details

    def search_markets(self, term):
        return self.hits

    def _call(self, method, path, version, params=None, context=""):
        epics = params["epics"].split(",")
        return {"marketDetails": [self.details[e] for e in epics if e in self.details]}


def hit(name, epic, bid, offer, expiry="DFB", status="TRADEABLE"):
    from ig_swing.ig_client import MarketSummary
    return MarketSummary(name=name, epic=epic, expiry=expiry, status=status, bid=bid, offer=offer, instrument_type="SHARES")


def detail(epic, name, bid, offer, margin="20", min_stake=0.01, market_id="UBER"):
    return {"instrument": {"epic": epic, "name": name, "marketId": market_id, "marginFactor": margin},
            "dealingRules": {"minDealSize": {"value": min_stake}, "minNormalStopOrLimitDistance": {"value": 5.0}},
            "snapshot": {"bid": bid, "offer": offer, "percentageChange": 1.2, "netChange": 90.0, "marketStatus": "TRADEABLE"}}


def test_any_market_on_ig_can_be_resolved_by_search_words_name_or_epic():
    hits = [hit("Uber Technologies Inc", "UB.D.UBER.DAILY.IP", 8000.0, 8030.0), hit("Uber Technologies Inc (24 Hours)", "UB.D.UBER24.DAILY.IP", 8001.0, 8009.0),
            hit("Uber Fund", "XX.D.UF.DAILY.IP", 100.0, 110.0), hit("Uber future", "XX.F.UBER.DEC.IP", 8000.0, 8010.0, expiry="DEC-26")]
    details = {"UB.D.UBER24.DAILY.IP": detail("UB.D.UBER24.DAILY.IP", "Uber Technologies Inc (24 Hours)", 8001.0, 8009.0),
               "UB.D.AAPL.DAILY.IP": detail("UB.D.AAPL.DAILY.IP", "Apple", 27000.0, 27020.0, market_id="AAPL"),
               "UA.D.AAPL.DAILY.IP": detail("UA.D.AAPL.DAILY.IP", "Apple Inc (24 Hours)", 27000.0, 27020.0, market_id="AAPL")}
    c = SearchClient(hits, details)
    r = ch.resolve_market(c, "Uber")
    assert r["epic"] == "UB.D.UBER24.DAILY.IP" and r["margin"] == 20.0 and r["min_stake"] == 0.01 and r["spread_pct"] == pytest.approx(0.0999, abs=0.001)   # the 24 hour market wins, futures are ignored
    assert ch.resolve_market(c, "UA.D.AAPL.DAILY.IP")["name"] == "Apple Inc (24 Hours)"                                                                  # an epic goes straight through
    assert ch.resolve_market(c, "Apple")["epic"] == "UA.D.AAPL.DAILY.IP"                                                                                # a default-list name
    with pytest.raises(ch.InvalidIdea):
        ch.resolve_market(SearchClient([hit("Nothing", "ZZ.D.X.DAILY.IP", 1.0, 2.0, expiry="DEC-26")], {}), "nothing")


def test_the_fit_check_uses_the_live_margin_and_minimum_stake():
    big = {"name": "Apple", "epic": "UA.D.AAPL.DAILY.IP", "margin": 20.0, "min_stake": 0.01, "bid": 27000.0, "offer": 27020.0, "spread_pct": 0.07}
    small = {"name": "Uber", "epic": "UB.D.UBER.DAILY.IP", "margin": 20.0, "min_stake": 0.01, "bid": 8000.0, "offer": 8010.0, "spread_pct": 0.12}
    assert "does NOT fit" in ch.fit_line(big, 45.57) and "£54.02" in ch.fit_line(big, 45.57)          # £270 exposure x 20% margin is over 60% of £45.57
    assert "fits the account" in ch.fit_line(small, 45.57)                                              # £80 exposure x 20% = £16
    assert "fits the account" in ch.fit_line(big, 500.0)                                                # the same market fits a bigger account


def test_tickers_and_earnings_for_shares():
    assert ch.ticker_for("Meta", "UB.D.FB.DAILY.IP") == "META" and ch.ticker_for("Uber Technologies Inc", "UB.D.UBER.DAILY.IP") == "UBER"
    assert ch.ticker_for("Gold", "CS.D.USCGC.TODAY.IP") is None and ch.ticker_for("US 500", "IX.D.SPTRD.DAILY.IP") is None
    cal = [{"symbol": "NVDA", "date": "2026-11-19", "hour": "amc", "epsEstimate": 1.23}, {"symbol": "ZZZ", "date": "2026-11-12", "hour": "bmo"},
           {"symbol": "AAPL", "date": "2026-11-13", "hour": None, "epsEstimate": None}]
    lines = ch.format_earnings(cal, {"NVDA", "AAPL"})
    assert lines == ["  AAPL: 2026-11-13 time n/a", "  NVDA: 2026-11-19 after the close, EPS estimate 1.23"]
    assert ch.fetch_earnings(None, None, NOW.date(), NOW.date()) == []                                    # no key: nothing, no crash

    class Sess:
        def get(self, *a, **k):
            raise RuntimeError("down")

    assert ch.fetch_earnings(Sess(), "k", NOW.date(), NOW.date()) == []


def test_per_market_news_has_no_macro_padding_and_unknown_markets_use_their_name():
    from datetime import datetime, timezone
    from ig_swing.news import Headline
    now = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    hs = [Headline("Fed minutes point to slower cuts", now, "a"), Headline("Uber shares jump as ride bookings beat estimates", now, "b"), Headline("Meta unveils new AI glasses", now, "c")]
    assert ch.relevant_headlines(hs, "Meta", limit=5, macro_padding=False) == [hs[2]]
    assert [h.source for h in ch.relevant_headlines(hs, "Uber Technologies Inc (24 Hours)", limit=5)] == ["b", "a"]   # a market not in the table: words from its own name, then macro


def test_ideas_can_be_for_any_market_and_are_scored_by_their_epic():
    i = idea(market="Uber Technologies Inc (24 Hours)", epic="UB.D.UBER24.DAILY.IP")
    assert i["epic"] == "UB.D.UBER24.DAILY.IP" and idea()["epic"] is None
    seen = []
    ch.measure([i], lambda key: seen.append(key) or bars([FLAT]))
    assert seen == ["UB.D.UBER24.DAILY.IP"]                                                                 # bars are fetched by epic, not by a table name


def test_keywords_match_whole_words_only():
    from datetime import datetime, timezone
    from ig_swing.news import Headline
    now = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    hs = [Headline("Stock Market Today: Rally on November 11; Oil Rises, Gold Slips - Eurasia Business News", now, "a"),
          Headline("Crunch Fitness seeks $800 million from franchise-backed bonds", now, "b"),
          Headline("Euro slips as ECB's Lagarde warns on growth", now, "c"), Headline("Swiss franc climbs as SNB holds rates", now, "d")]
    assert [h.source for h in ch.relevant_headlines(hs, "EUR/USD", macro_padding=False)] == ["c"]            # not Eurasia
    assert [h.source for h in ch.relevant_headlines(hs, "USD/CHF", macro_padding=False)] == ["d"]            # not franchise
    assert ch._has_word("the euros are strong", "euro") and not ch._has_word("eurasia", "eur") and ch._has_word("tariffs rise", "tariff")
