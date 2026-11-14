"""Wording of a warning.

A warning states how many recent reels went against what the user said they
wanted. It never calls content bad and never tells the user what to do. The
value-grounded reason for each reel is stored on its record.
"""

from __future__ import annotations

NOTIFICATION_TITLE = "A check-in on your feed"


def notification_text(*, conflict_count: int, batch_size: int) -> tuple[str, str]:
    """Return ``(title, body)`` for a triggered batch."""
    body = (
        f"{conflict_count} of your last {batch_size} reels went against what "
        f"you told me you wanted."
    )
    return NOTIFICATION_TITLE, body
