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

Automation does NOT: generate trade signals on its own, let an LLM place trades, or run any order without the user confirming it. An autonomous news or LLM signal bot is explicitly out of scope until the demo evidence justifies it.

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
