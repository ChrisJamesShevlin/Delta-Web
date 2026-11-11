# Portfolio spec v1: core plus cushion, IG futures spread bets (2026-11-11)

Written for the user. No secrets in this file. Status: a plan to paper-track, **not** yet approved for real money.

## What it is, in plain words
A hands-off, long-term pot of money held through IG **futures** (quarterly, rolled by IG), with two parts:
1. **Core**: a diversified long holding, 70% US 500 and 30% bonds. It earns the market's return and has deep falls.
2. **Cushion**: a slow trend overlay across several markets (blend of 1, 3 and 12 month trends, long or short, sized by volatility). On its own it earns little. Added to the core it cut the worst fall and lifted the return in our history.

It is not swing trading, it needs about ten minutes a month, and it is not an income machine (see "Honest scale").

## What was tested, and what was not
- **Tested** (pre-registered, IG data 2002 to 2026): the 14-market trend overlay on its own. It **failed the user's bar** (4% a year over every 10-year window): +1.3% a year net, 2010s -4.05%, and still about -0.8% a year before any costs. It **passed** the diversification bar when added to a 70/30 mix.
- **Descriptive only, not a new test**: the combined figures below. They reuse the same history, so treat them as optimistic.
- **Not tested**: the exact live contracts, the 12-market subset, Long Gilt as the bond (the test used a US 10-year proxy), and any overlay size other than full.

## Costs (futures terms, as the user instructed)
No financing charge. Cost = futures spread on each trade plus IG's roll cost of one closing and one opening spread per roll (IG closes the old contract at the official closing level and opens the next at its closing level). The futures price carries the cost of carry (roughly interest less dividends for indices). **The cash not used as margin must earn interest somewhere** (ISA cash or a money-market fund), or the core earns about 2 to 3% a year less than the index.

## Instruments (live contracts read from IG on 2026-11-11; re-check at every roll)
| Market | Role | Epic now | Margin | Spread | Last dealing | Min stake notional |
|---|---|---|---|---|---|---|
| US 500 | core + overlay | IX.D.SPTRD.MONTH1.IP | 5% | 0.013% | 18 Dec 2026 | £76 |
| Long Gilt | core bond | IR.D.FLG.Month1.IP | 3.33% | 0.023% | 26 Nov 2026 | £86 |
| US Tech 100 | overlay | IX.D.NASDAQ.MONTH4.IP | 5% | 0.011% | 18 Dec 2026 | £285 |
| Germany 40 | overlay | IX.D.DAX.VFAR.IP | 5% | 0.023% | 18 Dec 2026 | £268 |
| Japan 225 | overlay | IX.D.NIKFUT.FAR.IP | 5% | 0.032% | 10 Dec 2026 | £621 |
| EUR/USD fwd | overlay | CF.D.EURUSD.DEC.IP | 3.33% | 0.098% | 11 Dec 2026 | £111 |
| GBP/USD fwd | overlay | CF.D.GBPUSD.DEC.IP | 3.33% | 0.079% | 11 Dec 2026 | £133 |
| USD/JPY fwd | overlay | CF.D.USDJPY.DEC.IP | 3.33% | 0.063% | 11 Dec 2026 | £160 |
| Gold | overlay | MT.D.GC.Month2.IP | 5% | 0.020% | 24 Nov 2026 | £40 |
| Silver | overlay | MT.D.SI.Month1.IP | 10% | 0.193% | 25 Nov 2026 | £52 |
| Copper | overlay | MT.D.HG.Month2.IP | 10% | 0.064% | 25 Nov 2026 | £627 |
| Crude oil | overlay | EN.D.CL.Month1.IP | 10% | 0.070% | 19 Nov 2026 | £86 |
| Natural gas | overlay | EN.D.NG.Month3.IP | 10% | 0.310% | 24 Nov 2026 | £32 |
| AUD/USD | overlay | not looked up yet | | | | |

Notes: every market has a 0.01 pounds-per-point minimum stake. The **commodity epics above expire in November, not on a quarterly cycle**: choose contracts that expire three months apart (for example March 2027) so rolls stay quarterly, and re-read the last dealing date from IG each time.

## Monthly routine (about ten minutes, first trading day of the month)
1. Run the plan (to be built): it reads month-end closes, computes each market's signal, the volatility sizes, the sleeve scaling, and the **target stake in pounds per point** for the account size. It never places an order.
2. Compare with current positions and list the trades needed. Rolls are handled by IG's rolling feature or by hand before the last dealing date.
3. The user confirms each order. Every order has a protective stop set wide (about four times the market's monthly volatility away), a disaster stop and not a strategy stop.
4. Everything is journalled.

## Sizing for a £3,000 account (scales linearly)
- **Core**: 70% is about £2,100 of US 500, roughly £0.27 a point; 30% is about £900 of Long Gilt, roughly £0.10 a point. Margin on both is under 5% of the account.
- **Cushion at half size**: the typical position per market is small. **At £3,000 only some markets fit**, because a position smaller than one minimum stake cannot be held. Capital needed for the typical half-size position to be at least one minimum stake: gold £0.9k, AUD/USD £1.0k, US 500 £1.2k, EUR/USD £1.4k, GBP/USD £1.7k, USD/JPY £1.9k, silver £2.0k, natural gas £2.4k, crude oil £3.9k, Germany 40 £6.5k, US Tech 100 £6.7k, Japan 225 £15.7k, copper £17.3k.
- So at £3,000 the cushion is an 8-market version (US 500, the four currencies, gold, silver, natural gas); crude joins from about £4,000, the DAX and Nasdaq from about £7,000, and the Nikkei and copper only at a much larger size.

## Descriptive in-sample figures (not a test), futures-terms costs, 2002 to 2026
| | Return a year | Worst monthly fall | Longest under water | Worst 10-year | 2008 |
|---|---|---|---|---|---|
| Core only (70/30, price returns) | +7.5% | -37.0% | 50 months | +4.9% | -24.2% |
| Core + half-size cushion | +9.4% | -22.3% | 31 months | +5.9% | -7.2% |
| Core + full-size cushion | +11.1% | -26.5% | 63 months | +4.8% | +11.8% |
| Cushion alone, futures costs | +3.2% | -34.8% | n/a | -3.0% | n/a |

The half-size choice is for prudence because the cushion was weak in the 2010s (-2.3% a year there on futures terms). It also looks best in this table, so the in-sample edge is optimistic. These numbers include the Nikkei and copper, which cannot be run at £3,000.

## Honest scale
- At £3,000, a 7 to 9% year is about £200 to £280. Even a real 9% a year is not "income": £1,000 a year would need roughly £11,000 to £14,000.
- For true passive income, dividends and bond coupons are best held in the ISA. This pot is for growth and cushioning, and spread-bet profits are tax free and can be withdrawn.
- Drawdowns will still happen: the in-sample worst was -22% at half size.

## Paper-tracking before any real money
IG's demo account is broken, so the plan is tracked in the journal on live prices for 6 to 12 months. Compare realised costs, rolls and fills against these assumptions. Real money only with the user's explicit go, the minimum stakes first, the AGREED risk limits (max open positions and total risk to be re-set for this portfolio), and an order flow for these contracts that does not exist yet.
