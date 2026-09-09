# Garmin vs Strava gear check

Finds runs where the shoe assigned on Garmin Connect doesn't match the one on
Strava — missing on one side, or a different shoe entirely.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 1. Strava — register an app and authorise

```bash
python strava_auth.py
```

It walks you through it. If you don't have a Strava API app yet, register one at
<https://www.strava.com/settings/api>:

| Field | Value |
|---|---|
| Application Name | anything, e.g. `Gear Check` |
| Category | Data Importer |
| Website | `http://localhost` |
| **Authorization Callback Domain** | **`localhost`** — exactly this, no scheme or port |

The callback domain must be `localhost` so the script's redirect to
`http://localhost:8721/callback` is accepted. Paste the Client ID and Secret when
prompted; the script opens the consent screen, catches the redirect, and writes
the tokens to `.env`.

Scopes requested: `read`, `activity:read_all` (so private runs are included) and
`profile:read_all` (so shoe names can be resolved). Access tokens are refreshed
automatically on later runs.

### 2. Garmin — no setup needed

`compare_gear.py` prompts for your Garmin email and password the first time (MFA
too, if your account has it). Those are used once to mint session tokens, which
are cached at `~/.garminconnect`. Later runs reuse the cache and never prompt.
Your Garmin password is never written to disk.

> Note: `garminconnect` 0.3.x replaced its `garth` dependency with a built-in
> token store. Same behaviour — cached tokens, no re-auth per run — via
> `login(tokenstore=...)`. Override the location with `GARMINTOKENS` in `.env`.

## Run

```bash
python compare_gear.py --days 90
```

| Flag | Default | What it does |
|---|---|---|
| `--days N` | 90 | Look back N days |
| `--start` / `--end` | — | Explicit `YYYY-MM-DD` window |
| `--tolerance-seconds` | 180 | Start-time slack when matching by time |
| `--fuzzy-threshold` | 0.85 | Name similarity above which two labels are the same shoe |
| `--use-cache` | off | Re-render from the last pull in `data/` without hitting the APIs |
| `--problems-only` | off | Drop the full activity table from the markdown |
| `--out-dir` | `reports/` | Where to write |

Outputs `reports/gear_comparison_<start>_to_<end>.csv` and `.md`.

## How activities are matched

1. **`external_id`** — Strava stores Garmin uploads with an id like
   `garmin_push_1234567890`, which embeds the Garmin activity id. Exact when present.
2. **Start time** — nearest Strava run within `--tolerance-seconds`, rejected if
   the distances disagree by more than 5%.

Anything left over is reported as `No Strava activity` / `No Garmin activity`
rather than silently dropped.

## How gear names are compared

Garmin stores the full make and model (`Nike Air Zoom Pegasus 41`); Strava shows
whatever nickname you typed (`Pegasus 41`). Comparing the raw strings
over-reports mismatches, so:

- **Version numbers are compared exactly first.** `Pegasus 40` vs `Pegasus 41`
  are different shoes even though they're 93% similar as strings — that's
  precisely the mismatch worth catching.
- If one name's words are a subset of the other's, it's the same shoe.
- Otherwise, string similarity (including a windowed comparison, so a brand
  prefix on one side doesn't sink the score) decides, at `--fuzzy-threshold`.

Anything resolved this way is reported under **Name variants** so you can check
the call. To settle a case permanently, copy `gear_aliases.example.json` to
`gear_aliases.json` and declare the equivalence.

## Verdicts

| Verdict | Meaning |
|---|---|
| Match | Same gear on both |
| Match (name differs) | Same shoe, different spelling — listed for review |
| Different shoe | Genuinely different gear assigned |
| Missing on Strava | Garmin has a shoe, Strava has none |
| Missing on Garmin | Strava has a shoe, Garmin has none |
| Missing on both | No shoe assigned anywhere |
| No Strava / Garmin activity | The run didn't pair up at all |

## Credentials

`.env`, `data/`, `reports/` and the token cache are gitignored. Nothing secret is
printed — `strava_auth.py` suppresses the HTTP log line that would otherwise
echo the OAuth code, and secrets are only ever shown masked. `data/` and
`reports/` hold your activity history, which is why they're ignored too.
