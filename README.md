# Dashboard Inflacji Dla Czynników Motor

Flask + Plotly app that pulls public inflation data and visualizes selected CPI/HICP factors across supported countries.

## Run locally

```bash
pip install -r requirements.txt
flask --app app run
```

Then open `http://127.0.0.1:5000`.

## Deploy On Vercel

Vercel can deploy this as a Python Flask app.

1. Push this folder to a GitHub repository.
2. In Vercel, choose **Add New... > Project**.
3. Import the GitHub repository.
4. Deploy.

No secrets are required. The app fetches public inflation data at runtime.

The repository root should contain `app.py`, `requirements.txt`, and `vercel.json`.

## Repository Location

Local repository folder:

```text
C:\Users\zebami1\Documents\Codex\2026-05-29\i-want-to-deploy-this-app
```

## Commit And Push To GitHub

Run these commands from the repository folder:

```bash
cd C:\Users\zebami1\Documents\Codex\2026-05-29\i-want-to-deploy-this-app
git status
git add app.py README.md requirements.txt vercel.json
git commit -m "Update CPI dashboard"
git push
```

If you want to create a pull request instead of pushing directly to `main`, create and push a feature branch:

```bash
cd C:\Users\zebami1\Documents\Codex\2026-05-29\i-want-to-deploy-this-app
git checkout -b update-cpi-dashboard
git add app.py README.md requirements.txt vercel.json
git commit -m "Update CPI dashboard"
git push -u origin update-cpi-dashboard
```

Then open GitHub and use **Compare & pull request**.

## Manual Vercel Deployment

Before uploading manually, make sure these files are in the project root:

```text
app.py
requirements.txt
vercel.json
README.md
```

Recommended Vercel project settings:

```text
Framework Preset: Other
Build Command: leave empty
Output Directory: leave empty
Install Command: leave empty, or pip install -r requirements.txt
```

Manual deployment options:

1. Merge the GitHub PR into the branch connected to Vercel, usually `main`. Vercel should redeploy automatically.
2. If uploading manually, upload the repository root folder contents to Vercel, not a parent folder.
3. If using Vercel CLI, run:

```bash
cd C:\Users\zebami1\Documents\Codex\2026-05-29\i-want-to-deploy-this-app
vercel --prod
```

After deployment, open the base Vercel URL without old query parameters after `?`, because old URL parameters can preserve an older date range.
