# Dopamine to Navidrome Rating & Loved Sync

This script provides an interactive utility to migrate your track ratings and "Loved" (starred) statuses from multiple **Dopamine SQLite databases** into a **Navidrome** music server via the Subsonic API.

It compiles tracks from all your designated Dopamine instances, resolves metadata conflicts natively in-memory, tracks progress via a persistent local cache, and lets you safely human-verify matches before making API updates.

---

## Features

* **Multi-Database Ingestion**: Aggregates tracks across multiple versions of Dopamine 3.0 series databases.
* **Smart Conflict Resolution**: When merging duplicates across databases, true "Love" statuses take absolute precedence, and the highest rating value wins.
* **Persistent Cache Management**: Tracks progress via a local SQLite state database (`merge.db`). If you abort execution (`Ctrl+C`), you can pick up exactly where you left off.
* **Fuzzy Confidence Matching**: Scores potential Navidrome candidates using text similarity heuristics (Artist, Title, Album) combined with proximity metrics for duration.
* **Safety Bypass Logic**: Automatically passes over tracks if a single Navidrome candidate matches perfectly and already has an identical or superior rating/love state.
* **Interactive Human Verification**: Presents conflicting multi-match candidates or unrated targets in aligned ASCII terminal tables for quick choice confirmation.

---

## How the Sync Mapping Works

### Loved Status

If a Dopamine track has a value of `1` in its `Love` column, the script flags that track to be starred in Navidrome. If a track is already starred in Navidrome but unloved in Dopamine, Navidrome's starred status is left completely intact.

### Star Ratings

**Untested:** The following may lead to inaccurate conversion if you were using a 5-star system. These may actually be different database columns (Rating vs NewRating), but **use at your own risk**.
Because Dopamine optionally utilizes half-stars on a 1–10 integer scale, and Navidrome relies on standard 1–5 integer star ratings, the script safely down-converts your metrics using standard ceiling adjustments:

| Dopamine Score (1–10 Scale) | Converted Navidrome Stars (1–5 Scale) |
| --- | --- |
| 9 or 10 | 5 Stars |
| 7 or 8 | 4 Stars |
| 5 or 6 | 3 Stars |
| 3 or 4 | 2 Stars |
| 1 or 2 | 1 Star |

---

## Configuration

Open the script and configure your environment primitives directly in the `# --- CONFIGURATION ---` section:

```python
# Paths to your local Dopamine SQLite files
DOPAMINE_DB_PATHS = [
    Path(r"/home/sam/.config/dopamine/Dopamine.db"),
    Path(r"/home/sam/.config/Dopamine/Dopamine.db")
]

# Your target Navidrome or Subsonic endpoint server credentials
NAVIDROME_URL = "http://localhost:4533" 
NAVIDROME_USER = "YourUsername"
NAVIDROME_PASS = "YourPassword"

```

---

## Usage

Ensure you have your prerequisites installed via your package manager:

```bash
pip install requests

```

Run the script directly using Python:

```bash
python migrate-dopamine-ratings-to-navidrome.py

```

### Interactive Prompt Commands

When multiple candidates are discovered, you will be prompted for an action:

* **`n`**: Skip this specific track entirely. It will mark the song as `SKIPPED` in `merge.db` and will not prompt you again on subsequent runs.
* **`<Index Integer>`**: Enter a candidate integer (e.g., `1`) to apply the metadata update directly to that specific Navidrome track.
* **`<Comma-delimited Integers>`**: Enter a list of choices (e.g., `1,2`) to apply updates across multiple target duplicates simultaneously.

---

## Output Metrics

Upon completion or interruption, the script flushes diagnostic reporting counters straight to your log file and your screen:

```text
====================================
SYNC STATISTICS
====================================
Databases read:                    2
Tracks examined:              18,442
Loved tracks:                    931
Rated tracks:                  2,144
Matched:                       2,081
Not found:                        63
Loved updated:                   124
Ratings updated:                 847
Already starred:                 807
Already rated:                 1,297
Songs remaining:                 142
Errors:                            3
====================================

```