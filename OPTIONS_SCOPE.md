# Options on IG spread bets: scope (draft, 2026-11-19)

Purpose: decide, on evidence, whether options are ruled in or out, as an addition to the challenge, not instead of it. Nothing here is built or traded. Every live step needs the user's go.

The user's position (2026-11-19): money we can afford to lose (the main investments sit in a self-managed ISA), more funds can be added for options, and risk or account size alone is not a reason to rule an idea out. Selling options, including short strangles managed by rolling, is in scope.

## 1. These are spread bets on an option's price, not US-style option contracts

Everything below is about IG Index spread-bet options (the account the API uses is the spread bet account). What that changes, from IG's own pages and the live API:

| | IG spread-bet option | Traditional (US-listed) option |
|---|---|---|
| What you hold | a bet on the option's price, in £ per point; IG is the counterparty and its dealing desk sets the price (from the underlying's level, time to expiry and volatility) | a contract on an exchange, 100 shares or a fixed index multiplier per contract |
| Size | any stake from the market's minimum (£1/pt on US 500, Germany 40, FTSE, Wall Street, VIX; £0.20/pt on US Tech 100; £0.25/pt on silver) | whole contracts only |
| Profit or loss | (closing price of the option minus opening price) × stake, in pounds, whatever the underlying's currency | (price change) × multiplier × contracts, in USD |
| Exercise and assignment | none: no early exercise, no assignment, no delivery, no pin risk on delivery | holders can exercise; sellers can be assigned early |
| Settlement | always cash, at the settlement level on the deal ticket; **no closing spread when held to expiry** (IG daily options page) | physical or cash |
| Buying | margin = option price × stake, which is also the most you can lose | premium paid |
| Selling | IG takes margin upfront; losses not limited. Observed on VIX: margin = 20% of the underlying × stake (£4.54 for one Jan 27 call at £1/pt on a 22.68 future), with no premium added. Formula not published; US 500 not yet checked on a ticket | broker margin rules (Reg T or portfolio margin) |
| Costs | the spread only; the opening cost is half the spread (VIX ticket: £0.18 on a 0.35 spread); no commission; no overnight funding charge shown on the option ticket, and IG says monthly and quarterly options "usually save on funding" (to confirm) | commission plus the bid-offer spread |
| Rolling | there is no roll: a "roll" is a close plus a new bet, paying the spread on both | the same in substance, but often done as one spread order |
| Stops and limits | not allowed through the API (stopsLimitsAllowed false on every option checked); the platform ticket shows a limit field only | stop orders possible on exchange |
| Tax | UK spread-bet winnings are free of capital gains tax and stamp duty (individual circumstances apply) | capital gains tax |
| Multi-leg orders | each leg is a separate bet, dealt one at a time (legging risk between legs) | combination orders filled together |

Consequences for strategy design: a strangle or condor is two or four separate bets, each paying its own spread to open and, unless held to expiry, to close; every roll pays the spread twice; nothing protects a short leg between checks except its size and any bought wing.

## 2. What IG offers (live API, 19 Nov 2026, about 17:30 UTC, searched by name and strike)

| Underlying | Expiries found through the API | Minimum stake | Spread as a share of the option's price |
|---|---|---|---|
| US 500 | weekly (Fri 20 Nov, Mon 23, Wed 25), end of month (30 Nov, 31 Dec), monthly Nov to Jun 27 | £1/pt | near the money 2 to 6% (EOM 7250 put 95.18/97.14; Dec 7000 put 87.02/89.54); weekly 7000 put 9%; far out 22 to 44% |
| Germany 40 | weekly (Fri, Mon, Wed), end of month, monthly Nov to Mar 27 | not checked | not checked |
| US Tech 100 | weekly, end of month, monthly to Mar 27 | £0.20/pt | 4 to 9% |
| Wall Street | weekly, monthly Nov to Jan 27 | not checked | not checked |
| US Russell 2000 | weekly, monthly to Jan 27 | not checked | not checked |
| FTSE 100 | weekly, monthly Nov to Jun 27 | £1/pt | 14 to 25% |
| Japan 225 | monthly Dec, Jan | not checked | not checked |
| VIX | monthly Dec to Apr 27 | £1/pt | Jan 27 call 2.18/2.53, 14% (the user's ticket) |
| Gold | weekly, monthly Dec to Mar 27 (on the futures) | not checked | not checked |
| Silver | Mon and Wed weeklies, monthly Dec to Mar 27 (on the futures) | £0.25/pt | far out no bid |
| US crude, natural gas | monthly Dec to Feb 27 | not checked | not checked |
| FX (EUR/USD, GBP/USD, USD/CHF, EUR/GBP), share options (FTSE 100 shares, leading US shares), daily options | **not found through the API**, although IG's pages list them; check in the platform | | |

Other facts:
- **Prices through the API.** IG quotes these options through the API, including VIX options, although it does not quote the VIX market itself. The VIX future can be backed out of the chain by put-call parity: on the Jan chain at strike 23, call mid 3.205 minus put mid 3.525 gives 22.68, which matches IG's figure.
- **Settlement differs by series.** US 500 monthlies settle on the next morning's opening print (SET, last dealing the day before); end-of-month ones on the cash close; weeklies and VIX on their own rules. The code must read each series' terms.
- **Daily options** (IG: expire at that day's close, spreads "as low as one point") are not in scope for swing checks a few times a day, and were not found through the API.
- **IG's option price history:** not checked; option epics expire, so history is short anyway.

## 3. Strategies in scope

| | Strategy | Where any edge comes from | Worst case | Funds needed at the minimum stake (rough) |
|---|---|---|---|---|
| A | **Buy options** (calls or puts) on the same ideas as the spread bets | none expected; changes the payoff shape (loss fixed at the price paid, a big move pays) | the price paid | VIX £2 to £5; US 500 weekly about £12; US 500 monthly £40 to £90 |
| B | **Credit spreads and iron condors** (sell an option, buy a cheaper one further out) | the volatility risk premium, less the cost of the wings | the width between strikes minus the credit, known at entry | US 500, 100-point wide spread at £1/pt: up to about £100 a side |
| C | **Short strangle, managed by rolling** (the user's idea) | the volatility risk premium | open-ended on the call side; on the put side down to zero | US 500 at £1/pt: margin about £700, and about £1,000 (a one-day 1987-style fall) to £2,000 (a 2020-style month) to survive the tail; VIX at £1/pt: margin about £5 to £10, worst case about £30 to £55 (VIX to 60 or 80) |
| D | **Put writing only** (short puts, the classic) | the volatility risk premium plus the market's drift | the market falling hard | as C put side |
| E | **VIX-specific**: sell VIX calls or call spreads after a spike (the user's mean-reversion idea) | VIX reverts, and its futures and options price in more than usually happens | VIX spikes further (16 to 65 in Aug 2024, +116% in a day in Feb 2018) | as C, VIX |

Rolling rules for C, to be fixed in advance and not made up in the moment (a starting point, to be pre-registered): open about 30 to 45 days out at about 16 delta each side; take the profit at 50% of the credit; at 21 days left, close or roll out; when a short strike's delta reaches about 30, roll the untested side toward the price (more credit) and the tested side out in time for a credit, never for a debit; a hard exit when the loss reaches 2 to 3 times the credit taken in. Delta is not given by IG: compute it from the option's price (Black-76 implied volatility), using the underlying's price or, for VIX, the parity-implied future.

## 4. What the evidence says so far (secondary sources, not verified, all sponsor-funded or practitioner)

- **Selling index puts has a long, mostly positive record.** Cboe's PutWrite index (PUT, monthly at-the-money S&P 500 puts, fully cash-secured): 9.40% a year against 9.91% for the S&P 500 from July 1986 to August 2023, with volatility of 10.3% against 15.4% and a worst drawdown of about -33% against -51%. The return per unit of risk is better; the total return is about the same. Strong negative skew (-2.09), so ordinary risk figures understate the tail. Research commissioned by Cboe (Bondarenko 2016, 2019).
- **Managed short strangles:** practitioner backtests (tastylive, SteadyOptions) report positive returns for 16-delta SPX strangles managed at 50% profit or 21 days, for example about 6.3% a year from 2001 to May 2020 with no costs. Drawdowns in 2008 and 2020 were not found in the snippets. These are fully collateralised numbers, not leveraged ones.
- **Short VIX:** the premium is real on average, but the failures are famous (XIV, the short-VIX product, lost about 90% in a day in Feb 2018).
- **The open questions for IG specifically:** (1) do IG's spreads (2 to 6% near the money on US 500, 14 to 25% on VIX and FTSE, 22 to 100% far out) leave any of the premium? (2) can a strategy managed at a few checks a day, with no stops, survive the gaps? (3) what size is sensible against the funds the user will put in?

## 5. Plan: three phases, each ending in a go or no-go from the user

**Phase 1: historical test (free data, synthetic prices, run once, pre-registered before any data is pulled).**
- Data: Yahoo S&P 500 and VIX daily from 1990 (US 500 strategies); VIX futures settlement history from Cboe and VVIX from 2007 (VIX strategies).
- Prices: Black-Scholes or Black-76 with the VIX as the 30-day implied volatility, plus a fixed skew assumption stated in advance. This is an approximation: it gets the real path of the market right, and the option prices only roughly.
- Costs: IG's spreads observed today, as a share of the option price by how far out it is: half the spread on every opening leg, half on every closing leg (none for a leg held to expiry), so a roll pays two halves on each leg moved.
- Pricing check: IG's desk prices are its own, so the synthetic prices are compared with IG's live quotes for the same strikes during phase 2.
- Strategies: A to E above, each with its rules fixed in advance; the number of variants stated.
- Measured: net return on the funds needed, worst month, worst drawdown and time to recover, 2008, Feb 2018, 2020 and 2022 on their own, share of months positive, and how often rolling saved or deepened a loss.
- Pass bars, set by the user BEFORE the run (for example: net positive after costs; worst drawdown no more than X% of the pot; worth more than holding the US 500 per unit of risk).
- Option, the user's choice: buy real end-of-day SPX option data (Cboe DataShop or a reseller, tens to a few hundred pounds) for an exact test instead of the synthetic one.

**Phase 2: forward shadow book on IG's live quotes (about 8 to 12 weeks, no money).**
Every check records the option positions each strategy would hold, at IG's real bid and offer, with every roll at the real prices. This tests what the history cannot: IG's real costs, fills and settlement, and whether a few checks a day are enough.

**Phase 3: live at the minimum stake, in a separate pot.**
Its own risk profile, set with the user: a stress-loss limit instead of a stop (for example, a 20% one-day US 500 fall and VIX at 80 together must not cost more than X% of the pot), buy-and-sell rules from phase 1, and a dated changelog entry. Kept apart from the challenge so both are measured cleanly; the book view adds the options' exposure so a short put and an index short are seen together.

## 6. Build list (for phases 1 to 3)

Option chain lookup (strike, expiry, call or put) and quotes, searched by strike as IG's search needs; each series' settlement terms read from IG; implied volatility, delta and parity-implied forward; the historical simulator for phase 1; a shadow options book (journal tables, scoring at checks and at settlement); the rolling command; the order code for options (each leg a separate spread bet, dealt in a set order with a check that every leg filled, no stop, defined-risk checks, sells allowed only within the agreed stress limit); reconcile at expiry; a section on the report page.

## 7. Reasons that could still rule it out (each one is measured, not assumed)

1. IG's spreads take most of the premium (phase 1 costs, phase 2 real fills).
2. The tail: the worst historical period costs more than the user will accept for the pot.
3. Few checks a day are not enough to roll in time (phase 2).
4. The pattern of returns (small steady gains, rare large losses) is not one the user can live with in practice: the "great gains to nothing" problem in its strongest form.
5. The time it takes away from the challenge and from poker.

Not reasons to rule it out: that it is risky, or that the current £53 account is too small.
