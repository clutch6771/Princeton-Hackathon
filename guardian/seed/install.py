"""Install the persona's initial values for demos, tests and the evaluation.

The values go through the same path as any other value: the persona's onboarding
statements are recorded as chat messages, each value is proposed, and each proposal is
approved. Nothing writes values directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from langgraph.store.base import BaseStore

from guardian.datasource import SEED_DIR, DataSource
from guardian.memory import proposals, records, values

SOURCE_SEED = "seed"
ONBOARDING_EVIDENCE = "said at onboarding"


def install_initial_values(
    store: BaseStore,
    datasource: DataSource,
    user_id: str,
    now: datetime,
    seed_dir: Path = SEED_DIR,
) -> list[str]:
    """Approve the persona's three onboarding values for a user who has none.

    Returns the IDs of the user's values. Does nothing if the user already has values.
    """
    if values.has_values(store, user_id):
        return [value.id for value in values.list_values(store, user_id)]

    documents = json.loads((seed_dir / "initial_values.json").read_text())
    for index, document in enumerate(documents):
        records.append_chat_turn(
            store, user_id, records.ROLE_USER, document["statement"], now
        )
        created = proposals.create_proposal(
            store,
            datasource,
            user_id,
            value_id=proposals.TARGET_NEW,
            new_text=document["entry"],
            evidence=ONBOARDING_EVIDENCE,
            source={"kind": SOURCE_SEED, "index": str(index)},
            now=now,
        )
        if created.proposal is None:
            raise ValueError(f"seed value {index} was refused: {created.error}")
        proposals.decide(
            store,
            user_id,
            created.proposal.id,
            action=proposals.ACTION_APPROVE,
            edited_text=None,
            now=now,
        )
    return [value.id for value in values.list_values(store, user_id)]
