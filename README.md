# Garmin vs Strava gear check

Finds runs where the shoe assigned on Garmin Connect doesn't match the one on
Strava — missing on one side, or a different shoe entirely.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 1. Strava — pick a route

**Since 1 June 2026 the Strava API requires a paid Strava subscription**
(Standard Tier). Data export stayed free, so there are two options:

| | Bulk export CSV | API |
|---|---|---|
| Cost | free | needs a Strava subscription |
| Freshness | snapshot; re-request per run | live |
| Setup | request an archive, wait a few hours | OAuth, one-time |

#### Route A — bulk export (free, no subscription)

1. On strava.com: **Settings → My Account → Download or Delete Your Account →
   Request your archive**. The email takes a few hours. (If the link in the
   email won't resolve, a DNS-level ad blocker — ProtonVPN NetShield, Pi-hole,
   etc. — is blocking Strava's click-tracker; the same download is available on
   the settings page.)
2. Point the tool at the zip. It unpacks just the CSVs into `data/strava_export/`:

   ```bash
   python compare_gear.py --strava-csv ~/Downloads/export_12345678.zip
   ```

3. From then on, no flag needed — it reuses the last unpacked export:

   ```bash
   python compare_gear.py
   ```

   Pass `--strava-csv` again only when you have a *new* archive. The output
   always says which export it used and how recent it is.

The export has no Garmin activity id, so pairing falls back to start time
plus a distance (or duration) check rather than the exact `external_id` route.
Runs after the export's newest activity are skipped rather than reported as
missing — the export is a snapshot, and Garmin is live.

To check an export's structure before trusting it:

```bash
python compare_gear.py --inspect-strava-csv data/strava_export/activities.csv
```

#### Route B — API (needs a subscription)

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

### 2. Garmin — optional credential setup

Nothing is required: the scripts prompt for your email and password the first
time and cache session tokens afterwards.

To let *any* shell or editor session log in without a prompt, put the
credentials in `.env`:

```ini
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

They are only read when the cached tokens are missing or expired. `.env` is
gitignored and never committed, but it is a **plaintext file readable by
anything running under your account** — a convenience, not a vault.

**Where to put it.** The first of these that exists is used, for both reading
and writing:

| Order | Path |
|---|---|
| 1 | `$GEARCHECK_ENV`, if set |
| 2 | `.env` in the repository root |
| 3 | `.env` in the **main checkout**, when running from a git worktree |
| 4 | `~/.gearcheck.env` |

Rule 3 exists because tools that spawn git worktrees — Claude Code among them —
run the code from a throwaway copy of the repo. An untracked `.env` never exists
in that copy, and anything written to one is lost when the worktree is recycled,
so credentials would appear to vanish between sessions. A worktree's `.git` is a
file pointing back at the main checkout, so `env_store` follows it and one `.env`
in the repository root serves every worktree.

Rule 4 is the escape hatch if you keep several clones and want one shared file.

To keep it off disk entirely, pass it for a single shell instead. This is also
the fix when the hidden password prompt won't accept a paste (common in Git
Bash, where Python has no real console):

```bash
read -s -p "Garmin password: " GARMIN_PASSWORD && export GARMIN_PASSWORD && echo
python compare_gear.py --days 30 --garmin-check
unset GARMIN_PASSWORD    # tokens are cached; the password isn't needed again
```

`read -s` is a bash builtin, so pasting works. The value lives only in the
shell's memory — it is not written to disk, not echoed, and not saved in shell
history (it never appears on a command line). `GARMIN_EMAIL` works the same way.


`compare_gear.py` prompts for your Garmin email and password the first time (MFA
too, if your account has it). Those are used once to mint session tokens, which
are cached at `~/.garminconnect`. Later runs reuse the cache and never prompt.
Your Garmin password is never written to disk.

> Note: `garminconnect` 0.3.x replaced its `garth` dependency with a built-in
> token store. Same behaviour — cached tokens, no re-auth per run — via
> `login(tokenstore=...)`. Override the location with `GARMINTOKENS` in `.env`.

## Run

The easy ways:

- **Double-click `gearcheck.cmd`** — compares using the last-loaded export and
  opens the HTML report. **Drag a new export zip onto it** to load that zip first.
- **From bash**, add to `~/.bashrc` once:

  ```bash
  alias gearcheck='/c/projects/garmin-strava-gear/gearcheck.sh'
  ```

  then `gearcheck` from anywhere, or `gearcheck ~/Downloads/export_12345678.zip`
  to load a new one. Extra flags pass straight through (`gearcheck --days 30`).

The full command underneath:


```bash
python compare_gear.py --days 90
```

| Flag | Default | What it does |
|---|---|---|
| *(none)* | — | With `--strava-csv`: the whole export. Otherwise 90 days |
| `--all` | — | Whole export, or everything since 2000 via the API |
| `--days N` | — | Look back N days |
| `--start` / `--end` | — | Explicit `YYYY-MM-DD` window |
| `--tolerance-seconds` | 180 | Start-time slack when matching by time |
| `--fuzzy-threshold` | 0.85 | Name similarity above which two labels are the same shoe |
| `--strava-csv ZIP` | last unpacked | The Strava export zip (or its folder / `activities.csv`) |
| `--inspect-strava-csv PATH` | — | Show detected columns/units for an export, then exit |
| `--gear-names` | — | List every gear name per platform and how they pair up, then exit |
| `--use-cache` | off | Re-render from the last pull in `data/` without hitting the APIs |
| `--problems-only` | off | Drop the full activity table from the markdown |
| `--open` | off | Open the HTML report in your browser when done |
| `--out-dir` | `reports/` | Where to write |

Outputs `reports/gear_comparison_<start>_to_<end>.csv`, `.md` and `.html`.

The `.html` is the easiest way to work through the results: a single
self-contained page (no server, no network) with the verdict counts as
clickable filters, the action plan as collapsible batches with "open all on
Strava/Garmin" buttons, and a searchable, sortable table of every activity.
Each problem row has a checkbox so you can tick it off as you fix it; that
state lives in your browser's localStorage, keyed by activity id, so it
survives regenerating the report.

## How activities are matched

1. **`external_id`** — Strava stores Garmin uploads with an id like
   `garmin_push_1234567890`, which embeds the Garmin activity id. Exact when
   present, but the API is the only source that carries it; the CSV export
   doesn't include it.
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
the call.

### Mapping names explicitly

Guesswork is a fallback. The reliable route is to tell the tool what maps to
what in `gear_aliases.json`:

```json
{
  "New Balance 880": ["Newbies"],
  "Saucony Peregrine 12": ["Saucony Peregrine 12 wide", "Saucony Peregrine"]
}
```

Key = the name you want in the report; list = every spelling either platform
uses. Both sides are rewritten to the key before comparing, so the report reads
consistently and the fuzzy matcher never gets involved. The platform's original
spelling is kept in the CSV as `garmin_gear_raw` / `strava_gear_raw`.

To see what needs mapping:

```bash
python compare_gear.py --days 365 --strava-csv activities.csv --gear-names
```

That lists every gear name on each platform and how often each pair of names
landed on the same run — the co-occurrence counts make the mapping obvious.

Two mappings come for free: Garmin's own nickname → make/model
(`Newbies` → `New Balance 880`, from the gear's `customMakeModel`) is applied
automatically, and anything in `gear_aliases.json` wins over it.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

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
