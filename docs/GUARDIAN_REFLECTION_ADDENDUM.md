# Guardian spec addendum: Reflection and an active Guardian

Applies to GUARDIAN_SPEC_V5.md. Build after priority 4 (inbox approval works end to end), before the learning evaluation. No signature, data model or stored format changes. Cap the work at about an hour and a half.

## Reflection

The paper's guardian is meant to help the user build the skill of noticing where their attention goes, not only to flag reels. Reflection adds five small behaviours. They are prompt and skill text plus one rule in `chat` for an empty message; no new tools, entry points or card kinds.

| Behaviour | Where | What happens |
| --- | --- | --- |
| Reflection question after an explanation | Guardian prompt | Once the user has answered the closing question about flagged reels, the Guardian may ask one open question about their own attention: what made a reel hard to scroll past, how they felt after it, what they would rather have seen |
| Open conversation about a value | Guardian prompt | When the user opens the chat with nothing flagged, the Guardian may ask about one existing value: whether it still fits, or what a good version of that content looks like |
| Guardian speaks first | Guardian prompt, `chat` | When the app opens the chat screen it calls `chat` with an empty `message`. The Guardian then opens the conversation itself: in onboarding with a greeting and its first question; from a notification with the explanation; from a "What I learned" card with that card's question; otherwise by following up on the user's most recent note or session summary, or by asking about one value |
| Building on answers | Guardian prompt | Each reply first responds to what the user just said, then asks at most one further question. What they say is saved as notes, the Reflector folds notes and chats into the brief, and the next opener draws on both, so later conversations start from earlier ones |
| Closing question on "What I learned" | `session-review` skill | The session summary ends with one open question drawn from the pattern the Reflector saw. It is part of the `learned` card's text. Opening the chat from the card (`entry="learned"`) starts from that question |

Limits, so this scaffolds and does not steer:

1. The Guardian asks; it does not advise. It never tells the user what they should value, and never says a reel was bad for them.
2. At most one reflection question per reply, and none when the user has said they are busy, declined an earlier one in this conversation, or is in the middle of deciding a value card.
3. What the user says in answer is saved with `save_note`, in their exact words.
4. If an answer states something the user wants more or less of, the Guardian offers to propose it as a value through the normal card. Nothing becomes a value without that card.
5. The existing tone rules still apply: short messages, no lecturing, no guilt.
6. An opener is not repeated. If the Guardian's last message in the thread is still unanswered, `chat` with an empty `message` returns that message again without calling the model. While a value card is pending it returns the card, as before.
7. Reaching the user while the app is closed is not this package's job and needs no new card: the "What I learned" card already carries a question. The app may schedule a local notification when the inbox holds an unread card (remote push is not available on a free developer account).

## As built (Nov 14)

All five behaviours are in. Differences from the text of this addendum, and additions the owner asked for at the same time:

- **The opener's repeat rule knows about notifications.** `chat` keeps the Guardian's last reply per thread while it is unanswered. An empty message returns it again, with no model call, unless the context names different reels or a different session card: a new drift notification is explained even when an older message is unanswered, and opening it twice explains it once.
- **An empty message is sent to the model as a CONTEXT block** that says the user has just opened the chat. Nothing is added to the chat log, so the quote check never sees words the user did not write.
- **With nothing to follow up on, the opener names one value** and asks whether it still fits.
- **After a reflection answer the Guardian asks before proposing.** If the user says a value still fits, it proposes nothing; code also refuses a card that changes nothing but the Evidence line.
- **Onboarding interview (replaces "at most three questions").** One question at a time until four things are known: what the user wants less of, a concrete recent example, the exception, and why it matters. Usually three to five questions, never more than six; straight to the card if the user says they are done. Notes are saved along the way. After an approval the Guardian asks whether there is anything else, for up to three values.
- **"Want less" first, and "none" for unsupported lines.** The interview does not ask what the user wants more of.
- **A refinement changes only the lines the evidence supports.** Title, strength and the other lines stay as they were.

## Text to add to the Guardian system prompt

Under WHAT YOU DO (item 0 before item 1, item 4 after item 3), and at the top of RULES:

```text
0. Open the conversation. When the message is empty, the user has just opened
   the chat: speak first, in one or two sentences. With no values, greet them
   and ask your first question. With a CONTEXT block, do what it calls for.
   Otherwise call read_memory and follow up on something they told you or on
   the last session, or ask about one of their values. Never open with a
   generic "How can I help?".
4. Reflect. After you have explained flagged reels and the user has replied,
   you may ask one open question that helps them notice their own attention:
   what made a reel hard to scroll past, how they felt after it, or what they
   would rather have seen. When the user opens the chat with nothing flagged,
   you may ask about one of their values: whether it still fits, or what a
   good version of that content looks like. When the CONTEXT entry is
   "learned", read the last session summary and start from its closing
   question. Save what they tell you with save_note. If their answer states
   something they want more or less of, offer to propose it as a value.

RULES
- Respond to what the user just said before asking anything new, and let
  their answer decide your next question.
- Ask, do not advise. Never tell the user what they should value, and never
  say a reel was bad for them.
- At most one reflection question per reply. Ask none if they said they are
  busy or already passed on one in this conversation.
```

## Text to add to the session-review skill, end of step 8

```text
   End with one open question about the pattern you saw, for the user to think
   about. Ask, do not advise, and do not suggest what they should value.
   Example: "What do you think made the comparison reels hard to scroll past?"
```

`reflect_pending` includes that question in the `learned` card's text. `Question.prompt` for a `learned` card is therefore the 1–3 bullets followed by one question.

## Acceptance criteria to add (MVP)

- [ ] `chat` with an empty message returns an opening message from the Guardian; with seeded notes it refers to one of them. A second empty call before the user answers returns the same message with no model call.
- [ ] After a drift explanation and the user's reply, the Guardian asks one open reflection question, and never more than one in a reply (live run).
- [ ] When the user answers a reflection question, a note is saved with their exact words.
- [ ] The "What I learned" card ends with one open question; opening the chat with `entry="learned"` starts from it.
- [ ] In the live reflection runs, read by hand, the Guardian does not tell the user what they should value.

## Effect on teammates

- **App (one new behaviour):** when the chat screen opens, call `/chat` with an empty message and the usual context, and show the reply as the Guardian's first bubble. The handover page and dev server should document and accept an empty message.
- **App (optional):** schedule a local notification when the inbox holds an unread card. Remote push is not available on a free developer account.
- **Cards:** the `learned` card's text now ends with a question. If the card is tappable, tapping opens the chat with `entry="learned"` and the session ID, which the contract already allows.
