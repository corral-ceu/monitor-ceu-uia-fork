from io import BytesIO
import pandas as pd
import requests
from sqlalchemy import create_engine

def main():
    url = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipi_manufacturero_2026.xls"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.ms-excel,application/octet-stream,*/*",
        "Referer": "https://www.indec.gob.ar/",
    }

    print(f"Descargando IPI desde: {url}")
    try:
        r = requests.get(url, timeout=60, headers=headers)
        r.raise_for_status()

        head = r.content[:200].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            raise ValueError("El INDEC devolvió una página web en lugar de un Excel.")

        xls = BytesIO(r.content)
        df_c2 = pd.read_excel(xls, sheet_name="Cuadro 2", header=None, engine="xlrd")
        xls.seek(0)
        df_c5 = pd.read_excel(xls, sheet_name="Cuadro 5", header=None, engine="xlrd")

        # --- CONEXIÓN A SQL ---
        print("Enviando datos a PostgreSQL...")
        cadena_conexion = 'postgresql://postgres:123@localhost:5432/monitor_uia'
        engine = create_engine(cadena_conexion)

        # TRUCO TÉCNICO: Pandas nombra las columnas como números enteros (0, 1, 2...).
        # SQL no se lleva bien con columnas llamadas con números puros. 
        # Las pasamos temporalmente a texto para guardarlas.
        df_c2.columns = df_c2.columns.astype(str)
        df_c5.columns = df_c5.columns.astype(str)

        df_c2.to_sql('ipi_cuadro_2', engine, if_exists='replace', index=False)
        df_c5.to_sql('ipi_cuadro_5', engine, if_exists='replace', index=False)

        print("OK. Tablas 'ipi_cuadro_2' e 'ipi_cuadro_5' guardadas en SQL.")

    except Exception as e:
        print(f"Error actualizando el IPI: {e}")

if __name__ == "__main__":
    main()