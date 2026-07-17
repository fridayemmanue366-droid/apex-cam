# Deploying the Apex Cam cloud server

This is the server that holds the **secret keys**, the **accounts**, and the
**tamper-proof credits**. It must run on a host — never inside the app you ship.

## Why not "just put the keys in the app"

A key inside the app can always be extracted (memory, network capture, or opening
the file). Whoever extracts your **Flutterwave secret** can call Flutterwave *as
you* — refund money to themselves, drain the balance, read customer payment data.
Same for the **Decart key** (they'd burn your AI credits for free). The server
exists so that never happens.

## Cost reality

| Item | Cost | Notes |
|---|---|---|
| Render **Starter** | **$7/mo** | Needed for the persistent disk |
| Domain (optional) | ~$12/yr | Render gives a free `*.onrender.com` address |

**Do NOT use Render's free tier for real customers**: its filesystem is ephemeral,
so the database (accounts, credits, subscriptions) is wiped on every restart. You
would lose customers' paid credits.

## Option A — Fly.io (~$2–3/mo, cheapest with a free address)

Uses `fly.toml` + `Dockerfile`. Free address: `https://<app>.fly.dev`.

1. Install flyctl: `iwr https://fly.io/install.ps1 -useb | iex` (PowerShell), then
   `fly auth signup` (or `fly auth login`).
2. From the `server/` folder:
   ```
   fly launch --no-deploy            # keep the existing fly.toml when asked
   fly volumes create apexcam_data --size 1 --region fra
   ```
3. Set the secrets — **these only ever live on Fly, never in git, never in the app**:
   ```
   fly secrets set APEXCAM_SECRET=<long-random-string> \
                   APEXCAM_DECART_KEY=<your Decart key> \
                   FLW_SECRET=<your LIVE Flutterwave secret> \
                   APEXCAM_PUBLIC_URL=https://<app>.fly.dev
   ```
4. `fly deploy` → check `https://<app>.fly.dev/health` returns `{"status":"ok"}`.
5. Point the app at it (see "After deploying" below).

`auto_stop_machines = false` keeps one machine warm so customers never hit a cold
start. The volume keeps the database across deploys.

## Option B — Render ($7/mo, simplest)

1. **Push this repo to GitHub** (private is fine).
2. Create a free account at <https://render.com>.
3. **New → Blueprint** → connect the repo → Render reads `server/render.yaml`.
4. Render will ask for the secrets marked `sync: false`. Paste them **in the Render
   dashboard only**:
   - `APEXCAM_DECART_KEY` — your Decart API key
   - `FLW_SECRET` — your **LIVE** Flutterwave secret key
   - `APEXCAM_PUBLIC_URL` — the URL Render gives you, e.g.
     `https://apexcam-api.onrender.com`
5. Deploy. Check `https://<your-url>/health` returns `{"status":"ok"}`.
6. **Point the app at it**: build the desktop app with
   `VITE_APEXCAM_SERVER=https://<your-url>`, then rebuild the installer
   (`build-bundle.ps1` + `build-installer.ps1`).
7. In the Flutterwave dashboard, set the **webhook** to
   `https://<your-url>/pay/webhook`.

## Never commit

`.decart.local`, `.flutterwave.local`, `*.local`, `.env`, `apexcam*.db` — these are
gitignored and the bundle build strips them. Keep it that way.

## After deploying

- Payments become **real** (live Flutterwave keys, server-side).
- **Apex Pro / Lucy** works for customers (Decart key stays on the server).
- Credits and the ₦20,000/month subscription are enforced server-side and can't be
  edited on the customer's PC.
