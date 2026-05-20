import re
from io import BytesIO
from pathlib import Path
from datetime import date

import pandas as pd
import requests
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[1]
SIPA_DIR = ROOT / "assets" / "sipa"
SIPA_DIR.mkdir(parents=True, exist_ok=True)

SIPA_LANDING_PAGE = (
    "https://www.argentina.gob.ar/trabajo/estadisticas/"
    "situacion-y-evolucion-del-trabajo-registrado"
)

SIPA_XLSX_RE = re.compile(
    r"https?://www\.argentina\.gob\.ar/sites/default/files/"
    r"trabajoregistrado_(\d{4})_estadisticas\.xlsx",
    re.IGNORECASE,
)


# Modificamos la regla para que el "https://www.argentina.gob.ar" sea opcional
SIPA_XLSX_RE = re.compile(
    r"(?:https?://www\.argentina\.gob\.ar)?/sites/default/files/trabajoregistrado_(\d{4})_estadisticas\.xlsx",
    re.IGNORECASE,
)

def resolver_latest_sipa_xlsx_url() -> str:
    # Agregamos un User-Agent. A veces el gobierno bloquea scripts que no parecen navegadores reales.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        r = requests.get(SIPA_LANDING_PAGE, headers=headers, timeout=30)
        r.raise_for_status()

        matches = list(SIPA_XLSX_RE.finditer(r.text))
        if matches:
            # Agarramos el número más alto (ej: 2602 le gana a 2601)
            best = max(matches, key=lambda m: int(m.group(1)))
            url_encontrada = best.group(0)
            
            # Si capturó una ruta relativa que empieza con "/", le pegamos el dominio para que requests pueda descargarla
            if url_encontrada.startswith("/"):
                url_encontrada = "https://www.argentina.gob.ar" + url_encontrada
                
            print(f"✅ Archivo detectado en la web: {url_encontrada}")
            return url_encontrada

    except Exception as e:
        print(f"Warning: no se pudo leer landing SIPA: {e}")

    # ... (el resto de la función con el bucle for hacia atrás queda igual) ...

    y = date.today().year
    m = date.today().month

    for _ in range(24):
        yymm = f"{y % 100:02d}{m:02d}"
        url = (
            "https://www.argentina.gob.ar/sites/default/files/"
            f"trabajoregistrado_{yymm}_estadisticas.xlsx"
        )

        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                return url
        except Exception:
            pass

        m -= 1
        if m == 0:
            m = 12
            y -= 1

    raise RuntimeError("No se pudo encontrar el XLSX vigente de SIPA.")


def parse_mes(x):
    if pd.isna(x):
        return pd.NaT

    if isinstance(x, pd.Timestamp):
        return pd.Timestamp(x.year, x.month, 1)

    # 1. Si es un número (formato fecha de Excel), lo procesamos
    if isinstance(x, (int, float)) and not pd.isna(x):
        try:
            dt = pd.to_datetime(x, unit="D", origin="1899-12-30", errors="coerce")
            if not pd.isna(dt):
                return pd.Timestamp(dt.year, dt.month, 1)
        except Exception:
            pass

    s = str(x).strip().lower()
    if not s:
        return pd.NaT

    s = s.replace("*", "").replace("/", "-").replace(".", "-")
    s = re.sub(r"\s+", "", s)

    # 2. Formato YYYYmMM (ej: 2026m02)
    m = re.match(r"^(?P<yyyy>\d{4})m(?P<mm>\d{1,2})$", s)
    if m:
        return pd.Timestamp(int(m.group("yyyy")), int(m.group("mm")), 1)

    # 3. PRIORIDAD ESPAÑOL: Diccionario manual para evitar que Pandas malinterprete "feb", "mar", "may"
    meses = {
        "ene": 1, "enero": 1,
        "feb": 2, "febrero": 2,
        "mar": 3, "marzo": 3,
        "abr": 4, "abril": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6,
        "jul": 7, "julio": 7,
        "ago": 8, "agosto": 8,
        "sep": 9, "set": 9, "sept": 9, "septiembre": 9,
        "oct": 10, "octubre": 10,
        "nov": 11, "noviembre": 11,
        "dic": 12, "diciembre": 12,
    }

    m = re.match(r"^(?P<mon>[a-záéíóúñ]{3,9})-?(?P<yy>\d{2,4})$", s)
    if m:
        mon = m.group("mon")
        yy = int(m.group("yy"))
        if mon in meses:
            year = yy if yy > 1900 else 2000 + yy
            return pd.Timestamp(year, meses[mon], 1)

    # 4. Si todo lo demás falla, recién ahí dejamos que Pandas intente adivinar
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if not pd.isna(dt):
        return pd.Timestamp(dt.year, dt.month, 1)

    return pd.NaT


def extraer_serie_colB(df_raw, col_fecha=0, col_val=1):
    tmp = df_raw.copy()
    tmp = tmp.rename(
        columns={
            tmp.columns[col_fecha]: "fecha_raw",
            tmp.columns[col_val]: "valor_raw",
        }
    )

    # 1. Parseamos la fecha
    tmp["fecha"] = tmp["fecha_raw"].apply(parse_mes)
    
    # 2. Limpieza EXTREMA del valor numérico
    # Si el ministerio tipeó a mano una coma en lugar de un punto, o dejó un espacio, lo forzamos a formato Python
    tmp["valor_limpio"] = tmp["valor_raw"].astype(str).str.replace(",", ".", regex=False).str.replace("*", "", regex=False).str.strip()
    tmp["valor"] = pd.to_numeric(tmp["valor_limpio"], errors="coerce")

    # --- DETECTOR DE ERRORES ---
    # Atrapamos las filas que Pandas está a punto de borrar (ignorando la fila que dice "Notas:")
    filas_rotas = tmp[ (tmp["fecha"].isna() | tmp["valor"].isna()) & (~tmp["fecha_raw"].astype(str).str.lower().str.contains("nota", na=False)) ]
    
    if not filas_rotas.empty:
        print("\n⚠️ ALERTA: Pandas está eliminando estas filas porque no pudo procesar la celda:")
        print(filas_rotas.tail(3)[["fecha_raw", "valor_raw", "fecha", "valor"]])
        print("-" * 60)
    # ---------------------------

    return (
        tmp.dropna(subset=["fecha", "valor"])[["fecha", "valor"]]
        .sort_values("fecha")
        .reset_index(drop=True)
    )


def extraer_sectores(df_raw):
    header = df_raw.iloc[1, 1:].copy().dropna()
    sectores = [str(x).strip() for x in header.tolist() if str(x).strip()]

    if not sectores:
        return pd.DataFrame(columns=["fecha"])

    data = df_raw.iloc[2:, : 1 + len(sectores)].copy()
    data.columns = ["fecha_raw"] + sectores

    data["fecha"] = data["fecha_raw"].apply(parse_mes)
    data = data.dropna(subset=["fecha"]).drop(columns=["fecha_raw"])

    for c in sectores:
        data[c] = pd.to_numeric(data[c], errors="coerce")

    return (
        data.dropna(how="all", subset=sectores)
        .sort_values("fecha")
        .reset_index(drop=True)
    )


def extraer_subsectores_industria(df_raw):
    if df_raw is None or df_raw.empty or df_raw.shape[1] < 2:
        return pd.DataFrame(columns=["fecha"])

    col_indices = list(range(1, min(8, df_raw.shape[1])))

    nombres = []
    for c in col_indices:
        nombres.append(str(df_raw.iloc[1, c]).strip())

    data = df_raw.iloc[2:, [0] + col_indices].copy()
    data.columns = ["fecha_raw"] + nombres

    data["fecha"] = data["fecha_raw"].apply(parse_mes)
    data = data.dropna(subset=["fecha"]).drop(columns=["fecha_raw"])

    for c in nombres:
        data[c] = pd.to_numeric(data[c], errors="coerce")

    return (
        data.dropna(how="all", subset=nombres)
        .sort_values("fecha")
        .reset_index(drop=True)
    )


def filtrar_fechas(df):
    if df.empty or "fecha" not in df.columns:
        return df

    df = df.copy()
    df = df[(df["fecha"] >= "2000-01-01") & (df["fecha"] <= "2035-12-01")]
    return df.sort_values("fecha").reset_index(drop=True)


def main():
    url = resolver_latest_sipa_xlsx_url()
    print(f"Descargando SIPA desde: {url}")

    r = requests.get(url, timeout=90)
    r.raise_for_status()

    xls = pd.ExcelFile(BytesIO(r.content), engine="openpyxl")

    t21 = pd.read_excel(xls, sheet_name="T.2.1", header=None, usecols=[0, 1])
    t22 = pd.read_excel(xls, sheet_name="T.2.2", header=None, usecols=[0, 1])

    a21 = pd.read_excel(xls, sheet_name="A.2.1", header=None, usecols=list(range(17)))
    a22 = pd.read_excel(xls, sheet_name="A.2.2", header=None, usecols=list(range(17)))

    a61 = pd.read_excel(xls, sheet_name="A.6.1", header=None, usecols=[0, 3, 4, 5, 6, 7, 8, 9])
    a62 = pd.read_excel(xls, sheet_name="A.6.2", header=None, usecols=[0, 3, 4, 5, 6, 7, 8, 9])

    s_orig = extraer_serie_colB(t21).rename(columns={"valor": "orig"})
    s_sa = extraer_serie_colB(t22).rename(columns={"valor": "sa"})

    df_total = s_orig.merge(s_sa, on="fecha", how="outer").sort_values("fecha")

    df_sec_orig = extraer_sectores(a21)
    df_sec_sa = extraer_sectores(a22)

    df_sub_orig = extraer_subsectores_industria(a61)
    df_sub_sa = extraer_subsectores_industria(a62)

    df_total = filtrar_fechas(df_total)
    df_sec_orig = filtrar_fechas(df_sec_orig)
    df_sec_sa = filtrar_fechas(df_sec_sa)
    df_sub_orig = filtrar_fechas(df_sub_orig)
    df_sub_sa = filtrar_fechas(df_sub_sa)

    print("Enviando datos a la base de datos PostgreSQL local...")
    
    cadena_conexion = 'postgresql://postgres:123@localhost:5432/monitor_uia'
    engine = create_engine(cadena_conexion)

    # if_exists='replace' pisa la tabla anterior y carga los datos frescos en cada corrida
    df_total.to_sql('sipa_total', engine, if_exists='replace', index=False)
    df_sec_orig.to_sql('sipa_sec_orig', engine, if_exists='replace', index=False)
    df_sec_sa.to_sql('sipa_sec_sa', engine, if_exists='replace', index=False)
    df_sub_orig.to_sql('sipa_sub_orig', engine, if_exists='replace', index=False)
    df_sub_sa.to_sql('sipa_sub_sa', engine, if_exists='replace', index=False)

    print("OK. Tablas guardadas con éxito en la base de datos.")
    # ------------------------------

    print(f"Última fecha total: {df_total['fecha'].max().date() if not df_total.empty else 'sin datos'}")

if __name__ == "__main__":
    main()
