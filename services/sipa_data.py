import pandas as pd
import streamlit as st
from sqlalchemy import create_engine

# 1. Creamos la conexión a la base de datos local
# IMPORTANTE: Reemplazá 'tu_contraseña' por la que usaste en PostgreSQL.
cadena_conexion = 'postgresql://postgres:123@localhost:5432/monitor_uia'
engine = create_engine(cadena_conexion)

def _leer_sql_sipa(nombre_tabla: str) -> pd.DataFrame:
    """
    Lee una tabla directamente desde PostgreSQL y devuelve un DataFrame.
    """
    # Usamos pandas para traer la tabla entera desde SQL
    df = pd.read_sql_table(nombre_tabla, con=engine)

    # Mantenemos tu lógica original para asegurar que la fecha sea tipo datetime
    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        df = df.dropna(subset=["fecha"]).sort_values("fecha").reset_index(drop=True)

    return df


@st.cache_data(show_spinner=False)
def cargar_sipa_excel():
    """
    Mantenemos el mismo nombre de función para no romper la UI en pages/empleo.py
    """
    try:
        # En lugar de leer "sipa_total.csv", leemos la tabla "sipa_total"
        df_total = _leer_sql_sipa("sipa_total")
        df_sec_orig = _leer_sql_sipa("sipa_sec_orig")
        df_sec_sa = _leer_sql_sipa("sipa_sec_sa")
        df_sub_orig = _leer_sql_sipa("sipa_sub_orig")
        df_sub_sa = _leer_sql_sipa("sipa_sub_sa")

        return df_total, df_sec_orig, df_sec_sa, df_sub_orig, df_sub_sa

    except Exception as e:
        st.error(f"No se pudieron cargar los datos SIPA desde PostgreSQL: {e}")

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )