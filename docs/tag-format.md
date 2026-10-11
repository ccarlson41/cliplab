# Tag files

A tag file tells ClipLab what happened in a clip, so the short can be trimmed to the action and labeled. It's optional: clips without one are rendered untagged (the last `max_seconds`, no text).

## Where it goes
Next to the clip, named after the clip's **full** file name plus `.json`:

```
Replay 2026-10-12 21-14-03.mp4
Replay 2026-10-12 21-14-03.mp4.json
```

Write it to a temporary name and rename it into place, so ClipLab never reads half a file. ClipLab waits up to `tag_wait` seconds (default 20) after a clip stops growing for its tag file to appear.

## Format

```json
{
  "version": 1,
  "game": "League of Legends",
  "savedAt": "2026-10-12T21:14:03-05:00",
  "bufferSeconds": 60,
  "player": "Name#TAG",
  "champion": "Ahri",
  "events": [
    { "type": "Multikill", "label": "TRIPLE KILL", "gameTime": 1421.3, "secondsBeforeEnd": 9.8 },
    { "type": "Ace",       "label": "ACE",         "gameTime": 1424.0, "secondsBeforeEnd": 7.1 }
  ],
  "title": "Triple kill into an ace on Ahri"
}
```

| Field | Used for |
|---|---|
| `events` | **Required, non-empty**, or the tag is ignored. |
| `events[].secondsBeforeEnd` | **Required.** How many seconds before the end of the clip the event happened. Measure it in wall-clock time, from when the event was seen to when the save was requested; don't map game time to video time. |
| `events[].label` | Text drawn on screen at that moment. Uppercased; only letters, digits and spaces are kept. Suggested labels: DOUBLE KILL, TRIPLE KILL, QUADRA KILL, PENTAKILL, ACE, FIRST BLOOD, BARON STEAL, DRAGON STEAL, HERALD STEAL. |
| `title` | First line of the caption file. |
| `champion` | Adds a champion hashtag to the caption and shows in the app. May be empty. |
| `version`, `game`, `savedAt`, `bufferSeconds`, `player`, `events[].type`, `events[].gameTime` | Not used yet. Keep them; new fields should only ever be added. |

## How ClipLab trims
- Start: `lead_in` seconds (default 4) before the earliest event.
- End: `tail` seconds (default 3) after the latest event.
- Shorter than `min_seconds` (default 8): extended earlier.
- Longer than `max_seconds` (default 60): the end is kept.
- Each label shows from 0.15 s before its event to 2.2 s after.

All of these are adjustable in the app's settings, and any single short can be re-cut by hand.
