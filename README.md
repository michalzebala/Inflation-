# Dashboard inflacji dla czynników Motor

Streamlit app that pulls Eurostat HICP annual-rate data and visualizes selected inflation factors for Poland, Germany, Austria, Greece, Estonia, Lithuania, and Latvia.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

The simplest path is Streamlit Community Cloud:

1. Push this folder to a GitHub repository.
2. Create a new Streamlit app from that repository.
3. Set the main file path to `app.py`.
4. Deploy.

No secrets are required. The app fetches public Eurostat data at runtime and caches results for one hour.
