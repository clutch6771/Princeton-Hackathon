"""Explicit development fakes.

Nothing here is wired unless configuration asks for it (``PROVIDER_MODE=fake``,
``STORAGE_MODE=memory``) and ``APP_ENV=development``. Production startup
refuses these modes; it never produces mock judgments.
"""
