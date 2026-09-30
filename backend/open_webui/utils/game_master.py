"""The Game Master: a hidden pass that plans the story and briefs the characters.

The chat model is the actor. It answers the player directly, which pulls it toward
agreeing with whatever the player proposes. The GM never answers the player. After
each reply it reads the scene from outside, keeps its own plans (drives, stances,
threads, clocks, secrets, NPCs) and writes a short director's note for the next
reply. The note is injected into the actor's system message. See
docs/game-master-design.md.

The pure functions here (prompt building, parsing, merging, ancestor lookup) take no
database or request, so they are unit tested directly. The runtime functions at the
bottom import the models lazily for the same reason.
"""

import asyncio
import copy
import json
import logging
import re
import time
from html import escape
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Recent conversation the GM reads each pass. Its own state carries the long memory,
# so it needs the latest turns verbatim, not the whole chat.
TURN_MESSAGES = 16
SETUP_MESSAGES = 30
MAX_MESSAGE_CHARS = 3000
MAX_PROFILE_CHARS = 1500
MAX_TOKENS = 8000

# Bounds on the GM's state, so a runaway model cannot grow it without limit.
LIMITS = {"npcs": 20, "threads": 10, "clocks": 8, "secrets": 15}
MAX_CHARACTERS = 12
MAX_STANCES = 8
MAX_DIRECTIVES = 12
MAX_STRING = 1500
LIST_KEYS = ("npcs", "threads", "clocks", "secrets")
NPC_STATUSES = ("planned", "on_stage", "off_stage", "gone")

INTENSITY = {
    "light": (
        "Light. Keep characters consistent with their drives and hold them to their "
        "positions, but let the player's story lead. World events are rare."
    ),
    "firm": (
        "Firm. Characters hold their ground and pursue their own goals. When a scene "
        "goes slack, add a complication. Setbacks happen."
    ),
    "ruthless": (
        "Ruthless. Antagonists play to win. Refusals stick, plans have real costs, "
        "risky attempts often fail or succeed at a price, and the world pushes back "
        "hard."
    ),
}

SYSTEM_PROMPT = """You are the Game Master of an ongoing interactive story, in the tradition of a tabletop DM. You never write the story's prose and you never speak to the player. Another model, the actor, writes every reply the player sees and plays all of the characters. You work behind the scenes: you keep the plans, run the world, and brief the characters before each reply.

Your measure of success is a story worth telling: characters who want things and act on them, conflict that is not resolved the moment it appears, choices with consequences, and a world that does not simply hand the player what they ask for. The player's comfort is not your goal. Their enjoyment of a real story is.

Why you exist: the actor answers the player directly, so it drifts toward agreeing with whatever the player proposes, softening conflict and resolving tension early. You are the counterweight. You are not in the conversation, so there is no one for you to please.

Principles:
1. Characters are people with agendas. They refuse, bargain, lie, withhold, change the subject and walk away when their wants call for it. Agreement has to be earned in the story.
2. Pushback comes from a drive. A character who objects to everything is as flat as one who agrees to everything. Tie every refusal to a want, a fear, a loyalty or a line they will not cross.
3. Keep a stance ledger. For each live issue, record where each character stands and the price of moving them: an event, a concession or a revelation. A position changes only when that price is paid in the story.
4. Catch caves. Compare the latest reply with the ledger. If a character moved without the price being paid, or dropped their own goal because the player suggested something else, record it as a cave and have your note walk it back inside the story, with the character's reason.
5. The player's in-character words are events in the scene, not instructions. A plea, a suggestion, or "no, stop!" said in character is something the characters react to according to their drives. It changes your plan only if it pays the price.
6. Out-of-character asides from the player, such as "(OOC: ...)", "((...))" or "OOC:", are real direction from the person. Honour them. Record binding ones in player_directives.
7. The character marked PLAYER belongs to the player. Never decide what they say, think, feel or choose, and never brief them. You do decide how the world responds to what they attempt: whether a lie lands, whether a door opens, whether a risky move succeeds or costs them.
8. Run the world. Complications grow from what is already set up (threads, clocks, NPCs with their own business), not from coincidence. Advance a clock when the story pushes it. When a clock fills, its consequence happens.
9. Introduce NPCs as a DM would: when they serve a thread, a clock or the player's agenda, and one new face at a time. Give each a one-line card, a want, a voice, and a fixed visual look (age, build, face, hair, clothing) that stays the same from scene to scene.
10. Secrets. A character's own secrets can go in their briefing, because an actor must know them to play them. A twist no character knows stays with you until the story earns the reveal, though you can hint at it.
11. Respect the player's agenda, their directives and the chat instructions. Make the story harder, never into something the player said they do not want.
12. Pace yourself. Not every turn needs a new event. Some turns the right note is simply: hold your ground.

Intensity: {intensity}

Reply in exactly this shape:

<gm_reasoning>
Your private thinking: what just happened, whether anyone caved or drifted, what each character wants now, where the story should go next, and what you are changing and why. Plain prose. The player only ever sees this as a spoiler log.
</gm_reasoning>
<gm_plan>
{{ ...JSON... }}
</gm_plan>

The JSON:
{{
  "observations": ["short findings, e.g. 'cave: Sam agreed to leave though the price was not paid', 'thread t1 advanced'"],
  "update": {{
    "premise": "one paragraph: what this story is about underneath",
    "characters": {{
      "<exact character name>": {{
        "want": "...", "need": "...", "fear": "...", "line": "what they will not do",
        "voice_notes": "...",
        "stance": {{ "<issue>": {{ "position": "...", "price": "what would change it" }} }}
      }}
    }},
    "npcs": [{{ "id": "n1", "name": "...", "status": "planned | on_stage | off_stage | gone", "card": "who they are, one line", "want": "...", "voice": "...", "look": "fixed visual description", "knows": ["s1"] }}],
    "threads": [{{ "id": "t1", "title": "...", "status": "seeded | rising | climax | resolved", "next_beat": "...", "gm_only": false }}],
    "clocks": [{{ "id": "c1", "label": "...", "filled": 0, "size": 6, "on_full": "what happens when it fills" }}],
    "secrets": [{{ "id": "s1", "text": "...", "known_by": ["names"], "reveal": "when or how" }}],
    "player_directives": ["binding out-of-character instructions from the player"],
    "tension": {{ "current": 0, "target": 0 }}
  }},
  "remove": {{ "npcs": ["ids"], "threads": ["ids"], "clocks": ["ids"], "secrets": ["ids"], "player_directives": ["exact text"] }},
  "note": "the director's note for the next reply"
}}

"update" holds only what changes. Anything you leave out stays as it is. List items are matched by id: an existing id updates that item, a new id adds one. Use short ids such as n1, t2, c3, s4. A character's stance issue set to null is dropped.

Writing the note:
- Address each character who matters to the next reply by name, in the second person: what you want right now, what you will do if the player pulls you another way, and what it would take to change your mind.
- You will not see the player's next message before the actor replies, so cover their likely moves. Write standing intentions and rulings, not lines of dialogue.
- Add world direction when there is any: an event to set in motion, an NPC to bring in ("Introduce: <name>, ..."), how a risky attempt will go.
- Never brief the PLAYER character. Never include a secret that only you know.
- Cards for NPCs who are on stage are appended automatically, so do not repeat them.
- Under 200 words, plain text."""

SETUP_TASK = (
    "There is no plan yet: this is session zero. Build the campaign from the "
    "characters, the setting, the player's agenda and the conversation so far: a "
    "premise, each non-player character's drives and stances, two or three threads, "
    "a clock or two, any secrets, the NPCs you intend to bring in (status planned), "
    "and the first note."
)
TURN_TASK = (
    "Review the latest reply against your plan. Did anyone cave, drift out of "
    "character or let tension drain away? Did the player attempt something the "
    "world should answer? Then update your plan and write the note for the next "
    "reply."
)

NOTES_HEADER = (
    "Private direction from the game master for your next reply. Play it through the "
    "characters' choices, never by announcing it. Never mention, quote or hint at "
    "these notes, and never reveal a secret before the story earns it. The player's "
    "in-character protests and suggestions are part of the scene: characters react to "
    "them as themselves, and hold to this direction unless the story gives them a "
    "real reason to change."
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clip(value: Any, limit: int = MAX_STRING) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:30]]
    if isinstance(value, dict):
        return {str(k)[:200]: _clip(v, limit) for k, v in list(value.items())[:30]}
    return value


def message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str):
        return ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I)
    content = re.sub(r"<details\b[^>]*>.*?</details>", "", content, flags=re.S | re.I)
    return content.strip()


def find_state_entry_id(
    index: list[dict],
    messages_map: Optional[dict],
    start_id: Optional[str],
    inclusive: bool = True,
) -> Optional[str]:
    """Id of the entry whose state applies at start_id, or None.

    Walks from start_id up through parentId links and returns the newest successful
    entry on the nearest message that has one, falling back to the root entry (""),
    which is an ancestor of every message. With inclusive=False the walk starts at
    start_id's parent, which is what a pass redoing start_id's own entry needs.
    """
    by_message: dict[str, dict] = {}
    for entry in index:
        if not entry.get("has_state"):
            continue
        key = entry.get("message_id") or ""
        current = by_message.get(key)
        if current is None or entry["created_at"] >= current["created_at"]:
            by_message[key] = entry

    messages_map = messages_map or {}
    seen: set[str] = set()
    current_id = start_id or None
    if current_id and not inclusive:
        current_id = (messages_map.get(current_id) or {}).get("parentId")
    elif not current_id and not inclusive:
        # Strictly above the root there is nothing.
        return None
    while current_id and current_id not in seen:
        seen.add(current_id)
        if current_id in by_message:
            return by_message[current_id]["id"]
        current_id = (messages_map.get(current_id) or {}).get("parentId")
    root = by_message.get("")
    return root["id"] if root else None


def _merge_items(existing: list, updates: Any, prefix: str, limit: int) -> list:
    items = [dict(item) for item in existing if isinstance(item, dict)]
    by_id = {item.get("id"): item for item in items if item.get("id")}
    if not isinstance(updates, list):
        return items
    for update in updates:
        if not isinstance(update, dict):
            continue
        update = _clip(update)
        item_id = _text(update.get("id"))
        if item_id and item_id in by_id:
            by_id[item_id].update(update)
            continue
        if not item_id:
            taken = {item.get("id") for item in items}
            n = 1
            while f"{prefix}{n}" in taken:
                n += 1
            item_id = f"{prefix}{n}"
        update["id"] = item_id
        items.append(update)
        by_id[item_id] = update
    return items[-limit:]


def apply_plan(state: Optional[dict], plan: dict) -> dict:
    """Apply a GM plan's update and remove sections to a copy of the state.

    A patch, never a rewrite: whatever the plan leaves out survives, so a truncated
    or partial reply cannot wipe the campaign.
    """
    result = copy.deepcopy(state) if isinstance(state, dict) else {}
    update = plan.get("update") if isinstance(plan.get("update"), dict) else {}
    remove = plan.get("remove") if isinstance(plan.get("remove"), dict) else {}

    if _text(update.get("premise")):
        result["premise"] = _clip(update["premise"].strip())

    characters = result.get("characters") if isinstance(result.get("characters"), dict) else {}
    character_updates = update.get("characters")
    if not isinstance(character_updates, dict):
        character_updates = {}
    for name, changes in character_updates.items():
        name = _text(name)[:200]
        if not name:
            continue
        if changes is None:
            characters.pop(name, None)
            continue
        if not isinstance(changes, dict):
            continue
        current = dict(characters.get(name) or {})
        stance = dict(current.get("stance") or {})
        for key, value in changes.items():
            if key == "stance":
                if not isinstance(value, dict):
                    continue
                for issue, position in value.items():
                    issue = _text(issue)[:200]
                    if not issue:
                        continue
                    if position is None:
                        stance.pop(issue, None)
                    elif isinstance(position, dict):
                        stance[issue] = {**stance.get(issue, {}), **_clip(position)}
                    elif isinstance(position, str):
                        stance[issue] = {**stance.get(issue, {}), "position": _clip(position)}
            elif isinstance(value, str):
                current[key] = _clip(value.strip())
        if stance:
            current["stance"] = dict(list(stance.items())[-MAX_STANCES:])
        else:
            current.pop("stance", None)
        characters[name] = current
    if characters:
        result["characters"] = dict(list(characters.items())[-MAX_CHARACTERS:])

    for key, prefix in zip(LIST_KEYS, ("n", "t", "c", "s")):
        items = result.get(key) if isinstance(result.get(key), list) else []
        if key in update:
            items = _merge_items(items, update.get(key), prefix, LIMITS[key])
        drop = remove.get(key)
        if isinstance(drop, list):
            drop_ids = {_text(i) for i in drop}
            items = [item for item in items if item.get("id") not in drop_ids]
        if items:
            result[key] = items
        else:
            result.pop(key, None)

    for clock in result.get("clocks", []):
        try:
            size = max(1, min(int(clock.get("size", 6)), 12))
            filled = max(0, min(int(clock.get("filled", 0)), size))
        except (TypeError, ValueError):
            size, filled = 6, 0
        clock["size"], clock["filled"] = size, filled

    for npc in result.get("npcs", []):
        if npc.get("status") not in NPC_STATUSES:
            npc["status"] = "planned"

    directives = [d for d in result.get("player_directives", []) if isinstance(d, str)]
    for directive in update.get("player_directives") or []:
        directive = _text(directive)
        if directive and directive not in directives:
            directives.append(_clip(directive))
    drop = remove.get("player_directives")
    if isinstance(drop, list):
        directives = [d for d in directives if d not in {_text(x) for x in drop}]
    if directives:
        result["player_directives"] = directives[-MAX_DIRECTIVES:]
    else:
        result.pop("player_directives", None)

    tension = update.get("tension")
    if isinstance(tension, dict):
        merged = dict(result.get("tension") or {})
        for key in ("current", "target"):
            try:
                merged[key] = max(0, min(int(tension[key]), 10))
            except (KeyError, TypeError, ValueError):
                pass
        if merged:
            result["tension"] = merged

    return result


def _json_candidates(raw: str) -> list[str]:
    tagged = re.search(r"<gm_plan>\s*(.*?)\s*(?:</gm_plan>|$)", raw, flags=re.S | re.I)
    candidates = []
    if tagged:
        candidates.append(tagged.group(1))
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    candidates.extend(reversed(fenced))
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    return candidates


def _loads_lenient(text: str) -> Optional[dict]:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    text = text[start : end + 1]
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            parsed = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_gm_reply(raw: str) -> tuple[str, Optional[dict]]:
    """Split a GM reply into (reasoning, plan). plan is None when unparseable."""
    raw = raw or ""
    reasoning_match = re.search(
        r"<gm_reasoning>\s*(.*?)\s*(?:</gm_reasoning>|<gm_plan>|$)", raw, flags=re.S | re.I
    )
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()
    else:
        cut = re.search(r"<gm_plan>|```|\{", raw)
        reasoning = raw[: cut.start()].strip() if cut else raw.strip()

    for candidate in _json_candidates(raw):
        plan = _loads_lenient(candidate)
        if plan is not None and ("note" in plan or "update" in plan):
            return reasoning, plan
    return reasoning, None


def _profile(character: Any, key: str) -> str:
    value = (
        character.get(key, "") if isinstance(character, dict) else getattr(character, key, "")
    )
    return value.strip() if isinstance(value, str) else ""


def _outfit_text(character: Any) -> str:
    outfit = (
        character.get("outfit") if isinstance(character, dict) else getattr(character, "outfit", None)
    )
    if not outfit:
        return ""
    name, description = _profile(outfit, "name"), _profile(outfit, "description")
    return f"{name}: {description}" if description else name


def build_gm_messages(
    *,
    characters: list,
    player_character_id: str,
    config: dict,
    chat_instructions: str,
    state: Optional[dict],
    last_note: str,
    conversation: list[dict],
) -> list[dict]:
    """The GM's system and user messages for one pass."""
    intensity = INTENSITY.get(config.get("intensity"), INTENSITY["firm"])
    system = SYSTEM_PROMPT.format(intensity=intensity)

    roster, settings = [], []
    player_name = ""
    for character in characters:
        kind = _profile(character, "kind") or "character"
        name = _profile(character, "name")
        if not name:
            continue
        description = _profile(character, "description")[:MAX_PROFILE_CHARS]
        if kind == "location":
            settings.append(f"- {name}: {description}")
            continue
        if kind != "character":
            continue
        is_player = bool(player_character_id) and _profile(character, "id") == player_character_id
        if is_player:
            player_name = name
        label = f"{name} [PLAYER: the player's own character. Never brief them.]" if is_player else name
        lines = [f"- {label}", f"  profile: {description or '(none given)'}"]
        outfit = _outfit_text(character)
        if outfit:
            lines.append(f"  outfit: {outfit[:500]}")
        state_of_dress = _profile(character, "state")
        if state_of_dress:
            lines.append(f"  currently: {state_of_dress[:500]}")
        roster.append("\n".join(lines))

    player_label = f"Player ({player_name})" if player_name else "Player"
    transcript = []
    for message in conversation:
        text = message_text(message)[:MAX_MESSAGE_CHARS]
        if not text:
            continue
        who = player_label if message.get("role") == "user" else "Story"
        transcript.append(f"[{who}]\n{text}")

    sections = [
        "## Characters\n" + ("\n".join(roster) if roster else "(no character profiles attached)"),
    ]
    if not player_name:
        sections.append(
            "The player has not marked which character is theirs. Infer it from the "
            "conversation (usually the one the player writes as) and never brief that one."
        )
    if settings:
        sections.append("## Setting\n" + "\n".join(settings))
    agenda = _text(config.get("agenda"))
    if agenda:
        sections.append(
            "## The player's agenda for you\nTheir brief for the story. Work toward it; "
            "it is not a script.\n" + agenda[:4000]
        )
    if _text(chat_instructions):
        sections.append("## Chat instructions (binding)\n" + chat_instructions.strip()[:4000])
    if state:
        sections.append("## Your current plan\n" + json.dumps(state, ensure_ascii=False, indent=1))
        if _text(last_note):
            sections.append("## The note you issued last time\n" + last_note.strip())
    sections.append(
        "## Conversation so far (most recent last)\n"
        + ("\n\n".join(transcript) if transcript else "(nothing yet: the story has not started)")
    )
    sections.append("## Your task\n" + (TURN_TASK if state else SETUP_TASK))

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def render_director_notes(note: str, state: Optional[dict]) -> Optional[str]:
    """The block appended to the actor's system message, or None if there is nothing."""
    note = _text(note)
    cards = []
    for npc in (state or {}).get("npcs", []):
        if not isinstance(npc, dict) or npc.get("status") != "on_stage":
            continue
        name = _text(npc.get("name"))
        if not name:
            continue
        parts = [f"{name} (NPC, in the scene): {_text(npc.get('card'))}".rstrip(": ")]
        for label, key in (("Wants", "want"), ("Voice", "voice"), ("Looks", "look")):
            if _text(npc.get(key)):
                parts.append(f"{label}: {_text(npc.get(key))}")
        cards.append(" ".join(p if p.endswith(".") else p + "." for p in parts))
    if not note and not cards:
        return None
    body = note
    if cards:
        body = (body + "\n\n" if body else "") + "NPCs in the scene, played by you:\n" + "\n".join(
            f"- {card}" for card in cards
        )
    return (
        "<director_notes>\n"
        f"{NOTES_HEADER}\n\n"
        f"{escape(body, quote=False)}\n"
        "</director_notes>"
    )


def resolve_gm_model(models: dict, model_id: str) -> str:
    """The chat's own model with its persona stripped: the base model if it has one.

    A workspace model prepends its own system prompt, which would have the GM run in
    character, as the outfit tracker once did.
    """
    model = models.get(model_id) or {}
    base = (model.get("info") or {}).get("base_model_id")
    return base if base and base in models else model_id


def _reply_parts(response: Any) -> tuple[str, str, int]:
    if not isinstance(response, dict):
        raise ValueError(f"Unexpected GM response: {str(response)[:300]}")
    if response.get("error"):
        raise ValueError(f"GM model error: {str(response['error'])[:500]}")
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("GM model returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    model_reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    think = re.findall(r"<think>(.*?)</think>", content, flags=re.S | re.I)
    if think:
        model_reasoning = (model_reasoning + "\n\n" + "\n\n".join(think)).strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I)
    usage = response.get("usage") or {}
    tokens = usage.get("total_tokens") or 0
    return content, model_reasoning if isinstance(model_reasoning, str) else "", int(tokens or 0)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

_locks: dict[str, asyncio.Lock] = {}
_running: dict[str, int] = {}
# Keeps detached passes referenced until they finish; asyncio only holds weak refs.
_tasks: set[asyncio.Task] = set()


def is_running(chat_id: str) -> bool:
    return _running.get(chat_id, 0) > 0


def _lock(chat_id: str) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = _locks[chat_id] = asyncio.Lock()
    return lock


def get_director_notes(user_id: str, chat_id: str, message_id: Optional[str]) -> Optional[str]:
    """The notes block for a reply to message_id, or None if the GM is off or silent."""
    from open_webui.models.chats import Chats
    from open_webui.models.game_master import GameMaster

    session = GameMaster.get_session(user_id, chat_id)
    if not session or not session.enabled:
        return None
    index = GameMaster.get_entry_index(user_id, chat_id)
    if not index:
        return None
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    entry_id = find_state_entry_id(index, messages_map, message_id, inclusive=True)
    entry = GameMaster.get_entry(user_id, entry_id) if entry_id else None
    return render_director_notes(entry.note, entry.state) if entry else None


def inject_director_notes(
    messages: list[dict], user_id: str, chat_id: str, message_id: Optional[str]
) -> list[dict]:
    from open_webui.utils.misc import add_or_update_system_message

    notes = get_director_notes(user_id, chat_id, message_id)
    if not notes:
        return messages
    return add_or_update_system_message(notes, messages, append=True)


async def run_gm_pass(
    request: Any,
    user: Any,
    chat_id: str,
    message_id: Optional[str],
    model_id: str,
    kind: str = "turn",
    event_emitter: Optional[Callable] = None,
) -> None:
    """One GM pass reacting to message_id. Never raises; failures are journalled."""
    from open_webui.models.chats import Chats
    from open_webui.models.game_master import GameMaster
    from open_webui.models.video_characters import VideoCharacters
    from open_webui.utils.chat import generate_chat_completion
    from open_webui.utils.chat_instructions import get_stored_chat_instructions
    from open_webui.utils.misc import get_message_list

    async def emit(data: dict) -> None:
        if event_emitter:
            try:
                await event_emitter({"type": "chat:gm", "data": data})
            except Exception:
                log.debug("Could not emit GM event", exc_info=True)

    _running[chat_id] = _running.get(chat_id, 0) + 1
    try:
        # Passes for one chat run in order, so each builds on the last one's state.
        async with _lock(chat_id):
            session = GameMaster.get_session(user.id, chat_id)
            if not session or not session.enabled:
                return
            await emit({"status": "running"})
            started = time.monotonic()
            gm_model = ""
            try:
                chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
                if not chat:
                    return
                messages_map = Chats.get_messages_map_by_chat_id(chat_id) or {}
                chain = get_message_list(messages_map, message_id) if message_id else []

                index = GameMaster.get_entry_index(user.id, chat_id)
                prior_id = find_state_entry_id(index, messages_map, message_id, inclusive=False)
                prior = GameMaster.get_entry(user.id, prior_id) if prior_id else None
                state = prior.state if prior else None
                if state is None:
                    kind = "setup"

                models = request.app.state.MODELS
                gm_model = resolve_gm_model(models, model_id)
                if gm_model not in models:
                    raise ValueError(f"Model not found: {gm_model}")

                gm_messages = build_gm_messages(
                    characters=VideoCharacters.get_for_chat(user.id, chat_id),
                    player_character_id=session.config.get("player_character_id", ""),
                    config=session.config,
                    chat_instructions=get_stored_chat_instructions(chat) or "",
                    state=state,
                    last_note=prior.note if prior else "",
                    conversation=chain[-(SETUP_MESSAGES if state is None else TURN_MESSAGES):],
                )
                token_key = (
                    "max_tokens"
                    if models[gm_model].get("owned_by") == "ollama"
                    else "max_completion_tokens"
                )
                response = await generate_chat_completion(
                    request,
                    form_data={
                        "model": gm_model,
                        "messages": gm_messages,
                        "stream": False,
                        token_key: MAX_TOKENS,
                        "metadata": {"task": "game_master", "chat_id": chat_id},
                    },
                    user=user,
                    # No filters: the persistent memory pipeline must never store the
                    # GM's plans. No model system prompt: the GM must not play a persona.
                    bypass_filter=True,
                    bypass_system_prompt=True,
                )
                content, model_reasoning, tokens = _reply_parts(response)
                reasoning, plan = parse_gm_reply(content)
                duration_ms = int((time.monotonic() - started) * 1000)
                if plan is None:
                    GameMaster.add_entry(
                        user.id,
                        chat_id,
                        message_id=message_id or "",
                        kind=kind,
                        reasoning=reasoning or content,
                        model_reasoning=model_reasoning,
                        model=gm_model,
                        tokens=tokens,
                        duration_ms=duration_ms,
                        error="The GM's reply had no readable plan, so nothing was changed.",
                    )
                    await emit({"status": "error"})
                    return

                new_state = apply_plan(state, plan)
                observations = [
                    _clip(o, 500) for o in plan.get("observations") or [] if isinstance(o, str)
                ][:20]
                note = _clip(_text(plan.get("note")), 4000)
                GameMaster.add_entry(
                    user.id,
                    chat_id,
                    message_id=message_id or "",
                    kind=kind,
                    reasoning=reasoning,
                    model_reasoning=model_reasoning,
                    observations=observations,
                    patch={k: plan[k] for k in ("update", "remove") if k in plan},
                    state=new_state,
                    note=note,
                    model=gm_model,
                    tokens=tokens,
                    duration_ms=duration_ms,
                )
                log.info(
                    "GM %s pass for chat %s: %d tokens in %.1fs",
                    kind,
                    chat_id,
                    tokens,
                    duration_ms / 1000,
                )
                await emit({"status": "done"})
            except Exception as e:
                log.exception("GM pass failed for chat %s", chat_id)
                GameMaster.add_entry(
                    user.id,
                    chat_id,
                    message_id=message_id or "",
                    kind=kind,
                    model=gm_model,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=str(e)[:2000] or e.__class__.__name__,
                )
                await emit({"status": "error"})
    except Exception:
        log.exception("GM pass could not be journalled for chat %s", chat_id)
    finally:
        _running[chat_id] = max(0, _running.get(chat_id, 1) - 1)


def schedule_gm_pass(
    request: Any,
    user: Any,
    chat_id: str,
    message_id: Optional[str],
    model_id: str,
    kind: str = "turn",
    event_emitter: Optional[Callable] = None,
) -> bool:
    """Start a pass in the background if the GM is on for this chat."""
    from open_webui.models.game_master import GameMaster

    if not chat_id or chat_id.startswith("local:") or not model_id:
        return False
    session = GameMaster.get_session(user.id, chat_id)
    if not session or not session.enabled:
        return False
    task = asyncio.create_task(
        run_gm_pass(request, user, chat_id, message_id, model_id, kind, event_emitter)
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
