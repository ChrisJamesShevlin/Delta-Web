---
name: market-check
description: Use when the user says "market check", "check the markets", "any trades?", or reports a sudden drop or spike in a market and asks whether there is a trade. Runs the read-only IG briefing or a single-market quick read, reads it, records shadow ideas with honest win probabilities and 1/8 Kelly sizing for the £45 play-money challenge, picks at most one live candidate, and shows the exact order. Places at most one order per check on its own under the user's standing authorisation, only when every guardrail in the skill passes; otherwise stops and asks.
---

# Market check (the £45 challenge)

Read the section "The £45 play-money challenge" in CLAUDE.md first. Those rules decide everything below. Nothing here is a proven edge: say so, plainly, every time.

## 1. Get the data (read-only)
- The user named a market or a move ("gold just dropped", "Nasdaq is falling"): run `python3 -m ig_swing.challenge quick --market "<market>"`. Markets: EUR/USD, GBP/USD, USD/JPY, AUD/USD, Gold, Crude oil, US Tech 100, US 500, Germany 40. It prints the price and move in daily ranges, the last 24 hours, the 20-day range, IG client positioning, open positions, the next 48 hours of calendar events and the headlines that mention that market or the macro backdrop.
- No market named, or a market-wide move: run `python3 -m ig_swing.challenge brief` (about 30 API calls). **Run it once per session.** IG rate-limits the API key: if you see `exceeded-api-key-allowance`, stop, tell the user, and do not retry in a loop.
- A drop in one market: also run the quick read on two or three related ones (for example US 500 and Nasdaq together, gold with the dollar pairs) instead of the full briefing.

What the data does and does not cover, so you do not overclaim: the calendar is US releases (BLS and FRED dates, the Fed's meeting dates) with times only where BLS gives them, and no forecasts or consensus numbers; there is no UK or euro-area calendar and no earnings calendar here. Headlines are titles only from the free RSS feeds and Finnhub. IG client positioning comes from IG's own sentiment feed. If the cause of a move is not in this data, use WebSearch to look for it, and say plainly when you still cannot find one.

## 1b. Research pass (for any idea you might take live, and for any sudden move you cannot explain)
Use WebSearch and WebFetch for about three to six lookups, not an open-ended trawl. Aim to find:
- the **cause and timing** of the move, from a primary or wire source (a central bank statement, the BLS or other official release, Reuters, AP, Bloomberg, the company filing) rather than a blog;
- for a data release, the **forecast against the actual number**: the surprise is what moves price, and the free calendar has no forecasts;
- **how much is already priced in**: when the news broke and how far price has moved since, because a move that has already happened is not a trade;
- the other side: the best argument against the idea, and what would prove it wrong.
Record how deep you went with `--research none|headlines|web` and the sources you relied on with `--source` (repeatable). More reading is not an edge: markets absorb news in seconds and you are reading it late, so do not raise `--p-win` because a story sounds convincing. A persuasive narrative found after the move is a reason for suspicion. The report compares results by research depth, which is how we find out whether reading more helps. Flag secondary or promotional sources as such.

## 2. Read it like an analyst
- How big is the move in daily ranges (ATR)? Over two is unusual. Is it market-wide or one market?
- Is there a cause in the headlines or the calendar (CPI, jobs, FOMC, geopolitics)? Say if you cannot find one.
- Where is price against its 20-day range (breakdown, breakout, middle)? What are IG clients doing (extreme positioning is only a hypothesis, never a rule)?
- Open positions, the £15 floor, the cooldown after two losses, and the drawdown brake. Read the equity line.
- For a sudden drop, the honest answer is often "no trade". Fading and following a spike both lack a validated edge: if it is unclear, record BOTH as shadow ideas and take neither live.

## 3. Write the ideas (zero to three)
For every idea you would seriously consider, record it with `python3 -m ig_swing.challenge idea --market ... --direction ... --stop <points> --target <points> --p-win <0.05 to 0.95> --playbook <event_reaction|news_thesis|earnings|trend|sentiment|other> --confidence <LOW|MEDIUM|HIGH> --thesis "<at most 300 characters>"`.
- Aim for at least 2 to 1 reward to risk. Stops are in points from the entry. Use sensible stops (about 1 to 1.5 daily ranges), not stops sized to fit the account.
- `--p-win` must be honest. Typical is 0.35 to 0.45. Never above 0.5 without a strong, stated reason. Overconfidence is the thing the calibration report exists to catch.
- Record the ideas the user will not take live too. Do not cherry-pick afterwards.
- The tool prints the 1/8 Kelly stake. If Kelly says do not trade, the idea stays shadow-only.

## 4. The live candidate (at most one per check): place it yourself
The user's standing instruction (2026-11-11, for the £45 challenge account only): place orders as part of this skill without asking each time. So do not wait for a yes. The guardrails in code still apply and are not negotiable.

Place it only if ALL of these hold:
- Kelly is positive (never use `--ignore-kelly`), the dry run says the order is allowed, and the profile in its first line is `challenge`.
- The stake fits (the dry run shows it), fewer than two positions are open, none is already open in that market, and the daily limit of two new trades is not reached.
- The market is tradeable and the spread on the dry run is no more than about three times its normal level from the briefing.
- There is a stated thesis, an invalidation (the stop), and an honest `--p-win`. You recorded the idea first with `idea` (record it live with `--live` if you place it).

How: run the dry run `python3 -m ig_swing.place --market ... --direction ... --stop ... --target ... --p-win ... --playbook ... --thesis "..." --idea-id N`, check it passes, then place it straight away with `IG_LIVE_ORDERS=yes python3 -m ig_swing.place ...` (same arguments) `--confirm <token>`. Then tell the user at once, in plain words: what, why, stake, stop level IG attached, target, risk in pounds and percent, the deal id, and that it is a gamble with no shown edge.

**Stop and ask the user instead of placing** if: any IG error or rate-limit message appears; the dry run is refused or the profile is not `challenge`; anything about the market or the order looks unusual or you are unsure of something material; the user's latest message told you to hold off. Show them the dry run and ask.

Placing is the only trade action this authorisation covers. There is no command to close or change a position yet: if an open position needs attention, tell the user and do not improvise. IG holds each stop server-side.

## 5. Report
Say what you saw, what you recorded, what (if anything) is proposed live, and what you do not know. Mention `python3 -m ig_swing.challenge report` for the scored shadow book and the probability check. Keep it short.

## Never
- Place an order outside the conditions in step 4, with `--ignore-kelly`, when the profile is not `challenge`, or on any account other than the challenge account.
- Change the risk caps, the floor, the cooldown, the Kelly fraction or any limit, or use `--ignore-kelly`, unless the user explicitly asks.
- Remove or widen a stop, add to a position, or average down.
- Retry IG calls in a loop after a rate-limit error, or print credentials.
- Claim an edge. Do not push to the remote without the user's go.
