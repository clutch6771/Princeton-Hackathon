"""Judge backend: reel ingestion, transcription and value-relative judgment.

Platform-neutral Python service. It receives sampled JPEG frames and app-audio
chunks, segments them into observed reel exposures, makes one multimodal model
call per reel that transcribes and judges it against the user's approved
values, stores the outcome in PostgreSQL (Tiger Data), and exposes verdicts
to the app.

Start with ``JUDGE_WORKFLOW.txt`` at the repository root.
"""

__version__ = "0.1.0"
