# Host the AGC RCA app online for FREE (GitHub + Render)

> Works today, no credit card. Read **"Free-tier rules"** below first —
> free hosting has hard limits (sleeping, wiped storage, slow CPU).

## Free-tier rules (important)
1. **Sleep:** the service sleeps after 15 min without visitors. The first
   visit afterwards takes ~1 min to wake up. This is normal.
2. **Wiped storage:** the free tier has NO persistent disk. The customer
   registry, contracts and uploaded photos are **erased on every restart
   or redeploy**. Always **download your 3 PDFs immediately**. The office
   PC (`start.bat`) is the real archive, not the free site.
3. **Slow CPU:** one OCR control (6 photos) takes ~2–4 min on free CPU
   (under 1 min on a PC). Watch the live `photo X/6` progress and wait.
4. **Public link:** the app has NO login screen. Anyone with the URL can
   use it. Keep the link private; test data only.

## Path A — you already deployed before (update to the latest version)
1. Download the newest `agc_rca_app.zip`, extract it.
2. In your local repo folder (GitHub Desktop), overwrite everything with
   the extracted files.
3. GitHub Desktop → write a summary → **Commit** → **Push origin**.
4. Render dashboard → your service → it rebuilds automatically (~5 min).
   If nothing happens: **Manual Deploy → Deploy latest commit**.
5. Open your URL → **Ctrl+F5** → the header chip must show the new
   version (e.g. **v5.3.5**). Done.

## Path B — first deployment from scratch (~25 min)
1. Create a free account at https://github.com (verify your email).
2. Install **GitHub Desktop** (https://desktop.github.com), sign in.
3. GitHub Desktop → **File → New repository**: name e.g. `agc-rca-test`,
   Local path anywhere → **Create** → copy ALL app files into that
   folder → summary → **Commit** → **Publish repository** (private OK).
4. Create a free account at https://render.com (**sign up with GitHub**).
5. Render → **New → Web Service** → connect the repo → settings:
   - Runtime: **Docker** — Region: **Frankfurt** — Plan: **Free**
   - Leave build/start commands empty (the Dockerfile handles it).
   - No environment variables needed → **Deploy**.
6. Wait ~5–15 min (first build installs Tesseract + OCR). Status turns
   **Live** → open `https://YOUR-NAME.onrender.com` → Ctrl+F5.

## Verify it works (5 min)
1. Header chip shows the current version; `/api/status` says
   `"tesseract": true, "rapidocr": true`.
2. Upload the **3 required photos** → LANCER LE CONTRÔLE OCR → step 2
   appears with merged fields.
3. Generate a contract → download Police, Quittance, Facture.
4. Repeat once with all **6 photos** (rectos + versos).

## Daily use
1. Open the URL (wait ~1 min if it was sleeping).
2. New file → upload client photos → run the OCR control.
3. Review/correct fields → Signature → Documents → **download the 3
   PDFs at once** (they vanish on the next restart).
4. Never treat the online registry as an archive — re-download rule.

## Updating later / stopping
- **Update:** same as Path A (overwrite → Commit → Push → auto-rebuild).
- **Pause:** Render → service → **Suspend** (free, keeps settings).
- **Delete:** Render → Settings → **Delete Web Service**.

## Troubleshooting
| Symptom | Cause → fix |
|---|---|
| Old version chip after push | Browser cache → **Ctrl+F5**. Still old → Render Events tab: if no build ran, Manual Deploy → Deploy latest commit. |
| Build fails on `libGL` / cv2 | Fixed since v5.3.1 (`libgl1` in Dockerfile) → update to latest zip. |
| `Timeout` in red, job slow | Free CPU + big photos: wait for the `photo X/6` counter; retry with 3 photos first; check Render Logs `[JOB …]` lines. |
| `Job lost (server restarted?)` | Free instance restarted mid-job (rare) → just retry. |
| Page loads but OCR button dead | Check the version chip + `/api/status`; hard-refresh. |
| Registry/contracts gone | Normal on free (ephemeral storage) → re-upload, download PDFs same session. |
| iPhone photo rejected (HEIC) | Convert to JPG first (Photos → Share → Save) or screenshot it. |
