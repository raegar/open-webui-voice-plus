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
# A table-talk answer is a few sentences.
TALK_MAX_TOKENS = 2000
# A portrait prompt is one paragraph; reasoning models need room before it.
PORTRAIT_PROMPT_TOKENS = 1500

# Bounds on the GM's state, so a runaway model cannot grow it without limit.
LIMITS = {"npcs": 20, "threads": 10, "clocks": 8, "secrets": 15, "player_requests": 12}
MAX_CHARACTERS = 12
MAX_STANCES = 8
MAX_DIRECTIVES = 12
MAX_STRING = 1500
# Id prefix for each list in the state; the order is the order they are merged.
PREFIXES = {"npcs": "n", "threads": "t", "clocks": "c", "secrets": "s", "player_requests": "r"}
LIST_KEYS = tuple(PREFIXES)
TALK_HISTORY = 8
NPC_STATUSES = ("planned", "entering", "on_stage", "off_stage", "gone")
# Shown in the scene: entering NPCs arrive in the very next reply.
PRESENT_STATUSES = ("entering", "on_stage")

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
9. Introduce NPCs as a DM would: when they serve a thread, a clock or the player's agenda, and one new face at a time. Give each a one-line card, a want, a voice, and a fixed visual look that stays the same from scene to scene, in two to four concrete sentences: for a person, age, build, face, hair and clothing; for anything else, its form, size, texture and colour, how it moves, and what it does to the light and space around it. The look is also what their portrait is drawn from, so make it vivid and specific. To bring an NPC in, set their status to "entering": the actor is then required to bring them into the very next reply, and they move on stage automatically once that reply is written. Keep NPCs you are saving for later as "planned". Once the player has asked for someone and you have agreed, do not keep postponing: set them entering.
10. Secrets. A character's own secrets can go in their briefing, because an actor must know them to play them. A twist no character knows stays with you until the story earns the reveal, though you can hint at it.
11. Respect the player's agenda, their directives and the chat instructions. Make the story harder, never into something the player said they do not want.
12. Pace yourself. Not every turn needs a new event. Some turns the right note is simply: hold your ground.
13. Work the player's requests (player_requests) into the story over the coming scenes, in ways that fit what is already happening. Mark one in_progress once you have started on it and done once it has landed.

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
    "npcs": [{{ "id": "n1", "name": "...", "status": "planned | entering | on_stage | off_stage | gone", "card": "who they are, one line", "want": "...", "voice": "...", "look": "fixed visual description", "knows": ["s1"] }}],
    "threads": [{{ "id": "t1", "title": "...", "status": "seeded | rising | climax | resolved", "next_beat": "...", "gm_only": false }}],
    "clocks": [{{ "id": "c1", "label": "...", "filled": 0, "size": 6, "on_full": "what happens when it fills" }}],
    "secrets": [{{ "id": "s1", "text": "...", "known_by": ["names"], "reveal": "when or how" }}],
    "player_requests": [{{ "id": "r1", "text": "what the player asked for", "status": "planned | in_progress | done | declined", "plan": "how you will work it in" }}],
    "player_directives": ["binding out-of-character instructions from the player"],
    "tension": {{ "current": 0, "target": 0 }}
  }},
  "remove": {{ "npcs": ["ids"], "threads": ["ids"], "clocks": ["ids"], "secrets": ["ids"], "player_requests": ["ids"], "player_directives": ["exact text"] }},
  "note": "the director's note for the next reply"
}}

"update" holds only what changes. Anything you leave out stays as it is. List items are matched by id: an existing id updates that item, a new id adds one. Use short ids such as n1, t2, c3, s4. A character's stance issue set to null is dropped.

Writing the note:
- Address each character who matters to the next reply by name, in the second person: what you want right now, what you will do if the player pulls you another way, and what it would take to change your mind.
- You will not see the player's next message before the actor replies, so cover their likely moves. Write standing intentions and rulings, not lines of dialogue.
- Add world direction when there is any: an event to set in motion, how a risky attempt will go. To bring in an NPC, set their status to "entering" rather than describing it here.
- Never brief the PLAYER character. Never include a secret that only you know.
- Cards for NPCs who are entering or on stage are added automatically, so do not repeat them.
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
CONSULT_TASK = (
    "The player asked you to take another look at the story as it stands, perhaps "
    "after changing your settings or their agenda. Check your plan against the "
    "conversation and the current settings, adjust it, and write a fresh note for the "
    "next reply. Do not record again any caves you have already recorded."
)
REROLL_TASK = (
    "The player rerolled your last direction: they want a different take on this "
    "moment. Plan again from the same starting point and write a note that goes "
    "somewhere meaningfully different, still true to the characters.\n"
    "The direction they rejected:\n{rejected}"
)
TALK_PLAN_TASK = (
    "The player has just talked to you out of character, and you answered them "
    "(above). Update your plan to match what you told them: a suggestion you took or "
    "adapted goes into player_requests with your plan for it, a binding instruction "
    "goes into player_directives, and a correction fixes your plan. Then rewrite the "
    "note for the next reply so the change shows at once where it should. Do not "
    "review the latest reply for caves again."
)
# Restated last, where the model reads it most recently: some models write their
# reasoning as prose and stop without the plan.
SHAPE_REMINDER = (
    "Reply in the required shape: <gm_reasoning>...</gm_reasoning>, then <gm_plan> "
    "with the JSON object. The plan is required: without it nothing you decide "
    "reaches the story."
)
# Sent once when a reply has no readable plan, continuing from the GM's own reasoning.
REPAIR_PROMPT = (
    "Your reply stopped before the plan, so none of it can reach the story yet. Reply "
    "now with only the <gm_plan> block: the JSON object from your instructions "
    "(observations, update, remove, note), following the reasoning you just wrote. "
    "No other text."
)

TALK_SYSTEM = """You are the Game Master of an ongoing interactive story, in the tradition of a tabletop DM. Another model, the actor, plays the characters and writes the story. You work behind the scenes: you keep the plans and brief the characters.

Right now the player is talking to you out of character, across the table, as they would to a DM. Answer them directly in your own voice: friendly, frank and opinionated. Plain prose, usually a few sentences. No headings, no lists unless they help, no JSON, no tags. Never write story prose and never speak as a character.

At the table:
- Answer questions about direction honestly, but keep your secrets and planned twists unless the player explicitly asks to be spoiled.
- A suggestion is a request, not an order. Take it, adapt it to what you already have planned, or push back, say why, and offer something better. You are a DM with opinions, not an assistant who agrees to everything.
- A clear instruction about the game ("stop doing that", "tone it down") or a correction to your facts is binding: say plainly that you will follow it.
- Say what will change, if anything, without spoiling how.

Your intensity setting: {intensity}

What you know:"""

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

    for key, prefix in PREFIXES.items():
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


def _by_id(items: Any) -> dict:
    if not isinstance(items, list):
        return {}
    return {item["id"]: item for item in items if isinstance(item, dict) and item.get("id")}


def _status_label(status: Any) -> str:
    return "arriving" if status == "entering" else str(status).replace("_", " ")


def describe_changes(before: Optional[dict], after: Optional[dict]) -> list[dict]:
    """What a pass changed, as readable lines for the transcript.

    Each line is {"text", "spoiler"}. Spoiler lines (secrets, GM-only threads, NPCs
    still waiting in the wings, drives and the premise) stay covered until clicked.
    """
    before, after = before or {}, after or {}
    lines: list[dict] = []

    def add(line: str, spoiler: bool = False) -> None:
        lines.append({"text": line[:600], "spoiler": spoiler})

    if _text(after.get("premise")) and after.get("premise") != before.get("premise"):
        add(f"Premise: {after['premise']}", True)

    old_characters = before.get("characters") or {}
    for name, character in (after.get("characters") or {}).items():
        if not isinstance(character, dict):
            continue
        old = old_characters.get(name) or {}
        for key, label in (
            ("want", "wants"),
            ("need", "needs"),
            ("fear", "fears"),
            ("line", "won't"),
            ("voice_notes", "voice"),
        ):
            if _text(character.get(key)) and character.get(key) != old.get(key):
                add(f"{name} {label}: {character[key]}", True)
        old_stance = old.get("stance") or {}
        new_stance = character.get("stance") or {}
        for issue, stance in new_stance.items():
            if not isinstance(stance, dict) or stance == old_stance.get(issue):
                continue
            position = _text(stance.get("position"))
            previous = _text((old_stance.get(issue) or {}).get("position"))
            line = f"{name} on {issue}: " + (
                f"{previous} → {position}" if previous and previous != position else position
            )
            if _text(stance.get("price")):
                line += f" (moves only if: {stance['price']})"
            add(line)
        for issue in old_stance:
            if issue not in new_stance:
                add(f"{name} on {issue}: settled")

    old_npcs = _by_id(before.get("npcs"))
    for npc_id, npc in _by_id(after.get("npcs")).items():
        name, status = _text(npc.get("name")) or npc_id, npc.get("status", "planned")
        old = old_npcs.get(npc_id)
        if not old:
            card = f": {npc['card']}" if _text(npc.get("card")) else ""
            if status == "planned":
                add(f"New NPC waiting in the wings: {name}{card}", True)
            elif status == "entering":
                add(f"NPC arriving next reply: {name}{card}")
            else:
                add(f"NPC enters: {name}{card}")
            continue
        if old.get("status") != status:
            add(
                f"{name}: {_status_label(old.get('status', 'planned'))} → {_status_label(status)}",
                status == "planned",
            )
        if _text(npc.get("look")) and npc.get("look") != old.get("look") and old.get("look"):
            add(f"{name} now looks: {npc['look']}")
    for npc_id, npc in old_npcs.items():
        if npc_id not in _by_id(after.get("npcs")):
            add(f"NPC dropped: {_text(npc.get('name')) or npc_id}", npc.get("status") == "planned")

    old_threads = _by_id(before.get("threads"))
    for thread_id, thread in _by_id(after.get("threads")).items():
        title = _text(thread.get("title")) or thread_id
        hidden = bool(thread.get("gm_only"))
        old = old_threads.get(thread_id)
        if not old:
            add(f"New thread: {title} ({thread.get('status', 'seeded')})", hidden)
            continue
        if old.get("status") != thread.get("status"):
            add(f"Thread '{title}': {old.get('status')} → {thread.get('status')}", hidden)
        if _text(thread.get("next_beat")) and thread.get("next_beat") != old.get("next_beat"):
            add(f"Next beat for '{title}': {thread['next_beat']}", True)

    old_clocks = _by_id(before.get("clocks"))
    for clock_id, clock in _by_id(after.get("clocks")).items():
        label = _text(clock.get("label")) or clock_id
        filled, size = clock.get("filled", 0), clock.get("size", 6)
        old = old_clocks.get(clock_id)
        if not old:
            add(f"New clock: {label} ({filled} of {size})")
        elif old.get("filled") != filled:
            add(f"{label}: {old.get('filled', 0)} → {filled} of {size}")
        if filled >= size and (not old or old.get("filled", 0) < size):
            add(f"{label} is full: {clock.get('on_full', 'its consequence happens')}")

    old_secrets = _by_id(before.get("secrets"))
    for secret_id, secret in _by_id(after.get("secrets")).items():
        old = old_secrets.get(secret_id)
        if not old:
            add(f"Secret: {secret.get('text', '')}", True)
        elif old.get("known_by") != secret.get("known_by"):
            known = ", ".join(secret.get("known_by") or []) or "nobody"
            add(f"Secret now known by {known}: {secret.get('text', '')}", True)

    old_requests = _by_id(before.get("player_requests"))
    for request_id, request in _by_id(after.get("player_requests")).items():
        old = old_requests.get(request_id)
        wording = _text(request.get("text")) or request_id
        if not old:
            add(f"Your request: {wording} ({request.get('status', 'planned')})")
        elif old.get("status") != request.get("status"):
            add(f"Your request '{wording}': {old.get('status')} → {request.get('status')}")

    old_directives = set(before.get("player_directives") or [])
    new_directives = set(after.get("player_directives") or [])
    for directive in sorted(new_directives - old_directives):
        add(f"Your directive: {directive}")
    for directive in sorted(old_directives - new_directives):
        add(f"Directive lifted: {directive}")

    old_tension, tension = before.get("tension") or {}, after.get("tension") or {}
    if tension.get("current") is not None and tension.get("current") != old_tension.get("current"):
        target = f" (aiming for {tension['target']})" if tension.get("target") is not None else ""
        previous = old_tension.get("current")
        add(
            f"Tension: {previous} → {tension['current']}{target}"
            if previous is not None
            else f"Tension: {tension['current']}{target}"
        )
    return lines


def count_replies_since(
    messages_map: Optional[dict], start_id: Optional[str], stop_id: Optional[str]
) -> int:
    """Assistant replies from start_id (inclusive) back to stop_id (exclusive)."""
    messages_map = messages_map or {}
    count, current, seen = 0, start_id, set()
    while current and current != stop_id and current not in seen:
        seen.add(current)
        message = messages_map.get(current) or {}
        if message.get("role") == "assistant":
            count += 1
        current = message.get("parentId")
    return count


def npcs_needing_portraits(state: Optional[dict], portraits: dict) -> list[dict]:
    """Entering or on-stage NPCs with a look and no portrait of that NPC yet.

    portraits maps npc id to the name its portrait was drawn for. A different name
    under the same id is a different NPC (ids are reused across branches).
    """
    needed = []
    for npc in (state or {}).get("npcs", []):
        if not isinstance(npc, dict) or npc.get("status") not in PRESENT_STATUSES:
            continue
        if not _text(npc.get("look")) or not npc.get("id"):
            continue
        drawn_for = portraits.get(npc["id"])
        if drawn_for is None or drawn_for != _text(npc.get("name")):
            needed.append(npc)
    return needed


def scene_npcs(state: Optional[dict], portraits: dict) -> list[dict]:
    """The NPCs in the scene, with a ready portrait's file id where there is one.

    portraits maps npc id to {"name", "status", "file_id"}.
    """
    scene = []
    for npc in (state or {}).get("npcs", []):
        if not isinstance(npc, dict) or npc.get("status") not in PRESENT_STATUSES:
            continue
        name = _text(npc.get("name"))
        if not name:
            continue
        portrait = portraits.get(npc.get("id")) or {}
        ready = portrait.get("status") == "ready" and portrait.get("name") == name
        scene.append(
            {
                "npc_id": npc.get("id", ""),
                "name": name,
                "card": _text(npc.get("card")),
                "look": _text(npc.get("look")),
                "file_id": portrait.get("file_id", "") if ready else "",
            }
        )
    return scene


def settle_entrances(state: Optional[dict]) -> Optional[dict]:
    """After a reply has been written, entering NPCs are in the scene.

    Done here rather than left to the GM, which kept re-issuing an introduction
    without ever recording that it had happened.
    """
    if not state or not any(
        isinstance(npc, dict) and npc.get("status") == "entering" for npc in state.get("npcs", [])
    ):
        return state
    settled = copy.deepcopy(state)
    for npc in settled["npcs"]:
        if isinstance(npc, dict) and npc.get("status") == "entering":
            npc["status"] = "on_stage"
    return settled


def promote_introduced(state: dict, note: str) -> dict:
    """A planned NPC the note says to introduce is entering.

    Covers a GM that writes "Introduce: <name>" out of habit instead of setting the
    entering status, so the introduction is enforced either way.
    """
    lowered = (note or "").lower()
    if "introduc" not in lowered:
        return state
    for npc in state.get("npcs", []):
        if not isinstance(npc, dict) or npc.get("status") != "planned":
            continue
        name = _text(npc.get("name")).lower()
        # "Introduce: Marek (n1)", "introduce the stranger now": the name follows
        # the word closely, within the same sentence.
        if name and re.search(r"introduc\w*[^.\n]{0,40}?" + re.escape(name), lowered):
            npc["status"] = "entering"
    return state


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


def _context_sections(
    characters: list, player_character_id: str, config: dict, chat_instructions: str
) -> tuple[list[str], str]:
    """Characters, setting, agenda and instructions, plus the player's character name."""
    roster, settings = [], []
    player_name = ""
    for character in characters:
        character_kind = _profile(character, "kind") or "character"
        name = _profile(character, "name")
        if not name:
            continue
        description = _profile(character, "description")[:MAX_PROFILE_CHARS]
        if character_kind == "location":
            settings.append(f"- {name}: {description}")
            continue
        if character_kind != "character":
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
    return sections, player_name


def _conversation_section(conversation: list[dict], player_name: str) -> str:
    player_label = f"Player ({player_name})" if player_name else "Player"
    transcript = []
    for message in conversation:
        text = message_text(message)[:MAX_MESSAGE_CHARS]
        if not text:
            continue
        who = player_label if message.get("role") == "user" else "Story"
        transcript.append(f"[{who}]\n{text}")
    return "## Conversation so far (most recent last)\n" + (
        "\n\n".join(transcript) if transcript else "(nothing yet: the story has not started)"
    )


def build_gm_messages(
    *,
    characters: list,
    player_character_id: str,
    config: dict,
    chat_instructions: str,
    state: Optional[dict],
    last_note: str,
    conversation: list[dict],
    kind: str = "turn",
    talk: str = "",
    talk_reply: str = "",
    rejected_note: str = "",
) -> list[dict]:
    """The GM's system and user messages for one planning pass.

    kind is turn, setup, consult, reroll or talk_plan. A talk_plan pass follows a
    table-talk exchange: talk is what the player said and talk_reply the GM's answer.
    """
    intensity = INTENSITY.get(config.get("intensity"), INTENSITY["firm"])
    system = SYSTEM_PROMPT.format(intensity=intensity)

    sections, player_name = _context_sections(
        characters, player_character_id, config, chat_instructions
    )
    if state:
        sections.append("## Your current plan\n" + json.dumps(state, ensure_ascii=False, indent=1))
        if _text(last_note):
            sections.append("## The note you issued last time\n" + last_note.strip())
    sections.append(_conversation_section(conversation, player_name))

    tasks = []
    if kind == "talk_plan":
        sections.append(
            "## The player just said to you, out of character\n"
            + _text(talk)[:MAX_MESSAGE_CHARS]
            + "\n\n## You answered them\n"
            + _text(talk_reply)[:MAX_MESSAGE_CHARS]
        )
        tasks.append(TALK_PLAN_TASK)
        if not state:
            tasks.append(SETUP_TASK)
    elif not state:
        tasks.append(SETUP_TASK)
    elif kind == "consult":
        tasks.append(CONSULT_TASK)
    else:
        tasks.append(TURN_TASK)
    if kind == "reroll" and _text(rejected_note):
        tasks.append(REROLL_TASK.format(rejected=rejected_note.strip()))
    tasks.append(SHAPE_REMINDER)
    sections.append("## Your task\n" + "\n\n".join(tasks))

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def build_talk_messages(
    *,
    characters: list,
    player_character_id: str,
    config: dict,
    chat_instructions: str,
    state: Optional[dict],
    conversation: list[dict],
    talk_history: list[dict],
    talk: str,
) -> list[dict]:
    """A plain conversation with the GM, ending on the player's words.

    Kept apart from planning on purpose: buried in a planning prompt, the player's
    question was ignored and the model ran an ordinary turn review instead. Here it
    is the last thing the model reads, and answering it is the only job.
    """
    intensity = INTENSITY.get(config.get("intensity"), INTENSITY["firm"])
    sections, player_name = _context_sections(
        characters, player_character_id, config, chat_instructions
    )
    sections.append(
        "## Your current plan (private: never reveal secrets or planned twists unless "
        "the player explicitly asks to be spoiled)\n"
        + (json.dumps(state, ensure_ascii=False, indent=1) if state else "(no plan yet)")
    )
    sections.append(_conversation_section(conversation, player_name))
    messages = [
        {
            "role": "system",
            "content": TALK_SYSTEM.format(intensity=intensity) + "\n\n" + "\n\n".join(sections),
        }
    ]
    for item in talk_history or []:
        player, gm = _text(item.get("player")), _text(item.get("gm"))
        if player and gm:
            messages.append({"role": "user", "content": player[:MAX_MESSAGE_CHARS]})
            messages.append({"role": "assistant", "content": gm[:MAX_MESSAGE_CHARS]})
    messages.append({"role": "user", "content": _text(talk)[:MAX_MESSAGE_CHARS]})
    return messages


def clean_talk_reply(raw: str) -> str:
    """The GM's answer with any thinking or stray planning markup removed."""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    text = re.sub(r"<(gm_plan|gm_reasoning)>.*?(</\1>|$)", "", text, flags=re.S | re.I)
    text = re.sub(r"</?gm_reply>", "", text, flags=re.I)
    return text.strip()


def build_repair_messages(gm_messages: list[dict], reply: str) -> list[dict]:
    """The pass's messages, its planless reply, and a request for only the plan."""
    return [
        *gm_messages,
        {"role": "assistant", "content": reply},
        {"role": "user", "content": REPAIR_PROMPT},
    ]


def _npc_card(npc: dict) -> str:
    parts = [f"{_text(npc.get('name'))}: {_text(npc.get('card'))}".rstrip(": ")]
    for label, key in (("Wants", "want"), ("Voice", "voice"), ("Looks", "look")):
        if _text(npc.get(key)):
            parts.append(f"{label}: {_text(npc.get(key))}")
    return " ".join(p if p.endswith(".") else p + "." for p in parts)


def render_director_notes(note: str, state: Optional[dict]) -> Optional[str]:
    """The block appended to the actor's system message, or None if there is nothing.

    An entering NPC leads the block as a requirement. As one line at the end of the
    note ("Introduce: ...") the actor skipped it turn after turn.
    """
    note = _text(note)
    npcs = [
        npc
        for npc in (state or {}).get("npcs", [])
        if isinstance(npc, dict) and _text(npc.get("name"))
    ]
    entering = [_npc_card(npc) for npc in npcs if npc.get("status") == "entering"]
    on_stage = [_npc_card(npc) for npc in npcs if npc.get("status") == "on_stage"]
    if not note and not entering and not on_stage:
        return None
    sections = []
    if entering:
        sections.append(
            "Required in this reply: bring "
            + ("this new character" if len(entering) == 1 else "these new characters")
            + " into the scene. They arrive and act now, in person, as described; this is "
            "not optional and not to be put off to a later reply. You play them.\n"
            + "\n".join(f"- {card}" for card in entering)
        )
    if note:
        sections.append(note)
    if on_stage:
        sections.append(
            "NPCs in the scene, played by you:\n" + "\n".join(f"- {card}" for card in on_stage)
        )
    body = "\n\n".join(sections)
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
    if not entry:
        return None
    # The note may name an NPC to introduce without marking them entering.
    state = promote_introduced(copy.deepcopy(entry.state or {}), entry.note)
    return render_director_notes(entry.note, state)


def inject_director_notes(
    messages: list[dict], user_id: str, chat_id: str, message_id: Optional[str]
) -> list[dict]:
    from open_webui.utils.misc import add_or_update_system_message

    notes = get_director_notes(user_id, chat_id, message_id)
    if not notes:
        return messages
    return add_or_update_system_message(notes, messages, append=True)


_UNSET = object()


async def run_gm_pass(
    request: Any,
    user: Any,
    chat_id: str,
    message_id: Optional[str],
    model_id: str,
    kind: str = "turn",
    event_emitter: Optional[Callable] = None,
    talk: str = "",
    talk_reply: str = "",
    prior_override: Any = _UNSET,
    rejected_note: str = "",
    counted: bool = False,
) -> Optional[Any]:
    """One GM planning pass at message_id. Never raises; failures are journalled.

    Where it starts from: a turn pass reacts to a new reply, so it builds on the
    nearest pass *above* that reply (a regenerated reply starts where its sibling
    did). Consult and talk_plan build on the plan as it stands at message_id. A
    reroll passes its prior explicitly, to start again from where the rerolled pass
    did. Returns the new journal entry, or None if nothing ran.

    counted means schedule_gm_pass already marked the chat as running.
    """
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

    if not counted:
        _running[chat_id] = _running.get(chat_id, 0) + 1
    try:
        # Passes for one chat run in order, so each builds on the last one's state.
        async with _lock(chat_id):
            session = GameMaster.get_session(user.id, chat_id)
            if not session or not session.enabled:
                return None
            started = time.monotonic()
            gm_model = ""
            prior = None
            try:
                chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
                if not chat:
                    return None
                messages_map = Chats.get_messages_map_by_chat_id(chat_id) or {}
                chain = get_message_list(messages_map, message_id) if message_id else []

                index = GameMaster.get_entry_index(user.id, chat_id)
                if prior_override is not _UNSET:
                    prior_id = prior_override or None
                else:
                    prior_id = find_state_entry_id(
                        index, messages_map, message_id, inclusive=kind != "turn"
                    )
                prior = GameMaster.get_entry(user.id, prior_id) if prior_id else None
                state = prior.state if prior else None

                # Cadence: skip turn passes until enough replies have gone by. The
                # previous direction simply stays in force meanwhile.
                cadence = session.config.get("cadence", 1)
                pending = promote_introduced(
                    copy.deepcopy(state or {}), prior.note if prior else ""
                )
                arriving = any(
                    isinstance(npc, dict) and npc.get("status") == "entering"
                    for npc in pending.get("npcs", [])
                )
                # Never skip while someone is entering: the next pass is what records
                # their arrival, and skipping it would have them introduced twice.
                if kind == "turn" and prior and cadence > 1 and not arriving:
                    since = count_replies_since(messages_map, message_id, prior.message_id)
                    if since < cadence:
                        return None
                if state is None and kind in ("turn", "consult"):
                    kind = "setup"
                before = state
                # A turn pass follows a written reply, so whoever was entering has
                # now arrived. Other passes come before the next reply is written.
                if kind == "turn" and state is not None:
                    # Includes anyone the last note only named to introduce, since the
                    # actor was required to bring them in too.
                    state = settle_entrances(
                        promote_introduced(copy.deepcopy(state), prior.note if prior else "")
                    )

                await emit({"status": "running"})
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
                    kind=kind,
                    talk=talk,
                    talk_reply=talk_reply,
                    rejected_note=rejected_note,
                )
                token_key = _token_key(models, gm_model)
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
                if plan is None:
                    # One repair turn: ask for just the plan, after its own reasoning.
                    response = await generate_chat_completion(
                        request,
                        form_data={
                            "model": gm_model,
                            "messages": build_repair_messages(gm_messages, content),
                            "stream": False,
                            token_key: MAX_TOKENS,
                            "metadata": {"task": "game_master", "chat_id": chat_id},
                        },
                        user=user,
                        bypass_filter=True,
                        bypass_system_prompt=True,
                    )
                    repair, repair_reasoning, repair_tokens = _reply_parts(response)
                    tokens += repair_tokens
                    if repair_reasoning:
                        model_reasoning = (model_reasoning + "\n\n" + repair_reasoning).strip()
                    _, plan = parse_gm_reply(repair)
                    if plan is not None:
                        log.info("GM plan recovered by a repair turn for chat %s", chat_id)
                duration_ms = int((time.monotonic() - started) * 1000)
                common = {
                    "message_id": message_id or "",
                    "kind": kind,
                    "prior_id": prior.id if prior else "",
                    "model_reasoning": model_reasoning,
                    "model": gm_model,
                    "tokens": tokens,
                    "duration_ms": duration_ms,
                }

                if plan is None:
                    GameMaster.add_entry(
                        user.id,
                        chat_id,
                        **common,
                        reasoning=reasoning or content,
                        error="The GM's reply had no readable plan, so nothing was changed.",
                    )
                    await emit({"status": "error"})
                    return None

                note = _clip(_text(plan.get("note")), 4000)
                new_state = promote_introduced(apply_plan(state, plan), note)
                observations = [
                    _clip(o, 500) for o in plan.get("observations") or [] if isinstance(o, str)
                ][:20]
                patch = {k: plan[k] for k in ("update", "remove") if k in plan}
                patch["changes"] = describe_changes(before, new_state)
                entry = GameMaster.add_entry(
                    user.id,
                    chat_id,
                    **common,
                    reasoning=reasoning,
                    observations=observations,
                    patch=patch,
                    state=new_state,
                    note=note,
                )
                log.info(
                    "GM %s pass for chat %s: %d tokens in %.1fs",
                    kind,
                    chat_id,
                    tokens,
                    duration_ms / 1000,
                )
                if session.config.get("auto_portraits", True):
                    await queue_missing_portraits(request, user, chat_id, new_state, gm_model)
                await emit({"status": "done"})
                return entry
            except Exception as e:
                log.exception("GM pass failed for chat %s", chat_id)
                GameMaster.add_entry(
                    user.id,
                    chat_id,
                    message_id=message_id or "",
                    kind=kind,
                    prior_id=prior.id if prior else "",
                    model=gm_model,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=str(e)[:2000] or e.__class__.__name__,
                )
                await emit({"status": "error"})
                return None
    except Exception:
        log.exception("GM pass could not be journalled for chat %s", chat_id)
        return None
    finally:
        _running[chat_id] = max(0, _running.get(chat_id, 1) - 1)


def _token_key(models: dict, model_id: str) -> str:
    return "max_tokens" if models[model_id].get("owned_by") == "ollama" else "max_completion_tokens"


async def run_table_talk(
    request: Any,
    user: Any,
    chat_id: str,
    message_id: Optional[str],
    model_id: str,
    talk: str,
) -> Optional[Any]:
    """Answer the player out of character, then update the plan in the background.

    Two steps on purpose. The answer is a plain conversation ending on the player's
    words, so it comes back in seconds and cannot be mistaken for a planning job. The
    plan update that follows is an ordinary pass (talk_plan) told what was said.
    Returns the table_talk entry, or None if the GM gave no answer.
    """
    from open_webui.models.chats import Chats
    from open_webui.models.game_master import GameMaster
    from open_webui.models.video_characters import VideoCharacters
    from open_webui.utils.chat import generate_chat_completion
    from open_webui.utils.chat_instructions import get_stored_chat_instructions
    from open_webui.utils.misc import get_message_list

    entry = None
    _running[chat_id] = _running.get(chat_id, 0) + 1
    try:
        async with _lock(chat_id):
            session = GameMaster.get_session(user.id, chat_id)
            if not session or not session.enabled:
                return None
            started = time.monotonic()
            gm_model = ""
            prior = None
            try:
                chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
                if not chat:
                    return None
                messages_map = Chats.get_messages_map_by_chat_id(chat_id) or {}
                chain = get_message_list(messages_map, message_id) if message_id else []
                index = GameMaster.get_entry_index(user.id, chat_id)
                prior_id = find_state_entry_id(index, messages_map, message_id, inclusive=True)
                prior = GameMaster.get_entry(user.id, prior_id) if prior_id else None
                state = prior.state if prior else None

                models = request.app.state.MODELS
                gm_model = resolve_gm_model(models, model_id)
                if gm_model not in models:
                    raise ValueError(f"Model not found: {gm_model}")
                talk_messages = build_talk_messages(
                    characters=VideoCharacters.get_for_chat(user.id, chat_id),
                    player_character_id=session.config.get("player_character_id", ""),
                    config=session.config,
                    chat_instructions=get_stored_chat_instructions(chat) or "",
                    state=state,
                    conversation=chain[-TURN_MESSAGES:],
                    talk_history=[
                        {"player": item.user_message, "gm": item.gm_reply}
                        for item in GameMaster.get_table_talk(user.id, chat_id, TALK_HISTORY)
                    ],
                    talk=talk,
                )
                response = await generate_chat_completion(
                    request,
                    form_data={
                        "model": gm_model,
                        "messages": talk_messages,
                        "stream": False,
                        _token_key(models, gm_model): TALK_MAX_TOKENS,
                        "metadata": {"task": "game_master", "chat_id": chat_id},
                    },
                    user=user,
                    bypass_filter=True,
                    bypass_system_prompt=True,
                )
                content, model_reasoning, tokens = _reply_parts(response)
                reply = _clip(clean_talk_reply(content), 8000)
                fields = {
                    "message_id": message_id or "",
                    "kind": "table_talk",
                    "prior_id": prior.id if prior else "",
                    "user_message": talk,
                    "model_reasoning": model_reasoning,
                    "model": gm_model,
                    "tokens": tokens,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
                if not reply:
                    GameMaster.add_entry(
                        user.id, chat_id, **fields, error="The GM gave an empty answer."
                    )
                    return None
                # The answer changes nothing by itself: it carries the plan and the
                # direction over, so it is a safe point for later passes to build on.
                entry = GameMaster.add_entry(
                    user.id,
                    chat_id,
                    **fields,
                    gm_reply=reply,
                    state=copy.deepcopy(state) if state is not None else None,
                    note=prior.note if prior else "",
                )
                log.info(
                    "GM table talk for chat %s: %d tokens in %.1fs",
                    chat_id,
                    tokens,
                    fields["duration_ms"] / 1000,
                )
            except Exception as e:
                log.exception("GM table talk failed for chat %s", chat_id)
                GameMaster.add_entry(
                    user.id,
                    chat_id,
                    message_id=message_id or "",
                    kind="table_talk",
                    prior_id=prior.id if prior else "",
                    user_message=talk,
                    model=gm_model,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=str(e)[:2000] or e.__class__.__name__,
                )
                return None
    except Exception:
        log.exception("GM table talk could not be journalled for chat %s", chat_id)
        return None
    finally:
        _running[chat_id] = max(0, _running.get(chat_id, 1) - 1)

    # Outside the lock: the plan update queues behind it rather than holding the answer.
    schedule_gm_pass(
        request,
        user,
        chat_id,
        message_id,
        model_id,
        kind="talk_plan",
        talk=talk,
        talk_reply=entry.gm_reply,
    )
    return entry


def schedule_gm_pass(
    request: Any,
    user: Any,
    chat_id: str,
    message_id: Optional[str],
    model_id: str,
    kind: str = "turn",
    event_emitter: Optional[Callable] = None,
    **options: Any,
) -> bool:
    """Start a pass in the background if the GM is on for this chat.

    The chat counts as running from here, not from when the task first gets the event
    loop, so a status read straight after scheduling already shows the GM planning.
    """
    from open_webui.models.game_master import GameMaster

    if not chat_id or chat_id.startswith("local:") or not model_id:
        return False
    session = GameMaster.get_session(user.id, chat_id)
    if not session or not session.enabled:
        return False
    _running[chat_id] = _running.get(chat_id, 0) + 1
    task = asyncio.create_task(
        run_gm_pass(
            request, user, chat_id, message_id, model_id, kind, event_emitter, counted=True, **options
        )
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


# ---------------------------------------------------------------------------
# NPC portraits
# ---------------------------------------------------------------------------


def _can_generate_portraits(request: Any, user: Any) -> bool:
    from fastapi import HTTPException
    from open_webui.routers.videos import _check_video_access

    try:
        _check_video_access(request, user)
        return True
    except HTTPException:
        return False


async def write_portrait_prompt(
    request: Any, user: Any, model_id: str, npc: dict, direction: str = ""
) -> str:
    """An image prompt written for this NPC by the chat's own model.

    Falls back to the neutral reference template if there is no model or the model
    gives nothing usable, so a portrait is always drawn.
    """
    from open_webui.utils.chat import generate_chat_completion
    from open_webui.utils.videos.portrait import (
        build_portrait_prompt,
        build_portrait_writer_messages,
        clean_portrait_prompt,
    )

    look = _text(npc.get("look"))
    fallback = build_portrait_prompt(look)
    try:
        models = request.app.state.MODELS
        writer = resolve_gm_model(models, model_id) if model_id else ""
        if writer not in models:
            return fallback
        response = await generate_chat_completion(
            request,
            form_data={
                "model": writer,
                "messages": build_portrait_writer_messages(
                    _text(npc.get("name")), _text(npc.get("card")), look, _text(direction)
                ),
                "stream": False,
                _token_key(models, writer): PORTRAIT_PROMPT_TOKENS,
                "metadata": {"task": "game_master_portrait"},
            },
            user=user,
            bypass_filter=True,
            bypass_system_prompt=True,
        )
        content, _, _ = _reply_parts(response)
        prompt = clean_portrait_prompt(content)
        # A refusal or a stub is no better than the template.
        return prompt if len(prompt) >= 80 else fallback
    except Exception:
        log.exception("Could not write a portrait prompt for %s", npc.get("name"))
        return fallback


async def queue_npc_portrait(
    request: Any,
    user: Any,
    chat_id: str,
    npc: dict,
    seed: Optional[int] = None,
    model_id: str = "",
    direction: str = "",
) -> bool:
    """Queue one portrait behind any videos. False if portraits are unavailable.

    The prompt is written first, in the background: written inside the queued job it
    would hold the video queue while a language model thinks.
    """
    import random

    from open_webui.models.game_master import GameMaster
    from open_webui.routers.videos import _upload_video, enqueue_comfyui_job
    from open_webui.utils.videos.comfyui import ComfyUIVideoClient
    from open_webui.utils.videos.portrait import generate_portrait

    npc_id, name, look = npc.get("id"), _text(npc.get("name")), _text(npc.get("look"))
    if not npc_id or not name or not look or not _can_generate_portraits(request, user):
        return False
    seed = seed if seed is not None else random.randint(0, 2**53 - 1)
    GameMaster.set_portrait(
        user.id,
        chat_id,
        npc_id,
        name=name,
        look=look,
        status="queued",
        seed=seed,
        error="",
        file_id="",
        prompt="",
    )
    prompt = ""

    async def runner() -> dict:
        GameMaster.set_portrait(user.id, chat_id, npc_id, status="running")
        try:
            config = request.app.state.config
            client = ComfyUIVideoClient(
                config.COMFYUI_VIDEO_BASE_URL,
                config.COMFYUI_VIDEO_API_KEY,
                config.COMFYUI_VIDEO_TIMEOUT,
            )
            data, _, content_type = await generate_portrait(client, prompt, seed)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "npc"
            file_item, _ = await asyncio.to_thread(
                _upload_video,
                request,
                data,
                f"gm-portrait-{safe}.png",
                content_type,
                {"gm_portrait": {"chat_id": chat_id, "npc_id": npc_id, "name": name, "seed": str(seed)}},
                user,
            )
            GameMaster.set_portrait(
                user.id, chat_id, npc_id, status="ready", file_id=file_item.id, error=""
            )
            return {"file_id": file_item.id}
        except Exception as e:
            log.exception("Portrait for %s failed", name)
            GameMaster.set_portrait(
                user.id, chat_id, npc_id, status="failed", error=str(e)[:1000] or "Portrait failed"
            )
            raise

    async def prepare() -> None:
        nonlocal prompt
        try:
            prompt = await write_portrait_prompt(request, user, model_id, npc, direction)
            GameMaster.set_portrait(user.id, chat_id, npc_id, prompt=prompt)
            await enqueue_comfyui_job(user.id, "portrait", f"Portrait: {name}", runner)
        except Exception as e:
            log.exception("Could not queue a portrait for %s", name)
            GameMaster.set_portrait(
                user.id, chat_id, npc_id, status="failed", error=str(e)[:1000] or "Portrait failed"
            )

    task = asyncio.create_task(prepare())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def queue_missing_portraits(
    request: Any, user: Any, chat_id: str, state: dict, model_id: str = ""
) -> None:
    """Portraits for NPCs who have just come into the scene. Never raises."""
    from open_webui.models.game_master import GameMaster

    try:
        drawn = {p.npc_id: p.name for p in GameMaster.get_portraits(user.id, chat_id)}
        for npc in npcs_needing_portraits(state, drawn):
            if not await queue_npc_portrait(request, user, chat_id, npc, model_id=model_id):
                return
    except Exception:
        log.exception("Could not queue NPC portraits for chat %s", chat_id)


def get_scene_npcs(user_id: str, chat_id: str, message_id: Optional[str]) -> list[dict]:
    """NPCs on stage at message_id, with their portraits, for the Video Studio."""
    from open_webui.models.chats import Chats
    from open_webui.models.game_master import GameMaster

    index = GameMaster.get_entry_index(user_id, chat_id)
    if not index:
        return []
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    entry_id = find_state_entry_id(index, messages_map, message_id, inclusive=True)
    entry = GameMaster.get_entry(user_id, entry_id) if entry_id else None
    if not entry:
        return []
    portraits = {
        p.npc_id: {"name": p.name, "status": p.status, "file_id": p.file_id}
        for p in GameMaster.get_portraits(user_id, chat_id)
    }
    return scene_npcs(entry.state, portraits)
