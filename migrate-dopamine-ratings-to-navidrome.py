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

def extract_and_merge_dopamine_tracks(db_paths: List[Path], stats: SyncStats) -> List[TrackData]:
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
            
    # Calculate merged metrics
    unique_tracks = list(merged.values())
    for t in unique_tracks:
        if t.love: stats.loved_tracks += 1
        if t.rating_10 > 0: stats.rated_tracks += 1
        
    return unique_tracks

def apply_navidrome_updates(client: NavidromeClient, d_track: TrackData, n_track: Dict[str, Any], stats: SyncStats):
    track_id = n_track["id"]
    is_starred = n_track.get("starred") is not None
    
    if d_track.love:
        if not is_starred:
            try:
                client.set_love(track_id)
                stats.loved_updated += 1
                print(f"     -> [ID: {track_id}] Loved successfully!")
            except Exception as e:
                stats.errors += 1
                print(f"     -> [ID: {track_id}] Failed to love: {e}")
        else:
            stats.already_starred += 1
            print(f"     -> [ID: {track_id}] Already starred in Navidrome.")
            
    if d_track.rating_10 > 0:
        nd_rating = n_track.get("userRating")
        if not nd_rating:
            rating_5 = math.ceil(d_track.rating_10 / 2)
            try:
                client.set_rating(track_id, rating_5)
                stats.ratings_updated += 1
                print(f"     -> [ID: {track_id}] Rated {rating_5} stars (converted from {d_track.rating_10}/10)")
            except Exception as e:
                stats.errors += 1
                print(f"     -> [ID: {track_id}] Failed to rate: {e}")
        else:
            stats.already_rated += 1
            print(f"     -> [ID: {track_id}] Already rated ({nd_rating} stars) in Navidrome.")


# --- MAIN INTERACTIVE LOOP ---

def main():
    stats = SyncStats()
    tracks = extract_and_merge_dopamine_tracks(DOPAMINE_DB_PATHS, stats)
    
    print(f"\nSuccessfully collected {len(tracks)} unique tracks requiring sync.\n")
    
    if not tracks:
        print("No tracks found to sync. Exiting.")
        return

    client = NavidromeClient(NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS)
    
    try:
        for d_track in tracks:
            candidates = client.search_tracks(d_track.artists, d_track.title)
            
            if not candidates:
                stats.not_found += 1
                print(f"Skipping: '{d_track.title}' by {d_track.artists} (Not found in Navidrome)")
                continue
                
            for c in candidates:
                c['_confidence'] = calculate_confidence(d_track, c)
                
            candidates.sort(key=lambda x: x['_confidence'], reverse=True)

            # Widened formatting slightly to accommodate the two new columns
            print("\n" + "=" * 115)
            print("▶ DOPAMINE SOURCE TRACK")
            print("-" * 115)
            print(f"{'Artist':<22} | {'Title':<25} | {'Album':<22} | {'Dur(s)':<6} | {'Love':<4} | {'Rating'}")
            print(f"{d_track.artists[:22]:<22} | {d_track.title[:25]:<25} | {d_track.album[:22]:<22} | {normalize_duration(d_track.duration):<6} | {d_track.love!s:<4} | {d_track.rating_10}/10")
            
            print("\n▶ NAVIDROME CANDIDATES")
            print("-" * 115)
            print(f"{'Idx':<3} | {'Conf':<4} | {'Artist':<22} | {'Title':<25} | {'Album':<22} | {'Dur(s)':<6} | {'Love':<4} | {'Rating'}")
            for i, c in enumerate(candidates):
                c_artist = c.get('artist', '')[:22]
                c_title = c.get('title', '')[:25]
                c_album = c.get('album', '')[:22]
                c_dur = c.get('duration', 0)
                conf = c.get('_confidence', 0.0)
                
                # 2. Extract and format candidate Love/Rating metrics
                c_love = "Yes" if c.get('starred') else "No"
                c_rating = c.get('userRating', 'None')
                
                idx_str = f"[{i}]"
                print(f"{idx_str:<3} | {conf:.2f} | {c_artist:<22} | {c_title:<25} | {c_album:<22} | {c_dur:<6} | {c_love:<4} | {c_rating}")
            while True:
                resp = input("\nSelect match index(es) separated by commas, or 'N' to skip: ").strip().lower()
                
                if resp == 'n':
                    print("⏭ Skipped.")
                    break
                    
                try:
                    indices = [int(x.strip()) for x in resp.split(',')]
                    
                    if all(0 <= idx < len(candidates) for idx in indices):
                        print("Applying updates...")
                        stats.matched += 1
                        for idx in indices:
                            apply_navidrome_updates(client, d_track, candidates[idx], stats)
                        break
                    else:
                        print("Error: One or more indices are out of range. Try again.")
                except ValueError:
                    print("Error: Invalid input format. Please enter integers separated by commas, or 'N'.")
                    
    except KeyboardInterrupt:
        print("\n\nSync interrupted by user. Generating partial statistics...")
    finally:
        stats.print_report()

if __name__ == "__main__":
    main()