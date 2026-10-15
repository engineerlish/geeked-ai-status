# geeked-ai-status

A continuous, timestamped record of which AI models exist and whether the services behind them
are up, for the providers [Geeked](https://github.com/engineerlish/geeked) supports: Claude
(Anthropic), ChatGPT (OpenAI) and Grok (xAI).

Every 30 minutes a GitHub Actions workflow takes one snapshot and commits it to `main`,
**whether or not anything changed**. The history therefore has no gaps by design, and any gap
that GitHub itself causes (a delayed or dropped run) is recorded rather than hidden.

[Geeked](https://github.com/engineerlish/geeked) reads [`latest.json`](latest.json) to show a
status dot per provider and to announce new models. You can also just browse the history.

## What is in this repo

| Path | What |
| --- | --- |
| `latest.json` | The newest snapshot, overwritten every run. This is what Geeked fetches. |
| `history/YYYY/MM/DD/HHmm.json` | Every snapshot ever taken, by UTC time. Never overwritten. |
| `history/INDEX.md`, `history/index.json` | Per-day counts, every run where something changed, and missed slots. |
| `schema.json` | JSON Schema for a snapshot (version 1). Geeked mirrors it as TypeScript types. |
| `providers.json` | The provider list and where each one's data comes from. Add a provider here. |
| `collector/` | The collector (Python 3.11+, standard library only) and `tests/`. |

## Snapshot schema (version 1)

Published in full as [`schema.json`](schema.json). In short:

```jsonc
{
  "schema_version": 1,
  "checked_at": "2026-10-15T18:00:12Z",   // when it really ran
  "scheduled_for": "2026-10-15T18:00:00Z", // the 30-minute slot it was meant for
  "run": {
    "trigger": "schedule",                 // schedule | workflow_dispatch | local
    "run_id": "123456789",
    "late": false,                         // true when it started >10 min after its slot
    "late_seconds": 12,
    "missed_slots": [],                    // slots no run covered since the previous snapshot
    "missed_count": 0,
    "collector_version": "1.0.0"
  },
  "providers": {
    "anthropic": {
      "id": "anthropic", "name": "Claude", "vendor": "Anthropic", "geeked_provider": "claude",
      "models": {
        "state": "ok",                     // ok | error | unavailable
        "source": "https://api.anthropic.com/v1/models",
        "error": null,
        "items": [
          { "id": "claude-...", "display_name": "Claude ...", "created_at": "2026-09-01T00:00:00Z",
            "first_seen": "2026-10-15" }
        ]
      },
      "status": {
        "state": "ok",                     // ok | error
        "source": "https://status.claude.com/api/v2/summary.json",
        "overall": "operational",          // operational | degraded | partial_outage |
                                           // major_outage | maintenance | unknown
        "summary": "All Systems Operational",
        "incidents": [],                   // active ones: title, status, impact, url, times
        "affected_components": []          // components that are not operational
      }
    }
  },
  "changes": {                             // this snapshot against the previous one
    "any": false,
    "summary": "no changes",
    "providers": {
      "anthropic": {
        "models_added": [], "models_removed": [], "status_changed": null,
        "incidents_started": [], "incidents_resolved": []
      }
    }
  }
}
```

Rules worth knowing:

- **One provider failing never breaks the others.** A failed models call has `models.state:
  "error"` with the previous list carried over, each item marked `stale: true`, and no removals
  are reported. A failed status call is `overall: "unknown"` with the error noted. A missing API
  key is `models.state: "unavailable"`; the status is still recorded.
- **`first_seen`** is the UTC date the collector first saw a model, carried forward from the
  previous snapshot. It starts at the day collection began, not the day the vendor released it
  (`created_at` is the vendor's own date when it gives one).
- **A snapshot that fails validation is still committed**, as a failure snapshot with every
  provider `unknown` and `run.validation_errors` filled in, so the slot is never skipped.
- Commit messages are Conventional Commits with a timestamp and a one-line summary:
  `chore(status): 2026-10-15 18:30 UTC, Claude degraded, new model claude-sonnet-6`.

## Sources

| Provider | Models | Status |
| --- | --- | --- |
| Anthropic / Claude | `GET https://api.anthropic.com/v1/models` (official; header `x-api-key`, paginated) | [`status.claude.com`](https://status.claude.com) Atlassian Statuspage `api/v2/summary.json` |
| OpenAI / ChatGPT | `GET https://api.openai.com/v1/models` (official; bearer key; fine-tunes `ft:*` excluded) | [`status.openai.com`](https://status.openai.com) Statuspage-compatible `api/v2/summary.json` |
| xAI / Grok | `GET https://api.x.ai/v1/models` (official, OpenAI-compatible; bearer key) | [`status.x.ai`](https://status.x.ai) incident-history RSS `feed.xml` (**fragile**, see below) |

All model lists use the providers' official models endpoints, which cost no tokens.

**Fragile source: xAI status.** `status.x.ai` publishes no Statuspage-style JSON; the only
machine-readable source is its RSS incident history. An incident counts as live while its state
is anything but `RESOLVED` and it is under 14 days old; severity `outage` is `major_outage`,
`disruption` is `degraded`, a `maintenance` category is `maintenance`. If xAI changes the feed's
wording, status becomes `unknown` or less precise rather than wrong. The status page also sits
behind Cloudflare, which can refuse some automated clients; that shows as `unknown` with the
error noted.

## Reading the history

- **Right now**: open [`latest.json`](latest.json).
- **A day**: `history/2026/10/15/` has up to 48 files named by UTC time (`1830.json`). A second
  run inside the same minute is `HHmmss.json`; nothing is overwritten.
- **What changed and when**: [`history/INDEX.md`](history/INDEX.md) lists every run with a change,
  newest first, and every missed slot. `index.json` is the same data.
- **A model's arrival**: search the history for its id, or look at `first_seen` in `latest.json`.
- **Outages**: each snapshot's `changes.providers.<id>.status_changed`; `git log` subjects read
  naturally (`git log --oneline --grep='degraded'`).

## Setting it up (for a fork or a new provider)

1. Add repository secrets (Settings, Secrets and variables, Actions). Only the providers whose
   models you want listed need one; status works without any key:
   - `ANTHROPIC_API_KEY`: an API key from the Anthropic Console (`platform.claude.com`, API keys).
   - `OPENAI_API_KEY`: a key from the OpenAI Platform dashboard (API keys).
   - `XAI_API_KEY`: a key from the xAI Console (`console.x.ai`, API keys).
   Listing models consumes no tokens, so the keys can be restricted to read-only/model-list
   scopes where the provider offers them.
2. Adjust the commit identity in `.github/workflows/snapshot.yml` to your own noreply address
   (`<id>+<username>@users.noreply.github.com`; `gh api users/<username> -q .id` gives the id).
3. Run the workflow once with **Run workflow** (`workflow_dispatch`); the schedule takes over.

To add a provider, append an entry to `providers.json` (`models.kind` is `anthropic` or
`openai`; `status.kind` is `statuspage` or `rss`), add its key to the workflow's `env`, and add
a test.

## Scheduling notes

- GitHub documents that scheduled workflows can be **delayed** under load (especially at the top
  of the hour, which `*/30` includes) and that queued runs can be **dropped**. The collector
  records the actual run time, flags a run that started more than 10 minutes late (`run.late`),
  and lists slots that no run covered (`run.missed_slots`, and in `history/INDEX.md`). Moving the
  cron off :00 and :30 (for example `7,37 * * * *`) is documented to reduce delays; the slots
  here are the nominal :00 and :30 either way.
- GitHub **disables scheduled workflows in public repositories after 60 days with no repository
  activity**. This repository commits every 30 minutes, which is activity; if the workflow is
  ever disabled anyway (for instance after a long GitHub outage), re-enable it under the Actions
  tab and the next run records the gap as missed slots.
- Pushes made with the built-in `GITHUB_TOKEN` do not start other workflows, so the CI workflow
  does not run for snapshot commits (it ignores `history/**` and `latest.json` regardless).

## How big does it get?

A snapshot with about 160 models is roughly **37 KB** pretty-printed (about 2 KB compressed).
At 48 per day that is 17,520 snapshots a year:

| | Per year |
| --- | --- |
| Checked-out files (pretty-printed) | about 650 MB |
| Git object storage (each file zlib-compressed, before pack deltas) | about 40 MB |

The history is cheap to *store* but a full clone checks out every file, so after a year or two a
clone is heavy. Geeked only fetches `latest.json` over HTTPS, so the app is unaffected.

**Proposal (not implemented):** keep the last 30 days as individual files and fold older days
into one `history/YYYY/MM/DD.json.gz` (48 snapshots, about 100 KB compressed) with the index
pointing at it. That cuts a year to roughly 40 MB of files while keeping every snapshot
recoverable, and could run as a monthly job. An alternative is to drop unchanged `models.items`
from history snapshots (keeping them in `latest.json`), which shrinks each file to a few KB.

## Development

```bash
python -m unittest discover -s tests -t .      # tests (standard library only)
python -m collector --root /some/scratch/dir    # one real run against a scratch checkout
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
`docs:`, `test:`, `chore:`), exactly as in Geeked; the bot's snapshot commits use
`chore(status):`.

## License

[MIT](LICENSE)
