from ig_swing import themes as t


def snap(price, sma):
    return {"price": price, "sma20": sma}


def test_a_currency_pair_is_exposed_to_both_currencies():
    assert t.exposure("AUD/USD", "SELL") == {"AUD": -1, "USD": 1}
    assert t.exposure("USD/JPY", "BUY") == {"USD": 1, "JPY": -1}


def test_ig_names_map_to_the_briefing_names():
    assert t.canonical("Spot Gold") == "Gold" and t.canonical("NVIDIA Corp (24 Hours)") == "NVIDIA" and t.canonical("Something else") == "Something else"
    assert t.exposure("Something else", "BUY") == {}


def test_two_dollar_longs_overlap_and_a_dollar_short_offsets():
    open_ = [("AUD/USD", "SELL")]
    assert any("same bet as" in w and "USD" in w for w in t.overlaps("EUR/USD", "SELL", open_))
    assert any("same bet as" in w for w in t.overlaps("USD/JPY", "BUY", open_))
    assert any("offsets" in w for w in t.overlaps("EUR/USD", "BUY", open_))


def test_independent_markets_do_not_overlap():
    assert t.overlaps("France 40", "BUY", [("AUD/USD", "SELL")]) == []
    assert t.overlaps("Gold", "BUY", [("France 40", "BUY")]) == []


def test_two_european_index_longs_overlap_on_equities_and_region():
    w = t.overlaps("Germany 40", "BUY", [("France 40", "BUY")])
    assert len(w) == 1 and "Europe equities" in w[0] and "equities" in w[0]
    assert t.overlaps("US 500", "BUY", [("France 40", "BUY")])      # still one equity bet through the shared "equities" theme


def test_confirmation_counts_correlated_markets_with_the_same_trend_signal():
    snaps = {"EUR/USD": snap(1.10, 1.12), "GBP/USD": snap(1.30, 1.32), "NZD/USD": snap(0.55, 0.56), "USD/JPY": snap(160, 158),
             "AUD/USD": snap(0.68, 0.70), "France 40": snap(8300, 8000)}
    c = t.confirmation("AUD/USD", "SELL", snaps)
    assert c["label"] == "confirmed" and c["n"] == 4 and sorted(c["agree"]) == ["EUR/USD", "GBP/USD", "NZD/USD", "USD/JPY"] and c["disagree"] == []
    assert "France 40" not in c["agree"] + c["disagree"] and "AUD/USD" not in c["agree"] + c["disagree"]


def test_confirmation_labels_contradicted_mixed_and_too_few():
    against = {"EUR/USD": snap(1.12, 1.10), "GBP/USD": snap(1.32, 1.30), "NZD/USD": snap(0.56, 0.55)}
    assert t.confirmation("AUD/USD", "SELL", against)["label"] == "contradicted"
    mixed = {"EUR/USD": snap(1.10, 1.12), "GBP/USD": snap(1.32, 1.30)}
    assert t.confirmation("AUD/USD", "SELL", mixed)["label"] == "mixed"
    assert t.confirmation("AUD/USD", "SELL", {"EUR/USD": snap(1.10, 1.12)})["label"] == "too_few"
    assert t.confirmation("AUD/USD", "SELL", {}) is None
    assert t.confirmation("AUD/USD", "SELL", {"EUR/USD": {"price": 1.1, "sma20": None}})["n"] == 0     # shares and markets without a 20-day average are skipped


def test_the_same_market_under_ig_name_is_not_its_own_confirmation():
    snaps = {"Gold": snap(3800, 3900), "Silver": snap(50, 52)}
    c = t.confirmation("Spot Gold", "SELL", snaps)
    assert c["n"] == 1 and c["agree"] == ["Silver"]


def test_description_reads_plainly():
    assert "confirmed: 2 of 2" in t.describe_confirmation({"label": "confirmed", "n": 2, "agree": ["A", "B"], "disagree": [], "share": 1.0})
    assert "run the briefing" in t.describe_confirmation(None)


def test_latest_snapshots_returns_the_newest_row_per_market_inside_the_window(tmp_path):
    from ig_swing.journal import Journal
    j = Journal(tmp_path / "j.db")
    row = lambda m, price: {"market": m, "epic": m, "price": price, "sma20": 1.0}
    j.record_snapshots("2026-11-12T09:00:00+00:00", [row("EUR/USD", 1.0), row("Gold", 5.0)])
    j.record_snapshots("2026-11-12T16:00:00+00:00", [row("EUR/USD", 2.0)])
    got = j.latest_snapshots("2026-11-12T00:00:00+00:00")
    assert got["EUR/USD"]["price"] == 2.0 and got["Gold"]["price"] == 5.0
    assert "Gold" not in j.latest_snapshots("2026-11-12T10:00:00+00:00")

