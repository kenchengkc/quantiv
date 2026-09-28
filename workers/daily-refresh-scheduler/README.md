# Daily refresh scheduler

This Cloudflare Worker is the primary clock for the Quantiv daily refresh. It
dispatches the existing GitHub Actions workflow at 02:00 America/New_York and
checks again at 02:10. The native GitHub `schedule` remains enabled at 02:17 Eastern as a backup,
away from the top-of-hour load window; the workflow's daily-claim job prevents a
delayed native schedule from repeating provider work.

Cloudflare Cron Triggers are UTC-only, so the Worker registers both 06:00/07:00
UTC and admits only the trigger that maps to 02:00 Eastern. The same pattern is
used for the 02:10 watchdog. The spring-forward Sunday substitutes 03:00 local
because 02:00 does not exist on that date.

## GitHub token

Create a fine-grained token scoped only to `kenchengkc/quantiv` with:

- Actions: read and write
- Metadata: read (automatic)

No repository contents permission is required for the scheduler.

Store the token only as a Worker secret. The preferred deployment path is the
manual GitHub Actions workflow `Deploy daily refresh scheduler`. Configure these
repository Actions secrets first:

- `CLOUDFLARE_API_TOKEN` — scoped to the target Cloudflare account. The first deployment must be able to create the Worker; later deployments only need access sufficient to edit that Worker and its secret.
- `CLOUDFLARE_ACCOUNT_ID` — the target Cloudflare account ID.
- `DAILY_REFRESH_SCHEDULER_GITHUB_TOKEN` — the rotated fine-grained GitHub token described above.

The deployment workflow tests the scheduler, performs a trigger-free bootstrap
deploy (needed when the Worker does not exist yet), uploads the GitHub token to
Cloudflare as the Worker `GITHUB_TOKEN` secret, and then deploys the real Cron
Triggers. The bootstrap config deliberately omits `triggers`, so an initial
Worker cannot fire before its GitHub credential exists. Local deployment remains
available when needed:

```bash
cd workers/daily-refresh-scheduler
npm install
npx wrangler secret put GITHUB_TOKEN
npm test
npx wrangler deploy
```

Do not place the token in `wrangler.toml`, source code, shell history, issues,
or pull requests. Rotate it if it is ever pasted into a chat or log.

## Behavior

- 02:00 ET: dispatch `data-refresh.yml` in `normal` mode unless a normal run
  already exists for that Eastern date.
- 02:10 ET: query the workflow runs again. If no normal run exists, dispatch
  one. If a run exists but is still queued, log the condition and do not create
  a duplicate.
- GitHub's own delayed schedule is still allowed to arrive later. The workflow
  daily-claim step chooses the earliest normal run for the Eastern date and
  skips provider work in every later normal run.
