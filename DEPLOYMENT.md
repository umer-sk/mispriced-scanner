# Deployment Guide

## Prerequisites

- Schwab developer account approved (see spec Section 3)
- `token.json` generated locally via `python backend/auth_setup.py`
- GitHub account
- Render.com account (free tier)

---

## Step 1 — Backend on Render.com

1. Go to [render.com](https://render.com) and create a free account
2. New → Web Service → connect your GitHub repo
3. Render will detect `render.yaml` automatically
4. In the Render dashboard, set environment variables manually:
   - `SCHWAB_APP_KEY` = your App Key from Schwab developer portal
   - `SCHWAB_APP_SECRET` = your App Secret
   - `ALLOWED_ORIGIN` = `https://yourusername.github.io`
5. Upload `token.json` as a Secret File:
   - Render dashboard → your service → **Secret Files**
   - Filename: `/etc/secrets/token.json`
   - Contents: paste the full contents of your local `token.json`
6. Deploy. First deploy takes 3–5 minutes.
7. Copy your Render service URL (e.g. `https://qqq-scanner-backend.onrender.com`)

**Render free tier note:** The service spins down after 15 minutes of inactivity. The scheduled scans (8AM, 9:45AM, 11AM ET) keep it alive during the morning window. First load outside those hours may take 30–60 seconds while the server wakes up.

---

## Step 2 — Frontend on GitHub Pages

1. Push your repo to GitHub
2. GitHub repo → **Settings → Secrets and variables → Actions → New secret**
   - Name: `BACKEND_URL`
   - Value: your Render URL (e.g. `https://qqq-scanner-backend.onrender.com`)
3. GitHub repo → **Settings → Pages**
   - Source: Deploy from a branch
   - Branch: `gh-pages` / `/ (root)`
   - Save
4. On next push to main that touches `frontend/`, the deploy workflow runs automatically
5. Dashboard URL: `https://yourusername.github.io/qqq-scanner`

---

## Step 3 — Local Development

**Backend:**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env    # fill in your values
python auth_setup.py    # once, to generate token.json
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
echo "VITE_BACKEND_URL=http://localhost:8000" > .env.local
npm run dev
# Opens at http://localhost:5173
```

---

## Token Refresh (Weekly)

The Schwab refresh token expires every 7 days. The dashboard shows "Token expires in X days" in the health endpoint.

When it reaches 2 days remaining:
1. Run `python backend/auth_setup.py` locally
2. Re-upload the new `token.json` to Render Secret Files (overwrite the existing one)
3. Restart the Render service

Set a weekly Sunday calendar reminder to check this.

---

## Verifying the Deployment

1. Hit `https://your-render-url.onrender.com/health` — should return `{"status": "ok", ...}`
2. Open `https://yourusername.github.io/qqq-scanner` — dashboard should load
3. On the next scheduled scan time (8AM, 9:45AM, or 11AM ET), opportunities should populate
