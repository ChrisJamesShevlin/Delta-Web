---
name: market-check
description: Use when the user says "market check", "check the markets", "any trades?", or reports a sudden drop or spike in a market and asks whether there is a trade. Runs the read-only IG briefing or a single-market quick read, reads it, records shadow ideas with honest win probabilities and 1/8 Kelly sizing for the £45 play-money challenge, picks at most one live candidate, and shows the exact order. Never places an order without the user's explicit yes to that exact order.
---

# Market check (the £45 challenge)

Read the section "The £45 play-money challenge" in CLAUDE.md first. Those rules decide everything below. Nothing here is a proven edge: say so, plainly, every time.

## 1. Get the data (read-only)
- The user named a market or a move ("gold just dropped", "Nasdaq is falling"): run `python3 -m ig_swing.challenge quick --market "<market>"`. Markets: EUR/USD, GBP/USD, USD/JPY, AUD/USD, Gold, Crude oil, US Tech 100, US 500, Germany 40.
- No market named, or a market-wide move: run `python3 -m ig_swing.challenge brief` (about 30 API calls). **Run it once per session.** IG rate-limits the API key: if you see `exceeded-api-key-allowance`, stop, tell the user, and do not retry in a loop.
- A drop in one market: also run the quick read on two or three related ones (for example US 500 and Nasdaq together, gold with the dollar pairs) instead of the full briefing.

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

## 4. The live candidate (at most one)
Only if all of these hold: Kelly is positive, the stake fits the account (the briefing shows the stake hint and whether it is margin-limited or below the minimum), fewer than two positions are open, no cooldown or floor problem.
1. Run the dry run: `python3 -m ig_swing.place --market ... --direction ... --stop ... --target ... --p-win ... --playbook ... --thesis "..." --idea-id N`. It places nothing and prints the exact order and a confirmation token.
2. Show the user exactly that: market, direction, stake per point, stop, target, risk in pounds and percent, margin, spread. Say plainly that it is a gamble with no shown edge.
3. **Wait for the user to say yes to that exact order, in the chat.** A general "ok" earlier does not count. If anything changes (stake, stop, target, market, direction), run the dry run again and ask again.
4. Only then place it: `IG_LIVE_ORDERS=yes python3 -m ig_swing.place ... --confirm <token>`. Report the result, the deal id and the stop level IG attached.

## 5. Report
Say what you saw, what you recorded, what (if anything) is proposed live, and what you do not know. Mention `python3 -m ig_swing.challenge report` for the scored shadow book and the probability check. Keep it short.

## Never
- Place an order without the exact-order yes, or run a command with `IG_LIVE_ORDERS=yes` before it.
- Change the risk caps, the floor, the cooldown, the Kelly fraction or any limit, or use `--ignore-kelly`, unless the user explicitly asks.
- Remove or widen a stop, add to a position, or average down.
- Retry IG calls in a loop after a rate-limit error, or print credentials.
- Claim an edge. Do not push to the remote without the user's go.
