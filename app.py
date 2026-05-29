import functools
import re
import warnings
from html import escape

import eurostat
import pandas as pd
import plotly.express as px
from flask import Flask, request
from markupsafe import Markup


warnings.filterwarnings("ignore")

app = Flask(__name__)


COUNTRIES = {
    "Polska": "PL",
    "Niemcy": "DE",
    "Austria": "AT",
    "Grecja": "EL",
    "Estonia": "EE",
    "Litwa": "LT",
    "Łotwa": "LV",
}

FACTORS = {
    "headline": "Inflacja ogólna",
    "parts": "Części / materiały",
    "labor": "Robocizna / usługi naprawcze",
    "medical": "Zdrowie / koszty leczenia",
    "fuel_energy": "Paliwo i energia",
}

SERIES = [
    {
        "factor": "headline",
        "factor_label": "Inflacja ogólna",
        "indicator": "All-items HICP",
        "coicop": "CP00",
    },
    {
        "factor": "parts",
        "factor_label": "Części / materiały",
        "indicator": "Spare parts and accessories for vehicles",
        "coicop": "CP0721",
    },
    {
        "factor": "labor",
        "factor_label": "Robocizna / usługi naprawcze",
        "indicator": "Maintenance and repair of vehicles",
        "coicop": "CP0723",
    },
    {
        "factor": "medical",
        "factor_label": "Zdrowie / koszty leczenia",
        "indicator": "Health",
        "coicop": "CP06",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Paliwo i energia",
        "indicator": "Fuels and lubricants for personal transport",
        "coicop": "CP0722",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Paliwo i energia",
        "indicator": "Electricity, gas and other fuels",
        "coicop": "CP045",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Paliwo i energia",
        "indicator": "Energy aggregate",
        "coicop": "NRG",
    },
]

START_DATE = pd.Timestamp.today().normalize() - pd.DateOffset(years=5)


def is_time_column(col):
    value = str(col)
    return bool(
        re.match(r"^\d{4}-\d{2}$", value)
        or re.match(r"^\d{4}M\d{2}$", value)
        or re.match(r"^\d{4}Q[1-4]$", value)
        or re.match(r"^\d{4}$", value)
    )


def parse_period(period):
    value = str(period)

    if re.match(r"^\d{4}-\d{2}$", value):
        return pd.to_datetime(value + "-01", errors="coerce")

    if re.match(r"^\d{4}M\d{2}$", value):
        return pd.to_datetime(value.replace("M", "-") + "-01", errors="coerce")

    if re.match(r"^\d{4}Q[1-4]$", value):
        return pd.Period(value, freq="Q").to_timestamp()

    if re.match(r"^\d{4}$", value):
        return pd.to_datetime(value + "-01-01", errors="coerce")

    return pd.NaT


def melt_eurostat(raw):
    if raw is None or raw.empty:
        return pd.DataFrame()

    time_cols = [column for column in raw.columns if is_time_column(column)]
    if not time_cols:
        return pd.DataFrame()

    id_cols = [column for column in raw.columns if column not in time_cols]
    long = raw.melt(
        id_vars=id_cols,
        value_vars=time_cols,
        var_name="period",
        value_name="value",
    )
    long["date"] = long["period"].apply(parse_period)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["date", "value"])

    return long[["date", "value"]]


def build_status(country_name, spec, status, n_rows=0, min_date=None, max_date=None):
    return {
        "country": country_name,
        "factor": spec["factor_label"],
        "indicator": spec["indicator"],
        "coicop": spec["coicop"],
        "status": status,
        "n_rows": n_rows,
        "min_date": min_date,
        "max_date": max_date,
    }


def get_one_series(country_name, geo, spec):
    try:
        raw = eurostat.get_data_df(
            "prc_hicp_manr",
            flags=False,
            filter_pars={
                "unit": "RCH_A",
                "coicop": spec["coicop"],
                "geo": geo,
            },
        )

        if raw is None or raw.empty:
            return pd.DataFrame(), build_status(country_name, spec, "empty response")

        long = melt_eurostat(raw)
        if long.empty:
            return pd.DataFrame(), build_status(country_name, spec, "empty after melt")

        long = long[long["date"] >= START_DATE].copy()
        long["country"] = country_name
        long["geo"] = geo
        long["factor"] = spec["factor"]
        long["factor_label"] = spec["factor_label"]
        long["indicator"] = spec["indicator"]
        long["coicop"] = spec["coicop"]
        long["source"] = "Eurostat prc_hicp_manr"

        return long, build_status(
            country_name,
            spec,
            "ok" if len(long) > 0 else "no data in last 5 years",
            len(long),
            long["date"].min() if len(long) > 0 else None,
            long["date"].max() if len(long) > 0 else None,
        )

    except Exception as exc:
        return pd.DataFrame(), build_status(country_name, spec, f"error: {exc}")


@functools.lru_cache(maxsize=32)
def load_data(selected_countries):
    frames = []
    statuses = []

    for country_name in selected_countries:
        geo = COUNTRIES[country_name]
        for spec in SERIES:
            df, status = get_one_series(country_name, geo, spec)
            statuses.append(status)
            if not df.empty:
                frames.append(df)

    status_df = pd.DataFrame(statuses)
    if not frames:
        return pd.DataFrame(), status_df

    raw = pd.concat(frames, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["date"])
    raw["month"] = raw["date"].dt.to_period("M").dt.to_timestamp()

    return raw, status_df


def aggregate_for_charts(raw):
    if raw.empty:
        return pd.DataFrame()

    return (
        raw.groupby(["month", "country", "factor", "factor_label"], as_index=False)
        .agg(
            value=("value", "mean"),
            n_indicators=("indicator", "nunique"),
            indicators=("indicator", lambda values: ", ".join(sorted(set(values)))),
            coicops=("coicop", lambda values: ", ".join(sorted(set(values)))),
        )
        .rename(columns={"month": "date"})
        .sort_values(["factor", "country", "date"])
    )


def make_plot(chart_df, factor):
    sub = chart_df[chart_df["factor"] == factor].copy()
    if sub.empty:
        return f'<p class="notice">{escape(FACTORS[factor])}: brak danych.</p>'

    fig = px.line(
        sub,
        x="date",
        y="value",
        color="country",
        title=FACTORS[factor],
        labels={
            "date": "Data",
            "value": "Inflacja r/r, %",
            "country": "Kraj",
            "n_indicators": "Liczba wskaźników",
            "indicators": "Wskaźniki",
            "coicops": "COICOP",
        },
        hover_data={
            "n_indicators": True,
            "indicators": True,
            "coicops": True,
            "factor": False,
            "factor_label": False,
        },
    )
    fig.update_layout(height=520, hovermode="x unified", legend_title_text="Kraj")
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def selected_countries_from_request():
    selected = request.args.getlist("countries") or list(COUNTRIES.keys())
    selected = [country for country in selected if country in COUNTRIES]

    one_country = request.args.get("view", "Wszystkie wybrane kraje")
    if one_country in COUNTRIES:
        selected = [one_country]

    return tuple(selected), one_country


def render_options(selected, one_country):
    checkbox_html = []
    for country in COUNTRIES:
        checked = " checked" if country in selected else ""
        checkbox_html.append(
            f"""
            <label class="check">
                <input type="checkbox" name="countries" value="{escape(country)}"{checked}>
                {escape(country)}
            </label>
            """
        )

    view_options = ['<option>Wszystkie wybrane kraje</option>']
    for country in COUNTRIES:
        selected_attr = " selected" if country == one_country else ""
        view_options.append(
            f'<option value="{escape(country)}"{selected_attr}>{escape(country)}</option>'
        )

    return "\n".join(checkbox_html), "\n".join(view_options)


def dataframe_html(df):
    if df.empty:
        return '<p class="notice">Brak danych.</p>'
    return df.to_html(index=False, classes="dataframe", border=0, escape=True)


@app.route("/")
def index():
    selected, one_country = selected_countries_from_request()
    if not selected:
        selected = tuple(COUNTRIES.keys())

    checkbox_html, view_options = render_options(selected, one_country)
    should_load = request.args.get("load") == "1"

    if not should_load:
        charts_html = (
            '<p class="notice">Wybierz kraje i kliknij przycisk, aby pobrać dane z Eurostatu.</p>'
        )
        latest_html = ""
        metrics_html = ""
        status_html = ""
    else:
        raw, status_df = load_data(selected)
        status_view = status_df.assign(
            min_date=lambda df: pd.to_datetime(
                df["min_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
            max_date=lambda df: pd.to_datetime(
                df["max_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
        )
        status_html = f"""
        <h2>Diagnostyka pobierania danych</h2>
        <div class="table-wrap">{dataframe_html(status_view)}</div>
        """

        if raw.empty:
            charts_html = '<p class="notice error">Nie pobrano żadnych danych.</p>'
            latest_html = ""
            metrics_html = ""
        else:
            chart_df = aggregate_for_charts(raw)
            charts_html = "\n".join(
                f"<section>{make_plot(chart_df, factor)}</section>" for factor in FACTORS
            )
            latest = (
                chart_df.sort_values("date")
                .groupby(["country", "factor_label"], as_index=False)
                .tail(1)
                .sort_values(["factor_label", "country"])
            )
            latest = latest[
                [
                    "country",
                    "factor_label",
                    "date",
                    "value",
                    "n_indicators",
                    "indicators",
                    "coicops",
                ]
            ].assign(
                date=lambda df: df["date"].dt.strftime("%Y-%m-%d"),
                value=lambda df: df["value"].map(lambda value: f"{value:.2f}"),
            )
            latest_html = dataframe_html(latest)
            metrics_html = f"""
            <div class="metrics">
                <div><strong>{len(raw):,}</strong><span>Rekordy źródłowe</span></div>
                <div><strong>{len(chart_df):,}</strong><span>Rekordy po agregacji</span></div>
                <div><strong>{START_DATE.date()}</strong><span>Początek zakresu</span></div>
            </div>
            """.replace(",", " ")

    return Markup(
        f"""
        <!doctype html>
        <html lang="pl">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Dashboard inflacji Motor</title>
            <style>
                :root {{
                    color-scheme: light;
                    --ink: #17202a;
                    --muted: #607080;
                    --line: #d9e0e7;
                    --panel: #f6f8fa;
                    --accent: #1769aa;
                }}
                * {{ box-sizing: border-box; }}
                body {{
                    margin: 0;
                    font-family: Inter, Segoe UI, Arial, sans-serif;
                    color: var(--ink);
                    background: white;
                }}
                header {{
                    padding: 32px clamp(20px, 5vw, 64px) 20px;
                    border-bottom: 1px solid var(--line);
                }}
                h1 {{
                    margin: 0 0 8px;
                    font-size: clamp(28px, 4vw, 44px);
                    letter-spacing: 0;
                }}
                .subtitle {{ margin: 0; color: var(--muted); }}
                main {{
                    display: grid;
                    grid-template-columns: minmax(230px, 290px) minmax(0, 1fr);
                    gap: 28px;
                    padding: 28px clamp(20px, 5vw, 64px) 56px;
                }}
                aside {{
                    align-self: start;
                    position: sticky;
                    top: 20px;
                    padding: 18px;
                    background: var(--panel);
                    border: 1px solid var(--line);
                    border-radius: 8px;
                }}
                fieldset {{
                    border: 0;
                    padding: 0;
                    margin: 0 0 18px;
                }}
                legend, label.select-label {{
                    display: block;
                    font-weight: 700;
                    margin-bottom: 10px;
                }}
                .check {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    margin: 8px 0;
                    color: #26313d;
                }}
                select, button {{
                    width: 100%;
                    min-height: 40px;
                    font: inherit;
                    border-radius: 6px;
                    border: 1px solid var(--line);
                    background: white;
                }}
                button {{
                    margin-top: 18px;
                    border-color: var(--accent);
                    background: var(--accent);
                    color: white;
                    font-weight: 700;
                    cursor: pointer;
                }}
                button[aria-busy="true"] {{
                    opacity: 0.78;
                    cursor: progress;
                }}
                .loader {{
                    display: none;
                    align-items: center;
                    gap: 10px;
                    margin-top: 14px;
                    color: var(--muted);
                    font-size: 14px;
                }}
                .loader.is-active {{
                    display: flex;
                }}
                .spinner {{
                    width: 18px;
                    height: 18px;
                    border: 3px solid #c8d3de;
                    border-top-color: var(--accent);
                    border-radius: 50%;
                    animation: spin 0.8s linear infinite;
                }}
                @keyframes spin {{
                    to {{ transform: rotate(360deg); }}
                }}
                .metrics {{
                    display: grid;
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                    gap: 12px;
                    margin-bottom: 24px;
                }}
                .metrics div {{
                    padding: 16px;
                    border: 1px solid var(--line);
                    border-radius: 8px;
                }}
                .metrics strong {{
                    display: block;
                    font-size: 24px;
                    margin-bottom: 4px;
                }}
                .metrics span {{ color: var(--muted); }}
                section {{
                    padding: 8px 0 22px;
                    border-bottom: 1px solid var(--line);
                }}
                .notice {{
                    padding: 14px 16px;
                    border-radius: 8px;
                    background: var(--panel);
                }}
                .error {{ color: #9d1c1c; }}
                .table-wrap {{ overflow-x: auto; margin: 12px 0 30px; }}
                table.dataframe {{
                    width: 100%;
                    border-collapse: collapse;
                    font-size: 14px;
                }}
                table.dataframe th, table.dataframe td {{
                    padding: 9px 10px;
                    border-bottom: 1px solid var(--line);
                    text-align: left;
                    vertical-align: top;
                }}
                table.dataframe th {{ background: var(--panel); }}
                @media (max-width: 860px) {{
                    main {{ grid-template-columns: 1fr; }}
                    aside {{ position: static; }}
                    .metrics {{ grid-template-columns: 1fr; }}
                }}
            </style>
        </head>
        <body>
            <header>
                <h1>Dashboard inflacji dla czynników Motor</h1>
                <p class="subtitle">Dane Eurostat HICP, zakres ostatnich 5 lat.</p>
            </header>
            <main>
                <aside>
                    <form method="get" id="data-form">
                        <fieldset>
                            <legend>Kraje</legend>
                            {checkbox_html}
                        </fieldset>
                        <label class="select-label" for="view">Widok</label>
                        <select id="view" name="view">
                            {view_options}
                        </select>
                        <input type="hidden" name="load" value="1">
                        <button type="submit" id="load-button">Pobierz dane i pokaż wykresy</button>
                        <div class="loader" id="loader" role="status" aria-live="polite">
                            <span class="spinner" aria-hidden="true"></span>
                            <span>Pobieram dane z Eurostatu...</span>
                        </div>
                    </form>
                </aside>
                <div>
                    {metrics_html}
                    {status_html}
                    {charts_html}
                    <h2>Najnowsze dostępne odczyty</h2>
                    <div class="table-wrap">{latest_html}</div>
                </div>
            </main>
            <script>
                const form = document.getElementById("data-form");
                const loader = document.getElementById("loader");
                const button = document.getElementById("load-button");

                form.addEventListener("submit", () => {{
                    loader.classList.add("is-active");
                    button.setAttribute("aria-busy", "true");
                    button.textContent = "Pobieram...";
                }});
            </script>
        </body>
        </html>
        """
    )
