# Dashboard Inflacji Dla Czynników Motor

Flask + Plotly app that pulls Eurostat HICP annual-rate data and visualizes selected inflation factors for Poland, Germany, Austria, Greece, Estonia, Lithuania, and Latvia.

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

No secrets are required. The app fetches public Eurostat data at runtime.

The repository root should contain `app.py`, `requirements.txt`, and `vercel.json`.
