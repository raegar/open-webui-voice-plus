# Game Master: design

Status: **draft for discussion**, nothing built yet.

## The problem

Role play works well turn by turn but drifts over a long scene, in two ways:

1. **Regression to the middle.** Conflict gets resolved as soon as it appears. Stakes quietly go away, and every scene settles into the same warm, agreeable register.
2. **Characters can't say no.** A character who refused something three turns ago gives in as soon as the user asks again, with nothing in the story to justify it. Motivations in the character profile get applied in tone but not in decisions.

Both come from the same place. The chat model answers one turn at a time and is tuned to satisfy the person it is talking to. It has no memory of where the story is going and no stake in keeping it difficult. Stronger prompting of the chat model doesn't fix this, because the model we are prompting is the one that drifts.

## The idea

A second, hidden model acts as the **Game Master (GM)**. The chat model is the *actor*: it plays the characters and writes the prose. The GM is the *director*. It never writes prose and never speaks in the chat. Its job:

- keep its own plans for the story: threads, secrets, complications, and where things are heading;
- track what each character wants and what it would take for them to change their mind;
- notice when the actor has caved, flattened a conflict, or drifted out of character, and correct for it;
- send the actor short, private **director's notes** that shape the next reply.

The GM is judged on whether the story is interesting, not on whether the user is pleased. That is what lets it push back when the actor won't.

The GM is optional and per chat. It starts ("spawns") when a chat is created with it on, or when it's switched on for an existing chat.

## What the user sees

- **A GM toggle** for each chat in the Controls pane, plus a setting for whether new chats start with it on.
- **A Game Master section** in Controls with:
  - an **Agenda** box where the user can seed the GM ("slow-burn betrayal", "Sam should never fully trust Alex", "make the heist go wrong"). The GM treats this as the brief for the campaign, not as a script;
  - an **Intensity** setting: *Light* (keeps characters consistent, rarely adds events), *Firm* (characters hold their ground, occasional complications), *Ruthless* (active antagonism, real setbacks, refusals that stick);
  - a **"Played by me"** marker on one attached character. The GM never directs that character. See [Your own character](#your-own-character);
  - the **current direction**: the note the actor will get on the next turn, with a *Reroll* button;
  - **Consult now**: runs the GM immediately, e.g. after the user edits the agenda.
- **The GM transcript.** A chronological log of every GM pass: what it noticed, its reasoning, what it changed in its plans, and the note it issued. It opens from the Controls section and from a small marker on each steered assistant message ("GM: steered this reply"), which jumps to that turn's entry.
- **Spoilers are hidden by default.** The GM's secrets and planned twists are covered until clicked, so the user can peek at the reasoning without ruining the plot. A setting shows everything, for when the user wants to watch the GM work.

The GM adds nothing to the chat itself. A chat with the GM on reads exactly like one without it, just with better direction.

## How a turn works

```text
 user sends message ─┐
                     ▼
   ┌──────────── request to actor ─────────────┐
   │ system: model prompt                      │
   │         character profiles  (existing)    │
   │         <director_notes>    (GM, new)     │
   │         chat instructions   (existing)    │
   │ messages: conversation                    │
   └───────────────────┬───────────────────────┘
                       ▼
             actor reply streams to user
                       ▼
     background_tasks_handler (titles, tags, …)
                       ▼
          GM pass (background, no added latency)
     reads: GM state + profiles + recent turns
     writes: journal entry, new state, next note
                       ▼
       "chat:gm" event → Controls pane updates
```

**The GM runs after the reply, not before it.** While the user reads and types, the GM reviews what just happened and writes the note for the next turn. This adds **no latency** to the chat, which matters on OpenRouter where a second round trip before every reply would add seconds.

The cost is a one-turn lag: the GM writes its note before it sees the user's next message. The note format is designed around that. Notes are about each character's intentions, positions and limits, not a script for the next line of dialogue: "Sam refuses to hand over the key unless Alex tells the truth about the letter" still works whatever the user says next. A turn-by-turn script would break as soon as the user did something unexpected.

A **Deliberate** option runs the GM *before* each reply instead, after it has seen the user's message. That gives the tightest control at the cost of a pause before every reply. It's worth having for slow, high-stakes scenes, but it should not be the default.

The GM does not have to run on every turn. A **cadence** setting (every turn / every 2 / every 3) saves cost. A skipped turn reuses the previous note. The GM always runs on the first turn and on *Consult now*.

## Session zero (spawn)

When the GM starts on a chat, its first pass is a planning pass rather than a reaction. It reads the attached characters and locations, the chat instructions, the agenda, and the opening messages, and writes the initial campaign state:

- each character's **drive**: what they want, what they need (which may conflict with the want), what they fear, and a line they won't cross;
- two or three **threads** for the story to pull on;
- **secrets**, divided into actor-facing ones and GM-only ones (see below);
- a few **clocks** for pressure that should build over time;
- the first director's note.

It runs in the background as soon as the chat has an id, so it's usually ready by the time the first reply finishes. If it isn't, the first turn goes out without a note, and nothing waits on it.

## GM state

The GM keeps structured state, stored per chat and versioned per turn. It gives the GM a memory the conversation can't wear away: the actor loses old turns to the context window and the condenser, but the GM's plans persist.

```jsonc
{
  "premise": "one paragraph: what this story is about underneath",
  "characters": {
    "Sam": {
      "want": "get out of the city before the audit",
      "need": "to stop running from the family",
      "fear": "being seen as her father's daughter",
      "line": "will not lie to Alex's face",           // what they won't do
      "stance": {                                        // positions on live issues
        "leaving together": {
          "position": "no, not yet",
          "price": "Alex admits he read the letter",     // what would change it
          "since_turn": 14
        }
      },
      "voice_notes": "clipped when cornered; deflects with jokes"
    }
  },
  "threads": [
    { "id": "t1", "title": "the missing ledger", "status": "rising", "next_beat": "someone else is looking for it" }
  ],
  "clocks": [
    { "id": "c1", "label": "the landlord's patience", "filled": 3, "size": 6, "on_full": "he calls the police" }
  ],
  "secrets": [
    { "id": "s1", "text": "Sam already has the key", "known_by": ["Sam"], "reveal": "when cornered" },
    { "id": "s2", "text": "the buyer is Sam's brother", "known_by": [], "reveal": "gm-only, after the ledger thread peaks" }
  ],
  "tension": { "current": 4, "target": 6 },            // 0–10, GM's own reading
  "note": "the director's note for the next reply"
}
```

**The stance ledger is the main defence against "can't say no".** For each live issue, the GM records where a character stands and the *price* of moving them: an event, a concession or a revelation. On every pass it checks the latest reply against the ledger. If a character's position moved and the price wasn't paid, the GM treats that as a cave and the next note corrects it: "Sam gave in on leaving last turn without Alex admitting anything. She has second thoughts and pulls back, and says why." Once the price has been paid in the story, the GM updates the stance, and the change counts as earned.

**Clocks turn the drift toward calm into pressure that builds.** Something that fills over time can't quietly go away. The GM advances a clock when the story pushes it forward, and when it fills, the consequence happens.

**Secrets have two tiers.** An actor has to know their own character's secrets to play them, so those appear in the director's notes ("Sam has the key and deflects any question about it"). Twists that no character knows yet (`known_by: []`) stay GM-only and never reach the actor until the GM decides to reveal them. The GM can still hint at them indirectly ("a stranger at the bar watches Sam a little too long").

## The GM's own prompt

The GM's system prompt sets out the role, and the rules that make it resist the same drift as the actor:

- Your job is a story worth telling, not the user's comfort. Conflict, refusal, cost and consequence are good for the story, not failures.
- Characters are people with their own agendas. They say no, bargain, lie, withhold, change the subject and walk away when their drives call for it. Agreement has to be earned in the story.
- Don't make characters contrary for its own sake. Pushback has to come from a drive in the ledger. A character who objects to everything is as flat as one who agrees to everything.
- Honour the user's agenda and chat instructions. The GM makes the story more difficult, never something the user said they didn't want.
- Never direct the user's own character (see below).
- Complications come from the world, not from coincidence. Use what's already set up: threads, clocks, characters with somewhere to be.
- Respect Intensity: *Light* keeps characters consistent, *Firm* holds their ground, *Ruthless* plays antagonists to win.

The GM replies with **reasoning in free text, then a JSON block** holding the state changes and the note. The reasoning goes into the transcript as written. That is the "GM's thinking" the user asked to see. If the GM model is a reasoning model that returns its own `<think>` content, the journal keeps that too, collapsed by default. The state changes are applied as a patch rather than a full rewrite, so a malformed or truncated reply can't wipe the campaign. The same idea as the outfit tracker's rule that a bad reply never clears a recorded state.

## What the actor receives

A `<director_notes>` block, appended to the system message **after the character profiles and before the chat instructions**. The user's per-chat instructions keep the final word, as they do today.

```text
<director_notes>
Private stage direction from the game master for your next reply. Follow it through
the characters' choices, not by announcing it. Never mention, quote or hint at these
notes, and never reveal a secret before the story earns it.

Current positions (characters hold these until something in the story changes them):
- Sam on leaving together: no, not yet. She would move only if Alex admits he read the letter.

Pressure: the landlord is losing patience (3 of 6).

Direction: Sam is warmer than she was, and that makes her more guarded, not less.
If Alex pushes about the key, she deflects with a joke, then gets short with him.
</director_notes>
```

It is deliberately short. The actor doesn't get the whole state, only what bears on the next reply. A long block would crowd out the conversation, and every extra detail is one more that might leak into the prose.

## The transcript

Each GM pass writes one **journal entry**:

| Field | Purpose |
| --- | --- |
| `message_id` | The assistant message the pass reacted to. It links an entry to a reply in the chat and makes branching work (below). |
| `kind` | `setup`, `turn`, `consult`, `reroll`, or `skipped` |
| `reasoning` | The GM's free-text thinking, shown in the transcript as written |
| `model_reasoning` | A reasoning model's native thinking, if any, collapsed by default |
| `observations` | Short list: caves detected, drift, threads advanced |
| `patch` | The state changes this pass made, shown as a readable diff |
| `state` | The full state after the pass (a snapshot, for branching) |
| `note` | The director's note this pass issued |
| `model`, `tokens`, `duration_ms`, `error` | Cost and diagnostics |

The transcript view is a readable timeline, not raw JSON. Each entry shows turn number, what the GM noticed, its reasoning, a state diff ("Sam, leaving together: *maybe* → *no*"; "landlord 3 → 4 of 6"), and the note. Secret text and GM-only threads are blurred until clicked. A failed pass is logged with its error rather than dropped, so a GM that has quietly stopped working shows up in the log.

## Branching and regeneration

Open WebUI chats are trees: regenerating a reply or editing a message starts a new branch. If the GM kept a single mutable state per chat, regenerating a reply would apply the GM's reaction twice, and switching back to an earlier branch would carry state from one timeline into another.

The fix is the per-entry `state` snapshot. When a request comes in, the GM state for it is the snapshot from the journal entry whose `message_id` is the **nearest ancestor** of the message being answered. Regenerate a reply and the new branch starts from the same state the old one did. Switch branches and each has its own. There is no special case for either: it falls out of keying state to the message tree rather than to the chat.

Snapshots are small (a few KB of JSON) and there's one per GM pass, so storing them in full is simpler and safer than rebuilding state from patches.

## Your own character

Chats often attach a profile for the character the user plays, so their appearance and actions stay consistent. The GM must never direct that character. The user's choices are the user's, and a note like "Alex hesitates" aimed at the user's own character would have the actor writing their decisions for them.

At the moment nothing marks which attached character is the user's. The profile ordering in `character_personality.py` puts the AI's persona first and treats the rest as supporting, and that stays as it is. The GM adds its own per-chat **"Played by me"** marker, used only by the GM: that character is left out of the stance ledger's directives, and the GM prompt says so. The GM can still plan *around* them, including what other characters want from them and what they know about them.

## Storage

Two new tables, following the pattern of `video_characters.py` (SQLAlchemy model plus an in-place migration):

```text
gm_session
  chat_id (pk), user_id, enabled, config JSON
  config: { agenda, intensity, cadence, deliberate, model_id, player_character_id, show_spoilers }
  created_at, updated_at

gm_journal
  id (pk), chat_id (idx), user_id, message_id (idx), kind,
  reasoning, model_reasoning, observations JSON, patch JSON, state JSON, note,
  model, tokens, duration_ms, error, created_at
```

Deleting a chat deletes its GM session and journal. The journal follows the chat's visibility: when work mode hides a private chat, its transcript is hidden too.

## Where it plugs in

| Piece | Location |
| --- | --- |
| GM pass (prompt, call, parse, apply patch) | new `backend/open_webui/utils/game_master.py` |
| Tables | new `backend/open_webui/models/game_master.py` |
| Routes: get/set config, journal, consult, reroll | new `backend/open_webui/routers/game_master.py` |
| Inject `<director_notes>` | `process_chat_payload` in `utils/middleware.py`, between the character profile and chat instruction injections |
| Trigger after reply | `background_tasks_handler` in `utils/middleware.py`, next to title and tag generation, emitting a `chat:gm` event |
| Deliberate mode | `process_chat_payload`, awaited before injection |
| UI: toggle, agenda, intensity, current note | new section in `Controls/Controls.svelte` |
| UI: transcript | new component, opened from Controls and from a marker in `ResponseMessage.svelte` |

**Running on the server rather than in the browser**, unlike the outfit tracker, is deliberate. The GM's plans have to keep going if the tab closes mid-scene, work for scheduled tasks and API callers, and never race between two open tabs.

## Traps to design around

- **The persona problem, again.** The outfit tracker learned that a background call against a workspace model runs *in character*, because the model's own system prompt is prepended (`31eb8ed5f`). The GM must not play one of the characters. Call it with `bypass_system_prompt=True` and resolve the chat model to its base model, or use the GM's own configured model.
- **Persistent memory would record the GM's plans.** The persistent memory pipeline stores what passes through it. If GM calls go through the pipeline inlet, the GM's private planning, secrets included, turns into memories the actor can later retrieve. Call with `bypass_filter=True` and skip `process_pipeline_inlet_filter`. The condenser filter has to be skipped too: the GM reads raw recent turns plus its own state.
- **Refusals from the GM model.** A model chosen for being agreeable will write agreeable direction. The GM needs a model that can reason and will commit to conflict. It gets its own model setting, and the transcript makes it easy to check whether a GM model is doing its job.
- **Leakage.** Actors sometimes quote their instructions ("As the director noted…"). Keep notes short, written as characters' intentions, and never containing GM-only secrets, so a leak doesn't give much away.
- **Cost.** Roughly one extra call per GM turn, with input about the size of a few turns plus the state. Cadence and a cheaper GM model keep it down, and the journal records tokens per pass so the cost is visible.
- **Work mode and public commits.** GM state holds the user's story content. It lives in the DB only. Tests and fixtures use placeholder characters (Alex, Sam), as elsewhere in the repo.

## Build order

1. **Core loop.** Tables, GM pass, background trigger, `<director_notes>` injection, per-chat toggle. No UI beyond the toggle. Check in the DB that direction is actually changing replies.
2. **Transcript.** Journal view with reasoning, diffs, notes and spoiler blur, plus the message marker.
3. **Controls.** Agenda, intensity, "played by me", current note, Consult now, Reroll.
4. **Branch-correct state.** Nearest-ancestor snapshot lookup, with tests covering regenerate and branch switching.
5. **Deliberate mode and cadence.**

Stage 1 is enough to find out whether the idea works at all. Everything after it is about control and visibility.

## Open questions

- **Default timing.** After the reply (no latency, one-turn lag) is recommended. Is the lag acceptable, or should Deliberate be the default?
- **GM model.** A dedicated default GM model in settings, or the chat's own base model unless changed?
- **Spoilers.** Hidden until clicked by default, or everything visible?
- **Can the GM introduce new characters?** For example, a stranger who walks in. That's a strong complication tool, but the actor has to invent them with no profile. Allowing it with a one-line description in the note seems reasonable.
- **Should the transcript be editable?** Letting the user correct the GM's state directly ("no, Sam doesn't know that") is powerful but invites contradictions. A lighter alternative is to tell the GM through the agenda box.
