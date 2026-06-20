# Quantiv → Oracle Cloud Always Free migration runbook

Moves the two Railway services (`quantiv` FastAPI + `quantiv-quote-worker`) onto
one always-free VM, for **$0/mo** instead of Railway's $5/mo floor.

## What changes vs. what doesn't

**Doesn't change (zero edits needed):**
- Vercel frontend — still calls `https://api.usequantiv.com` with the same
  `BACKEND_SHARED_SECRET`. We keep the domain; only the DNS A record moves.
- Neon Postgres, Upstash Redis, Cloudflare R2 — all external, same creds.
- The app code, the Docker image, every env var, the quote-lease protocol.

**Changes:**
- Host: Railway → your VM.
- TLS: Railway gave it for free; Caddy now issues/renews a Let's Encrypt cert.
- `/data`: was a Railway volume; now reconstructed from R2 on boot (it's just a
  model + forecasts cache — verified: R2 holds today's forecasts + all models).

Total downtime at cutover: **none** if you smoke-test before flipping DNS
(the lease lets both workers run side-by-side during overlap).

---

## 0. Prereqs (already done on your laptop)
- `deploy/oracle/.env` was generated from Railway (26 vars, gitignored, chmod 600).
- Artifacts in `deploy/oracle/`: `docker-compose.yml`, `Caddyfile`, `pull-data.sh`,
  `bootstrap.sh`. Repo-root `.dockerignore` added so the VM build stays small.
- These must be **committed** so the VM's `git clone` has them (the `.env` and
  `data/` stay gitignored — you'll `scp` the `.env` separately in step 4).

---

## 1. Create the VM (Oracle Cloud console)
- Compute → Instances → **Create instance**.
- **Shape:** *Change shape* → Ampere → **VM.Standard.A1.Flex**, **1 OCPU / 6 GB**
  (Always Free allows up to 4 OCPU / 24 GB of A1 total — arm64, which is what the
  image was build-tested on). If A1 shows "out of host capacity", try another
  Availability Domain/region, or fall back to **VM.Standard.E2.1.Micro** (x86,
  1 GB — then add 2 GB swap before building, see Appendix B).
- **Image:** Canonical **Ubuntu 22.04**.
- **SSH keys:** upload your public key (or let Oracle generate one and save it).
- Networking: keep the default VCN/subnet, **assign a public IPv4**.
- Create, then note the **public IP**.

## 2. Open ports 80 + 443 (TWO layers — this is the classic Oracle gotcha)
**(a) VCN security list / NSG:** Networking → your VCN → default Security List →
Add Ingress Rules: source `0.0.0.0/0`, TCP, dest ports **80** and **443**
(22 is already open).

**(b) Host firewall:** Oracle's Ubuntu image ships iptables that drop everything
but SSH. SSH in (`ssh ubuntu@<IP>`) and run:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Install Docker + rclone on the VM
```bash
sudo apt-get update
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu        # then log out/in so `docker` works sans sudo
sudo apt-get install -y rclone git
docker compose version                # confirm the compose plugin is present
```

## 4. Get the code + secrets onto the VM
```bash
# On the VM — fresh clone is small (tracked files only, no data/ or .tmp):
git clone https://github.com/kenchengkc/quantiv.git
cd quantiv/deploy/oracle
```
```bash
# From your LAPTOP — copy the real secrets (never committed):
scp deploy/oracle/.env ubuntu@<IP>:~/quantiv/deploy/oracle/.env
```

## 5. Bring it up (build + start, BEFORE touching DNS)
```bash
# On the VM:
cd ~/quantiv/deploy/oracle
./bootstrap.sh            # pulls /data from R2, builds image, starts api+worker+caddy,
                          # installs the daily forecasts re-pull cron
docker compose logs -f api worker
```
Wait for the api log line `✅ Services initialized ... ml_ready=True` and the
worker line `quote worker started`. (Caddy will fail to get a cert until DNS
points here — that's expected; it retries automatically.)

## 6. Smoke-test BEFORE cutover (prove parity without moving traffic)
```bash
# Health (no TLS/DNS needed — hit the container directly):
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health',timeout=5).read().decode())"
```
You can also test the public path locally by faking the Host + cert check:
```bash
curl -sk --resolve api.usequantiv.com:443:127.0.0.1 https://api.usequantiv.com/health
```
Expect the same JSON your Railway `/health` returns (postgres + duckdb + ml ready).
A signed `/em/...` request is exercised by `scripts/smoke_signed.py` (see Appendix A).

## 7. DNS cutover
- At your DNS provider (Cloudflare), change `api.usequantiv.com` from the Railway
  **CNAME** to an **A record → `<VM public IP>`**.
- **If the record is proxied (orange cloud): set it to "DNS only" (grey cloud)**
  so Caddy can complete the ACME challenge. (Or keep proxied and switch Caddy to
  the DNS-01 challenge + a Cloudflare Origin cert — more setup; grey-cloud is simplest.)
- Within ~1–2 min Caddy issues the cert. Watch: `docker compose logs -f caddy`
  for `certificate obtained successfully`.

## 8. Verify end-to-end
```bash
curl -s https://api.usequantiv.com/health           # real domain, real cert
```
- Load the live site (usequantiv.com) — symbol pages, expected-move, history,
  ML forecasts should all render exactly as before (Vercel → VM backend).
- **During market hours** (09:25–16:45 ET), confirm the VM worker owns the lease:
  ```bash
  docker compose exec api python -c "import redis,os;r=redis.from_url(os.environ['REDIS_URL']);print(r.get('quote:worker:status'))"
  ```
  The payload's `batch_writes` should be `1`. If the old Railway worker still
  holds the lease, stop it (next step) and the VM worker acquires it within
  `LEASE_TTL_S` (90 s).

## 9. Decommission Railway (after ~1 day of clean parity)
- Stop the worker first so the VM worker takes the lease cleanly:
  `railway down` / remove the `quantiv-quote-worker` service.
- Remove the `quantiv` service.
- Delete the project (or just remove the payment method so the trial can't roll
  into a charge). Keep the Railway `.env` backup until you're confident.

---

## Rollback (any time before step 9)
Flip the `api.usequantiv.com` A record back to the Railway CNAME. Railway is
still running, so traffic returns instantly. Nothing else to undo.

## Ops cheatsheet
```bash
cd ~/quantiv/deploy/oracle
docker compose logs -f api worker caddy     # tail logs
docker compose restart api                  # restart one service
git pull && docker compose up -d --build    # deploy a new version
./pull-data.sh                              # manual forecasts/models refresh
```
The daily forecasts re-pull cron (07:15 UTC) keeps the DuckDB history view
advancing; recent data already comes live from Neon.

## Appendix A — signed request smoke test
`python deploy/oracle/smoke_signed.py https://api.usequantiv.com /api/ml/info`
signs with `BACKEND_SHARED_SECRET` from `.env` and prints the status — proves the
HMAC path Vercel uses still works.

## Appendix B — 1 GB x86 micro fallback
Building lightgbm/duckdb wheels needs RAM. On E2.1.Micro add swap first:
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
