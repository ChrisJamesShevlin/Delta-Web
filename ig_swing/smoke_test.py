"""Read-only smoke test against the IG DEMO API.

Run from the repo root:  python3 -m ig_swing.smoke_test [EPIC]

Does: login, market rules, 60 daily bars, open positions.
Never: places, amends or closes anything. Prints no tokens, account ids or
deal ids. Uses 60 price points from the weekly historical-data allowance
(skipped if a cached copy is under 6 hours old).
"""

from __future__ import annotations

import sys

from .config import RISK, base_url, load_credentials
from .ig_client import IGClient

DEFAULT_EPIC = "IX.D.SPTRD.DAILY.IP"  # US 500 daily funded bet
BARS = 60


def main(argv: list[str]) -> int:
    epic = argv[1] if len(argv) > 1 else DEFAULT_EPIC
    print(f"Base URL: {base_url()}  (demo only)")
    print(f"Risk limits loaded: {RISK.risk_per_trade_pct}% per trade, max {RISK.max_open_positions} open")

    try:
        client = IGClient(load_credentials())
        client.login()
        print("1. Login: OK")

        rules = client.get_market_rules(epic)
        print(f"2. Market rules: {rules.instrument_name} (expiry {rules.expiry})")
        print(f"   min deal size {rules.min_deal_size}, min stop distance {rules.min_stop_distance} points")
        print(f"   bid {rules.bid}, offer {rules.offer}, spread {rules.spread}")

        df = client.get_prices(epic, "DAY", BARS)
        last = df.iloc[-1]
        print(f"3. Prices: {len(df)} daily bars, {df['timestamp'].iloc[0].date()} to {df['timestamp'].iloc[-1].date()}")
        print(f"   last close {last['close']:.2f}")

        positions = client.get_positions()
        print(f"4. Open positions: {len(positions)}")
        for p in positions:
            stop = "none" if p.stop_level is None else p.stop_level
            print(f"   {p.instrument_name}: {p.direction} {p.size} @ {p.open_level}, stop {stop}, P&L {p.unrealised_pnl}")
            if p.stop_level is None:
                print("   WARNING: this position has no stop")
    except Exception as exc:  # message holds status and error code only, never a body
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    print("Smoke test finished. Nothing was placed or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
