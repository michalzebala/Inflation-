import re
import warnings
from datetime import date

import eurostat
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="Dashboard inflacji Motor",
    page_icon="📈",
    layout="wide",
)


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


@st.cache_data(ttl=60 * 60, show_spinner=False)
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


def plot_factor(chart_df, factor):
    sub = chart_df[chart_df["factor"] == factor].copy()

    if sub.empty:
        st.info(f"{FACTORS[factor]}: brak danych.")
        return

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
    st.plotly_chart(fig, use_container_width=True)


def format_status_dates(status_df):
    if status_df.empty:
        return status_df

    return status_df.assign(
        min_date=lambda df: pd.to_datetime(df["min_date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        ),
        max_date=lambda df: pd.to_datetime(df["max_date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        ),
    )


def main():
    st.title("Dashboard inflacji dla czynników Motor")
    st.caption(f"Zakres: ostatnie 5 lat, od {START_DATE.date()}.")

    with st.sidebar:
        st.header("Parametry aplikacji")
        selected = st.multiselect(
            "Kraje",
            options=list(COUNTRIES.keys()),
            default=list(COUNTRIES.keys()),
        )
        one_country = st.selectbox(
            "Widok",
            options=["Wszystkie wybrane kraje"] + list(COUNTRIES.keys()),
        )

        if one_country != "Wszystkie wybrane kraje":
            selected = [one_country]

        refresh = st.button("Pobierz dane i pokaż wykresy", type="primary")

        if st.button("Wyczyść cache danych"):
            st.cache_data.clear()
            st.rerun()

    if not selected:
        st.warning("Wybierz przynajmniej jeden kraj.")
        return

    if not refresh and "has_loaded" not in st.session_state:
        st.info("Kliknij przycisk w panelu bocznym, aby pobrać dane z Eurostatu.")
        return

    st.session_state.has_loaded = True

    with st.spinner("Pobieram dane z Eurostatu..."):
        raw, status_df = load_data(tuple(selected))

    st.subheader("Diagnostyka pobierania danych")
    st.dataframe(format_status_dates(status_df), use_container_width=True)

    if raw.empty:
        st.error("Nie pobrano żadnych danych.")
        return

    chart_df = aggregate_for_charts(raw)

    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Rekordy źródłowe", f"{len(raw):,}".replace(",", " "))
    metric_2.metric("Rekordy po agregacji", f"{len(chart_df):,}".replace(",", " "))
    metric_3.metric("Ostatnia aktualizacja widoku", date.today().strftime("%Y-%m-%d"))

    tabs = st.tabs(
        [
            "Inflacja ogólna",
            "Części / materiały",
            "Robocizna / usługi",
            "Zdrowie",
            "Paliwo i energia",
            "Najnowsze odczyty",
        ]
    )

    with tabs[0]:
        plot_factor(chart_df, "headline")
    with tabs[1]:
        plot_factor(chart_df, "parts")
    with tabs[2]:
        plot_factor(chart_df, "labor")
    with tabs[3]:
        plot_factor(chart_df, "medical")
    with tabs[4]:
        plot_factor(chart_df, "fuel_energy")
    with tabs[5]:
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
        ].assign(date=lambda df: df["date"].dt.strftime("%Y-%m-%d"))

        st.dataframe(
            latest.style.format({"value": "{:.2f}"}),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
