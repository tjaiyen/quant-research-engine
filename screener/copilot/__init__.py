"""screener/copilot — optional AI co-pilot (Claude reasoning overlay).

Claude reads each cycle's data and writes a first-person take (conviction +
concerns). ADVISORY ONLY: it never places trades — the deterministic quant
engine + 8 risk guards remain the sole trade path. Opt-in (default OFF) and
degrades gracefully when the SDK or an API key is absent.
"""
