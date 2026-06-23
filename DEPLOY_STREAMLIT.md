# Deploying the Crypto Screener to Streamlit Community Cloud

Free hosting, built for Streamlit apps. The app stays exactly as-is — no rewrite.

## What's already set up
- `crypto_screener_app.py` — the Streamlit app (entry point)
- `crypto_screener.py` — the screening logic it imports
- `requirements.txt` — includes `streamlit`, `requests`, `pandas`, `rich`

## One-time deploy steps

### 1. Push this repo to GitHub
Streamlit Cloud deploys from a GitHub repo. If you haven't pushed yet:

```bash
cd ~/investing
git add crypto_screener.py crypto_screener_app.py requirements.txt CRYPTO_SCREENER_README.md
git commit -m "Add crypto screener + streamlit app"

# create a repo on github.com first, then:
git remote add origin https://github.com/<your-username>/investing.git
git push -u origin main
```

### 2. Deploy on Streamlit Cloud
1. Go to **https://share.streamlit.io** and sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `<your-username>/investing`
   - **Branch:** `main`
   - **Main file path:** `crypto_screener_app.py`
4. Click **Deploy**.

That's it. Streamlit installs `requirements.txt`, boots the app, and gives you a
public URL like `https://<your-app>.streamlit.app`.

## After deploy
- Every `git push` to `main` auto-redeploys the app.
- The **🔄 Refresh data** button in the sidebar clears the 5-minute cache.
- No API keys or secrets needed — both CoinGecko and Coinbase endpoints are public.

## Notes
- The free tier sleeps the app after inactivity; it wakes on the next visit
  (first load takes a few extra seconds).
- A full scan takes ~15s because of the deliberate 1.5s pauses between API calls
  (to respect the free rate limits). Results are cached for 5 minutes.
