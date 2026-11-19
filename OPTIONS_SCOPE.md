# Options on IG spread bets: scope (draft, 2026-11-19)

Purpose: decide, on evidence, whether options are ruled in or out, as an addition to the challenge, not instead of it. Nothing here is built or traded. Every live step needs the user's go.

The user's position (2026-11-19): money we can afford to lose (the main investments sit in a self-managed ISA), more funds can be added for options, and risk or account size alone is not a reason to rule an idea out. Selling options, including short strangles managed by rolling, is in scope.

## 1. What IG offers (checked on the live API, 19 Nov 2026, about 17:30 UTC)

| Underlying | Expiries seen | Minimum stake | Spread (offer minus bid) as a share of the option's price |
|---|---|---|---|
| US 500 | weekly (Mon, Wed, Fri), end of month, monthly to Jun 27 | £1/pt | near the money 2 to 6% (EOM 7250 put 95.18/97.14; Dec 7000 put 87.02/89.54); weekly 7000 put 9%; far out 22 to 44% |
| US Tech 100 | weekly, end of month | £0.20/pt | 4 to 9% |
| US Russell 2000 | weekly | not checked | not checked |
| FTSE 100 | weekly, monthly to Mar 27 | £1/pt | 14 to 25% |
| VIX | monthly (Dec, Jan seen) | £1/pt | Jan 27 call 2.18/2.53, 14%; from the user's platform screenshot |
| Silver | Dec | £0.25/pt | far out no bid, 100% |
| Gold, EUR/USD | none found by search | | |

Facts that shape every strategy:
- **No stops or limits on options** (IG returns stopsLimitsAllowed false for every option checked). Risk control is position size, defined-risk structures, and closing or rolling by hand at a check.
- **Selling margin.** VIX: £4.54 to sell one Jan 27 call at £1/pt (the user's ticket), which equals 20% of the 22.68 January future. US 500: IG lists a 5% margin factor on its options; by the same method about £360 per short option at £1/pt. The US 500 figure is an estimate and must be checked on a ticket.
- **Prices through the API.** IG quotes US 500, Nasdaq, FTSE, silver and VIX options through the API, although it does not quote the VIX market itself. The VIX future can be backed out of the option chain by put-call parity: on the Jan chain at strike 23, call mid 3.205 minus put mid 3.525 gives 22.68, which matches IG's figure.
- **Settlement differs.** US 500 monthlies settle on the next morning's opening print (SET); end-of-month ones on the cash close; VIX on its own settlement value. The code must handle each one.
- **IG's option price history:** not checked. Option epics expire, so history is short anyway.

## 2. Strategies in scope

| | Strategy | Where any edge comes from | Worst case | Funds needed at the minimum stake (rough) |
|---|---|---|---|---|
| A | **Buy options** (calls or puts) on the same ideas as the spread bets | none expected; changes the payoff shape (loss fixed at the price paid, a big move pays) | the price paid | VIX £2 to £5; US 500 weekly about £12; US 500 monthly £40 to £90 |
| B | **Credit spreads and iron condors** (sell an option, buy a cheaper one further out) | the volatility risk premium, less the cost of the wings | the width between strikes minus the credit, known at entry | US 500, 100-point wide spread at £1/pt: up to about £100 a side |
| C | **Short strangle, managed by rolling** (the user's idea) | the volatility risk premium | open-ended on the call side; on the put side down to zero | US 500 at £1/pt: margin about £700, and about £1,000 (a one-day 1987-style fall) to £2,000 (a 2020-style month) to survive the tail; VIX at £1/pt: margin about £5 to £10, worst case about £30 to £55 (VIX to 60 or 80) |
| D | **Put writing only** (short puts, the classic) | the volatility risk premium plus the market's drift | the market falling hard | as C put side |
| E | **VIX-specific**: sell VIX calls or call spreads after a spike (the user's mean-reversion idea) | VIX reverts, and its futures and options price in more than usually happens | VIX spikes further (16 to 65 in Aug 2024, +116% in a day in Feb 2018) | as C, VIX |

Rolling rules for C, to be fixed in advance and not made up in the moment (a starting point, to be pre-registered): open about 30 to 45 days out at about 16 delta each side; take the profit at 50% of the credit; at 21 days left, close or roll out; when a short strike's delta reaches about 30, roll the untested side toward the price (more credit) and the tested side out in time for a credit, never for a debit; a hard exit when the loss reaches 2 to 3 times the credit taken in. Delta is not given by IG: compute it from the option's price (Black-76 implied volatility), using the underlying's price or, for VIX, the parity-implied future.

## 3. What the evidence says so far (secondary sources, not verified, all sponsor-funded or practitioner)

- **Selling index puts has a long, mostly positive record.** Cboe's PutWrite index (PUT, monthly at-the-money S&P 500 puts, fully cash-secured): 9.40% a year against 9.91% for the S&P 500 from July 1986 to August 2023, with volatility of 10.3% against 15.4% and a worst drawdown of about -33% against -51%. The return per unit of risk is better; the total return is about the same. Strong negative skew (-2.09), so ordinary risk figures understate the tail. Research commissioned by Cboe (Bondarenko 2016, 2019).
- **Managed short strangles:** practitioner backtests (tastylive, SteadyOptions) report positive returns for 16-delta SPX strangles managed at 50% profit or 21 days, for example about 6.3% a year from 2001 to May 2020 with no costs. Drawdowns in 2008 and 2020 were not found in the snippets. These are fully collateralised numbers, not leveraged ones.
- **Short VIX:** the premium is real on average, but the failures are famous (XIV, the short-VIX product, lost about 90% in a day in Feb 2018).
- **The open questions for IG specifically:** (1) do IG's spreads (2 to 6% near the money on US 500, 14 to 25% on VIX and FTSE, 22 to 100% far out) leave any of the premium? (2) can a strategy managed at a few checks a day, with no stops, survive the gaps? (3) what size is sensible against the funds the user will put in?

## 4. Plan: three phases, each ending in a go or no-go from the user

**Phase 1: historical test (free data, synthetic prices, run once, pre-registered before any data is pulled).**
- Data: Yahoo S&P 500 and VIX daily from 1990 (US 500 strategies); VIX futures settlement history from Cboe and VVIX from 2007 (VIX strategies).
- Prices: Black-Scholes or Black-76 with the VIX as the 30-day implied volatility, plus a fixed skew assumption stated in advance. This is an approximation: it gets the real path of the market right, and the option prices only roughly.
- Costs: IG's spreads observed today, as a share of the option price by how far out it is, crossed on every open, close and roll.
- Strategies: A to E above, each with its rules fixed in advance; the number of variants stated.
- Measured: net return on the funds needed, worst month, worst drawdown and time to recover, 2008, Feb 2018, 2020 and 2022 on their own, share of months positive, and how often rolling saved or deepened a loss.
- Pass bars, set by the user BEFORE the run (for example: net positive after costs; worst drawdown no more than X% of the pot; worth more than holding the US 500 per unit of risk).
- Option, the user's choice: buy real end-of-day SPX option data (Cboe DataShop or a reseller, tens to a few hundred pounds) for an exact test instead of the synthetic one.

**Phase 2: forward shadow book on IG's live quotes (about 8 to 12 weeks, no money).**
Every check records the option positions each strategy would hold, at IG's real bid and offer, with every roll at the real prices. This tests what the history cannot: IG's real costs, fills and settlement, and whether a few checks a day are enough.

**Phase 3: live at the minimum stake, in a separate pot.**
Its own risk profile, set with the user: a stress-loss limit instead of a stop (for example, a 20% one-day US 500 fall and VIX at 80 together must not cost more than X% of the pot), buy-and-sell rules from phase 1, and a dated changelog entry. Kept apart from the challenge so both are measured cleanly; the book view adds the options' exposure so a short put and an index short are seen together.

## 5. Build list (for phases 1 to 3)

Option chain lookup (strike, expiry, call or put) and quotes; implied volatility, delta and parity-implied forward; the historical simulator for phase 1; a shadow options book (journal tables, scoring at checks and at settlement); the rolling command; the order code for options (no stop, defined-risk checks, sells allowed only within the agreed stress limit); reconcile at expiry; a section on the report page.

## 6. Reasons that could still rule it out (each one is measured, not assumed)

1. IG's spreads take most of the premium (phase 1 costs, phase 2 real fills).
2. The tail: the worst historical period costs more than the user will accept for the pot.
3. Few checks a day are not enough to roll in time (phase 2).
4. The pattern of returns (small steady gains, rare large losses) is not one the user can live with in practice: the "great gains to nothing" problem in its strongest form.
5. The time it takes away from the challenge and from poker.

Not reasons to rule it out: that it is risky, or that the current £53 account is too small.
