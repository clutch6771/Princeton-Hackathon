"""Developer commands, typed into the Guardian chat as "/dev <command>".

They are for the team, to inspect and clear what the server holds while testing
and before a demo. A "/dev" message is handled here, in code: it never reaches the
model and is not kept in the chat log.

    /dev help                 this list
    /dev whoami               your user ID, today's thread and what is stored for you
    /dev users                every user profile on this server, with its values
    /dev values               your approved values, as the Judge reads them
    /dev memory               everything the Guardian holds about you
    /dev wipe-values          force delete your approved values
    /dev wipe-values all      force delete the approved values of every user profile
    /dev reset                erase everything stored about you

The commands are off unless GUARDIAN_DEV_COMMANDS=1, because the wipe and reset
commands cannot be undone and "wipe-values all" reaches every user of the server.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from guardian import api, config
from guardian_router import background, reset_demo

PREFIX = "/dev"
ENABLE_VAR = "GUARDIAN_DEV_COMMANDS"  # the commands answer only when this is "1"
ALL_USERS = "all"
EMPTY = "(empty)"

DISABLED = (
    f"Developer commands are off on this server. Set {ENABLE_VAR}=1 and restart it "
    "to use them."
)
HELP = """Developer commands:
/dev help - this list
/dev whoami - your user ID, today's thread and what is stored for you
/dev users - every user profile on this server, with its values
/dev values - your approved values, as the Judge reads them
/dev memory - everything the Guardian holds about you
/dev wipe-values - force delete your approved values (no card, no confirmation)
/dev wipe-values all - force delete the approved values of every user profile
/dev reset - erase everything stored about you: values, cards, notes, chat and reviews

The wipe and reset commands cannot be undone."""
UNKNOWN = "Unknown developer command {name!r}."
WIPE_USAGE = 'Use "/dev wipe-values" for your own values or "/dev wipe-values all".'
NOTHING_TO_WIPE = "No approved values were stored, so nothing was deleted."
RESET_WHILE_RECORDING = (
    "A recording session is still open. Stop the recording, then send /dev reset again."
)
RESET_WHILE_REVIEWING = (
    "A review of the last session is still running. Try /dev reset again in a few "
    "minutes."
)
RESET_FAILED = "The reset failed and nothing was deleted."


@dataclass(frozen=True)
class Caller:
    """Who sent the command, and what the chat adapter lends to carry it out."""

    user_id: str
    thread_id: str
    database_url: str | None  # the Guardian's Postgres connection; None when in memory
    forget: Callable[[str], None]  # drop the adapter's memory of one user's chat


def is_command(text: str) -> bool:
    words = text.split()
    return bool(words) and words[0].lower() == PREFIX


def enabled() -> bool:
    config.load_env()  # so the switch may be set in the repository's .env
    return os.environ.get(ENABLE_VAR, "").strip() == "1"


def run(text: str, caller: Caller) -> str:
    """The reply to one "/dev ..." message."""
    if not enabled():
        return DISABLED
    words = [word.lower() for word in text.split()[1:]]
    name, arguments = (words[0], words[1:]) if words else ("help", [])
    if name == "help":
        return HELP
    if name == "whoami":
        return _whoami(caller)
    if name == "users":
        return _users()
    if name == "values":
        return api.get_values(caller.user_id) or EMPTY
    if name == "memory":
        return _memory(caller.user_id)
    if name == "wipe-values":
        return _wipe_values(caller, arguments)
    if name == "reset":
        return _reset(caller)
    return f"{UNKNOWN.format(name=name)}\n\n{HELP}"


def _value_ids(user_id: str) -> list[str]:
    """The IDs of a user's approved values, read from the entry headers."""
    headers = [
        line for line in api.get_values(user_id).splitlines() if line.startswith("## ")
    ]
    return [header.removeprefix("## ").split(":", 1)[0].strip() for header in headers]


def _whoami(caller: Caller) -> str:
    value_ids = _value_ids(caller.user_id)
    card = api.pending_card(caller.user_id, caller.thread_id)
    return "\n".join(
        (
            f"User: {caller.user_id}",
            f"Thread: {caller.thread_id}",
            f"Storage: {'Postgres' if caller.database_url else 'in memory'}",
            f"Approved values: {', '.join(value_ids) or 'none'}",
            f"Card waiting in chat: {'yes' if card else 'no'}",
        )
    )


def _users() -> str:
    users = api.list_users()
    if not users:
        return "No user profiles are stored."
    lines = [f"{user}: {', '.join(_value_ids(user)) or 'no values'}" for user in users]
    return "\n".join([f"{len(users)} user profile(s):", *lines])


def _memory(user_id: str) -> str:
    files = api.get_memory(user_id)
    return "\n\n".join(
        f"== {name} ==\n{text.strip() or EMPTY}" for name, text in files.items()
    )


def _wipe_values(caller: Caller, arguments: list[str]) -> str:
    if arguments not in ([], [ALL_USERS]):
        return WIPE_USAGE
    users = api.list_users() if arguments else [caller.user_id]
    deleted = {user: api.force_delete_values(user) for user in users}
    emptied = {user: ids for user, ids in deleted.items() if ids}
    if not emptied:
        return NOTHING_TO_WIPE
    total = sum(len(ids) for ids in emptied.values())
    lines = [f"{user}: {', '.join(ids)}" for user, ids in emptied.items()]
    heading = f"Force-deleted {total} value(s) from {len(emptied)} user profile(s):"
    return "\n".join([heading, *lines])


def _reset(caller: Caller) -> str:
    user_id = caller.user_id
    if api.is_recording(user_id):
        return RESET_WHILE_RECORDING
    if background.is_reviewing(user_id):
        return RESET_WHILE_REVIEWING
    if caller.database_url:
        try:
            deleted = reset_demo.reset(caller.database_url, user_id, confirmed=True)
        except psycopg.Error:
            return RESET_FAILED
    else:
        deleted = api.erase_user(user_id)
    caller.forget(user_id)
    counts = ", ".join(f"{label}: {count}" for label, count in deleted.items())
    return f"Erased everything stored about {user_id} ({counts or 'nothing found'})."
