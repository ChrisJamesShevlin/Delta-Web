# Rule changelog

Every change to a trading or staking rule goes here with its date, so results can be split into before and after (`python3 -m ig_swing.challenge review` reads the newest date). A change is judged on ideas written AFTER it, never by re-scoring old ones. One change at a time, and say what you expect it to do before looking at the numbers. Entries start with `- YYYY-MM-DD`.

- 2026-11-11 Challenge profile started: ceiling 10% of equity at risk per trade, 5% until my stated probabilities are shown to be honest (30 or more finished ideas within 10 points of the hit rate), 1/8 Kelly, floor at £15, cooldown of 24 hours after two losing trades, drawdown brake at 25% below the peak, at most two new trades per UTC day.
- 2026-11-11 Position management started: cut losers early when the thesis is broken, tighten stops (never widen), trailing stop from +1R at 1R behind the best price, take-profit extended while the move is working.
- 2026-11-11 Mechanical baseline started: long if the price is above its 20-day average, else short; 1.5 ATR stop, 3 ATR target, 5 days; one per market per UTC day from the first briefing of the day; shares excluded.
- 2026-11-11 Shadow book scores each idea four ways: fixed stop and target, and a trailing stop from +1R at 1R, 1.5R and 2R behind the best price.
