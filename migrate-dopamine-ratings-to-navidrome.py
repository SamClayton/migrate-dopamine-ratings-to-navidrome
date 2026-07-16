import sqlite3
import hashlib
import random
import string
import requests
import math
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from difflib import SequenceMatcher

# --- CONFIGURATION ---
DOPAMINE_DB_PATHS = [
    Path(r"/home/sam/.config/dopamine/Dopamine.db"),
    Path(r"/home/sam/.config/Dopamine/Dopamine.db")
]
MERGE_DB_PATH = Path("merge.db")

NAVIDROME_URL = "http://localhost:4533" 
NAVIDROME_USER = "Samantha"
NAVIDROME_PASS = "Y*9$ApQMUW!RibHrmyQ$x"
# ---------------------

@dataclass
class TrackData:
    artists: str
    title: str
    album: str
    duration: float
    love: bool
    rating_10: int

@dataclass
class SyncStats:
    databases_read: int = 0
    tracks_examined: int = 0
    loved_tracks: int = 0
    rated_tracks: int = 0
    matched: int = 0
    not_found: int = 0
    loved_updated: int = 0
    ratings_updated: int = 0
    already_starred: int = 0
    already_rated: int = 0
    errors: int = 0

    def print_report(self):
        print("\n" + "=" * 35)
        print("SYNC STATISTICS")
        print("=" * 35)
        print(f"{'Databases read:':<24} {self.databases_read:>9,}")
        print(f"{'Tracks examined:':<24} {self.tracks_examined:>9,}")
        print(f"{'Loved tracks:':<24} {self.loved_tracks:>9,}")
        print(f"{'Rated tracks:':<24} {self.rated_tracks:>9,}")
        print(f"{'Matched:':<24} {self.matched:>9,}")
        print(f"{'Not found:':<24} {self.not_found:>9,}")
        print(f"{'Loved updated:':<24} {self.loved_updated:>9,}")
        print(f"{'Ratings updated:':<24} {self.ratings_updated:>9,}")
        print(f"{'Already starred:':<24} {self.already_starred:>9,}")
        print(f"{'Already rated:':<24} {self.already_rated:>9,}")
        print(f"{'Errors:':<24} {self.errors:>9,}")
        print("=" * 35 + "\n")


class NavidromeClient:
    """A Subsonic API client using a persistent Session."""
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.api_version = "1.16.1"
        self.client_name = "DopamineSyncScript"
        self.session = requests.Session()
        
    def _get_auth_params(self) -> Dict[str, str]:
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        token = hashlib.md5((self.password + salt).encode('utf-8')).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": self.api_version,
            "c": self.client_name,
            "f": "json"
        }

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        full_params = self._get_auth_params()
        full_params.update(params)
        
        response = self.session.get(f"{self.base_url}/rest/{endpoint}", params=full_params)
        response.raise_for_status()
        
        data = response.json()
        if data.get("subsonic-response", {}).get("status") == "failed":
            error = data["subsonic-response"].get("error", {})
            raise RuntimeError(f"Navidrome API Error: {error.get('message')}")
            
        return data["subsonic-response"]

    def search_tracks(self, artist: str, title: str) -> List[Dict[str, Any]]:
        query = f"{artist} {title}"
        response = self._make_request("search3", {"query": query, "songCount": 10})
        return response.get("searchResult3", {}).get("song", [])

    def set_love(self, track_id: str):
        self._make_request("star", {"id": track_id})

    def set_rating(self, track_id: str, rating_5: int):
        rating_5 = max(1, min(5, rating_5))
        self._make_request("setRating", {"id": track_id, "rating": rating_5})

# --- DATA PROCESSING & HEURISTICS ---

def normalize_duration(val: Optional[float]) -> int:
    if not val: return 0
    if val > 10_000_000: return int(val / 10_000_000)
    elif val > 10_000: return int(val / 1_000)
    return int(val)

def similar(a: str, b: str) -> float:
    if not a or not b: return 0.0
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def calculate_confidence(d_track: TrackData, n_track: Dict[str, Any]) -> float:
    score_artist = similar(d_track.artists, n_track.get('artist', ''))
    score_title = similar(d_track.title, n_track.get('title', ''))
    score_album = similar(d_track.album, n_track.get('album', ''))
    
    d_dur = normalize_duration(d_track.duration)
    n_dur = n_track.get('duration', 0)
    
    if d_dur > 0 and n_dur > 0:
        diff = abs(d_dur - n_dur)
        if diff <= 2: score_dur = 1.0
        elif diff <= 10: score_dur = 1.0 - (diff / 10.0)
        else: score_dur = 0.0
    else:
        score_dur = 0.5 

    return (score_artist * 0.35) + (score_title * 0.40) + (score_album * 0.15) + (score_dur * 0.10)

def init_merge_db():
    with sqlite3.connect(MERGE_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cached_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artists TEXT,
                title TEXT,
                album TEXT,
                duration REAL,
                love INTEGER,
                rating_10 INTEGER,
                status TEXT DEFAULT 'PENDING',
                UNIQUE(artists, title, album)
            )
        """)

def cache_and_merge_dopamine_tracks(db_paths: List[Path], stats: SyncStats):
    init_merge_db()
    merged: Dict[tuple, TrackData] = {}
    
    for db_path in db_paths:
        if not db_path.exists():
            print(f"Warning: Database not found at {db_path}")
            continue
            
        stats.databases_read += 1
        print(f"Aggregating database: {db_path.name}...")
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Fast count of all tracks in the database to satisfy "Tracks examined" stat
                cursor.execute("SELECT COUNT(*) FROM Track")
                stats.tracks_examined += cursor.fetchone()[0]

                cursor.execute("""
                    SELECT Artists, TrackTitle, AlbumTitle, Duration, Love, NewRating 
                    FROM Track 
                    WHERE Love = 1 OR NewRating > 0
                """)

                for row in cursor.fetchall():
                    # 1. Strip surrounding semicolons and spaces from Artists
                    clean_artists = (row["Artists"] or "").strip("; ")
                    key = (
                        clean_artists.lower(),
                        (row["TrackTitle"] or "").strip().lower(),
                        (row["AlbumTitle"] or "").strip().lower()
                    )
                    
                    love_val = bool(row["Love"])
                    rating_val = row["NewRating"] if row["NewRating"] is not None else 0
                    
                    if key not in merged:
                        merged[key] = TrackData(
                            artists=clean_artists or "Unknown",
                            title=row["TrackTitle"] or "Unknown",
                            album=row["AlbumTitle"] or "Unknown",
                            duration=row["Duration"] or 0,
                            love=love_val,
                            rating_10=rating_val
                        )
                    else:
                        merged[key].love = merged[key].love or love_val
                        merged[key].rating_10 = max(merged[key].rating_10, rating_val)
                        
        except sqlite3.Error as e:
            print(f"Failed to read {db_path}: {e}")
            
    with sqlite3.connect(MERGE_DB_PATH) as conn:
        for t in merged.values():
            conn.execute("""
                INSERT INTO cached_tracks (artists, title, album, duration, love, rating_10)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(artists, title, album) DO UPDATE SET
                    love = MAX(love, excluded.love),
                    rating_10 = MAX(rating_10, excluded.rating_10)
            """, (t.artists, t.title, t.album, t.duration, 1 if t.love else 0, t.rating_10))

def load_pending_tracks(stats: SyncStats) -> List[Dict[str, Any]]:
    with sqlite3.connect(MERGE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT love, rating_10 FROM cached_tracks")
        for row in cursor.fetchall():
            if row["love"]: stats.loved_tracks += 1
            if row["rating_10"] > 0: stats.rated_tracks += 1
            
        cursor.execute("SELECT id, artists, title, album, duration, love, rating_10 FROM cached_tracks WHERE status = 'PENDING'")
        return [dict(row) for row in cursor.fetchall()]

def update_track_status(track_id: int, status: str):
    with sqlite3.connect(MERGE_DB_PATH) as conn:
        conn.execute("UPDATE cached_tracks SET status = ? WHERE id = ?", (status, track_id))

def apply_navidrome_updates(client: NavidromeClient, d_track: Dict[str, Any], n_track: Dict[str, Any], stats: SyncStats):
    track_id = n_track["id"]
    is_starred = n_track.get("starred") is not None
    track_display = f"\"{n_track.get('artist', 'Unknown')} - {n_track.get('title', 'Unknown')}\""
    
    if d_track["love"]:
        if not is_starred:
            try:
                client.set_love(track_id)
                stats.loved_updated += 1
                print(f"     -> [ID: {track_id}] {track_display} Loved successfully!")
            except Exception as e:
                stats.errors += 1
                print(f"     -> [ID: {track_id}] {track_display} Failed to love: {e}")
        else:
            stats.already_starred += 1
            print(f"     -> [ID: {track_id}] {track_display} Already starred in Navidrome.")
            
    if d_track["rating_10"] > 0:
        nd_rating = n_track.get("userRating")
        if not nd_rating:
            rating_5 = math.ceil(d_track["rating_10"] / 2)
            try:
                client.set_rating(track_id, rating_5)
                stats.ratings_updated += 1
                print(f"     -> [ID: {track_id}] {track_display} Rated {rating_5} stars (converted from {d_track['rating_10']}/10)")
            except Exception as e:
                stats.errors += 1
                print(f"     -> [ID: {track_id}] {track_display} Failed to rate: {e}")
        else:
            stats.already_rated += 1
            print(f"     -> [ID: {track_id}] {track_display} Already rated ({nd_rating} stars) in Navidrome.")

def main():
    stats = SyncStats()
    cache_and_merge_dopamine_tracks(DOPAMINE_DB_PATHS, stats)
    tracks = load_pending_tracks(stats)
    
    print(f"\nSuccessfully collected {len(tracks)} unique pending tracks requiring sync.\n")
    
    if not tracks:
        print("No pending tracks found to sync. Exiting.")
        return

    client = NavidromeClient(NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS)
    
    try:
        for d_track in tracks:
            # Reconstruct TrackData structure for similarity analytics
            d_track_obj = TrackData(
                artists=d_track["artists"], title=d_track["title"], album=d_track["album"],
                duration=d_track["duration"], love=bool(d_track["love"]), rating_10=d_track["rating_10"]
            )
            
            candidates = client.search_tracks(d_track["artists"], d_track["title"])

            if not candidates:
                print(f"Skipping: '{d_track['title']}' by {d_track['artists']} (Not found in Navidrome)")
                update_track_status(d_track["id"], "SKIPPED")
                stats.not_found += 1
                continue

            for c in candidates:
                c['_confidence'] = calculate_confidence(d_track_obj, c)
                
            candidates.sort(key=lambda x: x['_confidence'], reverse=True)

            # Silently skip if there is exactly 1 candidate and it already matches perfectly
            if len(candidates) == 1:
                best = candidates[0]
                # Check if there are any differences that actually require updates
                is_loved_diff = d_track["love"] and (best.get("starred") is None)
                is_rating_diff = d_track["rating_10"] > 0 and (best.get("userRating") is None)
                
                if not is_loved_diff and not is_rating_diff:
                    # Silently mark as processed since no changes are needed
                    update_track_status(d_track["id"], "PROCESSED")
                    continue

            # Interactive loop for multiple candidates OR single candidates requiring actual updates
            if len(candidates) >= 1:
                # Print Dopamine Track Header
                print("\n▶ DOPAMINE SOURCE TRACK")
                print("-" * 135)
                d_love_str = "Yes" if d_track["love"] else "No"
                d_rating_str = f"{d_track['rating_10']}/10" if d_track["rating_10"] > 0 else "None"
                print(f"{'':<10} | {'Artist':<22} | {'Title':<25} | {'Album':<44} | {'Dur ':<4} | {'Love':<4} | {'Rating'}")
                print(f"           | {d_track['artists']:<22.22} | {d_track['title']:<25.25} | {d_track['album']:<44.44} | {normalize_duration(d_track['duration']):<4} | {d_love_str:<4.4} | {d_rating_str}")
                
                # Print Candidates Table
                print("\n▶ NAVIDROME CANDIDATES")
                print("-" * 135)
                print("Idx | Conf | Artist                 | Title                     | Album                                        | Dur  | Love | Rating")
                for i, c_track in enumerate(candidates):
                    c_conf = c_track.get('_confidence', 0.0)
                    c_artist = c_track.get('artist', 'Unknown')
                    c_title = c_track.get('title', 'Unknown')
                    c_album = c_track.get('album', 'Unknown')
                    c_dur = c_track.get('duration', 0)
                    c_love = "Yes" if c_track.get('starred') is not None else "No"
                    c_rating = c_track.get('userRating', "None")
                    idx_str = f"{i + 1}"
                    print(f"{idx_str:<3} | {c_conf:.2f} | {c_artist:<22.22} | {c_title:<25.25} | {c_album:<44.44} | {c_dur:<4} | {c_love:<4} | {c_rating}")
                
                skipped = False
                while True:                
                    resp = input("\nSelect match index(es) separated by commas, or 'n' to skip: ").strip().lower()
                    if resp == 'n':
                        print("⏭ Skipped.")
                        update_track_status(d_track["id"], "SKIPPED")
                        skipped = True
                        break
                        
                    try:
                        indices = [int(x.strip()) - 1 for x in resp.split(',')]
                        
                        if all(0 <= idx < len(candidates) for idx in indices):
                            print("Applying updates...")
                            stats.matched += 1
                            for idx in indices:
                                # Apply updates to selected candidate(s)
                                target_cand = candidates[idx]
                                apply_navidrome_updates(client, d_track, target_cand, stats)
                            update_track_status(d_track["id"], "PROCESSED")
                            break
                        else:
                            print("Error: One or more indices are out of range. Try again.")
                    except ValueError:
                        print("Error: Invalid input format. Please enter integers separated by commas, or 'N'.")
                
                if skipped:
                    continue

    except KeyboardInterrupt:
        print("\n\nSync interrupted by user. Generating partial statistics...")
    finally:
        stats.print_report()

if __name__ == "__main__":
    main()
