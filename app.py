import functools
import base64
import json
import os
import re
import smtplib
import warnings
from datetime import datetime
from email.utils import parsedate_to_datetime
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import eurostat
import numpy as np
import pandas as pd
import plotly.express as px
from flask import Flask, jsonify, request
from markupsafe import Markup


warnings.filterwarnings("ignore")

app = Flask(__name__)


def smtp_config():
    return {
        "host": os.getenv("SMTP_HOST"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "sender": os.getenv("SMTP_FROM") or os.getenv("SMTP_USERNAME"),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() != "false",
    }


def smtp_is_configured(config):
    return all(
        [
            config["host"],
            config["port"],
            config["username"],
            config["password"],
            config["sender"],
        ]
    )


def fetch_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 GlobalClaimsCPIDashboard/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        latest_data_upload = parse_upload_date(response.headers.get("Last-Modified"))
        payload = json.loads(response.read().decode("utf-8"))
    return payload, latest_data_upload


COUNTRIES = {
    "Poland": "PL",
    "Germany": "DE",
    "Austria": "AT",
    "Greece": "EL",
    "Estonia": "EE",
    "Lithuania": "LT",
    "Latvia": "LV",
    "Thailand": "TH",
    "Singapore": "SG",
    "India": "IN",
}

EUROSTAT_COUNTRIES = {"Poland", "Germany", "Austria", "Greece", "Estonia", "Lithuania", "Latvia"}
IMF_COUNTRIES = {"Thailand", "Singapore", "India"}
WORLD_BANK_COUNTRIES = {
    "TH": "THA",
    "SG": "SGP",
    "IN": "IND",
}

SINGSTAT_ROW_PATTERNS = {
    "headline": ["all items"],
    "parts": ["private transport", "personal transport", "operation of personal transport"],
    "labor": ["maintenance and repairs", "repair", "transport services"],
    "medical": ["health care", "health"],
    "fuel_energy": ["housing & utilities", "housing and utilities", "electricity", "gas"],
    "property_repair": ["accommodation", "housing & utilities", "housing and utilities"],
    "property_materials": ["household durables", "household equipment", "furnishings"],
    "property_services": ["accommodation", "housing & utilities", "housing and utilities"],
    "household_equipment": ["household durables", "household equipment", "furnishings"],
}

THAILAND_MOC_ENDPOINTS = {
    "headline": {
        "endpoint": "cpig-indexes",
        "label": "Consumer Price Index, All items",
    },
    "parts": {
        "endpoint": "ppi-cpa-indexes",
        "label": "Producer Price Index by Activity",
    },
    "property_materials": {
        "endpoint": "csi-indexes",
        "label": "Construction Materials Price Index",
    },
    "household_equipment": {
        "endpoint": "ppi-cpa-indexes",
        "label": "Producer Price Index by Activity",
    },
}

OFFICIAL_HEADLINE_RELEASES = {
    "Thailand": [
        {
            "date": "2026-06-01",
            "value": 2.42,
            "indicator": "Headline CPI inflation",
            "source": "Thailand TPSO/MOC Inflation Report Summary",
        },
    ],
    "Singapore": [
        {
            "date": "2026-05-01",
            "value": 1.80,
            "indicator": "CPI-All Items inflation",
            "source": "Singapore MAS/MTI Consumer Price Developments",
        },
        {
            "date": "2026-06-01",
            "value": 1.90,
            "indicator": "CPI-All Items inflation",
            "source": "Singapore MAS/MTI Consumer Price Developments",
        },
    ],
    "India": [
        {
            "date": "2026-04-01",
            "value": 3.48,
            "indicator": "All India CPI General inflation",
            "source": "India PIB/MoSPI CPI press release",
        },
        {
            "date": "2026-05-01",
            "value": 3.93,
            "indicator": "All India CPI General inflation",
            "source": "India PIB/MoSPI CPI press release",
        },
        {
            "date": "2026-06-01",
            "value": 4.38,
            "indicator": "All India CPI General inflation",
            "source": "India PIB/MoSPI CPI press release",
        },
    ],
}

FACTORS = {
    "headline": "Headline inflation",
    "parts": "Parts and materials",
    "labor": "Labor and repair services",
    "medical": "Health and medical costs",
    "fuel_energy": "Fuel and energy",
    "property_repair": "Property repair and maintenance",
    "property_materials": "Property repair materials",
    "property_services": "Property repair services",
    "household_equipment": "Household equipment and maintenance",
}

FACTOR_ICONS = {
    "headline": "bar-chart-3",
    "parts": "component",
    "labor": "wrench",
    "medical": "heart-pulse",
    "fuel_energy": "zap",
    "property_repair": "home",
    "property_materials": "brick-wall",
    "property_services": "hard-hat",
    "household_equipment": "washing-machine",
}

SERIES = [
    {
        "factor": "headline",
        "factor_label": "Headline inflation",
        "indicator": "All-items HICP",
        "coicop": "CP00",
        "coicop18": "TOTAL",
        "imf_indicator": "PCPI_IX",
        "imf_indicator_label": "Consumer Price Index, All items",
    },
    {
        "factor": "parts",
        "factor_label": "Parts and materials",
        "indicator": "Spare parts and accessories for vehicles",
        "coicop": "CP0721",
        "coicop18": "CP0721",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "labor",
        "factor_label": "Labor and repair services",
        "indicator": "Maintenance and repair of vehicles",
        "coicop": "CP0723",
        "coicop18": "CP0723",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "medical",
        "factor_label": "Health and medical costs",
        "indicator": "Health",
        "coicop": "CP06",
        "coicop18": "CP06",
        "imf_indicator": "PCPIM_IX",
        "imf_indicator_label": "Health",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Fuels and lubricants for personal transport",
        "coicop": "CP0722",
        "coicop18": "CP0722",
        "imf_indicator": "PCPIH_IX",
        "imf_indicator_label": "Housing, Water, Electricity, Gas and Other Fuels",
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Electricity, gas and other fuels",
        "coicop": "CP045",
        "coicop18": "CP045",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "fuel_energy",
        "factor_label": "Fuel and energy",
        "indicator": "Energy aggregate",
        "coicop": "NRG",
        "coicop18": "NRG",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "property_repair",
        "factor_label": "Property repair and maintenance",
        "indicator": "Maintenance, repair and security of the dwelling",
        "coicop": "CP043",
        "coicop18": "CP043",
        "imf_indicator": "PCPIH_IX",
        "imf_indicator_label": "Housing, Water, Electricity, Gas and Other Fuels",
    },
    {
        "factor": "property_materials",
        "factor_label": "Property repair materials",
        "indicator": "Security equipment and materials for dwelling maintenance and repair",
        "coicop": "CP0431",
        "coicop18": "CP0431",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "property_materials",
        "factor_label": "Property repair materials",
        "indicator": "Materials for the maintenance and repair of the dwelling",
        "coicop": "CP04311",
        "coicop18": "CP04311",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "property_services",
        "factor_label": "Property repair services",
        "indicator": "Services for the maintenance, repair and security of the dwelling",
        "coicop": "CP0432",
        "coicop18": "CP0432",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "property_services",
        "factor_label": "Property repair services",
        "indicator": "Services for the maintenance, repair and security of the dwelling",
        "coicop": "CP04320",
        "coicop18": "CP04320",
        "imf_indicator": None,
        "imf_indicator_label": None,
    },
    {
        "factor": "household_equipment",
        "factor_label": "Household equipment and maintenance",
        "indicator": "Furnishings, household equipment and routine household maintenance",
        "coicop": "CP05",
        "coicop18": "CP05",
        "imf_indicator": "PCPIHO_IX",
        "imf_indicator_label": "Furnishings, household equipment and routine household maintenance",
    },
]

DEFAULT_YEARS = 10
START_DATE = pd.Timestamp("2019-01-01")
EUROSTAT_API_BASE = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
)
IMF_API_BASE = "https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData/CPI"
IMF_SDMX_BASE = "dataservices.imf.org/REST/SDMX_JSON.svc/CompactData"
WORLD_BANK_API_BASE = "https://api.worldbank.org/v2/country"
SINGSTAT_API_BASE = "https://tablebuilder.singstat.gov.sg/api/table/tabledata"
THAILAND_MOC_API_BASE = "https://dataapi.moc.go.th"
PRIMARY_EUROSTAT_DATASET = "prc_hicp_minr"
FALLBACK_EUROSTAT_DATASET = "prc_hicp_manr"
SINGSTAT_CPI_RESOURCE_ID = "M213751"


def is_time_column(col):
    value = str(col)
    return bool(
        re.match(r"^\d{4}-\d{2}$", value)
        or re.match(r"^\d{4}-\d{2}-\d{2}", value)
        or re.match(r"^\d{4}M\d{2}$", value)
        or re.match(r"^\d{4}-M\d{2}$", value)
        or re.match(r"^\d{4}Q[1-4]$", value)
        or re.match(r"^\d{4}$", value)
    )


def parse_period(period):
    value = str(period)

    if isinstance(period, pd.Timestamp):
        return period.to_period("M").to_timestamp()

    if re.match(r"^\d{4}-\d{2}$", value):
        return pd.to_datetime(value + "-01", errors="coerce")

    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return pd.NaT
        return parsed.to_period("M").to_timestamp()

    if re.match(r"^\d{4}M\d{2}$", value):
        return pd.to_datetime(value.replace("M", "-") + "-01", errors="coerce")

    if re.match(r"^\d{4}-M\d{2}$", value):
        return pd.to_datetime(value.replace("-M", "-") + "-01", errors="coerce")

    if re.match(r"^\d{4}Q[1-4]$", value):
        return pd.Period(value, freq="Q").to_timestamp()

    if re.match(r"^\d{4}-Q[1-4]$", value):
        return pd.Period(value.replace("-Q", "Q"), freq="Q").to_timestamp()

    if re.match(r"^\d{4}$", value):
        return pd.to_datetime(value + "-01-01", errors="coerce")

    return pd.NaT


def melt_eurostat(raw):
    if raw is None or raw.empty:
        return pd.DataFrame()

    if {"TIME_PERIOD", "OBS_VALUE"}.issubset(raw.columns):
        long = raw.rename(
            columns={"TIME_PERIOD": "period", "OBS_VALUE": "value"}
        ).copy()
        long["date"] = long["period"].apply(parse_period)
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long.dropna(subset=["date", "value"])
        return long[["date", "value"]]

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


def build_status(
    country_name,
    spec,
    status,
    n_rows=0,
    min_date=None,
    max_date=None,
    latest_data_upload=None,
):
    return {
        "country": country_name,
        "factor": spec["factor_label"],
        "indicator": spec["indicator"],
        "coicop": spec["coicop"],
        "coicop18": spec["coicop18"],
        "status": status,
        "n_rows": n_rows,
        "min_date": min_date,
        "max_date": max_date,
        "latest_data_upload": latest_data_upload,
    }


def parse_upload_date(value):
    if not value:
        return pd.NaT

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return pd.NaT

    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)
    return timestamp.normalize()


def parse_calendar_date(value):
    if not value:
        return pd.NaT

    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT

    return pd.Timestamp(parsed).normalize()


def normalize_thai_year(value):
    if value is None or value == "":
        return None

    year = int(float(value))
    if year > 2400:
        year -= 543
    return year


def normalize_month_number(value):
    if value is None or value == "":
        return None

    month_names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    normalized = str(value).strip().lower()
    if normalized in month_names:
        return month_names[normalized]

    return int(float(normalized))


def jsonstat_time_series(payload):
    dimensions = payload.get("dimension", {})
    dimension_ids = payload.get("id", [])
    sizes = payload.get("size", [])

    if "time" not in dimensions or "value" not in payload:
        return pd.DataFrame()

    time_index = dimension_ids.index("time")
    time_category = dimensions["time"].get("category", {})
    time_positions = time_category.get("index", {})

    if isinstance(time_positions, list):
        time_positions = {label: position for position, label in enumerate(time_positions)}

    value_data = payload["value"]
    stride = 1
    for size in sizes[time_index + 1 :]:
        stride *= size

    records = []
    for period, position in sorted(time_positions.items(), key=lambda item: item[1]):
        flat_index = position * stride
        if isinstance(value_data, list):
            value = value_data[flat_index] if flat_index < len(value_data) else None
        else:
            value = value_data.get(str(flat_index))

        if value is None:
            continue

        records.append({"date": parse_period(period), "value": value})

    if not records:
        return pd.DataFrame()

    long = pd.DataFrame(records)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["date", "value"])
    return long[["date", "value"]]


def fetch_eurostat_api_series(geo, spec):
    params = {
        "format": "JSON",
        "lang": "en",
        "freq": "M",
        "unit": "RCH_A",
        "coicop18": spec["coicop18"],
        "geo": geo,
        "sinceTimePeriod": START_DATE.strftime("%Y-%m"),
    }
    url = f"{EUROSTAT_API_BASE}{PRIMARY_EUROSTAT_DATASET}?{urlencode(params)}"

    payload, latest_data_upload = fetch_json(url)

    return jsonstat_time_series(payload), latest_data_upload


def fetch_eurostat_package_series(geo, spec):
    raw = eurostat.get_data_df(
        FALLBACK_EUROSTAT_DATASET,
        flags=False,
        filter_pars={
            "freq": "M",
            "unit": "RCH_A",
            "coicop": spec["coicop"],
            "geo": geo,
        },
    )
    return melt_eurostat(raw), pd.NaT


def normalize_imf_series(series):
    if not series:
        return []

    return series if isinstance(series, list) else [series]


def fetch_imf_series(country_code, spec):
    indicator = spec.get("imf_indicator")
    if not indicator:
        return pd.DataFrame(), pd.NaT, "not available from IMF CPI source"

    start_period = (START_DATE - pd.DateOffset(months=13)).strftime("%Y-%m")
    end_period = pd.Timestamp.today().normalize().strftime("%Y-%m")
    candidates = []
    for frequency in ["M", "Q"]:
        for dataset in ["CPI", "IFS"]:
            for scheme in ["https", "http"]:
                candidates.append(
                    {
                        "frequency": frequency,
                        "url": f"{scheme}://{IMF_SDMX_BASE}/{dataset}/{frequency}.{country_code}.{indicator}",
                    }
                )
    latest_error = "empty IMF response"

    for candidate in candidates:
        url = f"{candidate['url']}?startPeriod={start_period}&endPeriod={end_period}"
        try:
            payload, latest_data_upload = fetch_json(url)
        except Exception as exc:
            latest_error = f"IMF request failed: {exc}"
            continue

        series = payload.get("CompactData", {}).get("DataSet", {}).get("Series")
        records = []

        for item in normalize_imf_series(series):
            observations = item.get("Obs", [])
            observations = observations if isinstance(observations, list) else [observations]
            for obs in observations:
                records.append(
                    {
                        "date": parse_period(obs.get("@TIME_PERIOD")),
                        "index_value": obs.get("@OBS_VALUE"),
                    }
                )

        data = calculate_yoy_from_index(records)
        if candidate["frequency"] == "Q" and not data.empty:
            data = expand_quarterly_to_monthly(data)

        if not data.empty:
            return data, latest_data_upload, "ok"

    return pd.DataFrame(), pd.NaT, latest_error


def fetch_world_bank_headline_series(country_code, spec):
    if spec["factor"] != "headline":
        return pd.DataFrame(), pd.NaT, "not available from World Bank fallback"

    wb_country = WORLD_BANK_COUNTRIES.get(country_code)
    if not wb_country:
        return pd.DataFrame(), pd.NaT, "not available from World Bank fallback"

    start_year = START_DATE.year
    end_year = pd.Timestamp.today().year
    url = (
        f"{WORLD_BANK_API_BASE}/{wb_country}/indicator/FP.CPI.TOTL.ZG"
        f"?format=json&date={start_year}:{end_year}&per_page=200"
    )

    payload, latest_data_upload = fetch_json(url)

    observations = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    records = []
    for obs in observations:
        if obs.get("value") is None:
            continue
        records.append(
            {
                "date": pd.Timestamp(f"{obs['date']}-01-01"),
                "value": obs["value"],
            }
        )

    if not records:
        return pd.DataFrame(), latest_data_upload, "empty World Bank response"

    data = pd.DataFrame(records)
    data["value"] = pd.to_numeric(data["value"], errors="coerce")
    data = data[data["date"] >= START_DATE].dropna(subset=["date", "value"])
    return data[["date", "value"]].sort_values("date"), latest_data_upload, "ok"


def official_headline_release_frame(country_name):
    releases = OFFICIAL_HEADLINE_RELEASES.get(country_name, [])
    if not releases:
        return pd.DataFrame()

    frame = pd.DataFrame(releases)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["latest_data_upload"] = frame["date"]
    return frame.dropna(subset=["date", "value"])


def enrich_with_official_headline_releases(country_name, spec, long):
    if spec["factor"] != "headline":
        return long

    release_frame = official_headline_release_frame(country_name)
    if release_frame.empty:
        return long

    release_frame["country"] = country_name
    release_frame["geo"] = COUNTRIES[country_name]
    release_frame["factor"] = spec["factor"]
    release_frame["factor_label"] = spec["factor_label"]
    release_frame["coicop"] = spec["coicop"]
    release_frame["coicop18"] = spec["coicop18"]

    if long.empty:
        return release_frame

    combined = pd.concat([long, release_frame], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (
        combined.sort_values(["date", "source"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
    )
    return combined


def combine_series(primary, fallback):
    if primary.empty:
        return fallback
    if fallback.empty:
        return primary

    combined = pd.concat([fallback, primary], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (
        combined.sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
    )
    return combined


def expand_quarterly_to_monthly(df):
    if df.empty:
        return df

    expanded = []
    for _, row in df.iterrows():
        quarter_start = pd.Timestamp(row["date"]).to_period("Q").start_time
        for offset in range(3):
            expanded_row = row.copy()
            expanded_row["date"] = quarter_start + pd.DateOffset(months=offset)
            expanded.append(expanded_row)

    return pd.DataFrame(expanded).sort_values("date")


def calculate_yoy_from_index(records):
    if not records:
        return pd.DataFrame()

    index_df = pd.DataFrame(records)
    index_df["index_value"] = pd.to_numeric(index_df["index_value"], errors="coerce")
    index_df = index_df.dropna(subset=["date", "index_value"]).sort_values("date")
    index_df["value"] = index_df["index_value"].pct_change(periods=12) * 100
    index_df = index_df[index_df["date"] >= START_DATE].dropna(subset=["value"])
    return index_df[["date", "value"]]


def flatten_singstat_rows(rows):
    if isinstance(rows, dict):
        rows = [rows]

    flattened = []
    for row in rows or []:
        flattened.append(row)
        flattened.extend(flatten_singstat_rows(row.get("children") or row.get("row")))
    return flattened


def flatten_singstat_columns(columns):
    if isinstance(columns, dict):
        columns = [columns]

    flattened = []
    for column in columns or []:
        if "value" in column:
            flattened.append(column)
        flattened.extend(flatten_singstat_columns(column.get("columns")))
    return flattened


def singstat_row_matches(row_text, patterns):
    normalized = str(row_text).lower()
    return any(pattern in normalized for pattern in patterns)


@functools.lru_cache(maxsize=16)
def fetch_singstat_table(cache_hour):
    url = f"{SINGSTAT_API_BASE}/{SINGSTAT_CPI_RESOURCE_ID}?limit=5000"
    payload, latest_data_upload = fetch_json(url)

    payload_upload = parse_calendar_date(payload.get("Data", {}).get("dataLastUpdated"))
    if not pd.isna(payload_upload):
        latest_data_upload = payload_upload

    data_section = payload.get("Data", {}) or payload.get("data", {})
    records_section = data_section.get("records", {})
    rows = data_section.get("row") or records_section.get("row") or []
    return payload, latest_data_upload, rows


def fetch_singapore_official_series(spec, cache_hour):
    patterns = SINGSTAT_ROW_PATTERNS.get(spec["factor"])
    if not patterns:
        return pd.DataFrame(), pd.NaT, "not available from SingStat CPI source", None

    payload, latest_data_upload, rows = fetch_singstat_table(cache_hour)
    matched_row = None
    flattened_rows = flatten_singstat_rows(rows)
    for row in flattened_rows:
        row_text = row.get("rowText") or row.get("rowTitle") or row.get("name")
        if singstat_row_matches(row_text, patterns):
            matched_row = row
            break

    if matched_row is None and spec["factor"] == "headline":
        for row in flattened_rows:
            row_text = row.get("rowText") or row.get("rowTitle") or row.get("name")
            if "all" in str(row_text).lower() and "item" in str(row_text).lower():
                matched_row = row
                break

    if not matched_row:
        return pd.DataFrame(), latest_data_upload, "not available from SingStat CPI source", None

    records = []
    columns = flatten_singstat_columns(
        matched_row.get("columns", []) or matched_row.get("column", [])
    )

    for column in columns:
        date_value = parse_month_text(column.get("key") or column.get("name"))
        if date_value is None:
            continue
        records.append({"date": date_value, "index_value": column.get("value")})

    data = calculate_yoy_from_index(records)
    if data.empty:
        return pd.DataFrame(), latest_data_upload, "empty SingStat response", None

    row_label = matched_row.get("rowText") or matched_row.get("rowTitle") or "SingStat CPI"
    return data, latest_data_upload, "ok", row_label


def fetch_thailand_moc_series(spec):
    config = THAILAND_MOC_ENDPOINTS.get(spec["factor"])
    if not config:
        return pd.DataFrame(), pd.NaT, "not available from Thailand MOC source", None

    start_year = START_DATE.year - 1
    end_year = pd.Timestamp.today().year
    params_dict = {
        "index_id": "0000000000000000",
        "from_year": start_year,
        "to_year": end_year,
    }
    if config["endpoint"] == "cpig-indexes":
        params_dict["region_id"] = "5"

    params = urlencode(params_dict)
    url = f"{THAILAND_MOC_API_BASE}/{config['endpoint']}?{params}"

    payload, latest_data_upload = fetch_json(url)

    if isinstance(payload, list):
        rows = payload
    else:
        rows = (
            payload.get("data")
            or payload.get("Data")
            or payload.get("result")
            or payload.get("results")
            or []
        )

    records = []
    yoy_records = []
    for row in rows:
        year = (
            row.get("year")
            or row.get("price_year")
            or row.get("index_year")
            or row.get("period_year")
        )
        month = row.get("month") or row.get("month_no") or row.get("period")
        index_value = (
            row.get("index")
            or row.get("index_value")
            or row.get("value")
            or row.get("price_index")
        )
        if not year or not month:
            continue
        year_value = normalize_thai_year(year)
        month_value = normalize_month_number(month)
        if year_value is None or month_value is None:
            continue
        date_value = parse_month(f"{year_value:04d}-{month_value:02d}")
        if row.get("yoy") is not None:
            yoy_records.append({"date": date_value, "value": row.get("yoy")})
        records.append({"date": date_value, "index_value": index_value})

    if yoy_records:
        data = pd.DataFrame(yoy_records)
        data["value"] = pd.to_numeric(data["value"], errors="coerce")
        data = data[data["date"] >= START_DATE].dropna(subset=["date", "value"])
    else:
        data = calculate_yoy_from_index(records)
    if data.empty:
        return pd.DataFrame(), latest_data_upload, "empty Thailand MOC response", None

    return data, latest_data_upload, "ok", config["label"]


def get_eurostat_series(country_name, geo, spec):
    try:
        source = "Eurostat API"
        try:
            long, latest_data_upload = fetch_eurostat_api_series(geo, spec)
        except Exception:
            source = "eurostat package fallback"
            long, latest_data_upload = fetch_eurostat_package_series(geo, spec)

        if long.empty:
            return pd.DataFrame(), build_status(country_name, spec, "empty response")

        long = long[long["date"] >= START_DATE].copy()
        long["country"] = country_name
        long["geo"] = geo
        long["factor"] = spec["factor"]
        long["factor_label"] = spec["factor_label"]
        long["indicator"] = spec["indicator"]
        long["coicop"] = spec["coicop"]
        long["coicop18"] = spec["coicop18"]
        long["latest_data_upload"] = latest_data_upload
        source_dataset = (
            PRIMARY_EUROSTAT_DATASET
            if source == "Eurostat API"
            else FALLBACK_EUROSTAT_DATASET
        )
        long["source"] = f"{source} {source_dataset}"

        return long, build_status(
            country_name,
            spec,
            "ok" if len(long) > 0 else "no data in last 10 years",
            len(long),
            long["date"].min() if len(long) > 0 else None,
            long["date"].max() if len(long) > 0 else None,
            latest_data_upload,
        )

    except Exception as exc:
        return pd.DataFrame(), build_status(country_name, spec, f"error: {exc}")


def get_imf_series(country_name, country_code, spec):
    try:
        long, latest_data_upload, status = fetch_imf_series(country_code, spec)

        if long.empty:
            if spec.get("imf_indicator"):
                return get_imf_series(country_name, country_code, spec)

            return pd.DataFrame(), build_status(
                country_name,
                spec,
                status,
                latest_data_upload=latest_data_upload,
            )

        indicator = spec["imf_indicator_label"] or spec["indicator"]
        long["country"] = country_name
        long["geo"] = country_code
        long["factor"] = spec["factor"]
        long["factor_label"] = spec["factor_label"]
        long["indicator"] = indicator
        long["coicop"] = spec["coicop"]
        long["coicop18"] = spec["coicop18"]
        long["latest_data_upload"] = latest_data_upload
        long["source"] = "IMF CPI CompactData"

        return long, build_status(
            country_name,
            {**spec, "indicator": indicator},
            "ok",
            len(long),
            long["date"].min(),
            long["date"].max(),
            latest_data_upload,
        )

    except Exception as exc:
        return pd.DataFrame(), build_status(country_name, spec, f"error: {exc}")


def get_local_official_series(country_name, country_code, spec, cache_hour):
    try:
        if country_name == "Singapore":
            long, latest_data_upload, status, indicator_label = fetch_singapore_official_series(
                spec,
                cache_hour,
            )
            source = "SingStat Table Builder API"
        elif country_name == "Thailand":
            long, latest_data_upload, status, indicator_label = fetch_thailand_moc_series(spec)
            source = "Thailand Ministry of Commerce Open Data API"
        else:
            return get_imf_series(country_name, country_code, spec)

        if long.empty:
            return pd.DataFrame(), build_status(
                country_name,
                spec,
                status,
                latest_data_upload=latest_data_upload,
            )

        indicator = indicator_label or spec["indicator"]
        long["country"] = country_name
        long["geo"] = country_code
        long["factor"] = spec["factor"]
        long["factor_label"] = spec["factor_label"]
        long["indicator"] = indicator
        long["coicop"] = spec["coicop"]
        long["coicop18"] = spec["coicop18"]
        long["latest_data_upload"] = latest_data_upload
        long["source"] = source

        return long, build_status(
            country_name,
            {**spec, "indicator": indicator},
            "ok",
            len(long),
            long["date"].min(),
            long["date"].max(),
            latest_data_upload,
        )

    except Exception:
        return get_imf_series(country_name, country_code, spec)


def get_imf_series(country_name, country_code, spec):
    try:
        source_name = "IMF CPI/IFS CompactData"
        long, latest_data_upload, status = fetch_imf_series(country_code, spec)

        if long.empty and spec["factor"] == "headline":
            source_name = "World Bank FP.CPI.TOTL.ZG"
            long, latest_data_upload, status = fetch_world_bank_headline_series(
                country_code,
                spec,
            )

        if long.empty:
            return pd.DataFrame(), build_status(
                country_name,
                spec,
                status,
                latest_data_upload=latest_data_upload,
            )

        indicator = spec["imf_indicator_label"] or spec["indicator"]
        long["country"] = country_name
        long["geo"] = country_code
        long["factor"] = spec["factor"]
        long["factor_label"] = spec["factor_label"]
        long["indicator"] = indicator
        long["coicop"] = spec["coicop"]
        long["coicop18"] = spec["coicop18"]
        long["latest_data_upload"] = latest_data_upload
        long["source"] = source_name

        return long, build_status(
            country_name,
            {**spec, "indicator": indicator},
            "ok",
            len(long),
            long["date"].min(),
            long["date"].max(),
            latest_data_upload,
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
            if country_name in EUROSTAT_COUNTRIES:
                df, status = get_eurostat_series(country_name, geo, spec)
            elif country_name in {"Thailand", "Singapore"}:
                df, status = get_local_official_series(country_name, geo, spec, cache_day)
            else:
                df, status = get_imf_series(country_name, geo, spec)

            if country_name not in EUROSTAT_COUNTRIES and spec.get("imf_indicator"):
                fallback_df, fallback_status = get_imf_series(country_name, geo, spec)
                df = combine_series(df, fallback_df)
                if not df.empty and status.get("status") != "ok":
                    status = fallback_status

            if country_name in OFFICIAL_HEADLINE_RELEASES and spec["factor"] == "headline":
                if df.empty or df["date"].min() > START_DATE + pd.DateOffset(months=6):
                    fallback_df, fallback_status = get_imf_series(country_name, geo, spec)
                    df = combine_series(df, fallback_df)
                    if not fallback_df.empty:
                        status = fallback_status

                df = enrich_with_official_headline_releases(country_name, spec, df)
                if not df.empty:
                    status = build_status(
                        country_name,
                        {**spec, "indicator": df["indicator"].iloc[-1]},
                        "ok",
                        len(df),
                        df["date"].min(),
                        df["date"].max(),
                        df["latest_data_upload"].max(),
                    )

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
            indicators=("indicator", lambda values: " | ".join(sorted(set(values)))),
            coicops=("coicop", lambda values: " | ".join(sorted(set(values)))),
            latest_data_upload=("latest_data_upload", "max"),
        )
        .rename(columns={"month": "date"})
        .sort_values(["factor", "country", "date"])
    )


def unavailable_country_notes(status_df):
    if status_df.empty:
        return {}

    notes = {}
    unavailable = status_df[status_df["status"] != "ok"].copy()

    if unavailable.empty:
        return notes

    for factor, group in unavailable.groupby("factor"):
        countries = sorted(group["country"].dropna().unique())
        if countries:
            notes[factor] = ", ".join(countries)

    return notes


def indicator_names_for_factor(factor, chart_df=None):
    if chart_df is not None and not chart_df.empty and "indicators" in chart_df.columns:
        indicator_values = chart_df.loc[chart_df["factor"] == factor, "indicators"].dropna()
        indicators = []
        for value in indicator_values:
            indicators.extend([item.strip() for item in str(value).split(" | ") if item.strip()])
        indicators = sorted(set(indicators))
        if indicators:
            return ", ".join(indicators)

    indicators = [spec["indicator"] for spec in SERIES if spec["factor"] == factor]
    return ", ".join(indicators)


def icon_svg(icon_name):
    icons = {
        "bar-chart-3": """
            <path d="M3 3v18h18"/>
            <path d="M18 17V9"/>
            <path d="M13 17V5"/>
            <path d="M8 17v-3"/>
        """,
        "component": """
            <path d="M5.5 8.5 9 5l3.5 3.5L9 12z"/>
            <path d="m12 12 3.5-3.5L19 12l-3.5 3.5z"/>
            <path d="M5.5 15.5 9 12l3.5 3.5L9 19z"/>
        """,
        "wrench": """
            <path d="M14.7 6.3a4 4 0 0 0-5 5L3.8 17.2a2.1 2.1 0 0 0 3 3l5.9-5.9a4 4 0 0 0 5-5l-2.6 2.6-3-3z"/>
        """,
        "heart-pulse": """
            <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 1 0-7.8 7.8l1 1"/>
            <path d="M4 13h4l2-4 4 8 2-4h4"/>
            <path d="m18.8 13.4-6.8 6.8-3-3"/>
        """,
        "zap": """
            <path d="M13 2 4 14h7l-1 8 9-12h-7z"/>
        """,
        "home": """
            <path d="m3 11 9-8 9 8"/>
            <path d="M5 10v10h14V10"/>
            <path d="M9 20v-6h6v6"/>
        """,
        "brick-wall": """
            <path d="M3 6h18v12H3z"/>
            <path d="M3 12h18"/>
            <path d="M9 6v6"/>
            <path d="M15 12v6"/>
        """,
        "hard-hat": """
            <path d="M2 18h20"/>
            <path d="M4 18a8 8 0 0 1 16 0"/>
            <path d="M9 10v8"/>
            <path d="M15 10v8"/>
            <path d="M8 6h8"/>
        """,
        "washing-machine": """
            <rect x="5" y="2" width="14" height="20" rx="2"/>
            <circle cx="12" cy="14" r="5"/>
            <path d="M8 6h.01"/>
            <path d="M11 6h5"/>
        """,
    }
    paths = icons.get(icon_name, icons["bar-chart-3"])
    return f"""
    <svg class="chart-icon-svg" viewBox="0 0 24 24" aria-hidden="true">
        {paths}
    </svg>
    """


def latest_upload_for_factor(chart_df, factor):
    if chart_df.empty or "latest_data_upload" not in chart_df.columns:
        return "Not available"

    latest_upload = pd.to_datetime(
        chart_df.loc[chart_df["factor"] == factor, "latest_data_upload"],
        errors="coerce",
    ).max()

    if pd.isna(latest_upload):
        return "Not available"

    return latest_upload.strftime("%Y-%m-%d")


def latest_visible_observation_for_factor(chart_df, factor):
    if chart_df.empty:
        return "Not available"

    latest_observation = pd.to_datetime(
        chart_df.loc[chart_df["factor"] == factor, "date"],
        errors="coerce",
    ).max()

    if pd.isna(latest_observation):
        return "Not available"

    return latest_observation.strftime("%B %Y")


def chart_header_html(factor, chart_df, unavailable_notes):
    icon = FACTOR_ICONS.get(factor, "chart")
    unavailable_note = ""
    if factor in unavailable_notes:
        unavailable_note = (
            '<p class="availability-note">'
            f"(Data not available for: {escape(unavailable_notes[factor])})"
            "</p>"
        )

    return f"""
    <div class="chart-heading">
        <span class="chart-icon">{icon_svg(icon)}</span>
        <div class="chart-title-block">
            <h2>{escape(FACTORS[factor])}</h2>
            <p>Indicator name: {escape(indicator_names_for_factor(factor, chart_df))}</p>
            <p>Latest visible observation: {escape(latest_visible_observation_for_factor(chart_df, factor))}</p>
            {unavailable_note}
        </div>
    </div>
    <div class="chart-rule"></div>
    """


def make_plot(chart_df, factor):
    sub = chart_df[chart_df["factor"] == factor].copy()
    if sub.empty:
        return f'<p class="notice">{escape(FACTORS[factor])}: no data available.</p>'

    fig = px.line(
        sub,
        x="date",
        y="value",
        color="country",
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
    fig.update_layout(
        height=500,
        hovermode="x unified",
        legend_title_text="Country",
        margin={"t": 18, "r": 24, "b": 48, "l": 64},
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def describe_direction(delta):
    if pd.isna(delta):
        return "no comparable prior data"
    if delta > 0:
        return f"up by {delta:.2f} pp"
    if delta < 0:
        return f"down by {abs(delta):.2f} pp"
    return "unchanged"


def claims_lens_for_factor(factor):
    if factor in {"parts", "labor", "fuel_energy"}:
        return "motor claims severity"
    if factor in {
        "property_repair",
        "property_materials",
        "property_services",
        "household_equipment",
    }:
        return "property claims severity"
    if factor == "medical":
        return "bodily injury and assistance costs"
    return "overall claims inflation context"


def trend_label(latest_value, previous_value, three_month_average):
    monthly_delta = latest_value - previous_value if not pd.isna(previous_value) else np.nan
    three_month_gap = (
        latest_value - three_month_average
        if not pd.isna(three_month_average)
        else np.nan
    )

    if not pd.isna(three_month_gap) and three_month_gap >= 1.5:
        return "clear upward breakout"
    if not pd.isna(three_month_gap) and three_month_gap <= -1.5:
        return "meaningful downward break"
    if not pd.isna(monthly_delta) and monthly_delta >= 0.7:
        return "near-term acceleration"
    if not pd.isna(monthly_delta) and monthly_delta <= -0.7:
        return "near-term easing"
    if latest_value >= 5:
        return "persistently elevated inflation"
    if latest_value < 0:
        return "deflationary pressure"
    return "stable recent trend"


def claims_implication(factor, latest_value, label):
    if "upward" in label or "acceleration" in label or latest_value >= 5:
        if factor in {"parts", "labor"}:
            return "This points to renewed pressure on motor repair invoices and reserve assumptions."
        if factor == "fuel_energy":
            return "This can feed into towing, mobility, supplier logistics and some repair overheads."
        if factor in {"property_repair", "property_materials", "property_services"}:
            return "This suggests pressure on property repair estimates, contractor rates and claim settlement costs."
        if factor == "household_equipment":
            return "This may lift replacement costs for household contents and selected property claim items."
        if factor == "medical":
            return "This can affect bodily injury, medical assistance and related service costs."
        return "This raises the general claims inflation backdrop."

    if "downward" in label or "easing" in label or latest_value < 0:
        if factor in {"parts", "labor"}:
            return "This reduces near-term pressure on motor repair severity, though supplier pricing should still be monitored."
        if factor in {"property_repair", "property_materials", "property_services"}:
            return "This may ease pressure on property repair budgets if the trend persists."
        if factor == "fuel_energy":
            return "This may reduce pressure on logistics-sensitive claim costs."
        return "This suggests a softer claims inflation environment."

    return "This looks broadly stable, so recent assumptions may not need immediate adjustment."


def generated_chart_insight_html(chart_df, factor):
    sub = chart_df[chart_df["factor"] == factor].copy()
    if sub.empty:
        return """
        <div class="chart-insight">
            <strong>Insights</strong>
            <p>No data is available for this chart in the selected range.</p>
        </div>
        """

    insights = []
    lens = claims_lens_for_factor(factor)
    for country, country_df in sub.groupby("country"):
        country_df = country_df.sort_values("date")
        latest = country_df.iloc[-1]
        latest_date = latest["date"]
        latest_value = latest["value"]

        previous_rows = country_df[country_df["date"] < latest_date]
        previous_value = previous_rows.iloc[-1]["value"] if not previous_rows.empty else np.nan

        three_month_rows = previous_rows.tail(3)
        three_month_average = (
            three_month_rows["value"].mean()
            if len(three_month_rows) > 0
            else np.nan
        )

        trailing_start = latest_date - pd.DateOffset(months=12)
        trailing_rows = country_df[
            (country_df["date"] >= trailing_start)
            & (country_df["date"] < latest_date)
        ]
        twelve_month_value = trailing_rows.iloc[0]["value"] if not trailing_rows.empty else np.nan

        monthly_delta = latest_value - previous_value if not pd.isna(previous_value) else np.nan
        trailing_delta = (
            latest_value - twelve_month_value
            if not pd.isna(twelve_month_value)
            else np.nan
        )
        label = trend_label(latest_value, previous_value, three_month_average)
        implication = claims_implication(factor, latest_value, label)

        insights.append(
            "<li>"
            f"<strong>{escape(str(country))}</strong> - "
            f"{label} for {lens}: {latest_value:.2f}% in {latest_date.strftime('%B %Y')}, "
            f"{describe_direction(monthly_delta)} vs prior month. {implication}"
            "</li>"
        )

    return f"""
    <div class="chart-insight">
        <strong>Insights</strong>
        <ul>{"".join(insights)}</ul>
    </div>
    """


def chart_insight_html(chart_df, factor, generate_insights):
    if generate_insights:
        return generated_chart_insight_html(chart_df, factor)

    return """
    <div class="chart-insight">
        <strong>Insights</strong>
        <p>Insights have not been generated yet. Use the Generate insights button after loading the charts.</p>
    </div>
    """


def selected_countries_from_request():
    selected = request.args.getlist("countries") or ["Poland"]
    selected = [country for country in selected if country in COUNTRIES]

    return tuple(selected)


def parse_month(value):
    if not value:
        return None

    parsed = pd.to_datetime(f"{value}-01", errors="coerce")
    if pd.isna(parsed):
        return None

    return parsed.to_period("M").to_timestamp()


def parse_month_text(value):
    parsed = pd.to_datetime(str(value), errors="coerce")
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
    default_start = max(available_min, START_DATE)

    use_custom_range = request.args.get("range_changed") == "1"
    requested_start = parse_month(request.args.get("start_month")) if use_custom_range else None
    requested_end = parse_month(request.args.get("end_month")) if use_custom_range else None
    start_month = requested_start if requested_start is not None else default_start
    end_month = requested_end if requested_end is not None else default_end

    start_month = max(start_month, available_min)
    end_month = min(end_month, available_max)

    if start_month > end_month:
        start_month, end_month = default_start, default_end

    return start_month, end_month, available_min, available_max, use_custom_range


def default_month_range():
    end_month = pd.Timestamp.today().normalize().to_period("M").to_timestamp()
    start_month = START_DATE
    return start_month, end_month


def filter_to_month_range(raw, start_month, end_month):
    return raw[(raw["month"] >= start_month) & (raw["month"] <= end_month)].copy()


def render_options(
    selected,
    start_month,
    end_month,
    min_month=None,
    max_month=None,
    use_custom_range=False,
):
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
        <input type="hidden" id="range-changed" name="range_changed" value="{"1" if use_custom_range else "0"}">
    </div>
    """

    return "\n".join(checkbox_html), range_html


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


@app.route("/send-report-pdf", methods=["POST"])
def send_report_pdf():
    payload = request.get_json(silent=True) or {}
    recipient = str(payload.get("email", "")).strip()
    pdf_data = str(payload.get("pdf", ""))

    if not recipient or "@" not in recipient:
        return jsonify({"ok": False, "message": "Enter a valid recipient email."}), 400

    if not pdf_data.startswith("data:application/pdf;base64,"):
        return jsonify({"ok": False, "message": "PDF payload is missing."}), 400

    config = smtp_config()
    if not smtp_is_configured(config):
        return jsonify(
            {
                "ok": False,
                "message": (
                    "Email sending is not configured. Set SMTP_HOST, SMTP_PORT, "
                    "SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM in Vercel."
                ),
            }
        ), 500

    pdf_bytes = base64.b64decode(pdf_data.split(",", 1)[1])
    message = EmailMessage()
    message["Subject"] = "Global Claims CPI Dashboard report"
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content("Please find the Global Claims CPI Dashboard report attached.")
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename="global-claims-cpi-dashboard.pdf",
    )

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=30) as server:
            if config["use_tls"]:
                server.starttls()
            server.login(config["username"], config["password"])
            server.send_message(message)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Email failed: {exc}"}), 500

    return jsonify({"ok": True, "message": "PDF sent."})


@app.route("/")
def index():
    selected = selected_countries_from_request()
    if not selected:
        selected = tuple(COUNTRIES.keys())

    should_load = request.args.get("load") == "1"
    generate_insights = should_load and request.args.get("generate_insights") == "1"
    default_start_month, default_end_month = default_month_range()
    checkbox_html, range_html = render_options(
        selected,
        default_start_month,
        default_end_month,
    )

    if not should_load:
        charts_html = (
            '<p class="notice">Select countries and click the button to fetch inflation data.</p>'
        )
        data_table_html = ""
        latest_html = ""
        metrics_html = ""
        status_html = ""
    else:
        raw, status_df = load_data(selected, datetime.utcnow().strftime("%Y-%m-%d-%H"))
        status_view = status_df.assign(
            min_date=lambda df: pd.to_datetime(
                df["min_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
            max_date=lambda df: pd.to_datetime(
                df["max_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
            latest_data_upload=lambda df: pd.to_datetime(
                df["latest_data_upload"], errors="coerce"
            ).dt.strftime("%Y-%m-%d"),
        ).rename(
            columns={
                "country": "Country",
                "factor": "Factor",
                "indicator": "Indicator",
                "coicop": "COICOP",
                "coicop18": "COICOP18",
                "status": "Status",
                "n_rows": "Rows",
                "min_date": "First date",
                "max_date": "Latest date",
                "latest_data_upload": "Latest data upload",
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
            (
                start_month,
                end_month,
                available_min,
                available_max,
                use_custom_range,
            ) = requested_month_range(raw)
            checkbox_html, range_html = render_options(
                selected,
                start_month,
                end_month,
                available_min,
                available_max,
                use_custom_range,
            )

            visible_raw = filter_to_month_range(raw, start_month, end_month)
            chart_df = aggregate_for_charts(visible_raw)
            unavailable_notes = unavailable_country_notes(status_df)

            if chart_df.empty:
                charts_html = '<p class="notice">No data is available for the selected range.</p>'
                data_table_html = """
                <h2>Chart Data</h2>
                <p class="notice">No data is available for the selected range.</p>
                """
                latest_html = ""
            else:
                charts_html = "\n".join(
                    (
                        '<section class="chart-section">'
                        f"{chart_header_html(factor, chart_df, unavailable_notes)}"
                        f"{make_plot(chart_df, factor)}"
                        f"{chart_insight_html(chart_df, factor, generate_insights)}"
                        "</section>"
                    )
                    for factor in FACTORS
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
                <div><span>Visible range</span><strong>{format_month(start_month)} – {format_month(end_month)}</strong></div>
            </div>
            """

    return Markup(
        f"""
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Motor Inflation Dashboard</title>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js" integrity="sha512-YcsIPGdhPK4P/uRW6/sruonBLp93bYnxCq5E4fW3r6o0vWg8kh/Cl3nM4QL5BrM6aLlH2zJRghT+nagwOe0Hnw==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
            <style>
                :root {{
                    color-scheme: light;
                    --ink: #17202a;
                    --muted: #607080;
                    --line: #d9e0e7;
                    --panel: #fff6f6;
                    --accent: #d71920;
                    --accent-dark: #a80f17;
                    --accent-soft: #ffe3e4;
                    --selector-red: #d71920;
                    --selector-red-soft: #fff0f1;
                    --selector-red-line: #f0b8bb;
                }}
                * {{ box-sizing: border-box; }}
                body {{
                    margin: 0;
                    font-family: Inter, Segoe UI, Arial, sans-serif;
                    color: var(--ink);
                    background: white;
                }}
                header {{
                    position: relative;
                    padding: 32px clamp(20px, 5vw, 64px) 20px;
                    border-bottom: 3px solid var(--accent);
                }}
                h1 {{
                    margin: 0 0 8px;
                    font-size: clamp(28px, 4vw, 44px);
                    letter-spacing: 0;
                }}
                .brand-logo {{
                    position: absolute;
                    top: 24px;
                    right: clamp(20px, 5vw, 64px);
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    min-width: 86px;
                    min-height: 34px;
                    padding: 6px 12px;
                    border: 2px solid #d71920;
                    color: #d71920;
                    background: #ffffff;
                    font-size: 20px;
                    font-weight: 900;
                    line-height: 1;
                    letter-spacing: 0;
                }}
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
                    background: #ffffff;
                    border: 1px solid #f0b8bb;
                    border-radius: 8px;
                }}
                fieldset {{
                    border: 0;
                    padding: 0;
                    margin: 0 0 18px;
                }}
                legend {{
                    display: block;
                    font-weight: 700;
                    margin-bottom: 10px;
                }}
                .check {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    margin: 7px 0;
                    padding: 7px 9px;
                    color: var(--accent-dark);
                    background: var(--selector-red-soft);
                    border: 1px solid var(--selector-red-line);
                    border-radius: 6px;
                    font-weight: 600;
                }}
                .check input {{
                    accent-color: var(--selector-red);
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
                button:hover {{
                    background: var(--accent-dark);
                    border-color: var(--accent-dark);
                }}
                button:disabled {{
                    opacity: 0.48;
                    cursor: not-allowed;
                }}
                .export-tools {{
                    margin-top: 22px;
                    padding-top: 18px;
                    border-top: 1px solid #f0b8bb;
                }}
                .export-tools h2 {{
                    margin: 0 0 12px;
                    font-size: 16px;
                    letter-spacing: 0;
                }}
                .export-tools label {{
                    display: block;
                    margin-top: 12px;
                }}
                .export-tools label span {{
                    display: block;
                    margin-bottom: 6px;
                    font-size: 13px;
                    font-weight: 700;
                    color: var(--muted);
                }}
                .export-tools input[type="email"] {{
                    width: 100%;
                    min-height: 40px;
                    padding: 0 10px;
                    font: inherit;
                    border-radius: 6px;
                    border: 1px solid var(--line);
                }}
                #export-status {{
                    min-height: 18px;
                    margin: 10px 0 0;
                    color: var(--muted);
                    font-size: 13px;
                    line-height: 1.4;
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
                    display: flex;
                    margin-bottom: 16px;
                }}
                .metrics div {{
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px 10px;
                    border: 1px solid #f0b8bb;
                    border-radius: 6px;
                    background: var(--accent-soft);
                }}
                .metrics strong {{
                    display: inline;
                    font-size: 14px;
                    font-weight: 700;
                    margin-bottom: 0;
                }}
                .metrics span {{
                    color: var(--muted);
                    font-size: 13px;
                }}
                section {{
                    padding: 8px 0 22px;
                    border-bottom: 1px solid var(--line);
                }}
                .chart-section {{
                    padding-top: 22px;
                }}
                .chart-heading {{
                    display: flex;
                    align-items: flex-start;
                    gap: 14px;
                    margin-bottom: 10px;
                }}
                .chart-title-block h2 {{
                    margin: 0 0 4px;
                    font-size: 22px;
                    font-weight: 800;
                    letter-spacing: 0;
                }}
                .chart-title-block p {{
                    margin: 0;
                    color: var(--muted);
                    font-size: 13px;
                    line-height: 1.45;
                }}
                .chart-title-block .availability-note {{
                    margin-top: 2px;
                    font-size: 12px;
                    color: #8a4b4f;
                }}
                .chart-rule {{
                    height: 4px;
                    width: 100%;
                    margin: 12px 0 12px;
                    border-radius: 2px;
                    background: linear-gradient(90deg, var(--accent), #f6b1b5);
                }}
                .chart-icon {{
                    position: relative;
                    display: inline-grid;
                    place-items: center;
                    flex: 0 0 38px;
                    width: 38px;
                    height: 38px;
                    border: 1px solid #f0b8bb;
                    border-radius: 8px;
                    background: var(--accent-soft);
                    color: var(--accent);
                }}
                .chart-icon-svg {{
                    width: 22px;
                    height: 22px;
                    stroke: currentColor;
                    stroke-width: 2;
                    stroke-linecap: round;
                    stroke-linejoin: round;
                    fill: none;
                }}
                .notice {{
                    padding: 14px 16px;
                    border-radius: 8px;
                    background: var(--accent-soft);
                }}
                .chart-insight {{
                    margin: 10px 0 6px;
                    padding: 14px 16px;
                    border-left: 4px solid var(--accent);
                    border-radius: 6px;
                    background: #fff7f7;
                }}
                .chart-insight strong {{
                    display: block;
                    margin-bottom: 4px;
                    color: var(--accent-dark);
                    font-size: 14px;
                }}
                .chart-insight p {{
                    margin: 0;
                    color: var(--ink);
                    font-size: 14px;
                    line-height: 1.5;
                }}
                .chart-insight ul {{
                    margin: 0;
                    padding-left: 18px;
                    color: var(--ink);
                    font-size: 14px;
                    line-height: 1.5;
                }}
                .chart-insight li + li {{
                    margin-top: 4px;
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
                table.dataframe th {{ background: var(--accent-soft); }}
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
                    background: var(--accent-soft);
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
                    header {{
                        padding-top: 76px;
                    }}
                    .brand-logo {{
                        left: 20px;
                        right: auto;
                    }}
                    main {{ grid-template-columns: 1fr; }}
                    aside {{ position: static; }}
                    .metrics {{ grid-template-columns: 1fr; }}
                }}
            </style>
        </head>
        <body>
            <header>
                <div class="brand-logo" aria-label="ERGO logo">ERGO</div>
                <h1>Global Claims CPI Dashboard</h1>
            </header>
            <main>
                <aside>
                    <form method="get" id="data-form">
                        <fieldset>
                            <legend>Countries</legend>
                            {checkbox_html}
                        </fieldset>
                        {range_html}
                        <input type="hidden" name="load" value="1">
                        <button type="submit" id="load-button">Fetch data and show charts</button>
                        <button
                            type="submit"
                            id="generate-insights-button"
                            name="generate_insights"
                            value="1"
                            {"disabled" if not should_load else ""}
                        >
                            Generate insights
                        </button>
                        <div class="loader" id="loader" role="status" aria-live="polite">
                            <span class="spinner" aria-hidden="true"></span>
                            <span>Fetching inflation data...</span>
                        </div>
                    </form>
                    <div class="export-tools">
                        <h2>Export report</h2>
                        <button type="button" id="download-pdf-button">Download PDF</button>
                        <label>
                            <span>Email address</span>
                            <input type="email" id="report-email" placeholder="name@example.com">
                        </label>
                        <button type="button" id="send-pdf-button">Send PDF by email</button>
                        <p id="export-status" role="status" aria-live="polite"></p>
                    </div>
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
                const insightsButton = document.getElementById("generate-insights-button");
                const rangeChanged = document.getElementById("range-changed");
                const downloadPdfButton = document.getElementById("download-pdf-button");
                const sendPdfButton = document.getElementById("send-pdf-button");
                const reportEmail = document.getElementById("report-email");
                const exportStatus = document.getElementById("export-status");

                const pdfOptions = {{
                    margin: 8,
                    filename: "global-claims-cpi-dashboard.pdf",
                    image: {{ type: "jpeg", quality: 0.98 }},
                    html2canvas: {{ scale: 2, useCORS: true, logging: false }},
                    jsPDF: {{ unit: "mm", format: "a4", orientation: "landscape" }},
                    pagebreak: {{ mode: ["avoid-all", "css", "legacy"] }}
                }};

                function setExportStatus(message) {{
                    exportStatus.textContent = message;
                }}

                function reportElement() {{
                    return document.body;
                }}

                async function buildPdfDataUri() {{
                    return await html2pdf()
                        .set(pdfOptions)
                        .from(reportElement())
                        .outputPdf("datauristring");
                }}

                form.addEventListener("submit", (event) => {{
                    loader.classList.add("is-active");
                    const submitter = event.submitter;
                    if (submitter === insightsButton) {{
                        insightsButton.setAttribute("aria-busy", "true");
                        insightsButton.textContent = "Generating...";
                    }} else {{
                        button.setAttribute("aria-busy", "true");
                        button.textContent = "Fetching...";
                    }}
                }});

                downloadPdfButton.addEventListener("click", async () => {{
                    setExportStatus("Preparing PDF...");
                    downloadPdfButton.setAttribute("aria-busy", "true");
                    await html2pdf().set(pdfOptions).from(reportElement()).save();
                    downloadPdfButton.removeAttribute("aria-busy");
                    setExportStatus("PDF downloaded.");
                }});

                sendPdfButton.addEventListener("click", async () => {{
                    const email = reportEmail.value.trim();
                    if (!email) {{
                        setExportStatus("Enter an email address first.");
                        return;
                    }}

                    setExportStatus("Preparing and sending PDF...");
                    sendPdfButton.setAttribute("aria-busy", "true");

                    try {{
                        const pdf = await buildPdfDataUri();
                        const response = await fetch("/send-report-pdf", {{
                            method: "POST",
                            headers: {{ "Content-Type": "application/json" }},
                            body: JSON.stringify({{ email, pdf }})
                        }});
                        const result = await response.json();
                        setExportStatus(result.message || (response.ok ? "PDF sent." : "Sending failed."));
                    }} catch (error) {{
                        setExportStatus(`Sending failed: ${{error.message}}`);
                    }} finally {{
                        sendPdfButton.removeAttribute("aria-busy");
                    }}
                }});

                document.querySelectorAll('input[type="month"]').forEach((input) => {{
                    input.addEventListener("change", () => {{
                        rangeChanged.value = "1";
                    }});
                }});

                window.addEventListener("load", () => {{
                    const chartDataTable = document.querySelector(".data-table");
                    if (chartDataTable) {{
                        chartDataTable.scrollLeft = chartDataTable.scrollWidth;
                    }}
                }});
            </script>
        </body>
        </html>
        """
    )
