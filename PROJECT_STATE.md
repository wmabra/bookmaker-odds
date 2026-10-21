# BookMaker Odds — Project State

> **Repo:** https://github.com/wmabra/bookmaker-odds · **Branch:** `main` · **Local path:** `~/bookmaker-odds`
> **Fresh-machine resume:** `git clone git@github.com:wmabra/bookmaker-odds.git && cd bookmaker-odds && pip install -r requirements.txt` then create `.env` from `.env.example` (the `ODDS_API_KEY` is not in the repo) and run `python3 odds_server.py`.
>
> **Last updated:** 2026-10-21

## What this is

A Python server that pulls live sports odds (NFL, NBA, MLB, NHL, NCAAF, NCAAB, MMA, EPL, La Liga, Liga MX) every 4 minutes via odds-api.io (free tier) and serves them as:

- **JSON API** (`/api/odds`) — embed in any frontend
- **HTML dashboard** (`/`) — ready-to-use odds display

American odds format. BetOnline.ag as data source (identical lines to BookMaker).

---

## Quick start (5 minutes)

```bash
cd bookmaker-odds

# 1. Install Python deps
pip install -r requirements.txt

# 2. Add the API key (already in .env)
#    If missing: echo 'ODDS_API_KEY=7b98640d...' > .env

# 3. Run
python3 odds_server.py
# → http://localhost:8080
```

The server will fetch events on startup (~15 seconds), then auto-refresh odds every 4 minutes. Open the dashboard to verify odds are showing.

---

## Production deployment

### Option A: Systemd service (Linux VPS)

```ini
# /etc/systemd/system/bookmaker-odds.service
[Unit]
Description=BookMaker Odds Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/bookmaker-odds
ExecStart=/usr/bin/python3 odds_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now bookmaker-odds
```

### Option B: Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python3", "odds_server.py"]
```

```bash
docker build -t bookmaker-odds .
docker run -d -p 8080:8080 --restart=always bookmaker-odds
```

### Option C: Cloud (Railway / Render / Fly.io)

Any platform that runs Python works. Just point it at `odds_server.py` on port 8080. No database needed — everything is in-memory.

---

## Integrate with your website

### Method 1: Iframe (easiest)

```html
<iframe
  src="http://your-server:8080"
  style="width:100%; height:800px; border:none;"
  title="Live Odds"
></iframe>
```

### Method 2: JavaScript fetch (recommended)

Fetch the JSON API and render with your own design:

```javascript
async function loadOdds() {
  const res = await fetch('http://your-server:8080/api/odds');
  const data = await res.json();

  // data.odds = [
  //   {
  //     id: 63302397,
  //     home: "Toronto",
  //     away: "Houston",
  //     status: "pending" | "live",
  //     date: "2026-06-24T...",
  //     scores: { home: 3, away: 1 },
  //     sport: { name: "Baseball", slug: "baseball" },
  //     league: { name: "USA - MLB", slug: "usa-mlb" },
  //     bookmaker_odds: [
  //       {
  //         name: "ML",
  //         odds: [{
  //           home: "1.75",
  //           home_american: "-133",
  //           away: "2.22",
  //           away_american: "+122"
  //         }]
  //       },
  //       { name: "Spread", odds: [...] },
  //       { name: "Totals", odds: [...] }
  //     ]
  //   }
  // ]

  // Render however you want
  for (const event of data.odds) {
    console.log(`${event.home} vs ${event.away}`);
    const ml = event.bookmaker_odds.find(m => m.name === 'ML');
    if (ml) {
      console.log(`  Home: ${ml.odds[0].home_american}`);
      console.log(`  Away: ${ml.odds[0].away_american}`);
    }
  }
}

// Poll every 60 seconds (server refreshes every 4 min)
setInterval(loadOdds, 60000);
loadOdds();
```

### Method 3: Filter by league

```javascript
// Get only NFL odds
fetch('http://your-server:8080/api/odds?league=NFL')

// Get only MLB odds
fetch('http://your-server:8080/api/odds?league=MLB')
```

---

## API reference

| Endpoint | Returns |
|----------|---------|
| `GET /` | HTML dashboard |
| `GET /api/odds` | All cached odds (JSON) |
| `GET /api/odds?league=NFL` | Filtered by league name |
| `GET /api/status` | Health check, event counts, errors |

### Status response

```json
{
  "bookmaker": "BetOnline.ag",
  "total_events": 90,
  "events_with_odds": 45,
  "last_events_refresh": "2026-06-24T22:51:39Z",
  "last_odds_refresh": "2026-06-24T22:51:46Z",
  "by_league": { "NFL": 30, "MLB": 30, "Premier League": 30 },
  "recent_errors": []
}
```

---

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `ODDS_API_KEY` | (required) | Free API key from odds-api.io |
| `BOOKMAKER` | `BetOnline.ag` | Bookmaker to pull odds from |
| `REFRESH_INTERVAL` | `240` | Seconds between odds refreshes (240 = 4 min) |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8080` | Server port |

---

## Rate limit

Free tier: **100 requests/hour**. At default settings (~65-80 req/hr typical, 95 worst case). If you hit the limit, increase `REFRESH_INTERVAL` (e.g., `300` = 5 min).

---

## Notes

- **Soccer is in offseason** due to World Cup — EPL/La Liga/Liga MX return 0 odds. This is expected and handled gracefully.
- BookMaker.eu is listed in the API but currently returns no odds data. BetOnline.ag uses near-identical lines.
- The server auto-detects the correct bookmaker name from the API on startup.
- If a league slug breaks (404), it's permanently skipped until server restart.
