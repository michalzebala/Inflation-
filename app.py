import functools
import re
import warnings
from datetime import date
from html import escape

import eurostat
import pandas as pd
import plotly.express as px
from flask import Flask, request
from markupsafe import Markup


warnings.filterwarnings("ignore")

app = Flask(__name__)


COUNTRIES = {
    "Poland": "PL",
    "Germany": "DE",
    "Austria": "AT",
    "Greece": "EL",
    "Estonia": "EE",
    "Lithuania": "LT",
    "Latvia": "LV",
}

FACTORS = {
    "headline": "Headline inflation",
    "parts": "Parts and materials",
    "labor": "Labor and repair services",
    "medical": "Health and medical costs",
    "fuel_energy": "Fuel and energy",
}

SERIES = [
    {
        "factor": "headline",
        "factor_label": "Headline inflation",
        "indicator": "All-items HICP",
        "coicop": "CP00",
    },
    {
        "factor": "parts",
        "factor_label": "Parts and materials",
        "indicator": "Spare parts and accessories for vehicles",
        "coicop": "CP0721",
    },
    {
        "factor": "labor",
        "factor_label": "Labor and repair services",
        "indicator": "Maintenance and repair of vehicles",
        "coicop": "CP0723",
    },
    {
        "factor": "medical",
        "factor_label": "Health and medical costs",
        "indicator": "Health",
        "coicop": "CP06",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Fuels and lubricants for personal transport",
        "coicop": "CP0722",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Electricity, gas and other fuels",
        "coicop": "CP045",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Energy aggregate",
        "coicop": "NRG",
    },
]

DEFAULT_YEARS = 10
START_DATE = pd.Timestamp.today().normalize() - pd.DateOffset(years=DEFAULT_YEARS)


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
            "ok" if len(long) > 0 else "no data in last 10 years",
            len(long),
            long["date"].min() if len(long) > 0 else None,
            long["date"].max() if len(long) > 0 else None,
        )

    except Exception as exc:
        return pd.DataFrame(), build_status(country_name, spec, f"error: {exc}")


@functools.lru_cache(maxsize=32)
def load_data(selected_countries, cache_day):
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
        return f'<p class="notice">{escape(FACTORS[factor])}: no data available.</p>'

    fig = px.line(
        sub,
        x="date",
        y="value",
        color="country",
        title=FACTORS[factor],
        labels={
            "date": "Date",
            "value": "Year-over-year inflation, %",
            "country": "Country",
            "n_indicators": "Number of indicators",
            "indicators": "Indicators",
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
    fig.update_layout(height=520, hovermode="x unified", legend_title_text="Country")
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def selected_countries_from_request():
    selected = request.args.getlist("countries") or ["Poland"]
    selected = [country for country in selected if country in COUNTRIES]

    one_country = request.args.get("view", "All selected countries")
    if one_country in COUNTRIES:
        selected = [one_country]

    return tuple(selected), one_country


def parse_month(value):
    if not value:
        return None

    parsed = pd.to_datetime(f"{value}-01", errors="coerce")
    if pd.isna(parsed):
        return None

    return parsed.to_period("M").to_timestamp()


def format_month(value):
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m")


def requested_month_range(raw):
    available_min = raw["month"].min()
    available_max = raw["month"].max()

    default_end = available_max
    default_start = max(available_min, default_end - pd.DateOffset(years=DEFAULT_YEARS))

    requested_start = parse_month(request.args.get("start_month"))
    requested_end = parse_month(request.args.get("end_month"))
    start_month = requested_start if requested_start is not None else default_start
    end_month = requested_end if requested_end is not None else default_end

    start_month = max(start_month, available_min)
    end_month = min(end_month, available_max)

    if start_month > end_month:
        start_month, end_month = default_start, default_end

    return start_month, end_month, available_min, available_max


def default_month_range():
    end_month = pd.Timestamp.today().normalize().to_period("M").to_timestamp()
    start_month = end_month - pd.DateOffset(years=DEFAULT_YEARS)
    return start_month, end_month


def filter_to_month_range(raw, start_month, end_month):
    return raw[(raw["month"] >= start_month) & (raw["month"] <= end_month)].copy()


def render_options(selected, one_country, start_month, end_month, min_month=None, max_month=None):
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

    view_options = ['<option>All selected countries</option>']
    for country in COUNTRIES:
        selected_attr = " selected" if country == one_country else ""
        view_options.append(
            f'<option value="{escape(country)}"{selected_attr}>{escape(country)}</option>'
        )

    min_attr = f' min="{escape(format_month(min_month))}"' if min_month is not None else ""
    max_attr = f' max="{escape(format_month(max_month))}"' if max_month is not None else ""

    range_html = f"""
    <div class="date-range">
        <label>
            <span>Start month</span>
            <input type="month" name="start_month" value="{escape(format_month(start_month))}"{min_attr}{max_attr}>
        </label>
        <label>
            <span>End month</span>
            <input type="month" name="end_month" value="{escape(format_month(end_month))}"{min_attr}{max_attr}>
        </label>
    </div>
    """

    return "\n".join(checkbox_html), "\n".join(view_options), range_html


def dataframe_html(df):
    if df.empty:
        return '<p class="notice">No data available.</p>'
    return df.to_html(index=False, classes="dataframe", border=0, escape=True)


def color_for_value(value, max_positive, min_negative):
    if pd.isna(value):
        return ""

    if value >= 0:
        if max_positive <= 0:
            intensity = 0
        else:
            intensity = min(abs(value) / max_positive, 1)
        red = 255
        green = round(248 - (98 * intensity))
        blue = round(248 - (98 * intensity))
        return f"background-color: rgb({red}, {green}, {blue});"

    if min_negative >= 0:
        intensity = 0
    else:
        intensity = min(abs(value) / abs(min_negative), 1)
    red = round(244 - (104 * intensity))
    green = round(252 - (72 * intensity))
    blue = round(246 - (88 * intensity))
    return f"background-color: rgb({red}, {green}, {blue});"


def pivot_chart_data_html(chart_df):
    if chart_df.empty:
        return '<p class="notice">No data available.</p>'

    table_source = chart_df.copy()
    table_source["month_label"] = table_source["date"].dt.strftime("%Y-%m")

    pivot = table_source.pivot_table(
        index=["country", "factor_label"],
        columns="month_label",
        values="value",
        aggfunc="mean",
    )

    date_columns = sorted(pivot.columns)
    pivot = pivot.reindex(columns=date_columns).sort_index()

    values = pivot.to_numpy().ravel()
    numeric_values = pd.Series(values).dropna()
    positives = numeric_values[numeric_values >= 0]
    negatives = numeric_values[numeric_values < 0]
    max_positive = positives.max() if not positives.empty else 0
    min_negative = negatives.min() if not negatives.empty else 0

    rows = [
        '<table class="dataframe excel-table">',
        "<thead><tr>",
        '<th class="sticky-col country-col">Country</th>',
        '<th class="sticky-col factor-col">Factor</th>',
    ]

    for month in date_columns:
        rows.append(f"<th>{escape(month)}</th>")

    rows.append("</tr></thead><tbody>")

    for (country, factor), row in pivot.iterrows():
        rows.append("<tr>")
        rows.append(f'<th class="sticky-col country-col">{escape(str(country))}</th>')
        rows.append(f'<th class="sticky-col factor-col">{escape(str(factor))}</th>')

        for month in date_columns:
            value = row[month]
            if pd.isna(value):
                rows.append("<td></td>")
                continue

            style = color_for_value(value, max_positive, min_negative)
            rows.append(f'<td style="{style}">{value:.2f}</td>')

        rows.append("</tr>")

    rows.append("</tbody></table>")
    return "\n".join(rows)


@app.route("/")
def index():
    selected, one_country = selected_countries_from_request()
    if not selected:
        selected = tuple(COUNTRIES.keys())

    should_load = request.args.get("load") == "1"
    default_start_month, default_end_month = default_month_range()
    checkbox_html, view_options, range_html = render_options(
        selected,
        one_country,
        default_start_month,
        default_end_month,
    )

    if not should_load:
        charts_html = (
            '<p class="notice">Select countries and click the button to fetch Eurostat data.</p>'
        )
        data_table_html = ""
        latest_html = ""
        metrics_html = ""
        status_html = ""
    else:
        raw, status_df = load_data(selected, date.today().isoformat())
        status_view = status_df.assign(
            min_date=lambda df: pd.to_datetime(
                df["min_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
            max_date=lambda df: pd.to_datetime(
                df["max_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
        ).rename(
            columns={
                "country": "Country",
                "factor": "Factor",
                "indicator": "Indicator",
                "coicop": "COICOP",
                "status": "Status",
                "n_rows": "Rows",
                "min_date": "First date",
                "max_date": "Latest date",
            }
        )
        status_html = f"""
        <h2>Data Fetch Diagnostics</h2>
        <div class="table-wrap">{dataframe_html(status_view)}</div>
        """

        if raw.empty:
            charts_html = '<p class="notice error">No data was fetched.</p>'
            data_table_html = ""
            latest_html = ""
            metrics_html = ""
        else:
            start_month, end_month, available_min, available_max = requested_month_range(raw)
            checkbox_html, view_options, range_html = render_options(
                selected,
                one_country,
                start_month,
                end_month,
                available_min,
                available_max,
            )

            visible_raw = filter_to_month_range(raw, start_month, end_month)
            chart_df = aggregate_for_charts(visible_raw)

            if chart_df.empty:
                charts_html = '<p class="notice">No data is available for the selected range.</p>'
                data_table_html = """
                <h2>Chart Data</h2>
                <p class="notice">No data is available for the selected range.</p>
                """
                latest_html = ""
            else:
                charts_html = "\n".join(
                    f"<section>{make_plot(chart_df, factor)}</section>" for factor in FACTORS
                )
                data_table_html = f"""
                <h2>Chart Data</h2>
                <div class="table-wrap data-table">{pivot_chart_data_html(chart_df)}</div>
                """
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
                ).rename(
                    columns={
                        "country": "Country",
                        "factor_label": "Factor",
                        "date": "Date",
                        "value": "Value",
                        "n_indicators": "Indicators",
                        "indicators": "Indicator names",
                        "coicops": "COICOP",
                    }
                )
                latest_html = dataframe_html(latest)

            metrics_html = f"""
            <div class="metrics">
                <div><strong>{len(visible_raw):,}</strong><span>Visible source records</span></div>
                <div><strong>{len(chart_df):,}</strong><span>Aggregated records</span></div>
                <div><strong>{format_month(start_month)} – {format_month(end_month)}</strong><span>Visible range</span></div>
            </div>
            """.replace(",", " ")

    return Markup(
        f"""
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Motor Inflation Dashboard</title>
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
                input[type="month"] {{
                    width: 100%;
                    min-height: 40px;
                    padding: 0 10px;
                    font: inherit;
                    border-radius: 6px;
                    border: 1px solid var(--line);
                    background: white;
                }}
                .date-range {{
                    display: grid;
                    gap: 10px;
                    margin-top: 18px;
                }}
                .date-range label span {{
                    display: block;
                    margin-bottom: 6px;
                    font-size: 13px;
                    font-weight: 700;
                    color: var(--muted);
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
                .data-table {{
                    max-height: 520px;
                    overflow: auto;
                    border: 1px solid var(--line);
                    border-radius: 8px;
                }}
                .excel-table {{
                    width: max-content;
                    min-width: max-content;
                    table-layout: fixed;
                    border-collapse: separate !important;
                    border-spacing: 0;
                }}
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
                .data-table table.dataframe thead th {{
                    position: sticky;
                    top: 0;
                    z-index: 1;
                }}
                .excel-table th,
                .excel-table td {{
                    white-space: nowrap;
                    min-width: 78px;
                    text-align: right;
                }}
                .excel-table .sticky-col {{
                    position: sticky;
                    z-index: 2;
                    text-align: left;
                    background: #ffffff;
                    box-shadow: 1px 0 0 var(--line);
                }}
                .excel-table tbody .sticky-col {{
                    top: auto;
                    z-index: 2;
                }}
                .excel-table thead .sticky-col {{
                    top: 0;
                    z-index: 4;
                    background: var(--panel);
                }}
                .excel-table .country-col {{
                    left: 0;
                    min-width: 118px;
                    width: 118px;
                    max-width: 118px;
                }}
                .excel-table .factor-col {{
                    left: 118px;
                    min-width: 190px;
                    width: 190px;
                    max-width: 190px;
                }}
                @media (max-width: 860px) {{
                    main {{ grid-template-columns: 1fr; }}
                    aside {{ position: static; }}
                    .metrics {{ grid-template-columns: 1fr; }}
                }}
            </style>
        </head>
        <body>
            <header>
                <h1>Motor Cost Inflation Dashboard</h1>
                <p class="subtitle">Eurostat HICP data for the last 10 years.</p>
            </header>
            <main>
                <aside>
                    <form method="get" id="data-form">
                        <fieldset>
                            <legend>Countries</legend>
                            {checkbox_html}
                        </fieldset>
                        <label class="select-label" for="view">View</label>
                        <select id="view" name="view">
                            {view_options}
                        </select>
                        {range_html}
                        <input type="hidden" name="load" value="1">
                        <button type="submit" id="load-button">Fetch data and show charts</button>
                        <div class="loader" id="loader" role="status" aria-live="polite">
                            <span class="spinner" aria-hidden="true"></span>
                            <span>Fetching Eurostat data...</span>
                        </div>
                    </form>
                </aside>
                <div>
                    {metrics_html}
                    {charts_html}
                    {data_table_html}
                    <h2>Latest Available Readings</h2>
                    <div class="table-wrap">{latest_html}</div>
                    {status_html}
                </div>
            </main>
            <script>
                const form = document.getElementById("data-form");
                const loader = document.getElementById("loader");
                const button = document.getElementById("load-button");

                form.addEventListener("submit", () => {{
                    loader.classList.add("is-active");
                    button.setAttribute("aria-busy", "true");
                    button.textContent = "Fetching...";
                }});
            </script>
        </body>
        </html>
        """
    )
