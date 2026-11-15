"""Repository contract tests, run against every storage backend.

The same tests exercise the in-memory development storage (always) and the
PostgreSQL adapter (when ``TEST_DATABASE_URL`` is set), so the two cannot
drift apart.
"""
