"""Versioned, immutable data contracts shared by every layer of the Judge.

Wire contracts (what a native adapter or app sends and receives) are exported
as JSON Schema to ``watcher_Agent/contracts/v1``. Storage records live here too
so that repositories, services and tests share one set of typed shapes.
"""
