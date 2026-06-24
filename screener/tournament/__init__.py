"""screener/tournament — strategy tournament (backtest competition + attribution).

Race N strategy variants over real historical prices, rank risk-adjusted, and
attribute the winner. The expensive signal scoring is computed ONCE into a
`panel` (causal — history sliced to each rebalance date); every variant is then
a cheap re-weight / re-select / re-size pass over that panel, so 20 variants cost
about as much as one. Framed as a guarded hypothesis (controls + out-of-sample),
never proof — same in-sample-aware caveat as the U4 backtest.
"""
