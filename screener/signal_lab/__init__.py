"""screener/signal_lab — diagnose each signal's true predictive power.

Works off the cached tournament panel (raw signal scores + forward returns), so
it's cheap. Computes per-signal IC (overall / by regime / stability), quintile
spread, and a correlation matrix — and a plain-English keep/drop/flip verdict.
Feeds a conservative, out-of-sample-validated re-weighting. Hypothesis-grade,
in-sample-aware; never a silent live change.
"""
