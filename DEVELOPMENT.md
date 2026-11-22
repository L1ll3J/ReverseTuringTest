# Developing AlibAi

Run these commands in PowerShell from the repository root. Python 3.12 is the currently tested interpreter. Use the source checkout for development; the existing README also describes an older distributed build.

## Set up and run

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

The game reads `OPENAI_API_KEY` from its process environment when it first needs the OpenAI client. Enter a key for the current PowerShell session without placing it in source or shell history:

```powershell
$alibaiKey = Read-Host 'OpenAI API key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $alibaiKey).Password
Remove-Variable alibaiKey
.\.venv\Scripts\python.exe main.py
```

The application does not automatically load a `.env` file. Keep credentials out of source, assets, traces, and executable bundles. A packaged application should inherit the key from its launch environment. There is no in-game key entry screen in this revision.

After closing the game, clear the session value if desired:

```powershell
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
```

## Model configuration

Set model overrides before launching the game. `ALIBAI_MODEL` supplies the fallback for speech, votes, and reply selection. A role-specific variable takes precedence over that fallback.

| Environment variable | Role | Default |
| --- | --- | --- |
| `ALIBAI_MODEL` | Shared fallback for the three dialogue roles | `gpt-6.1-terra` |
| `ALIBAI_SPEECH_MODEL` | Individual NPC reply proposals | Shared fallback |
| `ALIBAI_VOTE_MODEL` | Individual NPC ballots | Shared fallback |
| `ALIBAI_SELECTOR_MODEL` | Selection among proposed replies | Shared fallback |
| `ALIBAI_TRANSCRIPTION_MODEL` | Audio transcription backend | `gpt-transcribe` |

For example, a role override can be set with `$env:ALIBAI_SELECTOR_MODEL = 'gpt-6.2-sol'`. These strings configure requests; access to the chosen models still depends on the API account. Dialogue models must support the structured Responses requests used in `ai_runtime.py`. Request limits and reasoning effort are fields of `AIConfig`, rather than additional environment variables.

## Local progression and item testing

The Windows progression profile defaults to `%LOCALAPPDATA%\AlibAi\profile.json`. It contains unlock counters, unlocked content IDs, optional item-pool preferences, and event sequence bookkeeping. Inventory and the active run are held in memory; the profile does not resume a saved game.

Use an isolated profile for development so test play does not change your normal unlocks:

```powershell
$env:ALIBAI_PROFILE_PATH = Join-Path $env:TEMP 'AlibAi-development\profile.json'
.\.venv\Scripts\python.exe main.py
```

The profile directory is created when a write is needed. Writes use a sibling temporary file, atomic replacement, and a file lock. A sibling `.lock` file is expected. Malformed or unsupported profiles produce an error and remain unchanged; the application offers a progression retry instead of silently resetting them. Keep a backup before manually editing a profile.

The weapon selection screen shows unlock goals. Bare Hands is available initially. Banana of Friendship, Muffle, and Spare Paper form the default item pool. Guardian Angel unlocks by surviving a vote. Newly unlocked content becomes available on the next run; unlocked optional items can be excluded from the pool before starting. Default items remain in the pool.

Each room's first entry in a run offers three distinct items to choose from or skip. A selected item stays in inventory until used. One slot can be rerolled once per run when the pool contains an alternative; the initial three-item pool has no alternative. Items are used during conversation decision points, with AI work finished. Invalid uses do not consume the item. The implemented item and unlock definitions live in `items.py`, `game_rules.py`, and `content.py`.

NPC Camera equipment is separate from player unlocks. A run has either no Camera or one publicly visible NPC holder. Its night snapshot is presented the following morning, including when the holder has died. It records one bounded scene before the player's attack and does not establish an alibi for the entire night.

## Opt-in development traces

Tracing is disabled by default. Enable it explicitly before launching:

```powershell
$env:ALIBAI_DEV_MODE = '1'
$env:ALIBAI_TRACE_DIR = Join-Path $env:TEMP 'AlibAi-development\dev-runs'
.\.venv\Scripts\python.exe main.py
```

`ALIBAI_TRACE_DIR` is optional. On Windows its default is `%LOCALAPPDATA%\AlibAi\dev-runs`. Starting a game creates a separate folder named with a UTC timestamp and unique run ID. Opening the title screen alone does not start a trace.

Each run folder contains:

- `manifest.json`: package versions, source revision when available, prompt hashes, model configuration, and run settings.
- `events.jsonl`: ordered events with timestamps and correlation IDs. These include player actions, game-state snapshots, public dialogue, character memories and dispositions, hidden room assignments, inventory and item effects, AI request instructions and context, structured schemas, returned text and parsed results, token usage when supplied, selections, ballots, failures, and retry or stale-result boundaries.

These files contain private game state and the player's text. Review them before sharing. The recorder does not serialize the API client or authentication headers, and does not capture hidden model reasoning. It does not automatically upload, prune, or redact gameplay content. Recording failure displays a development notice and disables tracing while play continues.

The captured inputs support debugging and comparing later changes. There is no deterministic replay runner: model sampling, source changes, and configuration can change results. Prompt hashes identify the files used; they are not a source archive. Packaged builds can supply `ALIBAI_BUILD_REVISION` as a 40-character commit hash for source attribution. Trace output must be outside the application's bundled resource directory.

### Explicit vote shortcut

Trace mode alone keeps the normal vote threshold of 20 conversation entries. To shorten it during development, set both variables:

```powershell
$env:ALIBAI_DEV_MODE = '1'
$env:ALIBAI_DEV_VOTE_MINIMUM = '2'
```

The override is ignored outside development mode. Invalid values or values below 2 fall back to 20. The vote button still requires the conversation phase and no pending AI work. The threshold counts entries in the current conversation, rather than only messages written by the player.

To restore the normal launch configuration:

```powershell
Remove-Item Env:ALIBAI_DEV_MODE -ErrorAction SilentlyContinue
Remove-Item Env:ALIBAI_DEV_VOTE_MINIMUM -ErrorAction SilentlyContinue
```

## Run offline checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

The regression suites use fake API responses, offscreen Qt widgets, temporary profiles and traces, and fake audio input. They do not establish live model quality or physical microphone compatibility. Use a manual development run to assess dialogue, visual layout, actual device behavior, and API model availability.

Configuration and behavior references: `ai_runtime.py`, `main.py`, `dev_trace.py`, `progression.py`, `content.py`, `dictation.py`, and the corresponding modules under `tests/`.
