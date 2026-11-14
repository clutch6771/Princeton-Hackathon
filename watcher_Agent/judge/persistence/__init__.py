"""Persistence: repository interfaces and the PostgreSQL (Tiger Data) adapter.

Only text, metadata, embeddings and one thumbnail per accepted reel are ever
stored. No raw audio, video or additional frames reach the database.
"""
