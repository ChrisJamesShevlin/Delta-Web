# IG-Swing: discretionary swing trading with an automated safety layer

Read this whole file at the start of every session. It is the agreed plan, written 2026-11-11 after a long discussion. Do not re-litigate decisions in it unless the user asks.

## STATUS (2026-11-11): direction changed. Read this first.

The user has abandoned the US 500 idea. The project is now an **AI-assisted, long-only stock picker**: the assistant (Claude) reads company filings, earnings releases and news and makes the fundamental call, fixed technical rules confirm it, and only stocks that qualify are bought, with exits by stop, risk-to-reward target or a technical signal. The US 500 code, its tests and its results stay in the repo as a record and are parked. The user's own words on the role of the rules above: they are a starting point, not a constraint. The full design and its tests are pre-registered in the section "Pre-registration: AI-assisted long-only stock picker" below. Live trading of shares needs an order flow for shares that does not exist yet, and the user's explicit go.

Update, later on 2026-11-11: the earnings-based candidates tested flat after costs and the technical confirmation hurt them (Test A below). The current candidate is **insider-buying clusters**, pre-registered in the section after that. Five ideas or variants have now been tried on this project, so the evidence bar for the insider test is raised from z of 2 to 2.5.

**Current plan (2026-11-11, agreed with the user): a hands-off core plus cushion portfolio on IG futures. The one-page spec is `PORTFOLIO_SPEC.md`.** It is to be paper-tracked first. The trend overlay alone failed the user's 4% bar; the combination is descriptive and in-sample only.

**User instruction, repeated several times (2026-11-11): trade FUTURES, not daily funded bets.** Quarterly contracts rolled each quarter, wider spread, **no overnight financing charge**. Cost any strategy as the observed futures spread plus one spread per roll while in the position, with no financing line. Daily-bet history from IG is only a price proxy for the futures. The trend sleeve test charged 2.5% a year financing because its history is daily bets; its verdict (fails the 4% bar) holds with zero financing, see its result section. Cash not used as margin can earn interest outside IG.

## What this project is

A UK retail trader (the user) wants to trade **swing positions (days to weeks)** on **IG spread bets** (tax free in the UK), using their own **fundamental and news-driven judgement plus technical analysis**. The software does NOT decide what to trade. It does the safety-critical and repetitive work around the user's decisions.

Automation does:
- a morning briefing (news, economic calendar, watchlist moves, key levels) as input to the user's decisions
- position sizing from the stop distance, with a hard risk budget
- placing orders through the IG API with a mandatory stop attached
- daily and weekly loss limits (kill switch)
- a trade journal (every trade logged with thesis, entry, stop, target, outcome)
- reporting (expectancy, drawdown, costs)

Automation does NOT: let an LLM place trades, or run any order without the user confirming it. (Revised 2026-11-11, see the decision record below: the news layer now takes part in decisions, and news-only trades stay in shadow mode until the evidence supports them.)

## Decision record: news as a second vote (user decision, 2026-11-11)

The user decided that technical analysis alone has no edge, that the plan in this file is a starting point and not a constraint, and that news must factor into decisions for entries and for any open position, not only act as a brake. Implemented in `ig_swing/context.py`, `news_view.py`, `sentiment.py` and `shadow.py`:

- The assistant (Claude) reads the briefing in a session and records a structured, expiring **news view** (BULLISH/BEARISH/NEUTRAL, LOW/MEDIUM/HIGH, reasons). No external LLM API and no API key. The view is journalled.
- Entry size follows conviction, never above the profile's target risk: news backs the trade = full target, no view or neutral = half, adverse medium = quarter, adverse high = no trade.
- Open positions: an adverse high-confidence view proposes closing the whole position, adverse medium proposes halving. A technical flip that the news backs proposes closing the old position so the new direction can be entered.
- **News-only entries are not traded.** A high-confidence view on a no-signal day is recorded in shadow mode and measured (`python3 -m ig_swing.shadow`). Under 100 finished ideas means no conclusion.
- Unchanged hard limits: mandatory stop, `user_confirmed` on every order and close, size and loss limits, event blackout, live order placement disabled in code until the user says go.
- Honest limit: we cannot beat professionals on speed. Free feeds lag and the view is read when a session asks. The hypothesis is consistency and breadth, not speed, and it is unproven. Every plan archives what the context layer saw (`news_snapshot` events) so it can be tested.

## Pre-registration: technical backtest (written 2026-11-11, BEFORE any history was pulled or any result seen)

Question: does the technical engine alone (no news layer) earn a positive expectancy after the spread?

- Data: IG `IX.D.SPTRD.DAILY.IP` daily bars (mid of bid and ask), requested 1,500 bars via `python3 -m ig_swing.history`, using whatever IG returns. Cached to `data/history/US500_DAY.csv`.
- Test: `python3 -m ig_swing.technical_test`, one run with the settings in `FIXED` in `ig_swing/technical_test.py`: default `StrategyParams` (EMA 20/50, ATR 14, stop 1.5 ATR, target 2R, 20-day breakout, pullback 0.5 ATR, RSI 60/40, 80-bar warm-up), spread 1.0 point, `AGREED` profile, start equity 100,000, backtester defaults for the rest (next-open entry paying half the spread, stop checked before target, gaps filled at the open, trend-break exit, 40-bar time stop, one position at a time).
- Split: the whole series is run once, trades are split by entry date: the first 70% of bars is in-sample, the last 30% is the holdout.
- Reported: n, expectancy per trade in R with standard error, win rate, max drawdown, for all trades, in-sample, holdout, long only and short only.
- Reading it: fewer than 100 trades is no conclusion. An expectancy within 2 standard errors of zero is indistinguishable from no edge. If in-sample and holdout disagree in sign, say so. A clearly negative result counts as evidence against the rules.
- Rules for what happens next: no parameter is changed after seeing results. Any tuning uses the in-sample part only and is then judged once on the holdout, and the number of variants tried must be stated. A result that needs tuning to look good is treated as noise.
- Not modelled: financing, loss limits, the news and cross-asset layers, slippage beyond overnight gaps.

### Result of the technical backtest (run once on 2026-11-11, settings unchanged, nothing tuned)

1,500 daily bars, 2022-01-19 to 2026-11-11 (4.8 years). The last bar was the unfinished session of 2026-11-11.

| Subset | Trades | Expectancy (R) | SE | Win rate | Max drawdown |
|---|---|---|---|---|---|
| All | 102 | +0.11 | 0.13 | 33% | 4.2% |
| In-sample (to 2025-06-04) | 70 | +0.13 | 0.16 | 34% | 4.2% |
| Holdout | 32 | +0.07 | 0.22 | 31% | 2.7% |
| Long only | 68 | +0.21 | 0.17 | 37% | 2.5% |
| Short only | 34 | -0.08 | 0.22 | 26% | 4.3% |

Reading, against the pre-registered rules: n=102 meets the 100-trade minimum and the expectancy is **indistinguishable from zero** (0.11 is under 1 standard error). In-sample and holdout agree in sign. The long and short difference is within noise and the index roughly doubled over the period, so it is not evidence of anything. Exits: 40 stops, 33 targets, 28 trend breaks, 1 end of data. No evidence of a technical edge on its own, and no evidence against it. Financing, loss limits and the news layers were not modelled. No parameter has been changed.

### Data defect found after run 1, and the pre-registration of run 2 (2026-11-11, written before run 2)

After run 1 was reported, I found that IG stamps bars by UTC date, so the roughly 2-hour Sunday-evening session arrives as its own bar: 247 of the 1,500 bars (16%), mean range 15.7 points against about 75 for a normal day. Those bars distorted the ATR, the 20-day breakout and the EMAs, so **run 1 was run on flawed data and its numbers should not be relied on.** It stays on record above. The last bar was also an unfinished session. The fix is `ig_swing/bars.py`: Sunday bars are merged into the next trading day, and any bar dated today or later is dropped. It is applied to the history file, the daily plan and the runner, which now refuses unclean data.

Run 2 is the same test on the cleaned file (1,252 bars, 2022-01-19 to 2026-11-10): same `FIXED` settings, same 70/30 split, same reading rules, nothing tuned. Honest disclosure: the data fix was prompted after seeing run 1 (it was found by checking the data, not chosen for its effect on the result), and run 2 is the second look at this data. **Two runs have been made. Run 2 supersedes run 1 for conclusions. No further runs, and no parameter changes, are allowed on this data without a new pre-registration.**

### Result of run 2 (cleaned data, 2026-11-11). This supersedes run 1.

1,252 clean daily bars, 2022-01-19 to 2026-11-10. Settings unchanged, nothing tuned.

| Subset | Trades | Expectancy (R) | SE | Win rate | Max drawdown |
|---|---|---|---|---|---|
| All | 86 | +0.03 | 0.14 | 31% | 3.9% |
| In-sample (to 2025-06-02) | 58 | +0.09 | 0.18 | 34% | 3.9% |
| Holdout | 28 | -0.09 | 0.22 | 25% | 3.8% |
| Long only | 58 | +0.13 | 0.17 | 34% | 2.7% |
| Short only | 28 | -0.18 | 0.24 | 25% | 4.1% |

Reading, against the pre-registered rules: n=86 is under the 100-trade minimum, so no conclusion, and the expectancy (+0.03R, SE 0.14) is indistinguishable from zero. In-sample and holdout **disagree in sign** (+0.09 then -0.09). Exits: 34 stops, 24 targets, 26 trend breaks, 1 time, 1 end of data. Run 1 had said +0.11R on 102 trades: the cleaning moved the point estimate by 0.08R, about half a standard error, which is itself an illustration of how much noise a sample this size carries. Conclusion: no evidence of a technical edge, and the data cannot rule one out either.

## Pre-registration: VIX and oil rule test (written 2026-11-11, BEFORE the FRED history was fetched or any result seen)

Question: are the price rules in `ig_swing/context.py` (block new longs on a VIX spike of 10% or more or an oil rise of 3% or more, block new shorts on a VIX fall of 10% or more) supported by history?

- Data: FRED `VIXCLS` and `DCOILWTICO` from 2021-12-01, against the cleaned US 500 bars used in the technical backtest (`data/history/US500_DAY.csv`). Run with `python3 -m ig_swing.rules_test`, once.
- Method: event study. Trigger = the rule's own threshold on the FRED day-over-day percentage change. First trigger in a cluster only (events at least 5 bars apart). Compare the US 500 forward return from the trigger day's close with the unconditional forward return over all days. Statistic: difference in means over sd(all days)/sqrt(n events), a rough z.
- Primary read: 5-bar forward return. The 1-bar return is shown as a secondary read and is not used for conclusions.
- Rule claims: after a VIX spike or an oil spike the forward return is lower than usual, and after a VIX collapse it is higher than usual.
- Reading: fewer than 30 events is no conclusion. |z| under 2 means the rule is not validated. |z| of 2 or more in the claimed direction supports it. **|z| of 2 or more in the opposite direction means the rule is harmful.** I flag this up front because VIX spikes are often followed by rebounds, so the rules may be the wrong way round. Three primary tests are made, so a z just over 2 is weak evidence.
- Also reported, descriptively only: how many of the 86 backtest trades each rule would have vetoed, and their mean R. No conclusion is drawn from that count.
- What happens next: a rule that is contradicted is removed. A rule that is merely not validated stays in as an unvalidated judgement and is labelled so. No threshold is changed after seeing results. One run only, a fresh pre-registration is needed for any variant.
- Not modelled: the live rule uses IG's intraday move at the time of the plan, this uses the daily close-to-close change.

### Result of the VIX and oil rule test (run once on 2026-11-11, nothing changed)

US 500 bars 2022-01-19 to 2026-11-10. Primary read: 5-bar forward return after the first trigger in a cluster, against 5-bar returns over all days (mean +0.24%, a rising market).

| Rule | Events | Forward return after trigger | Difference | z | Reading |
|---|---|---|---|---|---|
| VIX spike (+10%), expects lower | 68 | -0.02% | -0.25% | -0.94 | not validated |
| VIX collapse (-10%), expects higher | 41 | +0.52% | +0.28% | +0.82 | not validated |
| Oil spike (+3%), expects lower | 73 | -0.02% | -0.25% | -0.98 | not validated |

All three differences point the way the rules claim, but none is near |z| 2, so none is supported and none is contradicted. The secondary 1-bar read for the VIX spike points the other way (+0.16%, z +1.52), the rebound pattern, and was not used for conclusions. Descriptive only: the rules would have vetoed 0, 3 and 5 of the 86 backtest trades, far too few to say anything. Under the pre-registered rules, all three stay in as **unvalidated judgements**, labelled as such, with no thresholds changed. The rules are cheap insurance on a small account, not a demonstrated edge.

## Pre-registration: post-earnings drift on US stocks (written 2026-11-11, BEFORE any data was fetched or any result seen)

Background: the US 500 technical test showed no edge (see above) and the user is willing to change direction. Individual stocks give hundreds of independent trades, and earnings releases are company-specific news. Question: after an earnings release that moves a stock a lot, does it keep moving the same way, by enough to beat realistic IG costs?

- Data: SEC EDGAR 8-K filings with item 2.02 (earnings releases, with exact acceptance times) for the 99 stocks in `UNIVERSE` in `ig_swing/pead_test.py` (a fixed list of liquid US large caps, chosen now), events from 2005-01-01; Yahoo daily prices adjusted for splits and dividends, plus SPY as the benchmark. Fetch with `python3 -m ig_swing.pead_test --fetch`, run once with `python3 -m ig_swing.pead_test`.
- Event rules: before the open (or on a weekend) the first session on or after the filing date is the reaction day; after the close, the first session after it; filings during the session are excluded and counted. Reaction = reaction-day close over the previous close. Trigger: |reaction| of 5% or more. Direction follows the sign (long after a rise, short after a fall).
- PRIMARY measure: market-adjusted gross return per trade, entry at the open after the reaction day, exit at the close of the 20th session, direction-signed, minus the direction-signed SPY return over the same dates. No costs and no stop, so it measures the effect alone. Standard errors are clustered by calendar quarter, because earnings seasons put many events on the same market days.
- Split: before 2017 is in-sample, 2017 onwards is the holdout. Also reported: by year, long only, short only.
- TRADED variant (secondary): same entry, mandatory stop at 2 x ATR(14) from the reaction-day close (gaps fill at the open), time exit after 20 sessions. Reported gross and under three cost scenarios taken from IG's published pages: A no costs; B spread 0.10% round trip plus financing (longs 6.5% a year, shorts 3.0%); C as B plus a 0.8% currency fee (IG lists this for trades in a currency other than the account's, and I could not confirm it applies to spread bets). The costs are assumptions to be checked, not facts.
- Reading: fewer than 100 events is no conclusion. In the holdout, a primary z of 2 or more (clustered) supports continuation. A z of -2 or less in the opposite direction contradicts it (reversal). Anything else is no evidence of drift. It is worth continuing only if the holdout primary z is 2 or more AND the holdout traded mean return under scenario B is positive. If scenario C is the real cost, the break-even is the primary mean, compared with 0.8% plus the other costs.
- What happens next: no parameter (threshold, hold, stop, universe, split) is changed after seeing results. One run only. Any variant needs a new pre-registration with the number of variants tried stated.
- Limits: survivorship bias (current large caps only, so results are an upper bound), cost scenarios are assumptions, positions are not capped (this is per-trade expectancy and not an account simulation), the EDGAR acceptance time can lag the press release, news-reading by the assistant is not part of this test.

### Result of the post-earnings drift test (run once on 2026-11-11, nothing changed)

Data: 97 of the 99 stocks (BK and WBA are missing from EDGAR's current ticker list), 8,351 earnings filings, 1,937 triggered events from 2005-01-20 to 2026-10-13. Skipped: 5,832 below the 5% threshold, 395 filed during the session, 99 without enough data.

PRIMARY, market-adjusted gross return per trade over 20 sessions, SE clustered by calendar quarter:

| Subset | Events | Mean | SE | z | Reading |
|---|---|---|---|---|---|
| All | 1,937 | +0.72% | 0.21% | +3.48 | supports continuation |
| In-sample (before 2017) | 847 | +0.72% | 0.33% | +2.19 | supports |
| Holdout (2017 on) | 1,090 | +0.72% | 0.26% | +2.72 | supports |
| Long only | 1,024 | +1.21% | 0.24% | +5.00 | supports |
| Short only | 913 | +0.16% | 0.30% | +0.54 | within noise |

By year the effect is positive in 18 of 22 years (negative in 2005, 2008, 2022, 2026), with no sign of fading by decade.

TRADED variant (2 x ATR stop, 20 sessions, unhedged), holdout, mean per trade: gross +0.49% (SE 0.25%); scenario B (spread 0.10% plus financing) +0.12% (SE 0.24%), or **+0.02R (SE 0.04R)**; scenario C (B plus a 0.8% currency fee) -0.68%, or **-0.12R**. Gross holdout expectancy +0.09R (SE 0.04). The average stop is 6.5% of price and the median hold 27 calendar days. About 88 triggered events a year across the universe.

Reading, against the pre-registered rules: the primary test passes (holdout z +2.72), and the secondary criterion is met on paper (holdout scenario B mean is positive), but **+0.12% with a standard error of 0.24% is indistinguishable from zero**, and if the 0.8% currency fee applies the strategy loses money. The gross effect is real in this data. Whether it survives costs is not shown.

Why the gross result should be discounted: (1) **survivorship and hindsight bias.** The universe is today's large caps, chosen because they succeeded, and the effect sits almost entirely in the long side (+1.21% against +0.16% for shorts), which is the pattern that bias would produce. The true effect is probably smaller and cannot be measured with free data. (2) The cost scenarios are assumptions from IG's pages. (3) Positions were not capped and the trades overlap in time. Not tested: whether reading the release text adds anything.

## Forward test: earnings watch (started 2026-11-11, nothing is traded)

The historical drift result is flattered by hindsight (today's winners). `ig_swing/earnings_watch.py` is the clean test: the same fixed 97 stocks, the same trigger (reaction of 5% or more), the same evaluation code as the historical test, run forward in real time with nothing ordered.

- Each evening after 22:00 UTC (23:00 UK in winter): `python3 -m ig_swing.earnings_watch scan` records new triggered events in the journal and prints the release links. The assistant reads each release and records a verdict with `... read --ticker X --direction ... --confidence ... --reason ...` BEFORE the outcome is known, so the test can show whether reading the text adds anything beyond the price move.
- `python3 -m ig_swing.earnings_watch report` measures finished events (20 sessions after entry) with the pre-registered primary measure, the three cost scenarios, and a split by whether the read backed the move.
- Events detected after their entry open (13:30 UTC the next business day) are kept on record but left out and cannot be read: they could not have been acted on in real time. The first five events (BKNG, CHTR, CVS, MCD, QCOM, reactions 4 to 6 November 2026) are in this backlog.
- Reading rule: fewer than 100 finished events is no conclusion. About 88 triggered events a year are expected, so this takes about a year. The costs are the pre-registered scenarios until IG's live spread and the currency-fee question are checked. Nothing here changes any parameter of the historical test.

### IG's real costs on a US share spread bet (observed 2026-11-11, from IG's own cost panel for NVDA)

At what is evidently 0.01 pounds per point (notional about £176): opening cost £0.11, closing cost £0.11 (each is half of the 22-point spread, 0.125% round trip), daily running cost £0.03 a night (about 0.017% of notional, roughly 6% a year for a long). **No currency conversion fee appears, so scenario C does not apply.** The short-side running cost was not observed. Scenario D was added to `pead_test.SCENARIOS` with these numbers (the other scenarios are unchanged).

Descriptive re-costing of the same holdout trades, NOT a new test and no new run: under scenario D the holdout mean per trade is +0.10% (SE 0.24%), +0.02R (SE 0.04R), indistinguishable from zero, as under scenario B. Split by side (post hoc, so not evidence): longs +1.33% (SE 0.35%), shorts -1.15% (SE 0.33%). The traded variant is unhedged, so that split mixes the earnings effect with the market's upward drift over 20 sessions (which helps longs and hurts shorts) and with survivorship bias (today's winners). A long-only version would be a new hypothesis needing its own pre-registration, and it is exactly where hindsight bias sits. The forward earnings watch is the test that can answer it.

## Pre-registration: AI-assisted long-only stock picker (written 2026-11-11, before any of it is built)

**Purpose.** Find out, honestly, whether a stock picker that combines the assistant's reading of filings with fixed technical confirmation makes money after IG's real costs, beyond what simply holding the market gives. Past results to keep in mind: the price reaction to earnings alone, after real costs, was indistinguishable from zero (+0.10% per trade, SE 0.24%), and the technical rules alone on the US 500 showed no edge (+0.03R, SE 0.14).

**Universe (fixed).** The 97 stocks in `UNIVERSE` in `ig_swing/pead_test.py`. Hindsight bias is acknowledged: they are today's large caps, so every historical number is an upper bound. A bigger universe needs its own pre-registration.

**The pipeline, fixed.**
1. Catalyst: a new earnings release (8-K item 2.02) from a universe stock.
2. Candidate (mechanical, cheap, applied before any reading): reaction-day return of +2.0% or more, AND technical confirmation at the reaction-day close: close above SMA(50), SMA(50) above SMA(200), and close no more than 2.0 x ATR(14) above EMA(20) (not stretched). The reaction cut exists so the reading workload stays practical; it is a pre-registered choice, not a result.
3. The assistant reads the release and records a structured assessment BEFORE the outcome is known: direction (BULLISH, BEARISH, NEUTRAL), confidence (LOW, MEDIUM, HIGH), growth (accelerating, stable, slowing), margins (expanding, stable, contracting), guidance (raised, maintained, lowered, none), quality flags (for example buyback-driven EPS, one-offs excluded, weak cash flow, rising debt, litigation), a thesis (at most 300 characters), what would invalidate it (at most 200) and what the assistant cannot see (at most 200). It is stored once in the journal and never edited. The EPS surprise from Finnhub (actual against estimate, free for the last four quarters) and the price reaction are recorded alongside as features, not as decision inputs.
4. Qualified: a candidate whose assessment is BULLISH with MEDIUM or HIGH confidence. Long only. No shorts.
5. Entry: the next open after the reaction-day close. Stake from the stop distance with the AGREED risk profile (0.5 to 1% per trade, 3 open positions, 2% total open risk), a mandatory stop, and the user's confirmation on every order.
6. Exits, whichever comes first, stop checked before target and gaps filled at the open: stop at entry minus 2 x ATR(14) (ATR at the reaction-day close); target at 3R (R is the stop distance); technical exit, the first close below SMA(50), executed at the next open; time exit at the close of the 40th session after entry.
7. Costs for every test: IG's observed ticket costs (scenario D in `pead_test.SCENARIOS`: spread 0.125% round trip, financing 6.2% a year for a long, no currency fee).

**Test A, the mechanical half, on history (does confirmation plus the exits add anything?).** No assessments are used. Events: every earnings reaction of +2.0% or more in the universe from 2005-01-20 (the data already fetched). Compare the trend-confirmed events with the unconfirmed ones on the market-adjusted gross 20-session return (SE clustered by calendar quarter, as in the drift test), and report the traded variant (the exit rules above, scenario D) in R and in percent. Holdout is 2017 onwards. Reading: the confirmation is shown to help only if the holdout difference has a clustered z of 2 or more in its favour; the mechanical half is worth building on only if the holdout traded net expectancy is positive with a z of 2 or more. Disclosure: this reuses the earnings dataset, whose long-side aggregates were already seen, so this is a second look at the same data, and its holdout is not pristine.

**Test B, the assistant's reading, forward in real time (the real test).** Extend `ig_swing/earnings_watch.py`: each evening after 22:00 UTC, list new candidates, the assistant reads each release and records the structured assessment, nothing is ordered. After the 20 to 40 sessions needed for the exits to resolve, measure: qualified candidates (BULLISH, MEDIUM or HIGH) against the other candidates, on the market-adjusted gross return and on the traded net expectancy in R and in percent under scenario D, with the sample size and a clustered standard error every time, and against holding the S&P 500 over the same dates. Events detected after their entry open are backlog: kept, not counted, never read. Interim numbers are descriptive only and are never used to change anything or to stop early. Live money is considered only when at least 100 qualified candidates have finished AND their net expectancy is positive with a clustered z of 2 or more AND they beat the other candidates. If fewer than 40 candidates a year turn up, the test is too slow to be useful, and that will be said plainly. Expected pace: not known until the first earnings season has been counted.

**Test C, a blinded historical sample, optional and last.** Sample 60 candidates at random (seed 20261111) from 2019-01-01 to 2025-12-31. A script removes company names, tickers, dates and the reporting period, and keeps the first 8,000 characters of the release. The assistant records verdicts before any outcome is loaded. Report qualified against other candidates on the market-adjusted 20-session return, with the standard error. It is an upper bound only: the assistant's training knowledge runs to mid-2026 and numbers or products can identify a company, so lookahead cannot be ruled out. It is a sanity check, never evidence for going live.

**What is not claimed.** That an AI can pick stocks (it is not shown). That a backtest of the assistant's reading is possible without lookahead (it is not). The filing text does not contain the market's expectations or the call, and on two backlog examples (CVS fell 12.5% after a 25% EPS beat and a raised guide, Booking rose 9.8% after a weak guide) the release alone pointed the wrong way about the reaction. Whether the reading adds anything to the drift after the reaction is the open question.

**Rules for what happens next.** No threshold, universe, rule or exit is changed after seeing a result. Any change needs a new pre-registration with the number of variants tried stated. Live trading needs the user's explicit go, an order flow for shares (not built), and starts at the minimum stake. Everything is journalled before outcomes are known.

### Result of Test A, the mechanical half (run once on 2026-11-11, nothing changed)

2,428 earnings reactions of +2% or more, 2005 to 2026, costs scenario D. Market-adjusted gross 20-session return, SE clustered by quarter.

| | Events | Mean | z |
|---|---|---|---|
| Confirmed (trend and not stretched) | 510 | -0.17% | -0.62 |
| Unconfirmed | 1,918 | +0.83% | +5.06 |
| Holdout, confirmed minus unconfirmed | | -1.12% | -2.18 |

Pre-registered readings: Reading 1, the confirmation **hurts** (holdout z -2.18). Reading 2, the mechanical half is **not** worth building on alone (holdout confirmed net +0.90%, z +1.39, under 2). Traded net per trade: confirmed +0.47% (z +1.11), unconfirmed +0.63% (z +2.23). Likely reason (an inference, untested): "not stretched" removes exactly the big reactions where the drift is, because a big positive reaction is stretched by definition. Pace: only about 24 confirmed candidates a year, under the pre-registered 40 a year, so the forward test as designed would be too slow. This was the third look at the same earnings dataset, so no historical holdout from it is clean. The earnings-based forward watch (`ig_swing/earnings_watch.py`) is built and parked.

## Pre-registration: insider-buying clusters (written 2026-11-11, BEFORE any of it is built or run)

**Why this idea.** The earnings and technical ideas are crowded and tested flat after costs. Clustered open-market insider purchases are, in the secondary sources I found (Insider Monkey, Interactive Brokers Campus, summaries of Lakonishok and Lee, Jeng, Metrick and Zeckhauser, Alldredge and Blank), the best-supported public signal: roughly 2.1% abnormal return in the following month for purchases within two days of another insider's, essentially nothing for routine trades, stronger in small stocks in one study and not significantly different by size in another. None of this is verified from the originals, and the effect may have weakened. It fits the long-only design, is niche, and is testable on history without the assistant's judgement.

**Evidence gathered before writing (2026-11-11).** The SEC publishes every insider transaction in bulk, free, quarterly, back to at least 2007 (`.../insider-transactions-data-sets/{yyyy}q{n}_form345.zip`, with transaction code, filing date, issuer, owner and owner role). About 316 distinct clusters in 2024 Q1 and 394 in 2007 Q1 under the rough definition below, so roughly 1,300 to 1,600 a year. **Survivorship:** Yahoo returned no prices for 8 of 10 well-known failed or delisted companies (SIVB, LEHMQ, WAMUQ, CIT, FRC, RAD, JCP, SHLDQ), and for two more (BBBY, SBNY) the ticker now belongs to a different company. So a historical test with free data is survivor-biased, worst exactly where this idea lives (insiders buying distressed small caps), and ticker reuse can silently give the wrong company. **IG costs observed from IG's platform** (user screenshots): about 0.46% to 0.58% spread for mid and small caps (Veradermics, Verallia, Vera Therapeutics, Verastem, Vera Bradley, Cerence, Veracyte), 0.24% for the larger Veralto, **4.8% for the micro-cap Wrap Technologies** (untradeable), and Veradigm is phone-dealing only. Financing for a long was about 6.2% a year on Nvidia; for small caps it was not observed.

**Event definition (fixed).**
- Source: Form 4 only (not 4/A), non-derivative transactions with code P (open-market purchase), value (shares times price) of at least $10,000 per transaction, reporting owner a director or an officer.
- Cluster: at least 2 distinct reporting owners whose qualifying Form 4 filing dates fall within 7 calendar days of the window's first filing, with at least $100,000 in total. The signal date is the filing date on which the cluster condition first becomes true. After a cluster, the same issuer is skipped for 30 days.
- Entry: the open of the first session strictly after the signal filing date. The bulk data has no filing time, so this is deliberately conservative.
- Events from 2007-01-01. Before 2017 is in-sample, 2017 onwards is the holdout.

**Tradability filter (fixed, a stand-in for IG's spread).** A price of at least $5 and a 20-session average dollar volume of at least $5 million before the signal, to exclude names like Wrap. It is a stand-in: IG's historical spreads are unknown, and it will be checked against the spreads recorded in the forward test.

**Price data guards.** Map the issuer's trading symbol to Yahoo, and require a price history of at least 250 sessions before the signal and a bar on the entry date. This catches reused tickers. Events without usable prices are counted as unpriced, by year.

**Costs (fixed).** Spread 0.55% round trip (the observed small and mid cap range), financing 6.2% a year (observed on a large cap, assumed here), no currency fee (observed).

**Historical test (an UPPER BOUND only).** Run once. Primary measure: the market-adjusted gross return from the entry open to the close of the 40th session, minus IWM (the Russell 2000 ETF) over the same dates, because the buyers skew small and mid cap. SE clustered by calendar quarter (insider buying spikes after market crashes, so events cluster in time). Secondary, not used for conclusions: 20 and 120 sessions, and SPY as the benchmark. Also reported: the traded variant (stop at 2 x ATR(14), 3R target, 40-session time exit, stop checked before target, gaps at the open, no technical exit) in R and in percent net of the fixed costs; coverage of priced against unpriced events by year; results excluding Q4 2008 to Q2 2009 and Q1 to Q2 2020; and the **break-even missing-event return**, the average return the unpriced events would need to have had to bring the pooled mean to zero. Reading: the bar is a clustered z of 2.5 or more (raised because five ideas or variants have been tried). **Failing even this upper bound ends the idea:** a holdout primary z under 2.5, or a holdout traded net expectancy of zero or less, is conclusive. **Passing is not evidence of an edge**: it only justifies the forward test.

**Forward test (the real test).** Build a daily scan of new Form 4 filings (EDGAR's daily index, parsing each filing's XML; not yet built or verified) that applies the same cluster definition and the same tradability filter, plus a live IG lookup per signal: is the stock offered online, and what are its bid and offer at detection. Anything IG does not offer online, or with a spread over 0.6%, is recorded as untradeable, counted, and left out of the traded measure. The assistant reads each tradable cluster and records a structured assessment BEFORE the outcome is known: direction, confidence, who bought (roles), first purchase in a long time or not, size against holdings and against market cap, any footnote on the reason (plan, offering, financing), distress context, a thesis (at most 300 characters), what would invalidate it (at most 200) and what the assistant cannot see (at most 200). Events found after their entry open are backlog: kept, never read, not counted. Measured after the 40-session horizon with the actual recorded spread, financing as assumed, the same exits, benchmark IWM, with n and a clustered SE every time. Interim numbers are descriptive only and change nothing.

**Decision rules.** Live money is considered only if at least 100 tradable forward clusters have finished AND their net expectancy after the recorded spread and financing is positive with a clustered z of 2.5 or more. The assistant's reading is used live only if the read-bullish group beats the rest by a clustered z of 2.5 or more; otherwise the mechanical rule alone is the candidate, if it passes. Fewer than 100 finished tradable clusters is no conclusion.

**What is not claimed.** That the effect exists today (the literature is older and I read it second-hand). That a free-data backtest can be unbiased (it cannot). That the stand-in filter equals IG's real spread. That the assistant's reading adds anything.

**Rules for what happens next.** No cluster definition, threshold, filter, horizon or cost is changed after seeing results; any change needs a new pre-registration with the variant count stated. Live trading needs the user's explicit go, an order flow for shares (not built), the AGREED risk profile (0.5 to 1% per trade, 3 open positions, 2% total) and the minimum stake first. Everything is journalled before outcomes are known.

## Pre-registration: multi-market trend sleeve on IG data (written 2026-11-11, BEFORE the data beyond a five-market depth probe was pulled or any result seen)

**What it is.** A slow, long-and-short, diversified futures-style trend sleeve for the user: an additional tactical sleeve beside their ISA and poker, always invested in a handful of markets, rolled quarterly when traded live, hedge-fund style. It is the backup the user chose after the stock ideas tested flat. It is not swing trading. It trades once a month.

**The user's bars (set by the user, 2026-11-11: "a decade of nothing is pointless, the money would be better in an ISA"; "4% is fine").**
1. **Return bar:** the primary version's net return (after costs, excess over cash) is at least 4% a year over EVERY rolling 120-month window.
2. **Sleeve bar:** the static mix plus the sleeve as an overlay must have a shallower worst drawdown than the static mix alone, with no lower return.
Missing either bar fails the idea and we stop. Nothing is adjusted afterwards to make it pass.

**Data.** IG monthly bars (the daily funded bet or ETF-based daily bet), 300 monthly bars requested per market (about 25 years, whatever IG returns), mid of bid and ask at the bar close, cached to `data/history/trend/`. Drop the current unfinished month. A missing month in a market excludes that market for the months it touches. Monthly bars hide moves within a month, so drawdowns are understated.

**Universe (fixed, 14 markets, epic, observed round-trip spread on 2026-11-11).** Stock indices: US 500 `IX.D.SPTRD.DAILY.IP` 0.0092%, US Tech 100 `IX.D.NASDAQ.CASH.IP` 0.0035%, Germany 40 `IX.D.DAX.DAILY.IP` 0.0116%, Japan 225 `IX.D.NIKKEI.DAILY.IP` 0.0193%. Rates: US Treasury 10 Year Note ETF `UD.D.UTENUS.DAILY.IP` 0.3475% (IG has no gilt or bund daily bet with history, so the test is conservative on rates cost). Currencies: EUR/USD `CS.D.EURUSD.TODAY.IP` 0.008%, GBP/USD `CS.D.GBPUSD.TODAY.IP` 0.011%, USD/JPY `CS.D.USDJPY.TODAY.IP` 0.006%, AUD/USD `CS.D.AUDUSD.TODAY.IP` 0.0131%. Metals: Gold `CS.D.USCGC.TODAY.IP` 0.008%, Silver `CS.D.USCSI.TODAY.IP` 0.0772%, Copper `CS.D.COPPER.TODAY.IP` 0.218% (the quote seen when the market was open). Energy: Crude oil `CC.D.CL.USS.IP` 0.060%, Natural gas `CC.D.NG.USS.IP` 0.0922%. A market joins the sleeve once it has 12 monthly returns.

**Signals (computed at each month end, held for the next month).**
- Primary, V2 (the blend, chosen from the research and not from our data): the average of the signs of the 1-month, 3-month and 12-month returns, so each market's signal is -1, -1/3, +1/3 or +1.
- Comparison, V1 (textbook): the sign of the 12-month return.
- Both versions are reported, the primary is fixed as V2 now, and two variants have been tried.

**Sizing.** Each market's raw weight is its signal times 10% divided by its own annualised volatility (the standard deviation of its last 12 to 36 monthly returns, annualised), divided by the number of active markets. The whole sleeve is then scaled to a 10% annual volatility target using the trailing 12 to 36 months of the raw sleeve's own returns, with leverage between 1 and 3 (leverage 1 until 12 months of sleeve returns exist). No look-ahead.

**Costs.** Spread: observed spread times 2 (a hedge for wider historical spreads), charged on traded notional at half the spread per side. Financing: 2.5% a year on gross notional (an assumption standing in for the daily-bet admin fee). Cash and collateral interest are not counted, so the sleeve's return is its excess over cash.

**What is measured.** Net annualised return, volatility, return per unit of volatility, worst monthly drawdown and the longest time under water, the worst rolling 120-month net return and the share of windows at or above 4%, returns by period (up to 2009, 2010 to 2019, 2020 onwards), the 2008 and 2022 calendar years, the correlation with the US 500, cost and financing drag a year, turnover, and for the sleeve bar a static mix of 70% US 500 and 30% US Treasury 10 Year ETF (monthly, price returns only, no income) with and without the sleeve added as an overlay.

**Reading.** PASS only if V2 meets both bars. V1 is shown for comparison only. With about two independent decades, no statistical significance is claimed: this is a consistency check on top of the published long-run evidence (SG Trend Index +20.9% in 2008 and +27.4% in 2022, weak through much of the 2010s, second-largest drawdown since 2000 of about 20% to May 2025: industry and secondary sources, not verified).

**Limits.** Daily bets leave out the carry that quarterly futures embed, and the financing is an assumption. Gilts and bunds could not be included, so rates are the US 10-year only and at a conservative cost. Historical spreads are unknown. At a few thousand pounds minimum stakes make 14 positions granular, which is not modelled. The sample is small and the 2010s are inside it, so any tweak chosen after seeing them would be curve fitting and is not allowed.

**If it passes.** The next step is paper-tracking on IG's live prices for a period before any real money, with the quarterly futures equivalents and an order flow that does not exist yet, and the user's explicit go. **If it fails,** we stop and the money stays in the ISA.

### Amendment to the trend sleeve pre-registration (2026-11-11, written after the data pull and BEFORE the test was run or any return computed)

Facts found when the monthly data was pulled (data availability only, no results): eleven markets have about 25 years (from December 2001: US 500, US Tech 100, Germany 40, Japan 225, EUR/USD, GBP/USD, USD/JPY, AUD/USD, gold, crude oil, natural gas, with EUR/USD missing one month); **silver starts August 2010, copper March 2015, and IG's US 10-year Treasury ETF bet only July 2025 (17 months)**. So the rates leg is effectively absent from IG's history. Changes made, because the plan cannot be carried out as written:
1. **Static mix for bar 2.** The bond leg cannot come from IG. It is a proxy built from FRED's 10-year Treasury yield (`DGS10`, month-end): monthly return = prior yield / 12 minus 8.0 times the change in yield (modified duration 8.0, convexity ignored). The static mix stays 70% US 500 (price returns, no income) and 30% this proxy.
2. **The primary test is unchanged.** V2 on the IG data as pre-registered. Markets join once they have 12 monthly returns, so silver, copper and the ETF join late and the rates leg is effectively missing for the primary run.
3. **One clearly labelled sensitivity run, not the verdict:** V2 with the FRED-based 10-year proxy added as a rates market (cost of the US 10-year ETF bet, doubled as everywhere). **It cannot change the verdict.** If the primary fails and the sensitivity run passes, that is reported and the user decides whether a new pre-registration is worth writing; the user's rule remains that missing a bar ends this version.
No threshold, bar, variant or cost is changed.

### Result of the trend sleeve test (run once on 2026-11-11, nothing changed): FAILS the user's bars. This version is ended.

IG monthly prices, 14 markets (eleven from December 2001; silver from 2010, copper from 2015; the US 10-year ETF bet only from July 2025), sleeve returns 2002-12 to 2026-10 (287 months). Costs: spread doubled, financing 2.5% a year on gross notional.

| | V2 (primary) | V1 (comparison) |
|---|---|---|
| Net return a year | +1.32% | +1.21% |
| Volatility | 10.3% | 10.5% |
| Worst monthly drawdown | -41.8% | -38.2% |
| Longest time under water | 139 months | 139 months |
| To 2009 / 2010 to 2019 / 2020 on | +7.85% / **-4.05%** / +2.86% | +4.47% / -0.91% / +1.02% |
| 2008 / 2015 / 2022 | +39.5% / -16.6% / -1.7% | +18.8% / +6.8% / +3.7% |
| Bar 1: worst rolling 10-year window, share at or above 4% | -4.93%, 8% (**FAILS**) | -3.80%, 1% (FAILS) |
| Bar 2: worst drawdown with the sleeve added (static -37.0%, return +7.47%) | -28.7%, +9.09% (passes) | -21.5%, +8.76% (passes) |

**Verdict under the pre-registered rule: bar 1 fails, so we stop and the money stays in the ISA.** The sensitivity run with a FRED-based rates leg did no better (net +0.15% a year, worst 10-year window -5.58%) and could not have changed the verdict anyway.

Descriptive decomposition of the same primary run (not a new test): the failure does not depend on the financing assumption. Before financing the sleeve made +4.43% a year but still -1.09% a year in 2010 to 2019, with a worst 10-year window of -1.70% and 36% of windows at or above 4%. Before all costs it made +4.70% a year, -0.81% a year in the 2010s, worst window -1.35%, 38% of windows at or above 4%. So the user's feared "decade of nothing" showed up in the 2010s even with no costs at all. What did hold: it earned strongly in 2008 (+39.5%) and since 2020, and it passed the diversification bar (shallower drawdown and a higher return when added to a 70/30 static mix), but a diversifier that earns nothing for ten years was ruled out by the user in advance.

Limits that apply to this result: monthly bars understate drawdowns, daily bets omit the carry of quarterly futures, the rates leg is missing from IG history and the proxy did not help, historical spreads are unknown, and about two independent decades means no significance is claimed. No parameter was changed.

### Insider-buying cluster test: ABANDONED UNRUN by the user's decision (2026-11-11). No result, not a pass and not a fail.

The user stopped the price fetch as a waste of time. State at the stop: the SEC bulk insider data was cached for 77 quarters (18,135 clusters under the fixed definition), and prices had been fetched for 4098 of 6348 symbols (65%), of which 1917 returned no price data. The historical test was never run, so the pre-registration above produced no evidence either way. The code (`ig_swing/insiders.py`, `ig_swing/insider_test.py`) and its tests stay in the repo, and the fetch is resumable (`python3 -m ig_swing.insider_test --fetch`) if the idea is ever revisited. One thing the partial fetch showed regardless: about half of the symbols that appear in insider clusters returned no price history from Yahoo, so free data cannot give an unbiased historical test of this idea.

## The £45 play-money challenge (user decision, 2026-11-11)

The live account holds £45.57 of money the user treats as a total play account: "let's test some things out and see if we 2x our capital like a challenge", any market on IG (the user repeated this several times: we can trade anything, so never narrow it to a list), news and earnings plays allowed, assistant-led. Recorded deviations from the earlier live profile, approved by the user:
- Risk profile `challenge` (`ig_swing/config.py`), used only when `IG_ENV=live` and `IG_RISK_PROFILE=challenge` (the user sets this line in `.env`): **10% ceiling of equity at risk per trade (lowered from 20% by the user's staking decision on 2026-11-11)**, two positions, 20% total open risk, 25% daily and 40% weekly loss limits, a **floor at £15 equity** (no new entries below it), a **24 hour cooldown after two losing trades in a row**, and a **drawdown brake**: stakes halve when equity is 25% or more below its peak. Unchanged: a mandatory stop, `user_confirmed` on every order, minimum stop and stake checks, no widening a stop.
- **Kelly staking, one eighth** (the user's choice). Each idea records the assistant's estimated win probability and reward to risk; the stake is 1/8 of full Kelly after an assumed 0.05R cost, capped at **5%** at risk until at least 30 ideas have finished and the stated probabilities match the hit rate within 10 points, then **10%**. The account's minimum stake (1p a point) already risks about 2 to 3% with a sensible stop, so Kelly mostly decides trade or skip. A zero or negative Kelly means no trade. Reason: a 10-point overestimate of the win chance (45% claimed, 35% true) turns a 17.5% full-Kelly bet into a ruinous one, and an eighth lands almost exactly on the true Kelly.
- Universe: the briefing scans a default list of 32 markets (indices, nine US shares, metals, energy, ten currency pairs) as a starting scan, not a limit. Any market IG offers as a daily bet can be named for `quick`, `idea` and `place` (resolved through IG by name, epic or search words, margin and minimum stake read live), and `find` looks any market up. News, IG client sentiment and earnings (US shares, Finnhub) are checked for everything considered.
- Shadow book (`python3 -m ig_swing.challenge brief | quick | find | idea | report`): ideas are written down BEFORE the outcome, scored on daily bars, split by playbook and confidence, and the probabilities are tested. The live book takes only the best idea at a time, human-confirmed. Fully automated execution is NOT approved: the order flow has never run live and no edge is shown.

Honest odds, from a simulation (2-to-1 reward to risk, 0.05R cost, floor £15, target £90): with no edge, about 30% reach £90 at 10 to 20% risk per trade and only about 10 to 20% at 3 to 5% (small bets mostly pay costs); with a small real edge small bets win more often but need 100 or more trades. An eighth of Kelly is a long-game setting and will not double the account quickly. Results on this account are far too few to count as evidence. **Standing authorisation (user, 2026-11-11): the assistant places challenge orders itself as part of the market-check skill, without asking for a yes each time.** Scope: the £45 challenge account only, through `python3 -m ig_swing.place`, at most one new order per check, only when every guardrail passes (Kelly positive, risk engine allows it, profile `challenge`, mandatory stop attached by IG, fewer than two positions, no daily cap on new trades (removed 2026-11-12), floor £15, cooldown after two losses, drawdown brake). Anything unusual, any IG error or rate limit, or an instruction from the user to hold off means stop and ask. **Extended the same day to managing open positions, unprompted: closing a position in profit or in loss (the user: cutting losers short is where most traders fail), tightening a stop, setting or moving a take-profit, and reversing (a close plus a fully guarded new entry through the same checks, including the daily cap and the cooldown).** Never widening or removing a stop, adding to a position or averaging down. Trailing stops are the standard once a trade is up 1R (`manage trailing`: stop to breakeven plus an IG trailing stop at the current gap; the API call is untested on a live position, and if IG refuses it the command prints manual steps for the user to do in the platform, one thing only). The shadow book scores each idea with a fixed stop and target AND with a trailing stop from +1R at three widths (1R, 1.5R, 2R behind the best price), and `report` compares them. Tools: `ig_swing/manage.py` (`list`, `close`, `stop`, `limit`, `trailing`, `trail`, `history`; a dry run unless `--execute` with `IG_LIVE_ORDERS=yes` and the challenge profile). Early exits are tagged with a reason type and `manage history` compares them with stop and target hits so the effect of cutting early can be tested. The user can revoke all of this at any time by saying so. Live order placement remains off by default in code (`IG_LIVE_ORDERS=yes` is passed for one command at a time).

**Staking and backup update (user go, 2026-11-12).** Live stake is the market's minimum stake until my stated probabilities are calibrated (30 or more finished ideas within 10 points of the hit rate); Kelly (1/8, cap 10% once calibrated) is a skip filter until then. Logged in `RULE_CHANGELOG.md`. The journal is backed up as JSON-lines files by `python3 -m ig_swing.backup export --commit --push` into a local backup folder (`$IG_BACKUP_DIR`, default `../IG-Swing-backup`) that is pushed to the `journal-backup` branch of the Delta-Web repo, which is public (the user accepted that, 2026-11-12; the journal holds no credentials), restored with `... backup restore`; `data/` stays gitignored here and the caches are not backed up.

**The learning loop (user-approved 2026-11-11).** Every briefing archives all markets' conditions (`snapshots` table) and records one mechanical baseline idea per market per day (`baseline_ideas`: long above the 20-day average, else short, 1.5 ATR stop, 3 ATR target, 5 days, shares excluded); every idea saves the conditions at the time (`features`); `python3 -m ig_swing.challenge review` compares my ideas with the baseline on the same market and day, splits results by playbook, confidence, research depth and exit style, checks calibration and shows live exits by reason; `manage reconcile` records trades IG closed on its own. Rule changes are logged with a date in `RULE_CHANGELOG.md` and judged only on ideas written afterwards. No tuning on fewer than about 30 results per playbook. Machine learning waits for about a year of data and would be a second vote only.

**How it is run.** The user opens a session and says "market check" (or reports a sudden move): the project skill `.claude/skills/market-check/SKILL.md` runs the read-only briefing or a one-market quick read (`python3 -m ig_swing.challenge brief | quick --market X`), the assistant records shadow ideas (`... idea ...`) and at most one live candidate, and shows the exact order from the dry run (`python3 -m ig_swing.place ...`, which places nothing). Under the standing authorisation above the assistant then places it with `IG_LIVE_ORDERS=yes ... --confirm <token>`, where the token is tied to the exact stake, stop, target, market and direction, and reports to the user immediately. IG holds the stop server-side, so a position is protected between sessions. Automation, if any, is data-only (a scheduled briefing and the scoring report); the assistant's reading and every order stay in a session.

## Why swing trading (decided, do not reopen)

- Scalping: the spread is 10 to 30% of a typical target, so costs dominate.
- Day trading: still pays the spread on every trade, many more trades.
- Swing trading: costs shrink relative to the move, no need for speed, suits fundamental and news ideas.
- Position trading (weeks to months): lowest cost per trade but overnight financing eats into it and it overlaps with plain investing.

Start with a few liquid markets the user knows (for example one or two indices, gold, oil), not a broad watchlist.

## Honest framing (keep this in mind, do not oversell)

- IG's own risk disclosure says roughly three quarters of retail accounts lose money. Most failures are overtrading, wide or missing stops, and costs.
- A very good discretionary trader might make 20 to 30% a year. On a small account that is learning money, not income. £12,000 a year would need roughly £40,000 to £60,000.
- UK retail clients have capped leverage and negative balance protection, so losses cannot exceed the account, but the account itself can go to zero.
- The user is also pursuing online poker as their main focus. This project is an exploration, not a replacement for it. Keep the time cost small.
- Evidence beats feelings. If the demo results are indistinguishable from zero, say so plainly.

## Success criteria (pre-registered, write results against these, never move them afterwards)

1. Demo trade on an IG **demo** account first. Minimum **100 trades** (or about 3 months) before any live money.
2. Judge by **expectancy per trade after spread and financing**, with a standard error, not by the P&L figure alone. State the sample size every time.
3. Maximum drawdown on the demo must stay inside the risk limits below.
4. Go live only at the minimum stake, only if 1 to 3 hold, and only with the user's explicit go.

## Recorded deviation: £15 live test account (approved by the user, 2026-11-11)

The IG demo index account is broken, so the user approved testing on a **live** account holding about £15 of money they treat as play money. This deviates from criterion 1 (100 demo trades before any live money). Criteria 1 to 4 stay as written and still apply before any larger stake. Results on £15 are too few and too noisy to count as the demo evidence.

- Live is off by default. It needs `IG_ENV=live`, the separate `IG_LIVE_*` credentials, and a client built with `allow_live=True`.
- **Live order placement and closing stay disabled in code** (`live_orders_enabled=False`) until the read-only live smoke test has passed and the user says go.
- Risk profile `live_test` applies only when `IG_ENV=live`: cap 5% per trade, daily loss limit 10%, weekly 20%, max 1 open position, max total open risk 5%. The agreed profile (0.5 to 1% per trade, 2% daily, 4% weekly, 3 open, 2% total) is unchanged and is the default on demo.
- Unchanged on live: mandatory stop, `user_confirmed`, minimum stop and size checks, no widening a stop.

## Risk rules (enforced in code, not left to discipline)

- Every order has a **stop**. The code refuses to place one without it.
- Risk per trade: 0.5 to 1% of the account, sized from the stop distance.
- Daily loss limit and weekly loss limit: halt new orders when hit (exact numbers to be agreed in the design).
- Maximum number of open positions and maximum total risk, to be agreed.
- No averaging down, no moving a stop further away.
- Check the market's **minimum stop distance**, **minimum deal size** and overnight **financing** before sizing.

## Security (a real incident, read carefully)

The user's earlier repos `API_Trader` and `Signal_Dashboard` are public and their **git history contains real credentials** (IG API key, username and password, OpenAI key, Finnhub key, Alpha Vantage key) even though the current files show placeholders. Assume those are compromised.

- Never copy a key, password or token into any file in this repo, any commit, any log, any chat message, or any test fixture.
- Load secrets from **environment variables** or a **gitignored `.env`**. Add `.env` and any data/log folders to `.gitignore` before the first commit.
- Before the first commit, run a secret scan over the working tree (and again before any push).
- Do not read, print or copy credentials from the old repos or from any file on the user's machine. If you need to see an old file's logic, read code only and never output key values.
- Remind the user, once, if they have not yet rotated the exposed credentials (IG password and API key, OpenAI key, Finnhub, Alpha Vantage) and made the old repos private.

## IG API notes

- Demo base URL: `https://demo-api.ig.com/gateway/deal`. Live: `https://api.ig.com/gateway/deal`. **Default to demo. A live switch is a deliberate, user-approved change, never a side effect.**
- Headers: `X-IG-API-KEY`, `CST` and `X-SECURITY-TOKEN` from the session login. Endpoint versions differ per call.
- Spread bet US 500 daily funded bet epic used before: `IX.D.SPTRD.DAILY.IP` (expiry `DFB`). Look up other epics through the API.
- Rate limits and the weekly historical-price data allowance are tight. Check IG's current limits before designing data pulls, and cache aggressively.
- Streaming prices are available through Lightstreamer if needed later.

## Proposed structure (design first, agree before building)

```
ig_client      login, session refresh, prices, market rules, order placement (stop mandatory)
briefing       news + economic calendar + watchlist moves + levels -> morning summary
indicators     ATR (Wilder), RSI, EMAs, daily-timeframe context
risk           sizing from stop distance, loss limits, exposure caps, kill switch
orders         human confirms every trade, enforces risk rules, attaches stop/limit
journal        sqlite trade journal (thesis, entry, stop, target, result, costs)
report         expectancy, standard error, drawdown, costs, by market
```

Salvageable ideas from the old `API_Trader/main.py` (read the logic, do not copy blindly, and never copy secrets): IG session and market-rules helpers, RSS news and Finnhub economic calendar fetchers, Wilder ATR / RSI / EMA code. Everything else is rewritten. The old design (hourly scan, LLM decides, fixed £1 per point, no risk layer) is NOT the plan.

## Working rules for this user

- **Confirm before running anything** that trades, places orders, spends money, or writes outside this repo. "Check X" means read-only.
- **No unasked changes.** Diagnose, explain, then wait for an explicit go before editing, restarting or pushing.
- **Never place a live order.** Demo only unless the user explicitly switches it.
- **Verify before asserting.** Read the code before claiming what it does. Say "untested" rather than guess.
- Commit verified work promptly with clear messages. Do not push without the user's go.
- Be direct and plain. The user can get frustrated when things break; stay calm, do not over-read tone, do not pad answers. UK English, no em dashes.
- Learned on the previous project (a snooker Betfair system, now paused): log everything, pre-register the success test, never move the goalposts, state sample size and standard error with every number, and treat a small positive result as noise until proven otherwise.

## First tasks for a new session

1. Read this file. Ask which of the old credentials have been rotated (do not look at them).
2. Propose a short design for the user to approve: markets, the risk numbers (per-trade, daily, weekly, max open), the journal schema, and the briefing contents.
3. Only after approval: scaffold the repo (`.gitignore` first, secret scan), then build in this order: `ig_client` (demo), `risk`, `journal`, `orders`, `briefing`, `report`.
4. Run on the demo, collect trades against the success criteria above.
