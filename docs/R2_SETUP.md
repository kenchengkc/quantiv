# Cloudflare R2 + GitHub Actions setup

One-time setup to enable `.github/workflows/daily-refresh.yml`. Roughly 10 minutes total.

## 1. Create the R2 bucket (5 min, Cloudflare dashboard)

1. Sign up / sign in at https://dash.cloudflare.com/
2. Left sidebar → **R2 Object Storage** → **Create bucket**
3. Name: `quantiv-data`
4. Location hint: `Automatic` (or pick the region closest to you)
5. Click **Create bucket**

## 2. Create an API token with R2 access (3 min)

1. R2 dashboard → **Manage R2 API Tokens** (right side)
2. Click **Create API token**
3. Permissions: **Admin Read & Write**
4. Specify bucket: **Apply to specific buckets** → select `quantiv-data`
5. TTL: leave as-is (no expiry)
6. Click **Create API Token**
7. **Copy the three values** shown on the next screen — they are only shown once:
   - `Access Key ID`
   - `Secret Access Key`
   - `Account ID` (also visible at the top right of the R2 dashboard)

## 3. Add secrets to GitHub

Go to https://github.com/kenchengkc/quantiv/settings/secrets/actions and add:

| Secret name              | Value                                   |
| ------------------------ | --------------------------------------- |
| `R2_ACCOUNT_ID`          | your Cloudflare account ID              |
| `R2_ACCESS_KEY_ID`       | from step 2                             |
| `R2_SECRET_ACCESS_KEY`   | from step 2                             |
| `R2_BUCKET`              | `quantiv-data`                          |

The workflow also needs the previously-set `FINNHUB_API_KEY` / `UPSTASH_*` / `DATABASE_URL` used by the hosted price refresher.

## 4. One-time local bootstrap: upload your data/ to R2

Install rclone locally:

```bash
# macOS
brew install rclone
# or
curl https://rclone.org/install.sh | sudo bash
```

Configure the `r2` remote (one time):

```bash
rclone config

# answers:
#   n) New remote
#   name> r2
#   Storage> s3
#   provider> Cloudflare
#   env_auth> 1 (enter credentials)
#   access_key_id> <your R2 Access Key ID>
#   secret_access_key> <your R2 Secret Access Key>
#   region> auto
#   endpoint> https://<your account id>.r2.cloudflarestorage.com
#   (press enter to skip the rest)
#   y/e/d> y  (confirm)
#   q  (quit)
```

Verify:

```bash
rclone lsd r2:quantiv-data    # should list nothing initially
```

Upload (one time — takes 10–30 min depending on upload speed; ~2 GB):

```bash
R2_BUCKET=quantiv-data bash scripts/maintenance/r2_bootstrap.sh
```

Verify size after:

```bash
rclone size r2:quantiv-data
# Total objects: ~600
# Total size:    ~2 GiB
```

## 5. Enable the workflow

```bash
git add .github/workflows/daily-refresh.yml scripts/r2_pull.sh scripts/r2_push.sh scripts/maintenance/r2_bootstrap.sh docs/R2_SETUP.md
git commit -m "Add daily R2-backed refresh workflow"
git push
```

Verify it runs:

```bash
# Trigger manually once to confirm everything works
gh workflow run daily-refresh.yml
gh run watch
```

From then on, it runs automatically at **11:00 UTC daily** (07:00 ET, after market close of the prior day).

## Cost expectations

- Storage: 2–3 GB used of **10 GB free**
- Class A ops (writes): ~10 k/day, free tier is 1 M/mo
- Class B ops (reads): ~2 k/day, free tier is 10 M/mo
- Egress: **unlimited & free on R2**

Expected monthly cost: **$0**.

## Troubleshooting

- **Workflow fails at "Pull data from R2"** → credentials wrong. Re-check the 4 secrets.
- **rclone sync hangs at upload** → bump `timeout-minutes: 45` in the workflow. Options data grows ~10 MB/day so a cold run after a long gap can be slow.
- **"No changes to commit"** — normal when parquet didn't change (weekends, holidays).
- **Vercel doesn't redeploy** → check that `apps/frontend/public/weekly.json` actually changed in the commit.
