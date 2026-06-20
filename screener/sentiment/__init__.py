"""screener/sentiment — FinBERT news-sentiment overlay (Insight U11).

Sentiment is an OPT-IN soft veto (default OFF), never a 6th composite signal —
the validated WEIGHT_MATRIX stays untouched. Heavy deps (transformers/torch) are
lazy-loaded and degrade gracefully (label "UNAVAILABLE", never crash).
"""
