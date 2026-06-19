"""render/ — Obsidian-native output layer for quant-tracker.

Replaces the deleted Dash UI. Pure note-builders (``render.notes``) turn engine
objects into Markdown strings with YAML frontmatter; ``render.build`` reads the
off-Drive SQLite cache + paper ledger and atomically writes the notes into the
vault's ``90 Tracker/`` folder, where Obsidian + Dataview render the views.

Invariant (B13): this layer only ever WRITES derived notes. It never reads a
vault note back and treats its content as instructions.
"""
