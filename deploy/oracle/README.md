# Black Swan — free "max performance" deploy: Oracle A1 + Cloudflare Tunnel

The backend runs on an **Oracle Cloud Always Free Ampere A1 VM** (up to 4 ARM
cores / 24 GB RAM, always-on, free forever) and is published through a **free
Cloudflare Tunnel** — a public `https://`/`wss://` URL with valid TLS and no
inbound firewall ports. The frontend goes on **Vercel Hobby** (free).

```
 Browser ──https──> Vercel (Next.js)  ──wss──>  *.trycloudflare.com
                                                      │ (tunnel, outbound-only)
                                                      ▼
                                          Oracle A1 VM : Docker : uvicorn :8000
                                          (FastAPI + TimesFM + SQLite)
```

Why a tunnel: it dials **out** to Cloudflare, so you never open Oracle security
lists or the VM firewall (the usual Oracle pain), and you get free HTTPS/WSS
without owning a domain.

---

## Step 1 — Create the Oracle A1 VM

1. Sign up at **cloud.oracle.com** (needs a card for identity verification;
   Always Free resources are never charged). Pick your home region carefully —
   A1 capacity is region-limited.
2. **Compute → Instances → Create Instance.**
   - **Image:** Canonical **Ubuntu 22.04** (or 24.04).
   - **Shape:** *Change shape* → **Ampere** → **VM.Standard.A1.Flex** →
     **4 OCPUs, 24 GB RAM** (the whole free ARM allowance).
   - **SSH keys:** upload/generate a key pair (you'll SSH with the private key).
   - Create.
   > **"Out of host capacity"** is common on A1. Retry over time, or try a
   > different Availability Domain / a less busy region. This is the one real
   > blocker of this route.
3. Note the instance's **public IP**. SSH in:
   ```bash
   ssh -i /path/to/key ubuntu@<PUBLIC_IP>
   ```

## Step 2 — Get the code + keys onto the VM

```bash
sudo apt-get update && sudo apt-get install -y git
# private repo: enter your GitHub username + a Personal Access Token when prompted
git clone https://github.com/ExceedingExpectations150/black-swan.git
cd black-swan

cp .env.example backend/.env
nano backend/.env      # fill GEMINI_API_KEY_PRIMARY and GEMINI_API_KEY_BACKUP
                       # leave ALLOWED_ORIGINS out for now (set it in Step 4)
```
*(A read-only fine-grained PAT is enough. Generate at GitHub → Settings →
Developer settings → Personal access tokens.)*

## Step 3 — Run the deploy script

```bash
bash deploy/oracle/setup.sh
```
It installs Docker + cloudflared, builds the ARM image, starts the backend on
`localhost:8000`, brings up the tunnel as a systemd service, and prints:

```
  BACKEND IS LIVE:  https://<random>.trycloudflare.com
    NEXT_PUBLIC_API_BASE = https://<random>.trycloudflare.com
    NEXT_PUBLIC_WS_URL   = wss://<random>.trycloudflare.com/ws
```
First build downloads torch; the first `/api/start` later downloads the ~1 GB
TimesFM checkpoint (cached in the `black-swan-hf` volume thereafter).

Sanity check: `curl https://<random>.trycloudflare.com/api/economy` should
return JSON.

## Step 4 — Frontend on Vercel + close the CORS loop

1. **vercel.com** → import **ExceedingExpectations150/black-swan** →
   **Root Directory: `frontend`** → add the two `NEXT_PUBLIC_*` env vars from
   Step 3 → **Deploy**. Note the Vercel URL.
2. Back on the VM, allow that origin:
   ```bash
   nano backend/.env        # ALLOWED_ORIGINS=https://<your-app>.vercel.app
   sudo docker restart black-swan-api
   ```
3. Open the Vercel URL → boot terminal → type a Black Swan event → the
   dashboard streams live over `wss://`.

---

## Operate

```bash
sudo docker logs -f black-swan-api            # backend logs
sudo journalctl -u cloudflared-quick -f       # tunnel logs + current URL
sudo docker restart black-swan-api            # reload after editing backend/.env
sudo systemctl restart cloudflared-quick      # restart tunnel (mints a NEW url)
```

## Caveats

- **The `trycloudflare.com` URL is not permanent** — it lives only while
  `cloudflared` runs. If the tunnel service restarts, the URL changes and you
  must update Vercel's `NEXT_PUBLIC_*` env and redeploy. Keep the VM + service
  up during a demo and it stays put.
- **Permanent URL (optional upgrade):** if you own a domain on Cloudflare, swap
  the quick tunnel for a *named* tunnel:
  ```bash
  cloudflared tunnel login
  cloudflared tunnel create black-swan
  cloudflared tunnel route dns black-swan api.yourdomain.com
  # then run: cloudflared tunnel run --url http://localhost:8000 black-swan
  ```
  Point Vercel at `https://api.yourdomain.com` (stable forever).
- **SQLite is on the container's disk** and resets if the container is removed
  (`docker rm`). It reseeds on boot — fine for a demo. To persist, add
  `-v black-swan-db:/app/data` to the `docker run` and set
  `DATABASE_URL=sqlite:////app/data/blackswan.db` in `backend/.env`.
- **Gemini free-tier quota** still 429s under load; the event macro-shock keeps
  the market moving regardless.
