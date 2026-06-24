"""
BookMaker Odds Fetcher & Display Server
========================================
Pulls odds from bookmaker.com via odds-api.io (free tier: 100 req/hr).
Refreshes every 4 minutes and serves a clean HTML dashboard + JSON API.

Sport coverage: NFL, NBA, NHL, MLB, NCAAF, NCAAB, MMA/UFC,
                Premier League, La Liga, Liga MX

Usage:
    1. Sign up for a free API key at https://odds-api.io/#pricing
    2. Copy .env.example to .env and add your key
    3. pip install -r requirements.txt
    4. python odds_server.py
    5. Open http://localhost:8080
"""

import os
import time
import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY = os.getenv("ODDS_API_KEY", "")
BASE_URL = "https://api.odds-api.io/v3"
BOOKMAKER = os.getenv("BOOKMAKER", "BetOnline.ag")
_bookmaker_name = BOOKMAKER  # mutable runtime value, may be updated after discovery


def dec_to_american(dec: float) -> str:
    """Convert decimal odds to American format string (e.g. 1.71 → '-141', 2.20 → '+120')."""
    try:
        d = float(dec)
    except (TypeError, ValueError):
        return str(dec)
    if d >= 2.0:
        return f"+{round((d - 1) * 100)}"
    else:
        return str(round(-100 / (d - 1)))


def convert_odds_to_american(market: dict) -> dict:
    """Add American odds alongside decimal odds in a market's odds list."""
    converted = []
    for entry in market.get("odds", []):
        new_entry = dict(entry)
        for key in ("home", "away", "draw", "over", "under"):
            if key in new_entry and new_entry[key] is not None:
                try:
                    new_entry[f"{key}_american"] = dec_to_american(new_entry[key])
                except (TypeError, ValueError):
                    pass
        converted.append(new_entry)
    market["odds"] = converted
    return market
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "240"))  # 4 min
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

# Sports we care about: (sport_slug, league_slug, display_name)
# North American priority first, soccer secondary
TARGET_LEAGUES = [
    # --- North American (priority) ---
    ("american-football", "usa-nfl",        "NFL"),
    ("american-football", "usa-ncaaf",      "NCAA Football"),
    ("basketball",        "usa-nba",        "NBA"),
    ("basketball",        "usa-ncaa",       "NCAA Basketball"),
    ("ice-hockey",        "usa-nhl",        "NHL"),
    ("baseball",          "usa-mlb",        "MLB"),
    ("mixed-martial-arts", "mma",           "MMA / UFC"),
    # --- Soccer (secondary) ---
    ("football",          "england-premier-league", "Premier League"),
    ("football",          "spain-la-liga",  "La Liga"),
    ("football",          "mexico-liga-mx", "Liga MX"),
]

# Alternative slugs to try if the primary returns 404
LEAGUE_FALLBACKS: dict[str, list[str]] = {
    "spain-la-liga":   ["spain-laliga", "spain-primera-division"],
    "mexico-liga-mx":  ["mexico-clausura", "mexico-apertura", "mexico-liga-mx-clausura"],
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bookmaker-odds")

# ---------------------------------------------------------------------------
# In-memory store (simple dict-based cache)
# ---------------------------------------------------------------------------
store: dict = {
    "events": {},       # event_id -> event_info
    "odds": {},         # event_id -> bookmaker_odds
    "last_events_refresh": None,
    "last_odds_refresh": None,
    "errors": [],
    "bad_slugs": set(),  # league slugs that returned 404 — don't retry
}

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
http = httpx.AsyncClient(timeout=30.0)

async def api_get(path: str, params: dict | None = None) -> dict | list:
    """Call odds-api.io with API key. Handles rate limits gracefully."""
    if params is None:
        params = {}
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}"
    log.debug("GET %s", url)
    resp = await http.get(url, params=params)
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "60")
        log.warning("Rate limited (429). Waiting %ss before retry...", retry_after)
        await asyncio.sleep(int(retry_after) + 1)
        resp = await http.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

async def fetch_events_for_league(sport: str, league: str) -> list[dict]:
    """Fetch upcoming + live events for a sport/league combo in ONE call."""
    # Try without status filter first (returns both pending + live)
    try:
        data = await api_get("/events", {
            "sport": sport,
            "league": league,
            "limit": 30,
        })
        if isinstance(data, list):
            return data
    except Exception:
        pass
    # Fallback: fetch separately
    events = []
    for status in ("pending", "live"):
        try:
            data = await api_get("/events", {
                "sport": sport,
                "league": league,
                "status": status,
                "limit": 20,
            })
            if isinstance(data, list):
                events.extend(data)
        except Exception as exc:
            log.warning("Failed events %s/%s status=%s: %s", sport, league, status, exc)
    return events


async def refresh_all_events():
    """Refresh the event list for all target leagues. Called every ~10 min."""
    log.info("Refreshing events for %d leagues...", len(TARGET_LEAGUES))
    all_events: dict = {}
    errors = []

    for sport, league, label in TARGET_LEAGUES:
        slug_key = f"{sport}/{league}"
        if slug_key in store.get("bad_slugs", set()):
            log.debug("  %s: skipping (bad slug)", label)
            continue
        try:
            evts = await fetch_events_for_league(sport, league)
            for e in evts:
                eid = e["id"]
                e["_sport_slug"] = sport
                e["_league_slug"] = league
                e["_label"] = label
                all_events[eid] = e
            log.info("  %s: %d events", label, len(evts))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                log.warning("  %s: league slug not found — skipping permanently", label)
                store.setdefault("bad_slugs", set()).add(slug_key)
            else:
                msg = f"Events {label}: {exc}"
                log.error(msg)
                errors.append(msg)
        except Exception as exc:
            msg = f"Events {label}: {exc}"
            log.error(msg)
            errors.append(msg)
        # Small delay between sports to avoid rate limits
        await asyncio.sleep(0.3)

    store["events"] = all_events
    store["last_events_refresh"] = datetime.now(timezone.utc).isoformat()
    store["errors"] = (store.get("errors") or []) + errors
    # Trim error log
    if len(store["errors"]) > 50:
        store["errors"] = store["errors"][-50:]
    log.info("Total events cached: %d", len(all_events))


def _find_bookmaker_key(bookmakers: dict) -> str | None:
    """Case-insensitive lookup for the target bookmaker in the API response."""
    target = _bookmaker_name.lower()
    for key in bookmakers:
        if key.lower() == target:
            return key
    # Also try partial match (e.g. "BookMaker.eu" matches "BookMaker")
    for key in bookmakers:
        if target in key.lower():
            return key
    return None


async def discover_bookmaker_name():
    """Call the bookmakers endpoint to find the exact name used by the API."""
    try:
        data = await api_get("/bookmakers")
        if isinstance(data, list):
            for entry in data:
                name = entry.get("name", "")
                if BOOKMAKER.lower() in name.lower():
                    log.info("Discovered bookmaker name: '%s'", name)
                    return name
        log.warning("Bookmaker '%s' not found in /bookmakers list", BOOKMAKER)
    except Exception as exc:
        log.warning("Failed to discover bookmakers: %s", exc)
    return BOOKMAKER  # fallback to configured name


async def refresh_all_odds():
    """Refresh odds for all cached events, batched via multi-odds endpoint."""
    events = store.get("events", {})
    if not events:
        log.warning("No events cached — skipping odds refresh")
        return

    event_ids = list(events.keys())
    batch_size = 10  # multi-odds max
    total = 0
    errors = []

    for i in range(0, len(event_ids), batch_size):
        batch = event_ids[i:i + batch_size]
        try:
            data = await api_get("/odds/multi", {
                "eventIds": ",".join(str(eid) for eid in batch),
                "bookmakers": _bookmaker_name,
            })
            if isinstance(data, list):
                for entry in data:
                    eid = entry.get("id")
                    if eid:
                        bookmakers = entry.get("bookmakers", {})
                        bm_key = _find_bookmaker_key(bookmakers)
                        bm_odds = bookmakers.get(bm_key) if bm_key else None
                        if bm_odds:
                            # Convert each market's odds to include American format
                            bm_odds_converted = [convert_odds_to_american(m) for m in bm_odds]
                            store["odds"][eid] = {
                                "id": eid,
                                "home": entry.get("home", ""),
                                "away": entry.get("away", ""),
                                "sport": entry.get("sport", {}),
                                "league": entry.get("league", {}),
                                "date": entry.get("date", ""),
                                "status": entry.get("status", ""),
                                "scores": entry.get("scores"),
                                "bookmaker_odds": bm_odds_converted,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            }
                            total += 1
        except Exception as exc:
            msg = f"Odds batch {i}: {exc}"
            log.error(msg)
            errors.append(msg)
        # Small delay between batches to be gentle
        await asyncio.sleep(0.5)

    store["last_odds_refresh"] = datetime.now(timezone.utc).isoformat()
    store["errors"] = (store.get("errors") or []) + errors
    if len(store["errors"]) > 50:
        store["errors"] = store["errors"][-50:]
    log.info("Odds refreshed: %d events with %s odds", total, BOOKMAKER)


async def full_refresh():
    """Run a complete refresh cycle."""
    try:
        await refresh_all_events()
    except Exception as exc:
        log.error("Event refresh failed: %s", exc)

    try:
        await refresh_all_odds()
    except Exception as exc:
        log.error("Odds refresh failed: %s", exc)


# ---------------------------------------------------------------------------
# Lifespan (startup/shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup and shutdown lifecycle."""
    # Startup
    if not API_KEY or API_KEY == "your_api_key_here":
        log.error("⚠️  ODDS_API_KEY not set! Copy .env.example to .env and add your key.")
        log.error("    Get a free key at: https://odds-api.io/#pricing")
    else:
        # Discover exact bookmaker name from API
        discovered = await discover_bookmaker_name()
        if discovered:
            global _bookmaker_name
            _bookmaker_name = discovered
            log.info("Bookmaker name: '%s'", _bookmaker_name)

        # Initial fetch
        await full_refresh()

        # Schedule refreshes
        scheduler = AsyncIOScheduler()
        # Odds refresh every REFRESH_INTERVAL seconds (default 5 min = 300s)
        scheduler.add_job(refresh_all_odds, "interval", seconds=REFRESH_INTERVAL,
                          id="odds_refresh")
        # Events refresh every 20 minutes (1200s) — events don't change often
        scheduler.add_job(refresh_all_events, "interval", seconds=1200,
                          id="events_refresh")
        scheduler.start()
        log.info("Scheduler: odds every %ds, events every 1200s", REFRESH_INTERVAL)

    yield  # Server runs here

    # Shutdown
    await http.aclose()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="BookMaker Odds Dashboard", lifespan=lifespan)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/odds", response_class=JSONResponse)
async def api_odds(league: Optional[str] = Query(None, description="Filter by league label")):
    """Return cached odds, optionally filtered by league."""
    odds = list(store.get("odds", {}).values())
    if league:
        odds = [o for o in odds
                if store["events"].get(o["id"], {}).get("_label", "").lower() == league.lower()]
    return {
        "bookmaker": _bookmaker_name,
        "last_events_refresh": store.get("last_events_refresh"),
        "last_odds_refresh": store.get("last_odds_refresh"),
        "count": len(odds),
        "odds": odds,
    }


@app.get("/api/status", response_class=JSONResponse)
async def api_status():
    """Health check + stats."""
    events = store.get("events", {})
    odds = store.get("odds", {})
    by_league = {}
    for eid, e in events.items():
        label = e.get("_label", "Unknown")
        by_league[label] = by_league.get(label, 0) + 1

    return {
        "bookmaker": _bookmaker_name,
        "total_events": len(events),
        "events_with_odds": len(odds),
        "last_events_refresh": store.get("last_events_refresh"),
        "last_odds_refresh": store.get("last_odds_refresh"),
        "by_league": by_league,
        "recent_errors": store.get("errors", [])[-5:],
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Main dashboard page."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        return template_path.read_text()
    return HTMLResponse("<h1>Dashboard template not found</h1>", status_code=500)


if __name__ == "__main__":
    import uvicorn
    log.info("Starting BookMaker Odds server on %s:%d", HOST, PORT)
    uvicorn.run("odds_server:app", host=HOST, port=PORT, reload=True,
                log_level="info")
