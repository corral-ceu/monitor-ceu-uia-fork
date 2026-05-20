import numpy as np
import pandas as pd
import requests
import streamlit as st
from io import BytesIO
from io import StringIO


# ============================================================
# Helper genérico (BCRA Monetarias) — PAGINADO ROBUSTO
# ============================================================
@st.cache_data(ttl=60 * 60)
def get_monetaria_serie(id_variable: int) -> pd.DataFrame:
    """
    Descarga series del endpoint Monetarias/{id_variable}.
    Devuelve columnas: Date, value
    Paginación robusta:
      - Si metadata.count existe: usa count.
      - Si no existe: corta cuando la página viene “corta” (< Limit).
    """
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id_variable}"
    params = {"Limit": 1000, "Offset": 0}
    data = []
    last_err = None

    for _ in range(3):
        try:
            params["Offset"] = 0
            data = []

            while True:
                r = requests.get(url, params=params, timeout=20, verify=False)
                r.raise_for_status()
                payload = r.json()

                results = payload.get("results", [])
                if not results:
                    break

                detalle = results[0].get("detalle", [])
                if not detalle:
                    break

                data.extend(detalle)

                meta = payload.get("metadata", {}).get("resultset", {}) or {}
                count = meta.get("count")

                params["Offset"] += params["Limit"]

                if count is not None:
                    # corte con count
                    if params["Offset"] >= count:
                        break
                else:
                    # corte por página corta
                    if len(detalle) < params["Limit"]:
                        break

            break  # ok
        except requests.exceptions.RequestException as e:
            last_err = str(e)

    if not data:
        # No rompemos: devolvemos vacío (las páginas lo manejan),
        # pero dejamos el error visible en la app (no en consola).
        if last_err:
            st.error(f"Error BCRA Monetarias/{id_variable}: {last_err}")
        return pd.DataFrame(columns=["Date", "value"])

    df = pd.DataFrame(data)
    df["Date"] = pd.to_datetime(df.get("fecha"), errors="coerce")
    df["value"] = pd.to_numeric(df.get("valor"), errors="coerce")

    return (
        df[["Date", "value"]]
        .dropna()
        .drop_duplicates(subset=["Date"])
        .sort_values("Date")
        .reset_index(drop=True)
    )


# ============================================================
# TC mayorista (A3500)
# ============================================================
@st.cache_data(ttl=60 * 60)
def get_a3500() -> pd.DataFrame:
    """
    A3500: intentamos id=5 (como venías usando).
    Si no trae nada (por cambios del BCRA / entorno), fallback a 84.
    Devuelve columnas: Date, FX
    """
    df = get_monetaria_serie(5)

    # fallback típico que suele ser A3500 en muchos códigos
    if df.empty:
        df = get_monetaria_serie(84)

    if df.empty:
        return pd.DataFrame(columns=["Date", "FX"])

    out = df.rename(columns={"value": "FX"}).copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["FX"] = pd.to_numeric(out["FX"], errors="coerce")

    return (
        out[["Date", "FX"]]
        .dropna()
        .drop_duplicates(subset=["Date"])
        .sort_values("Date")
        .reset_index(drop=True)
    )


# ============================================================
# REM
# ============================================================
@st.cache_data(ttl=60 * 60)
def get_rem_last() -> pd.DataFrame:
    url = (
        "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/"
        "historico-relevamiento-expectativas-mercado.xlsx"
    )
    df = pd.read_excel(url, sheet_name="Base de Datos Completa", skiprows=1)

    rem = df.loc[
        (df["Variable"] == "Precios minoristas (IPC nivel general; INDEC)")
        & (df["Referencia"] == "var. % mensual")
    ].copy()

    latest = rem["Fecha de pronóstico"].max()

    return (
        rem.loc[rem["Fecha de pronóstico"] == latest]
        .sort_values("Período")
        .tail(24)
        .rename(columns={"Período": "Date", "Mediana": "v_m_REM"})
        .assign(Date=lambda x: pd.to_datetime(x["Date"], errors="coerce"))
        .reset_index(drop=True)
    )


# ============================================================
# IPC INDEC (para macro_precios.py)
# ============================================================
@st.cache_data(ttl=12 * 60 * 60)
def get_ipc_indec_full() -> pd.DataFrame:
    url = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv"
    try:
        df = pd.read_csv(url, sep=";", decimal=",", encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(url, sep=";", decimal=",", encoding="latin1")

    # ✅ CLAVE: mantener Codigo como string (preserva B/S/Núcleo/Regulados/Estacional)
    df["Codigo"] = df["Codigo"].astype(str).str.strip()

    # ✅ versión numérica para filtros tipo Codigo == 0
    df["Codigo_num"] = pd.to_numeric(df["Codigo"], errors="coerce")

    df["Periodo"] = pd.to_datetime(df["Periodo"].astype(str), format="%Y%m", errors="coerce")

    for c in ["Descripcion", "Clasificador", "Region"]:
        df[c] = df[c].astype(str).str.strip()

    for c in ["Indice_IPC", "v_m_IPC", "v_i_a_IPC"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Periodo"]).sort_values("Periodo").reset_index(drop=True)


@st.cache_data(ttl=12 * 60 * 60)
def get_ipc_nacional_nivel_general() -> pd.DataFrame:
    df = get_ipc_indec_full()

    tmp = (
        df[(df["Codigo_num"] == 0) & (df["Region"] == "Nacional")]
        .dropna(subset=["v_m_IPC"])
        .rename(columns={"Periodo": "Date"})
        .sort_values("Date")
    )
    tmp["Period"] = tmp["Date"].dt.to_period("M")
    tmp["v_m_CPI"] = tmp["v_m_IPC"] / 100.0  # % -> decimal

    return (
        tmp[["Date", "v_m_CPI", "Period"]]
        .drop_duplicates("Period")
        .sort_values("Period")
        .reset_index(drop=True)
    )


# ============================================================
# IPC BCRA (id=27) para bandas
# ============================================================
@st.cache_data(ttl=12 * 60 * 60)
def get_ipc_bcra() -> pd.DataFrame:
    """
    IPC (% mensual) desde BCRA Monetarias idVariable=27.
    Devuelve v_m_CPI en DECIMAL (ej 2.8% -> 0.028).
    """
    df = get_monetaria_serie(27)
    if df.empty:
        return pd.DataFrame(columns=["Date", "v_m_CPI", "Period"])

    df = df.rename(columns={"value": "v_m_pct"}).copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["v_m_pct"] = pd.to_numeric(df["v_m_pct"], errors="coerce")
    df = df.dropna(subset=["Date", "v_m_pct"]).sort_values("Date")

    df["Period"] = df["Date"].dt.to_period("M")
    df["v_m_CPI"] = df["v_m_pct"] / 100.0

    return (
        df[["Date", "v_m_CPI", "Period"]]
        .drop_duplicates("Period")
        .sort_values("Period")
        .reset_index(drop=True)
    )


# ============================================================
# Bandas
# ============================================================
def build_bands_2025(start, end, lower0, upper0) -> pd.DataFrame:
    g_up = (1 + 0.01) ** (1 / 30)
    g_dn = (1 - 0.01) ** (1 / 30)

    dates = pd.date_range(start, end, freq="D")
    t = np.arange(len(dates))

    return pd.DataFrame({"Date": dates, "lower": lower0 * (g_dn**t), "upper": upper0 * (g_up**t)})


def build_bands_2026(bands_2025: pd.DataFrame, rem: pd.DataFrame, ipc: pd.DataFrame) -> pd.DataFrame:
    """
    ipc debe tener Period y v_m_CPI en DECIMAL.
    rem trae v_m_REM en %.
    """
    rem_m = rem.assign(Period=rem["Date"].dt.to_period("M"))[["Period", "v_m_REM"]]
    m = ipc.merge(rem_m, on="Period", how="outer").sort_values("Period")
    m["v_m_dec"] = np.where(m["v_m_CPI"].notna(), m["v_m_CPI"], m["v_m_REM"] / 100)

    end_month = m.loc[m["v_m_REM"].notna(), "Period"].max() + 2
    b = pd.DataFrame({"Period": pd.period_range("2026-01", end_month, freq="M")})
    b["ref"] = b["Period"] - 2
    b = b.merge(m[["Period", "v_m_dec"]].rename(columns={"Period": "ref"}), on="ref", how="left")

    lower0 = bands_2025.loc[bands_2025["Date"] == "2025-12-31", "lower"].iloc[0]
    upper0 = bands_2025.loc[bands_2025["Date"] == "2025-12-31", "upper"].iloc[0]

    cal = pd.DataFrame({"Date": pd.date_range("2026-01-01", b["Period"].max().to_timestamp("M"), freq="D")})
    cal["Period"] = cal["Date"].dt.to_period("M")
    cal = cal.merge(b[["Period", "v_m_dec"]], on="Period", how="left")

    r_d = (1 + cal["v_m_dec"]) ** (1 / 30) - 1
    cal["lower"] = lower0 * (1 - r_d).cumprod()
    cal["upper"] = upper0 * (1 + r_d).cumprod()

    return cal[["Date", "lower", "upper"]]


# ============================================================
# ITCRM (Excel BCRA) - ITCRM + bilaterales
# ============================================================
@st.cache_data(ttl=12 * 60 * 60)
def get_itcrm_excel_long() -> pd.DataFrame:
    """
    Descarga ITCRMSerie.xlsx del BCRA y devuelve formato largo:
    columnas: Date, Serie, Value
    """
    url = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/ITCRMSerie.xlsx"
    sheet = "ITCRM y bilaterales"

    r = requests.get(url, timeout=60)
    r.raise_for_status()

    df = pd.read_excel(
        BytesIO(r.content),
        sheet_name=sheet,
        header=1,
        engine="openpyxl",
    )

    df = df.rename(columns={df.columns[0]: "Date"}).copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])

    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    value_cols = [c for c in df.columns if c != "Date"]
    long_df = (
        df.melt(id_vars=["Date"], value_vars=value_cols, var_name="Serie", value_name="Value")
        .dropna(subset=["Value"])
        .sort_values(["Serie", "Date"])
        .reset_index(drop=True)
    )
    return long_df


# ============================================================
# DATOS.GOB.AR — SERIES (GENÉRICO)
# ============================================================
DATOS_GOB_AR_SERIES_URL = "https://apis.datos.gob.ar/series/api/series"


def _parse_datos_gob_series_csv(csv_text: str, series_id: str) -> pd.DataFrame:
    """
    Parsea CSV de datos.gob.ar.
    Soporta:
      - formato largo: indice_tiempo, serie_id, valor
      - formato ancho: indice_tiempo + columna con el id de la serie
      - formato simple: indice_tiempo, valor
    Devuelve DataFrame con columnas Date, Value.
    """
    try:
        df = pd.read_csv(StringIO(csv_text))
        if df.empty:
            return pd.DataFrame(columns=["Date", "Value"])

        df.columns = [c.strip() for c in df.columns]

        # 1) Formato largo
        if {"indice_tiempo", "serie_id", "valor"}.issubset(df.columns):
            out = df[df["serie_id"] == series_id][["indice_tiempo", "valor"]].copy()
            out = out.rename(columns={"indice_tiempo": "Date", "valor": "Value"})

        # 2) Formato ancho
        elif "indice_tiempo" in df.columns and series_id in df.columns:
            out = df[["indice_tiempo", series_id]].copy()
            out = out.rename(columns={"indice_tiempo": "Date", series_id: "Value"})

        # 3) Formato simple (una sola serie)
        elif {"indice_tiempo", "valor"}.issubset(df.columns):
            out = df[["indice_tiempo", "valor"]].copy()
            out = out.rename(columns={"indice_tiempo": "Date", "valor": "Value"})

        # 4) Fallback clásico
        elif {"fecha", "valor"}.issubset(df.columns):
            out = df.rename(columns={"fecha": "Date", "valor": "Value"})[["Date", "Value"]]

        else:
            return pd.DataFrame(columns=["Date", "Value"])

        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out["Value"] = pd.to_numeric(out["Value"], errors="coerce")

        return (
            out.dropna(subset=["Date", "Value"])
            .drop_duplicates(subset=["Date"])
            .sort_values("Date")
            .reset_index(drop=True)
        )

    except Exception:
        return pd.DataFrame(columns=["Date", "Value"])


@st.cache_data(ttl=12 * 60 * 60)
def get_datos_gob_series(series_id: str) -> pd.DataFrame:
    """
    Descarga una serie puntual desde datos.gob.ar.
    """
    params = {"ids": series_id, "format": "csv", "limit": 1000}

    try:
        r = requests.get(
            DATOS_GOB_AR_SERIES_URL,
            params=params,
            timeout=30,
            headers={
                "User-Agent": "monitor-ceu-uia/1.0 (streamlit)",
                "Accept": "text/csv,*/*",
            },
        )

        if r.status_code != 200:
            st.warning(f"datos.gob.ar ({series_id}) status={r.status_code}: {r.text[:200]}")
            return pd.DataFrame(columns=["Date", "Value"])

        return _parse_datos_gob_series_csv(r.text, series_id)

    except Exception as e:
        st.warning(f"datos.gob.ar ({series_id}) error: {e}")
        return pd.DataFrame(columns=["Date", "Value"])



# ============================================================
# INDEC — EMAE mensual base 2004 (Excel)
# ============================================================

EMAE_XLS_URL = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_emae_mensual_base2004.xls"

@st.cache_data(ttl=12 * 60 * 60)
def get_emae_excel_full() -> pd.DataFrame:
    """
    Devuelve:
    Date, Original, SA, Trend, MoM, YoY
    """
    try:
        r = requests.get(EMAE_XLS_URL, timeout=60)
        r.raise_for_status()

        raw = pd.read_excel(
            BytesIO(r.content),
            header=None,
            engine="xlrd"
        )

        meses = {
            "enero":1, "febrero":2, "marzo":3, "abril":4,
            "mayo":5, "junio":6, "julio":7, "agosto":8,
            "septiembre":9, "setiembre":9,
            "octubre":10, "noviembre":11, "diciembre":12
        }

        df = raw.iloc[5:, [0, 1, 2, 4, 6]].copy()
        df.columns = ["Year", "Month", "Original", "SA", "Trend"]

        df["Year"] = pd.to_numeric(df["Year"], errors="coerce").ffill()
        df["MonthNum"] = (
            df["Month"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(meses)
        )

        for c in ["Original", "SA", "Trend"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df = df.dropna(subset=["Year", "MonthNum"])
        df["Date"] = pd.to_datetime(
            dict(
                year=df["Year"].astype(int),
                month=df["MonthNum"].astype(int),
                day=1,
            ),
            errors="coerce",
        )

        df = (
            df[["Date", "Original", "SA", "Trend"]]
            .dropna(subset=["Date"])
            .sort_values("Date")
            .reset_index(drop=True)
        )

        df["MoM"] = (df["SA"] / df["SA"].shift(1) - 1.0) * 100.0
        df["YoY"] = (df["Original"] / df["Original"].shift(12) - 1.0) * 100.0

        return df

    except Exception as e:
        st.warning(f"INDEC EMAE Excel error: {e}")
        return pd.DataFrame(columns=["Date", "Original", "SA", "Trend", "MoM", "YoY"])


@st.cache_data(ttl=12 * 60 * 60)
def get_emae_original() -> pd.DataFrame:
    df = get_emae_excel_full()
    if df.empty:
        return pd.DataFrame(columns=["Date", "Value"])
    return df[["Date", "Original"]].rename(columns={"Original": "Value"}).dropna().reset_index(drop=True)


@st.cache_data(ttl=12 * 60 * 60)
def get_emae_deseasonalizado() -> pd.DataFrame:
    df = get_emae_excel_full()
    if df.empty:
        return pd.DataFrame(columns=["Date", "Value"])
    return df[["Date", "SA"]].rename(columns={"SA": "Value"}).dropna().reset_index(drop=True)



# ============================================================
# DATOS.GOB.AR — ISAC (INDEC)
# ============================================================

ISAC_ORIGINAL_ID = "33.2_ISAC_NIVELRAL_0_M_18_63"
ISAC_DESEASON_ID = "33.2_ISAC_SIN_EDAD_0_M_23_56"


@st.cache_data(ttl=12 * 60 * 60)
def get_isac_both_csv() -> pd.DataFrame:
    ids = f"{ISAC_ORIGINAL_ID},{ISAC_DESEASON_ID}"
    params = {"ids": ids, "format": "csv", "limit": 1000}

    r = requests.get(
        DATOS_GOB_AR_SERIES_URL,
        params=params,
        timeout=30,
        headers={
            "User-Agent": "monitor-ceu-uia/1.0 (streamlit)",
            "Accept": "text/csv,*/*",
        },
    )

    if r.status_code != 200:
        st.warning(f"datos.gob.ar ISAC both status={r.status_code}: {r.text[:200]}")
        return pd.DataFrame()

    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]

    # ✅ Caso A) columnas con nombre por ID
    if "indice_tiempo" in df.columns and (ISAC_ORIGINAL_ID in df.columns) and (ISAC_DESEASON_ID in df.columns):
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        return df.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    # ✅ Caso B) columnas genéricas (TU CASO)
    if {"indice_tiempo", "isac_nivel_general", "isac_sin_estacionalidad"}.issubset(df.columns):
        df = df.rename(
            columns={
                "isac_nivel_general": ISAC_ORIGINAL_ID,
                "isac_sin_estacionalidad": ISAC_DESEASON_ID,
            }
        )
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        return df.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    # ✅ Caso C) formato largo
    if {"indice_tiempo", "serie_id", "valor"}.issubset(df.columns):
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        wide = (
            df.pivot(index="indice_tiempo", columns="serie_id", values="valor")
              .reset_index()
              .rename_axis(None, axis=1)
        )
        return wide.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    st.warning(f"datos.gob.ar ISAC both: formato CSV inesperado. cols={df.columns.tolist()}")
    return pd.DataFrame()



@st.cache_data(ttl=12 * 60 * 60)
def get_isac_original() -> pd.DataFrame:
    df = get_isac_both_csv()
    if df.empty or ISAC_ORIGINAL_ID not in df.columns:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[["indice_tiempo", ISAC_ORIGINAL_ID]].rename(columns={"indice_tiempo": "Date", ISAC_ORIGINAL_ID: "Value"})
    return out.dropna().reset_index(drop=True)


@st.cache_data(ttl=12 * 60 * 60)
def get_isac_deseasonalizado() -> pd.DataFrame:
    df = get_isac_both_csv()
    if df.empty or ISAC_DESEASON_ID not in df.columns:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[["indice_tiempo", ISAC_DESEASON_ID]].rename(columns={"indice_tiempo": "Date", ISAC_DESEASON_ID: "Value"})
    return out.dropna().reset_index(drop=True)


# ============================================================
# DATOS.GOB.AR — IPI Manufacturero (INDEC)
# ============================================================

IPI_MANUF_ORIGINAL_ID = "453.1_SERIE_ORIGNAL_0_0_14_46"
IPI_MANUF_DESEASON_ID = "453.1_SERIE_DESEADA_0_0_24_58"

@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_manuf_both_csv() -> pd.DataFrame:
    ids = f"{IPI_MANUF_ORIGINAL_ID},{IPI_MANUF_DESEASON_ID}"
    params = {"ids": ids, "format": "csv", "limit": 1000}

    r = requests.get(
        DATOS_GOB_AR_SERIES_URL,
        params=params,
        timeout=30,
        headers={
            "User-Agent": "monitor-ceu-uia/1.0 (streamlit)",
            "Accept": "text/csv,*/*",
        },
    )

    if r.status_code != 200:
        st.warning(f"datos.gob.ar IPI manuf both status={r.status_code}: {r.text[:200]}")
        return pd.DataFrame()

    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]

    # ✅ Caso A) columnas con nombre por ID
    if "indice_tiempo" in df.columns and (IPI_MANUF_ORIGINAL_ID in df.columns) and (IPI_MANUF_DESEASON_ID in df.columns):
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        return df.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    # ✅ Caso B) columnas genéricas (TU CASO)
    if {"indice_tiempo", "serie_original", "serie_desestacionalizada"}.issubset(df.columns):
        df = df.rename(
            columns={
                "serie_original": IPI_MANUF_ORIGINAL_ID,
                "serie_desestacionalizada": IPI_MANUF_DESEASON_ID,
            }
        )
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        return df.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    # ✅ Caso C) formato largo
    if {"indice_tiempo", "serie_id", "valor"}.issubset(df.columns):
        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        wide = (
            df.pivot(index="indice_tiempo", columns="serie_id", values="valor")
              .reset_index()
              .rename_axis(None, axis=1)
        )
        return wide.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

    st.warning(f"datos.gob.ar IPI manuf both: formato CSV inesperado. cols={df.columns.tolist()}")
    return pd.DataFrame()



@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_manuf_original() -> pd.DataFrame:
    df = get_ipi_manuf_both_csv()
    if df.empty or IPI_MANUF_ORIGINAL_ID not in df.columns:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[["indice_tiempo", IPI_MANUF_ORIGINAL_ID]].rename(
        columns={"indice_tiempo": "Date", IPI_MANUF_ORIGINAL_ID: "Value"}
    )
    return out.dropna().reset_index(drop=True)


@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_manuf_deseasonalizado() -> pd.DataFrame:
    df = get_ipi_manuf_both_csv()
    if df.empty or IPI_MANUF_DESEASON_ID not in df.columns:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[["indice_tiempo", IPI_MANUF_DESEASON_ID]].rename(
        columns={"indice_tiempo": "Date", IPI_MANUF_DESEASON_ID: "Value"}
    )
    return out.dropna().reset_index(drop=True)


# ============================================================
# INDEC — IPI MINERO (Excel)
# ============================================================

IPI_MINERO_XLSX_URL = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipi_minero.xlsx"
IPI_MINERO_SHEET = "Cuadro 1"


def _month_es_to_num(m: str):
    if m is None or (isinstance(m, float) and np.isnan(m)):
        return None
    mm = str(m).strip().lower()
    map_es = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }
    return map_es.get(mm)


@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_minero_excel_long() -> pd.DataFrame:
    """
    Lee el Excel del INDEC y devuelve dos series en formato largo:
      columnas: Date, Serie, Value
    Según lo que pasaste:
      - arranca fila 9
      - A: año (con vacíos para meses)
      - B: mes
      - D: serie original (nivel general, números índice)
      - H: serie sin estacionalidad (nivel general, números índice)
    """
    try:
        r = requests.get(IPI_MINERO_XLSX_URL, timeout=60)
        r.raise_for_status()

        raw = pd.read_excel(
            BytesIO(r.content),
            sheet_name=IPI_MINERO_SHEET,
            header=None,
            engine="openpyxl",
        )

        # fila 9 -> índice 8 (0-based)
        df = raw.iloc[8:, :].copy()

        # Columnas reales del Excel INDEC:
        # año = 1, mes = 2, original = 3, desestacionalizada = 7
        df = df.iloc[:, [1, 2, 3, 7]]
        df.columns = ["Year", "Month", "Orig", "SA"]
        
        # limpia años tipo "2025*"
        df["Year"] = (
            df["Year"]
            .astype(str)
            .str.extract(r"(\d{4})")[0]
        )
        
        # forward fill
        df["Year"] = (
            pd.to_numeric(df["Year"], errors="coerce")
            .ffill()
        )


        # mes ES -> num
        df["MonthNum"] = df["Month"].apply(_month_es_to_num)

        # valores
        df["Orig"] = pd.to_numeric(df["Orig"], errors="coerce")
        df["SA"] = pd.to_numeric(df["SA"], errors="coerce")

        # fecha (inicio de mes)
        df = df.dropna(subset=["Year", "MonthNum"])
        df["Date"] = pd.to_datetime(
            dict(year=df["Year"].astype(int), month=df["MonthNum"].astype(int), day=1),
            errors="coerce",
        )

        df = df.dropna(subset=["Date"]).sort_values("Date")

        long_df = (
            df.melt(
                id_vars=["Date"],
                value_vars=["Orig", "SA"],
                var_name="Serie",
                value_name="Value",
            )
            .dropna(subset=["Value"])
            .sort_values(["Serie", "Date"])
            .reset_index(drop=True)
        )

        long_df["Serie"] = long_df["Serie"].map({"Orig": "original", "SA": "sa"}).fillna(long_df["Serie"])

        return long_df

    except Exception as e:
        st.warning(f"INDEC IPI minero excel error: {e}")
        return pd.DataFrame(columns=["Date", "Serie", "Value"])


@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_minero_original() -> pd.DataFrame:
    df = get_ipi_minero_excel_long()
    if df.empty:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[df["Serie"] == "original"][["Date", "Value"]].copy()
    return (
        out.dropna(subset=["Date", "Value"])
        .drop_duplicates(subset=["Date"])
        .sort_values("Date")
        .reset_index(drop=True)
    )


@st.cache_data(ttl=12 * 60 * 60)
def get_ipi_minero_deseasonalizado() -> pd.DataFrame:
    df = get_ipi_minero_excel_long()
    if df.empty:
        return pd.DataFrame(columns=["Date", "Value"])
    out = df[df["Serie"] == "sa"][["Date", "Value"]].copy()
    return (
        out.dropna(subset=["Date", "Value"])
        .drop_duplicates(subset=["Date"])
        .sort_values("Date")
        .reset_index(drop=True)
    )


# ============================================================
# EMAE — Apertura por sectores (CSV SSPM / infra.datos.gob.ar)
# ============================================================

EMAE_SECTORES_CSV_URL = (
    "https://infra.datos.gob.ar/catalog/sspm/dataset/11/distribution/11.3/"
    "download/emae-apertura-por-sectores-valores-mensuales-indice-base-2004.csv"
)

@st.cache_data(ttl=12 * 60 * 60)
def get_emae_sectores_wide() -> pd.DataFrame:
    """
    Descarga EMAE apertura por sectores (serie original, índice base 2004) en formato ancho.
    Columnas: indice_tiempo + sectores.
    """
    try:
        r = requests.get(
            EMAE_SECTORES_CSV_URL,
            timeout=30,
            headers={"User-Agent": "monitor-ceu-uia/1.0 (streamlit)"},
        )
        r.raise_for_status()

        df = pd.read_csv(StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]

        if "indice_tiempo" not in df.columns:
            st.warning(f"EMAE sectores: CSV inesperado. cols={df.columns.tolist()}")
            return pd.DataFrame()

        df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"], errors="coerce")
        df = df.dropna(subset=["indice_tiempo"]).sort_values("indice_tiempo")

        # numeric all sector cols
        for c in df.columns:
            if c != "indice_tiempo":
                df[c] = pd.to_numeric(df[c], errors="coerce")

        return df.reset_index(drop=True)

    except Exception as e:
        st.warning(f"EMAE sectores CSV error: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=12 * 60 * 60)
def get_emae_sectores_long() -> pd.DataFrame:
    """
    Devuelve formato largo:
      Date, Sector, Value
    """
    wide = get_emae_sectores_wide()
    if wide is None or wide.empty:
        return pd.DataFrame(columns=["Date", "Sector", "Value"])

    long_df = (
        wide.melt(id_vars=["indice_tiempo"], var_name="Sector", value_name="Value")
        .rename(columns={"indice_tiempo": "Date"})
        .dropna(subset=["Date", "Value"])
        .sort_values(["Sector", "Date"])
        .reset_index(drop=True)
    )
    return long_df

# ============================================================
# BCRA — Calidad de cartera por líneas
# Informe sobre Bancos / Anexo XLSX
# ============================================================
@st.cache_data(ttl=12 * 60 * 60)
def get_calidad_cartera_long() -> pd.DataFrame:
    url = (
        "https://www.bcra.gob.ar/archivos/Pdfs/"
        "PublicacionesEstadisticas/informes/InfBanc_Anexo.xlsx"
    )

    try:
        last_err = None
        content = None

        for _ in range(3):
            try:
                r = requests.get(
                    url,
                    timeout=90,
                    verify=False,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                r.raise_for_status()

                content = r.content

                # Evita leer un XLSX descargado a medias
                if content and len(content) > 500_000:
                    break

            except Exception as e:
                last_err = e
                content = None

        if content is None:
            raise RuntimeError(f"No se pudo descargar InfBanc_Anexo.xlsx: {last_err}")

        raw = pd.read_excel(
            BytesIO(content),
            sheet_name="Calidad de Cartera (por líneas)",
            header=None,
            engine="openpyxl",
        )

        fechas = raw.iloc[5, 1:]

        bloques = {
            "Total": (6, 15),
            "Familias": (58, 64),
            "Empresas": (102, 109),
        }

        dfs = []

        for agente, (i, j) in bloques.items():
            conceptos = raw.iloc[i:j, 0]
            valores = raw.iloc[i:j, 1:].copy()

            valores.columns = fechas.values
            valores.index = conceptos.values

            tmp = (
                valores
                .reset_index(names="concepto")
                .melt(
                    id_vars="concepto",
                    var_name="Date",
                    value_name="value",
                )
            )

            tmp["Date"] = pd.to_datetime(tmp["Date"], errors="coerce")
            tmp["value"] = pd.to_numeric(tmp["value"], errors="coerce").round(1)
            tmp["agente"] = agente

            dfs.append(tmp)

        return (
            pd.concat(dfs, ignore_index=True)
            [["Date", "agente", "concepto", "value"]]
            .dropna(subset=["Date", "agente", "concepto", "value"])
            .sort_values(["agente", "concepto", "Date"])
            .reset_index(drop=True)
        )

    except Exception as e:
        st.warning(f"BCRA Calidad de cartera error: {e}")
        return pd.DataFrame(columns=["Date", "agente", "concepto", "value"])
