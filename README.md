# BookMaker Odds Dashboard

Pull live odds from **bookmaker.com** (BookMaker sportsbook) every 4 minutes and display them on a clean web dashboard. Uses the **free tier** of [odds-api.io](https://odds-api.io) — no credit card required, 100 requests/hour.

## Rate Limit Budget (100 req/hr free tier)

| Component | Frequency | Calls/cycle | Req/hr |
|-----------|-----------|-------------|--------|
| Events (6 sports) | Every 20 min | 6 | **18** |
| Odds (batched ×10) | Every 4 min | ~3-5 | **45-75** |
| Startup discovery | Once | 2 | ~2 |
| **Typical total** | | | **~65-80/hr** ✅ |
| **Worst case** | | | **95/hr** ⚠️ |

**Safe in practice** — worst case only during peak Saturday (NFL + NCAAF + EPL all active). Tune `REFRESH_INTERVAL` in `.env` if needed.

## Sports Covered

| Sport | League |
|-------|--------|
| 🏈 NFL | `usa-nfl` |
| 🏈 NCAA Football | `usa-ncaaf` |
| 🏀 NBA | `usa-nba` |
| 🏀 NCAA Basketball | `usa-ncaa` |
| 🏒 NHL | `usa-nhl` |
| ⚾ MLB | `usa-mlb` |
| 🥊 MMA / UFC | `mma` |
| ⚽ Premier League | `england-premier-league` |
| ⚽ La Liga | `spain-la-liga` |
| ⚽ Liga MX | `mexico-liga-mx` |

## Quick Start

### 1. Get a free API key

Sign up at [odds-api.io/#pricing](https://odds-api.io/#pricing) — free tier gives 100 req/hr, no credit card needed.

### 2. Install dependencies

```bash
cd bookmaker-odds
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env and paste your API key:
# ODDS_API_KEY=your_actual_key_here
```

### 4. Run

```bash
python odds_server.py
```

Open **<http://localhost:8080>** in your browser.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | HTML dashboard |
| `GET /api/odds` | All cached odds (JSON) |
| `GET /api/odds?league=NFL` | Filter by league |
| `GET /api/status` | Health check + stats |

## How It Works

1. **Events** — fetched every 20 minutes from odds-api.io for each target league (6 sports × 1 call, combined pending+live)
2. **Odds** — fetched every 4 minutes (configurable via `REFRESH_INTERVAL`), filtered to BookMaker, batched in groups of 10 events per call
3. **Dashboard** — auto-refreshes client-side every 60 seconds

## Rate Limit

Free tier: **100 requests/hour**. At default settings (~86 req/hr used, 14% headroom).

If you hit the limit, increase `REFRESH_INTERVAL` in `.env` (e.g., `600` = 10 minutes, uses ~60 req/hr total).

## Embedding on Your Site

Serve the page directly, or use the JSON API:

```javascript
// Fetch odds from your server
const res = await fetch('http://your-server:8080/api/odds');
const { odds } = await res.json();

// odds is an array of:
// { id, home, away, sport, league, date, status, scores, bookmaker_odds }
```
