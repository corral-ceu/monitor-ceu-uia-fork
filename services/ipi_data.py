import pandas as pd
import streamlit as st
from sqlalchemy import create_engine

# Conexión a la base de datos local
cadena_conexion = 'postgresql://postgres:123@localhost:5432/monitor_uia'
engine = create_engine(cadena_conexion)

@st.cache_data(ttl=3600, show_spinner=False)
def cargar_ipi_excel():
    """
    Lee las tablas crudas del IPI directamente desde PostgreSQL.
    """
    try:
        # Leemos las tablas que generó nuestro script ETL
        df_c2 = pd.read_sql_table("ipi_cuadro_2", con=engine)
        df_c5 = pd.read_sql_table("ipi_cuadro_5", con=engine)

        # TRUCO TÉCNICO: Como en SQL tuvimos que guardar los nombres de las columnas 
        # como texto ('0', '1', '2'...), los volvemos a convertir a números enteros (0, 1, 2)
        # para que la función de abajo los encuentre correctamente.
        df_c2.columns = df_c2.columns.astype(int)
        df_c5.columns = df_c5.columns.astype(int)

        return df_c2, df_c5

    except Exception as e:
        st.error(f"IPI: error leyendo la base de datos SQL: {e}")
        return None, None


def procesar_serie_excel(df: pd.DataFrame, col_idx: int) -> pd.DataFrame:
    """Extrae serie mensual desde el formato del Excel de INDEC."""
    try:
        data = df.iloc[6:].copy()
        data[1] = data[1].ffill().astype(str).str.extract(r"(\d{4})")[0]
        data = data.dropna(subset=[1, 2, col_idx])

        meses_map = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        data["m"] = data[2].astype(str).str.lower().str.strip().map(meses_map)

        data["fecha"] = pd.to_datetime(
            data[1] + "-" + data["m"].astype(int).astype(str) + "-01",
            errors="coerce",
        )

        return (
            data[["fecha", col_idx]]
            .rename(columns={col_idx: "valor"})
            .dropna()
            .sort_values("fecha")
        )
    except Exception:
        return pd.DataFrame(columns=["fecha", "valor"])