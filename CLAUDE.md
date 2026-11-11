# IG-Swing: discretionary swing trading with an automated safety layer

Read this whole file at the start of every session. It is the agreed plan, written 2026-11-11 after a long discussion. Do not re-litigate decisions in it unless the user asks.

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
