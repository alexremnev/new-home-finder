"""The stages a run is made of.

Nothing is re-exported here on purpose: `match` holds decisions and needs no
database, `outbox` holds the queue, and `run` wires them into the two jobs. Import
what you need directly, so a reader can see which half of the pipeline a caller
touches.
"""
