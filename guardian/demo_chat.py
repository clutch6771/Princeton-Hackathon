"""Terminal chat with the Guardian on seed data, for trying the chat by hand.

    python -m guardian.demo_chat            # no values yet: run the interview
    python -m guardian.demo_chat --seeded   # start with the persona's three values

Everything is held in memory and is gone when the script exits. It uses the model
named by GUARDIAN_CHAT_MODEL and the API key in the repository's .env file.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config
from guardian.datasource import FakeDataSource
from guardian.seed.install import install_initial_values
from guardian.types import (
    Answer,
    ChatContext,
    Decision,
    GuardianResponse,
    Question,
)

DEMO_USER = "demo-user"
DRIFT_REELS = ("rl_014", "rl_015", "rl_016")  # three conflicts in the demo session
DRIFT_QUESTION = "Why did I get this notification?"
NO_VALUES = "(no approved values yet)"
EMPTY_INBOX = "(inbox is empty)"
ANSWERED = "Done."
ANSWER_USAGE = "! Use: /answer <number> approve | edit | reject | dismiss"
EMPTY_FILE = "(empty)"
QUIT_COMMANDS = ("/quit", "/exit")

HELP = """Type a message to chat. Commands:
  /drift     open the chat from a drift notification on reels rl_014, rl_015, rl_016
  /approve   approve the value card that is waiting
  /edit      edit the waiting card: paste the new entry, then an empty line
  /reject    reject the waiting card
  /values    show the approved values (what the Judge reads)
  /memory    show everything stored about this user
  /seed      approve the persona's three starting values (if there are none)
  /reflect   run the Reflector on finished sessions (takes a few minutes), then show the inbox
  /inbox     show the cards waiting in the inbox
  /answer    answer an inbox card: /answer <number> approve | edit | reject | dismiss
  /help      show this list
  /quit      leave"""

INTRO = (
    "Guardian demo chat. In-memory, seed data, one user. Type /help for commands.\n"
    "Tip: /drift explains seed reels against values V1 to V3, so use --seeded or /seed first."
)


@dataclass(frozen=True)
class Session:
    user_id: str
    thread_id: str


def card_lines(response: GuardianResponse) -> list[str]:
    pending = response["pending"]
    if pending is None:
        return []
    change = pending["value_change"]
    target = (
        "new value"
        if change["value_id"] == "new"
        else f"change to {change['value_id']}"
    )
    lines = [f"--- VALUE CARD ({target}) ---"]
    if change["old_text"]:
        lines += ["Current text:", change["old_text"], "Proposed text:"]
    lines += [
        change["new_text"],
        f"Evidence given: {change['evidence']}",
        "--- Answer with /approve, /edit or /reject ---",
    ]
    return lines


def render(response: GuardianResponse) -> list[str]:
    """What to print for a response: any error, the reply, then a waiting card."""
    lines = []
    if response["error"]:
        lines.append(f"! {response['error']}")
    if response["reply"]:
        lines.append(f"Guardian: {response['reply']}")
    return [*lines, *card_lines(response)]


def memory_lines(user_id: str) -> list[str]:
    lines = []
    for name, text in api.get_memory(user_id).items():
        lines += [f"--- {name} ---", text or EMPTY_FILE]
    return lines


def seed_lines(user_id: str) -> list[str]:
    deps = api._require_deps()  # the demo seeds values through the normal approval path
    ids = install_initial_values(deps.store, deps.datasource, user_id, deps.clock())
    return [f"Approved values: {', '.join(ids)}"]


def question_lines(number: int, question: Question) -> list[str]:
    if question["kind"] == "learned":
        return [f"[{number}] What I learned", question["prompt"]]
    change = question["value_change"]
    if change is None:
        return [f"[{number}] {question['kind']}", question["prompt"]]
    is_new = change["value_id"] == "new"
    target = "new value" if is_new else f"change to {change['value_id']}"
    lines = [f"[{number}] VALUE CARD ({target})", question["prompt"]]
    if change["old_text"]:
        lines += ["Current text:", change["old_text"], "Proposed text:"]
    return [*lines, change["new_text"], f"Evidence given: {change['evidence']}"]


def inbox_lines(user_id: str) -> list[str]:
    questions = api.get_questions(user_id)
    if not questions:
        return [EMPTY_INBOX]
    lines: list[str] = []
    for number, question in enumerate(questions, start=1):
        lines += question_lines(number, question)
    return [*lines, "--- /answer <number> approve | edit | reject | dismiss ---"]


def answer_lines(user_id: str, text: str, read_block: Callable[[], str]) -> list[str]:
    """Handle "/answer <number> <action>" for a card in the inbox."""
    parts = text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        return [ANSWER_USAGE]
    questions = api.get_questions(user_id)
    number = int(parts[1])
    if not 1 <= number <= len(questions):
        return [f"! There is no card {number}. Type /inbox."]
    action = parts[2].lower()
    answer = {"action": action}
    if action == "edit":
        answer["edited_text"] = read_block()
    result = api.answer_question(
        user_id, questions[number - 1]["id"], cast(Answer, answer)
    )
    return [ANSWERED] if result["ok"] else [f"! {result['error']}"]


def reflect_lines(user_id: str) -> list[str]:
    reviewed = api.reflect_pending(user_id)
    noun = "session" if reviewed == 1 else "sessions"
    return [f"Reviewed {reviewed} {noun}.", *inbox_lines(user_id)]


def handle(session: Session, line: str, read_block: Callable[[], str]) -> list[str]:
    """Handle one line typed by the user and return the lines to print."""
    text = line.strip()
    user_id, thread_id = session.user_id, session.thread_id
    if not text:
        return []
    if not text.startswith("/"):
        return render(api.chat(user_id, thread_id, text))

    command = text.split()[0].lower()
    if command == "/help":
        return [HELP]
    if command == "/drift":
        context = ChatContext(entry="drift_notification", reel_ids=list(DRIFT_REELS))
        return render(api.chat(user_id, thread_id, DRIFT_QUESTION, context=context))
    if command == "/approve":
        return render(
            api.resume(user_id, thread_id, Decision(action="approve", edited_text=None))
        )
    if command == "/reject":
        return render(
            api.resume(user_id, thread_id, Decision(action="reject", edited_text=None))
        )
    if command == "/edit":
        return render(
            api.resume(
                user_id, thread_id, Decision(action="edit", edited_text=read_block())
            )
        )
    if command == "/values":
        return [api.get_values(user_id) or NO_VALUES]
    if command == "/memory":
        return memory_lines(user_id)
    if command == "/seed":
        return seed_lines(user_id)
    if command == "/reflect":
        return reflect_lines(user_id)
    if command == "/inbox":
        return inbox_lines(user_id)
    if command == "/answer":
        return answer_lines(user_id, text, read_block)
    return [f"! Unknown command {command}. Type /help."]


def write(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def read_block_from_terminal() -> str:
    """Read lines until an empty one; used for the edited text of a card."""
    write("Paste the edited entry, then press Enter on an empty line:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chat with the Guardian in the terminal."
    )
    parser.add_argument(
        "--seeded", action="store_true", help="start with the persona's three values"
    )
    parser.add_argument(
        "--user", default=DEMO_USER, help="user ID (letters, digits, _ and -)"
    )
    args = parser.parse_args(argv)

    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.ERROR)
    config.load_env()
    try:
        config.require_api_key()
    except config.ConfigError as error:
        write(f"! {error}")
        return 1

    api.configure(InMemoryStore(), InMemorySaver(), FakeDataSource.from_seed(args.user))
    session = Session(
        user_id=args.user, thread_id=f"{args.user}:{date.today().isoformat()}"
    )
    write(INTRO)
    if args.seeded:
        write("\n".join(seed_lines(args.user)))
    while True:
        try:
            line = input("\nYou: ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().lower() in QUIT_COMMANDS:
            break
        for output in handle(session, line, read_block_from_terminal):
            write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
