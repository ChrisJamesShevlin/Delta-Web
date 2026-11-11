"""Offline tests for ig_swing.news and ig_swing.briefing."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ig_swing import news
from ig_swing.briefing import format_briefing
from ig_swing.events import HIGH, MEDIUM, Event
from ig_swing.news import Headline, fetch_finnhub_general, fetch_rss, load_headlines, select_headlines

UTC = timezone.utc
NOW = datetime(2026, 11, 11, 14, 0, tzinfo=UTC)
KEY = "SENTINEL-FINNHUB-KEY-12345"

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
<item><title>Fed's Goolsbee calls inflation report borderline disturbing</title>
<pubDate>Wed, 11 Nov 2026 13:29:55 GMT</pubDate><link>http://x/1</link></item>
<item><title>I want my husband to retire at 65</title>
<pubDate>Wed, 11 Nov 2026 13:15:00 GMT</pubDate></item>
<item><title>Oil jumps as OPEC signals cuts</title>
<pubDate>Tue, 10 Nov 2026 01:00:00 GMT</pubDate></item>
<item><title>No date but about the Fed</title></item>
</channel></rss>"""


class Resp:
    def __init__(self, status=200, content=b"", body=None):
        self.status_code, self.content, self._body = status, content, body

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, params=None, headers=None, timeout=None):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def h(title, hour, minute=0, source="T"):
    return Headline(title, datetime(2026, 11, 11, hour, minute, tzinfo=UTC), source)


def test_rss_parses_titles_and_times():
    items = fetch_rss(Session([Resp(content=RSS)]), "Test", "http://x")
    assert len(items) == 4
    assert items[0].published == datetime(2026, 11, 11, 13, 29, 55, tzinfo=UTC)
    assert items[3].published is None


def test_select_filters_old_irrelevant_undated_and_duplicates():
    items = fetch_rss(Session([Resp(content=RSS)]), "Test", "http://x")
    items.append(h("FED'S GOOLSBEE CALLS INFLATION REPORT: BORDERLINE DISTURBING", 13, 0, "Other"))
    got = select_headlines(items, NOW, lookback_hours=12)
    assert [x.title for x in got] == ["Fed's Goolsbee calls inflation report borderline disturbing"]


def test_select_sorts_newest_first_and_caps():
    items = [h(f"Fed story {i}", 8 + i) for i in range(5)]
    got = select_headlines(items, NOW, max_items=3)
    assert [x.title for x in got] == ["Fed story 4", "Fed story 3", "Fed story 2"]


def test_keyword_match_is_not_fooled_by_substrings_in_the_middle():
    assert select_headlines([h("The federation of cooks meets", 13)], NOW) == []
    assert len(select_headlines([h("Federal Reserve holds rates", 13)], NOW)) == 1
    assert select_headlines([h("Corporate America makes a trillion-dollar mistake", 13)], NOW) == []
    assert len(select_headlines([h("Dollar slides as Fed signals pause", 13)], NOW)) == 1
    assert len(select_headlines([h("Geopolitical risk lifts gold", 13)], NOW)) == 1


def test_finnhub_parse_and_http_error():
    body = [{"headline": "Treasury yields rise", "datetime": int(NOW.timestamp()), "source": "Reuters", "url": "u"}, {"headline": "", "datetime": 1}]
    got = fetch_finnhub_general(Session([Resp(body=body)]), KEY)
    assert [x.title for x in got] == ["Treasury yields rise"] and got[0].published == NOW
    try:
        fetch_finnhub_general(Session([Resp(status=403)]), KEY)
        raise AssertionError("expected SourceError")
    except news.SourceError as exc:
        assert str(exc) == "HTTP 403" and KEY not in str(exc)


def test_load_headlines_survives_failures_and_never_leaks_the_key():
    n_feeds = len(news.RSS_FEEDS)
    responses = [Resp(content=RSS)] + [Resp(status=500)] * (n_feeds - 1) + [RuntimeError(f"failed token={KEY}")]
    got, notes = load_headlines(Session(responses), KEY, NOW)
    assert len(got) == 1
    assert "Finnhub: unavailable (RuntimeError)" in notes
    assert sum("HTTP 500" in n for n in notes) == n_feeds - 1
    assert KEY not in " ".join(notes)


def test_load_headlines_notes_missing_key():
    n_feeds = len(news.RSS_FEEDS)
    _, notes = load_headlines(Session([Resp(content=RSS)] * n_feeds), None, NOW)
    assert any("FINNHUB_API_KEY" in n for n in notes)


def test_briefing_layout_uk_time_and_notes():
    evs = [
        Event(date(2026, 11, 13), "Producer Price Index (PPI)", HIGH, "BLS", datetime(2026, 11, 13, 13, 30, tzinfo=UTC)),
        Event(date(2026, 11, 17), "Retail sales (advance)", HIGH, "FRED", None),
        Event(date(2026, 11, 12), "Weekly jobless claims", MEDIUM, "FRED", None),
        Event(date(2026, 12, 30), "Too far ahead", HIGH, "FRED", None),
    ]
    text = format_briefing(NOW, evs, [h("Fed holds", 13, 29, "CNBC")], ["FRED: unavailable (HTTP 503)"])
    assert "High impact, next 7 days (2)" in text
    assert "Fri 13 Nov 13:30  Producer Price Index (PPI)  [BLS]" in text
    assert "Tue 17 Nov (time n/a)  Retail sales (advance)  [FRED]" in text
    assert "Too far ahead" not in text
    assert "13:29  Fed holds  (CNBC)" in text
    assert "FRED: unavailable (HTTP 503)" in text
    assert "No impact ratings" in text


def test_briefing_with_nothing_says_none_found():
    text = format_briefing(NOW, [], [], [])
    assert text.count("none found") == 3


def test_publisher_suffix_does_not_defeat_dedupe():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    items = [Headline("Fed holds rates steady - Reuters", now, "Google News"), Headline("Fed holds rates steady - CNBC", now, "Google News"),
             Headline("Fed holds rates steady", now, "CNBC")]
    assert len(select_headlines(items, now)) == 1
