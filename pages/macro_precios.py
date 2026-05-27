import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import numpy as np
import textwrap
import io
import re
import requests
import streamlit.components.v1 as components

from services.macro_data import get_ipc_indec_full


# ============================================================
# Helpers
# ============================================================
def _fmt_pct_es(x: float, dec: int = 1) -> str:
    try:
        return f"{float(x):.{dec}f}".replace(".", ",")
    except Exception:
        return "—"


def _mes_es(m: int) -> str:
    return {
        1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
        7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic"
    }[int(m)]


def _mmmyy_es(dt) -> str:
    dt = pd.to_datetime(dt)
    return f"{_mes_es(dt.month)}-{str(dt.year)[-2:]}"


def _is_nivel_general(label: str) -> bool:
    return str(label).strip().lower() == "nivel general"


def _clean_code(x) -> str:
    s = str(x).strip()
    if s.endswith(".0") and s.replace(".0", "").isdigit():
        return s[:-2]
    return s


def _arrow_cls(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ("", "")
    return ("▲", "fx-up") if v >= 0 else ("▼", "fx-down")


def _tick_step_from_months(n_months: int) -> int:
    if n_months <= 12:
        return 1
    if n_months <= 24:
        return 2
    if n_months <= 48:
        return 3
    return 6


def _apply_panel_wrap(marker_id: str):
    st.markdown(f"<span id='{marker_id}'></span>", unsafe_allow_html=True)
    components.html(
        f"""
        <script>
        (function() {{
          function applyPanelClass() {{
            const marker = window.parent.document.getElementById('{marker_id}');
            if (!marker) return;
            const block = marker.closest('div[data-testid="stVerticalBlock"]');
            if (block) block.classList.add('fx-panel-wrap');
          }}
          applyPanelClass();
          let tries = 0;
          const t = setInterval(() => {{
            applyPanelClass();
            tries += 1;
            if (tries >= 10) clearInterval(t);
          }}, 150);

          const obs = new MutationObserver(() => applyPanelClass());
          obs.observe(window.parent.document.body, {{ childList: true, subtree: true }});
          setTimeout(() => obs.disconnect(), 3000);
        }})();
        </script>
        """,
        height=0,
    )


def _range_accum_from_index(df: pd.DataFrame, date_col: str, group_col: str, idx_col: str, start_date: pd.Timestamp) -> pd.Series:
    """
    Acumulado (rango): (Idx_t / Idx_base - 1)*100,
    base = valor de índice en start_date; si no existe, usa el primer punto >= start_date.
    """
    out = pd.Series(index=df.index, dtype=float)
    if df.empty:
        return out

    d = df[[date_col, group_col, idx_col]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d = d.dropna(subset=[date_col, group_col, idx_col]).sort_values([group_col, date_col])

    for g, gg in d.groupby(group_col, sort=False):
        gg = gg.sort_values(date_col)
        base_row = gg[gg[date_col] == start_date]
        if base_row.empty:
            base_row = gg[gg[date_col] >= start_date].head(1)
        if base_row.empty:
            continue

        base = float(base_row[idx_col].iloc[0])
        if not np.isfinite(base) or base == 0:
            continue

        vals = (gg[idx_col].astype(float) / base - 1.0) * 100.0
        out.loc[vals.index] = vals.values

        base_idx = base_row.index[0]
        out.loc[base_idx] = 0.0

    return out


def _range_accum_from_monthly_pct(df: pd.DataFrame, date_col: str, group_col: str, vm_col: str, start_date: pd.Timestamp) -> pd.Series:
    """
    Acumulado (rango) desde variación mensual:
    - En start_date (o primer punto >= start_date) => 0%
    - Luego acumula con producto de (1 + vm/100), arrancando con factor 1 en el base.
    """
    out = pd.Series(index=df.index, dtype=float)
    if df.empty:
        return out

    d = df[[date_col, group_col, vm_col]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d = d.dropna(subset=[date_col, group_col, vm_col]).sort_values([group_col, date_col])

    for g, gg in d.groupby(group_col, sort=False):
        gg = gg.sort_values(date_col)

        base_row = gg[gg[date_col] == start_date]
        if base_row.empty:
            base_row = gg[gg[date_col] >= start_date].head(1)
        if base_row.empty:
            continue

        base_pos = gg.index.get_loc(base_row.index[0])
        tail = gg.iloc[base_pos:].copy()
        if tail.empty:
            continue

        factors = (1.0 + tail[vm_col].astype(float) / 100.0).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        factors.iloc[0] = 1.0
        cum = factors.cumprod()
        acc = (cum / cum.iloc[0] - 1.0) * 100.0

        out.loc[acc.index] = acc.values
        out.loc[tail.index[0]] = 0.0

    return out


# ============================================================
# IPCA (ENGHo 2017/18) — base 100=2025
# ============================================================
DIV_CODES = list(range(1, 13))
W_2017 = {
    1: 22.7 / 100, 2: 2.0 / 100, 3: 6.8 / 100, 4: 14.5 / 100,
    5: 5.5 / 100, 6: 6.4 / 100, 7: 14.3 / 100, 8: 5.1 / 100,
    9: 8.6 / 100, 10: 3.1 / 100, 11: 6.6 / 100, 12: 4.4 / 100,
}


def _compute_ipca_base_2025(ipc_raw: pd.DataFrame) -> pd.DataFrame:
    """
    IPCA (ENGHo 2017/18) desde IPC INDEC por divisiones (1..12).
    Base: promedio 2025 = 100.
    Output: Periodo, Serie, Indice, v_m, v_i_a
    """
    if ipc_raw is None or ipc_raw.empty:
        return pd.DataFrame()

    need = {"Periodo", "Codigo", "Codigo_num", "Indice_IPC", "Region", "Clasificador"}
    if not need.issubset(set(ipc_raw.columns)):
        return pd.DataFrame()

    d = ipc_raw.copy()

    # 1) Solo Nacional + Divisiones COICOP
    d = d[(d["Region"] == "Nacional") & (d["Clasificador"].str.contains("divisiones", case=False, na=False))].copy()

    # 2) Solo códigos 1..12
    d["Codigo_num"] = pd.to_numeric(d["Codigo_num"], errors="coerce")
    d = d[d["Codigo_num"].isin(DIV_CODES)].copy()
    if d.empty:
        return pd.DataFrame()

    d["Codigo_num"] = d["Codigo_num"].astype(int)
    d["Periodo"] = pd.to_datetime(d["Periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp(how="start")
    d["Indice_IPC"] = pd.to_numeric(d["Indice_IPC"], errors="coerce")

    d = d.dropna(subset=["Periodo", "Codigo_num", "Indice_IPC"])

    div_wide = (
        d.pivot_table(index="Periodo", columns="Codigo_num", values="Indice_IPC", aggfunc="last")
        .sort_index()
    )

    # asegurar columnas 1..12
    for c in DIV_CODES:
        if c not in div_wide.columns:
            div_wide[c] = np.nan
    div_wide = div_wide[DIV_CODES]

    # base: promedio 2025 (fallback: promedio total)
    base_mask = div_wide.index.year == 2025
    base_avg = div_wide.loc[base_mask, DIV_CODES].mean(axis=0)
    if base_avg.isna().any():
        base_avg = div_wide[DIV_CODES].mean(axis=0)

    ratios = div_wide.divide(base_avg, axis=1)
    wvec = np.array([W_2017[c] for c in DIV_CODES], dtype=float)
    level = 100.0 * (ratios.values @ wvec)

    out = pd.DataFrame({"Periodo": div_wide.index, "Serie": "ipca", "Indice": level}).sort_values("Periodo")
    out["v_m"] = out["Indice"].pct_change(1) * 100
    out["v_i_a"] = out["Indice"].pct_change(12) * 100
    return out.reset_index(drop=True)


# ============================================================
# Page
# ============================================================
def render_macro_precios(go_to):

    # =========================
    # Botón volver
    # =========================
    if st.button("← Volver"):
        go_to("macro_home")

    # =========================
    # CSS (mismo estilo que tipo de cambio)
    # =========================
    st.markdown(
        textwrap.dedent(
            """
        <style>
          .fx-wrap{
            background: linear-gradient(180deg, #f7fbff 0%, #eef6ff 100%);
            border: 1px solid #dfeaf6;
            border-radius: 22px;
            padding: 12px;
            box-shadow:
              0 10px 24px rgba(15, 55, 100, 0.16),
              inset 0 0 0 1px rgba(255,255,255,0.55);
          }

          .fx-title-row{
            display:flex;
            align-items:center;
            gap: 12px;
            margin-bottom: 8px;
            padding-left: 4px;
          }

          .fx-icon-badge{
            width: 64px;
            height: 52px;
            border-radius: 14px;
            background: linear-gradient(180deg, #e7eef6 0%, #dfe7f1 100%);
            border: 1px solid rgba(15,23,42,0.10);
            display:flex;
            align-items:center;
            justify-content:center;
            box-shadow: 0 8px 14px rgba(15,55,100,0.12);
            font-size: 32px;
            flex: 0 0 auto;
          }

          .fx-title {
            font-size: 23px;
            font-weight: 900;
            letter-spacing: -0.01em;
            color: #14324f;
            margin: 0;
            line-height: 1.0;
          }

          .fx-card{
            background: rgba(255,255,255,0.94);
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 18px;
            padding: 14px 14px 12px 14px;
            box-shadow: 0 10px 18px rgba(15, 55, 100, 0.10);
          }

          .fx-row{
            display: grid;
            grid-template-columns: auto 1fr auto;
            align-items: center;
            column-gap: 14px;
          }

          .fx-value{
            font-size: 56px;
            font-weight: 950;
            letter-spacing: -0.02em;
            color: #14324f;
            line-height: 0.95;
          }

          .fx-meta{
            font-size: 13px;
            color: #2b4660;
            font-weight: 800;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .fx-meta .sep{ opacity: 0.40; padding: 0 6px; }

          .fx-pills{
            display:flex;
            gap: 10px;
            justify-content: flex-end;
            align-items: center;
            white-space: nowrap;
          }

          .fx-pill{
            display:inline-flex;
            align-items:center;
            gap: 8px;
            padding: 7px 10px;
            border-radius: 12px;
            border: 1px solid rgba(15,23,42,0.10);
            font-size: 13px;
            font-weight: 800;
            box-shadow: 0 6px 10px rgba(15,55,100,0.08);
          }

          .fx-pill .lab{ color:#2b4660; font-weight: 900; }

          .fx-pill.red{
            background: linear-gradient(180deg, rgba(220,38,38,0.08) 0%, rgba(220,38,38,0.05) 100%);
          }
          .fx-pill.green{
            background: linear-gradient(180deg, rgba(22,163,74,0.10) 0%, rgba(22,163,74,0.06) 100%);
          }

          .fx-up{ color:#168a3a; font-weight: 900; }
          .fx-down{ color:#cc2e2e; font-weight: 900; }

          .fx-arrow{
            width: 14px;
            text-align:center;
            font-weight: 900;
          }

          .fx-panel-title{
            font-size: 12px;
            font-weight: 900;
            color: rgba(20,50,79,0.78);
            margin: 0 0 6px 2px;
            letter-spacing: 0.01em;
          }

          .fx-panel-gap{ height: 16px; }

          .fx-panel-wrap{
            background: rgba(230, 243, 255, 0.55);
            border: 1px solid rgba(15, 55, 100, 0.10);
            border-radius: 22px;
            padding: 16px 16px 26px 16px;
            box-shadow: 0 10px 18px rgba(15,55,100,0.06);
            margin-top: 10px;
          }

          .fx-panel-wrap div[data-testid="stSelectbox"],
          .fx-panel-wrap div[data-testid="stMultiSelect"],
          .fx-panel-wrap div[data-testid="stSlider"],
          .fx-panel-wrap div[data-testid="stPlotlyChart"],
          .fx-panel-wrap div[data-testid="stDownloadButton"],
          .fx-panel-wrap div[data-testid="stRadio"]{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
          }

          .fx-panel-wrap div[role="combobox"]{
            border-radius: 16px !important;
            border: 1px solid rgba(15,23,42,0.10) !important;
            background: rgba(255,255,255,0.94) !important;
            box-shadow: 0 10px 18px rgba(15, 55, 100, 0.08) !important;
          }

          .fx-panel-wrap div[data-testid="stSelectbox"] div[role="combobox"]{
            background: #0b2a55 !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            box-shadow: 0 10px 18px rgba(15, 55, 100, 0.10) !important;
          }
          .fx-panel-wrap div[data-testid="stSelectbox"] div[role="combobox"] *{
            color: #8fc2ff !important;
            fill: #8fc2ff !important;
            font-weight: 800 !important;
          }

          .fx-panel-wrap span[data-baseweb="tag"]{
            background: #0b2a55 !important;
            color: #ffffff !important;
            border-radius: 10px !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
          }
          .fx-panel-wrap span[data-baseweb="tag"] *{
            color: #ffffff !important;
            fill: #ffffff !important;
          }

          div[data-testid="stButton"] button{
            white-space: nowrap !important;
          }

          @media (max-width: 900px){
            .fx-row{ grid-template-columns: 1fr; row-gap: 10px; }
            .fx-meta{ white-space: normal; }
            .fx-pills{ justify-content: flex-start; }
          }

          /* ===========================
             HEADER PRECIOS (ticker)
             =========================== */
          .fx-value-prices{
            display:flex;
            flex-wrap:wrap;
            align-items:baseline;
            gap: 10px;
          }

          .fx-price-item{ white-space: nowrap; }

          .fx-price-val{
            font-size: 26px;
            font-weight: 950;
            letter-spacing: -0.01em;
            color: #14324f;
          }

          .fx-price-name{
            font-size: 18px;
            font-weight: 800;
            color: #4b5563;
            margin-left: 6px;
          }

          .fx-price-date{
            font-size: 12px;
            font-weight: 500;
            color: #9ca3af;
            margin-left: 6px;
          }

          .fx-price-sep{
            color: rgba(17,24,39,0.25);
            font-weight: 700;
            margin: 0 10px;
          }
        </style>
        """
        ),
        unsafe_allow_html=True,
    )

    # =========================
    # Datos: IPC (Nacional)
    # =========================
    ipc_raw = get_ipc_indec_full()

    # IPC “para el dashboard” (como ya lo venías usando)
    ipc = ipc_raw[ipc_raw["Region"] == "Nacional"].copy()
    if ipc.empty:
        st.warning("Sin datos IPC.")
        return

    ipc["Codigo_str"] = ipc["Codigo"].apply(_clean_code).astype(str).str.strip()
    ipc["Descripcion"] = ipc["Descripcion"].astype(str).str.strip()
    ipc["Periodo"] = pd.to_datetime(ipc["Periodo"], errors="coerce").dt.normalize()
    ipc = ipc.dropna(subset=["Periodo"]).sort_values("Periodo")


    # =========================
    # IPCA (ENGHo 2017/18) base 100=2025
    # (requiere Indice_IPC en el DF de IPC)
    # =========================
    ipca = _compute_ipca_base_2025(ipc_raw)

    # Labels IPC
    label_fix = {"B": "Bienes", "S": "Servicios"}
    sel = ipc[["Codigo_str", "Descripcion"]].drop_duplicates().copy()

    def _is_empty_desc(d: str) -> bool:
        ds = str(d).strip().lower()
        return ds in ("", "nan", "none")

    def build_label(code: str, desc: str) -> str:
        code = str(code).strip()
        if code.isdigit() and int(code) == 0:
            return "Nivel general"
        if not _is_empty_desc(desc):
            return str(desc).strip()
        if code in label_fix:
            return label_fix[code]
        return code

    sel["Label"] = sel.apply(lambda r: build_label(r["Codigo_str"], r["Descripcion"]), axis=1)
    sel["ord0"] = sel["Label"].apply(lambda x: 0 if _is_nivel_general(x) else 1)
    sel = sel.sort_values(["ord0", "Label"]).drop(columns=["ord0"])

    options_ipc = sel["Codigo_str"].tolist()
    code_to_label = dict(zip(sel["Codigo_str"], sel["Label"]))

    def _find_code_by_label(name: str) -> str | None:
        name = str(name).strip().lower()
        for c, lab in code_to_label.items():
            if str(lab).strip().lower() == name:
                return c
        return None

    ipc_code_general = _find_code_by_label("nivel general") or (options_ipc[0] if options_ipc else None)
    ipc_code_servicios = _find_code_by_label("servicios")
    ipc_code_bienes = _find_code_by_label("bienes")

    if ipca.empty: st.warning("IPCA vacío (revisar filtros de divisiones 1..12 / clasificador).")


    # =========================
    # Datos: IPIM (INDEC)
    # =========================
    IPIM_URL = "https://www.indec.gob.ar/ftp/cuadros/economia/indice_ipim.csv"

    @st.cache_data(ttl=12 * 60 * 60)
    def _load_ipim_simple() -> pd.DataFrame:
        r = requests.get(IPIM_URL, timeout=60)
        r.raise_for_status()
        raw = r.content

        df = None
        for sep in [";", ",", "\t"]:
            try:
                tmp = pd.read_csv(io.BytesIO(raw), sep=sep, engine="python")
                if tmp is None or tmp.empty:
                    continue
                cols = [c.strip().lower() for c in tmp.columns]
                if ("periodo" in cols) and ("nivel_general_aperturas" in cols) and ("indice_ipim" in cols):
                    df = tmp
                    break
            except Exception:
                continue

        if df is None or df.empty:
            return pd.DataFrame()

        out = df[["periodo", "nivel_general_aperturas", "indice_ipim"]].copy()
        out = out.rename(columns={"periodo": "Periodo_raw", "nivel_general_aperturas": "Apertura_raw", "indice_ipim": "Indice_raw"})

        out["Apertura"] = (
            out["Apertura_raw"].astype(str).str.strip().str.lower()
            .str.replace("\u00a0", " ", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(" ", "_", regex=False)
            .str.replace("__", "_", regex=False)
        )

        per = pd.to_datetime(out["Periodo_raw"].astype(str).str.strip(), format="%Y-%m-%d", errors="coerce")
        out["Periodo"] = per.dt.to_period("M").dt.to_timestamp(how="start")

        s = out["Indice_raw"].astype(str).str.strip()
        s = s.str.replace("\u00a0", " ", regex=False).str.replace(" ", "", regex=False)
        has_comma = s.str.contains(",", na=False)
        s.loc[has_comma] = s.loc[has_comma].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        out["Indice"] = pd.to_numeric(s, errors="coerce")

        out = out.dropna(subset=["Periodo", "Apertura", "Indice"]).sort_values(["Apertura", "Periodo"]).reset_index(drop=True)
        return out

    ipim = _load_ipim_simple()
    if ipim.empty:
        st.warning("No se pudo cargar IPIM (INDEC).")
        return

    ipim = ipim.sort_values(["Apertura", "Periodo"]).copy()
    ipim["v_m"] = ipim.groupby("Apertura")["Indice"].pct_change(1) * 100
    ipim["v_i_a"] = ipim.groupby("Apertura")["Indice"].pct_change(12) * 100

    # ============================================================
    # 0) PANEL NUEVO ARRIBA: PRECIOS (IPC + IPIM Manufacturados + IPCA)
    # ============================================================
    if "precios_medida" not in st.session_state:
        st.session_state["precios_medida"] = "Mensual"
    if "precios_vars" not in st.session_state:
        st.session_state["precios_vars"] = ["IPC - General", "IPIM - Manufacturados"]

    if st.session_state["precios_medida"] not in ["Mensual", "Anual", "Acumulado"]:
        st.session_state["precios_medida"] = "Mensual"

    PRECIOS_OPTIONS = [
        "IPC - General", "IPC - Servicios", "IPC - Bienes",
        "IPC (ENGHo 2017/18, base 100=2025)",
        "IPIM - Manufacturados",
    ]

    precios_map = {
        "IPC - General": ("ipc", ipc_code_general),
        "IPC - Servicios": ("ipc", ipc_code_servicios),
        "IPC - Bienes": ("ipc", ipc_code_bienes),
        "IPC (ENGHo 2017/18, base 100=2025)": ("ipca", "ipca"),
        "IPIM - Manufacturados": ("ipim", "d_productos_manufacturados"),
    }

    def _last_val(df: pd.DataFrame, key_col: str, key_val: str, ycol: str) -> tuple[pd.Timestamp | None, float | None]:
        if key_val is None:
            return (None, None)
        s = df[df[key_col] == key_val].dropna(subset=[ycol]).sort_values("Periodo")
        if s.empty:
            return (None, None)
        return (pd.to_datetime(s["Periodo"].iloc[-1]), float(s[ycol].iloc[-1]))

    # header ticker: último mensual de IPC general + IPIM manufacturas
    ipc_last_dt, ipc_last_vm = _last_val(ipc, "Codigo_str", ipc_code_general, "v_m_IPC")
    ipim_last_dt, ipim_last_vm = _last_val(ipim, "Apertura", "d_productos_manufacturados", "v_m")

    parts = []
    if (ipc_last_dt is not None) and (ipc_last_vm is not None) and pd.notna(ipc_last_vm):
        parts.append(
            f'<span class="fx-price-item">'
            f'<span class="fx-price-val">{_fmt_pct_es(ipc_last_vm, 1)}%</span>'
            f'<span class="fx-price-name">IPC</span>'
            f'<span class="fx-price-date">{_mmmyy_es(ipc_last_dt)}</span>'
            f'</span>'
        )
    if (ipim_last_dt is not None) and (ipim_last_vm is not None) and pd.notna(ipim_last_vm):
        parts.append(
            f'<span class="fx-price-item">'
            f'<span class="fx-price-val">{_fmt_pct_es(ipim_last_vm, 1)}%</span>'
            f'<span class="fx-price-name">IPIM manufacturas</span>'
            f'<span class="fx-price-date">{_mmmyy_es(ipim_last_dt)}</span>'
            f'</span>'
        )

    sep = '<span class="fx-price-sep">|</span>'
    header_prices_line = sep.join(parts) if parts else "—"

    st.markdown(
        "\n".join([
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">🏷️</div>',
            '    <div class="fx-title">PRECIOS</div>',
            "  </div>",
            '  <div class="fx-card">',
            f'    <div class="fx-value-prices">{header_prices_line}</div>',
            "  </div>",
            "</div>",
        ]),
        unsafe_allow_html=True,
    )
    st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

    _apply_panel_wrap("precios_panel_marker")

    c1p, c2p = st.columns(2, gap="large")
    with c1p:
        st.markdown("<div class='fx-panel-title'>Seleccioná la medida</div>", unsafe_allow_html=True)
        st.selectbox(
            "",
            ["Mensual", "Anual", "Acumulado"],
            index=["Mensual", "Anual", "Acumulado"].index(st.session_state["precios_medida"]),
            label_visibility="collapsed",
            key="precios_medida",
        )

    with c2p:
        st.markdown("<div class='fx-panel-title'>Seleccioná la variable</div>", unsafe_allow_html=True)
        st.multiselect(
            "",
            PRECIOS_OPTIONS,
            label_visibility="collapsed",
            key="precios_vars",
        )

    selected_precios = st.session_state.get("precios_vars", [])
    if not selected_precios:
        selected_precios = ["IPC - General", "IPIM - Manufacturados"]
        st.session_state["precios_vars"] = selected_precios

    # rango PRECIOS (default ene-24)
    all_dates = []
    for opt in selected_precios:
        which, code = precios_map.get(opt, (None, None))
        if which is None or code is None:
            continue

        if which == "ipc":
            d = ipc[ipc["Codigo_str"] == code][["Periodo"]].dropna()
        elif which == "ipim":
            d = ipim[ipim["Apertura"] == code][["Periodo"]].dropna()
        else:  # ipca
            d = ipca[ipca["Serie"] == "ipca"][["Periodo"]].dropna()

        all_dates.append(d)

    if not all_dates:
        st.warning("No hay datos para las selecciones.")
        return

    dates_union = pd.concat(all_dates, ignore_index=True)
    min_dt = pd.to_datetime(dates_union["Periodo"].min()).to_period("M").to_timestamp(how="start")
    max_dt = pd.to_datetime(dates_union["Periodo"].max()).to_period("M").to_timestamp(how="start")

    months = pd.date_range(min_dt, max_dt, freq="MS")
    if len(months) < 2:
        st.warning("Rango insuficiente.")
        return

    jan24 = pd.Timestamp("2024-01-01")
    default_start = jan24 if jan24 in set(months) else months[0]
    default_end = months[-1]

    st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
    start_m_p, end_m_p = st.select_slider(
        "",
        options=list(months),
        value=(default_start, default_end),
        format_func=lambda d: _mmmyy_es(d),
        label_visibility="collapsed",
        key="precios_range_month",
    )
    start_m_p = pd.to_datetime(start_m_p).normalize()
    end_m_p = pd.to_datetime(end_m_p).normalize()
    end_exclusive_p = end_m_p + pd.DateOffset(months=1)

    # construir series para gráfico PRECIOS
    medida_p = st.session_state.get("precios_medida", "Mensual")

    fig = go.Figure()
    ylab = "Variación (%)"

    for opt in selected_precios:
        which, code = precios_map.get(opt, (None, None))
        if which is None or code is None:
            continue

        if which == "ipc":
            d = ipc[ipc["Codigo_str"] == code].copy()
            d = d[(d["Periodo"] >= start_m_p) & (d["Periodo"] < end_exclusive_p)].sort_values("Periodo")
            if d.empty:
                continue

            if medida_p == "Mensual":
                y = d["v_m_IPC"]
                ylab = "Variación mensual (%)"
            elif medida_p == "Anual":
                y = d["v_i_a_IPC"]
                ylab = "Variación anual (%)"
            else:
                d["acc_range"] = _range_accum_from_monthly_pct(d, "Periodo", "Codigo_str", "v_m_IPC", start_m_p)
                y = d["acc_range"]
                ylab = "Variación acumulada (%)"

            d["y"] = pd.to_numeric(y, errors="coerce")
            d = d.dropna(subset=["Periodo", "y"])
            if d.empty:
                continue

        elif which == "ipim":
            d = ipim[ipim["Apertura"] == code].copy()
            d = d[(d["Periodo"] >= start_m_p) & (d["Periodo"] < end_exclusive_p)].sort_values("Periodo")
            if d.empty:
                continue

            if medida_p == "Mensual":
                y = d["v_m"]
                ylab = "Variación mensual (%)"
            elif medida_p == "Anual":
                y = d["v_i_a"]
                ylab = "Variación anual (%)"
            else:
                d["acc_range"] = _range_accum_from_index(d, "Periodo", "Apertura", "Indice", start_m_p)
                y = d["acc_range"]
                ylab = "Variación acumulada (%)"

            d["y"] = pd.to_numeric(y, errors="coerce")
            d = d.dropna(subset=["Periodo", "y"])
            if d.empty:
                continue

        else:  # ipca
            d = ipca.copy()
            d = d[(d["Periodo"] >= start_m_p) & (d["Periodo"] < end_exclusive_p)].sort_values("Periodo")
            if d.empty:
                continue

            if medida_p == "Mensual":
                y = d["v_m"]
                ylab = "Variación mensual (%)"
            elif medida_p == "Anual":
                y = d["v_i_a"]
                ylab = "Variación anual (%)"
            else:
                # acumulado desde índice (base = start del slider)
                d["acc_range"] = _range_accum_from_index(d, "Periodo", "Serie", "Indice", start_m_p)
                y = d["acc_range"]
                ylab = "Variación acumulada (%)"

            d["y"] = pd.to_numeric(y, errors="coerce")
            d = d.dropna(subset=["Periodo", "y"])
            if d.empty:
                continue

        fig.add_trace(
            go.Scatter(
                x=d["Periodo"],
                y=d["y"],
                name=opt,
                mode="lines+markers",
                marker=dict(size=5),
                hovertemplate="%{y:.1f}%<extra></extra>",
            )
        )

    if len(fig.data) == 0:
        st.warning("No hay datos en el rango seleccionado.")
        return

    # ticks adaptativos
    x_min = start_m_p
    x_max = end_m_p + pd.DateOffset(months=1)
    all_ticks = pd.date_range(x_min, x_max, freq="MS")
    n_months = len(all_ticks)
    step = _tick_step_from_months(n_months)
    tickvals = list(all_ticks[::step])
    ticktext = [_mmmyy_es(d) for d in tickvals]

    fig.update_layout(
        hovermode="x unified",
        height=520,
        margin=dict(l=10, r=20, t=10, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1.0),
        dragmode=False,
    )
    fig.update_yaxes(title_text=ylab, ticksuffix="%", fixedrange=False)
    fig.update_xaxes(
        title_text="",
        range=[x_min, x_max],
        tickmode="array",
        tickvals=tickvals,
        ticktext=ticktext,
        tickangle=-90 if n_months >= 36 else 0,
        fixedrange=False,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
        key="precios_chart",
    )

    st.markdown(
        "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:6px;'>Fuente: INDEC.</div>",
        unsafe_allow_html=True,
    )

    # ============================================================
    # DESDE ACÁ (st.divider() de IPC en adelante) ES IGUAL A TU ARCHIVO
    # (no hay cambios para sumar IPCA al primer gráfico)
    # ============================================================

    # ============================================================
    # 1) IPC (Nacional) — PANEL (abajo) + Acumulado por rango
    # ============================================================
    st.divider()

    # Negrita en dropdown IPC (JS)
    st.markdown("<span id='ipc_bold_options_marker'></span>", unsafe_allow_html=True)
    components.html(
        """
        <script>
        (function () {
          const targets = new Set(["nivel general","bienes","estacional","núcleo","nucleo","regulados","servicios"]);
          function applyBold() {
            const doc = window.parent.document;
            const opts = doc.querySelectorAll('div[role="option"]');
            if (!opts || !opts.length) return;
            opts.forEach(el => {
              const t = (el.innerText || "").trim().toLowerCase();
              if (targets.has(t) || Array.from(targets).some(k => t.startsWith(k))) el.style.fontWeight = "800";
            });
          }
          applyBold();
          const obs = new MutationObserver(() => applyBold());
          obs.observe(window.parent.document.body, { childList: true, subtree: true });
          setTimeout(() => obs.disconnect(), 8000);
        })();
        </script>
        """,
        height=0,
    )

    # --- TODO LO QUE SIGUE, PEGALO TAL CUAL DE TU VERSION ORIGINAL ---
    # (no toqué nada más)

    DEFAULT_SELECTED = [ipc_code_general] if ipc_code_general else []
    if "ipc_medida" not in st.session_state:
        st.session_state["ipc_medida"] = "Mensual"
    if "ipc_vars" not in st.session_state:
        st.session_state["ipc_vars"] = DEFAULT_SELECTED

    medida_state = st.session_state.get("ipc_medida", "Mensual")
    selected_state = st.session_state.get("ipc_vars", DEFAULT_SELECTED)

    if medida_state not in ["Mensual", "Anual", "Acumulado"]:
        medida_state = "Mensual"
    if not isinstance(selected_state, (list, tuple)) or len(selected_state) == 0:
        selected_state = DEFAULT_SELECTED

    header_code = ipc_code_general if ipc_code_general in options_ipc else (selected_state[0] if selected_state else options_ipc[0])
    hdr_label = code_to_label.get(header_code, header_code)

    # valor grande del header: mensual/anual; si acumulado, mostramos mensual (porque el acumulado depende del rango)
    if medida_state == "Mensual":
        y_col_hdr = "v_m_IPC"
        medida_txt_hdr = "Variación mensual"
    elif medida_state == "Anual":
        y_col_hdr = "v_i_a_IPC"
        medida_txt_hdr = "Variación anual"
    else:
        y_col_hdr = "v_m_IPC"
        medida_txt_hdr = "Variación acumulada"

    hdr_series = ipc[ipc["Codigo_str"] == header_code].dropna(subset=[y_col_hdr]).sort_values("Periodo")
    if hdr_series.empty:
        st.warning("Sin datos para armar el header IPC.")
        return

    last_period = pd.to_datetime(hdr_series["Periodo"].iloc[-1])
    last_val = float(hdr_series[y_col_hdr].iloc[-1])

    hdr_all = ipc[ipc["Codigo_str"] == header_code].sort_values("Periodo")
    m_val = hdr_all["v_m_IPC"].dropna().iloc[-1] if hdr_all["v_m_IPC"].dropna().shape[0] else np.nan
    a_val = hdr_all["v_i_a_IPC"].dropna().iloc[-1] if hdr_all["v_i_a_IPC"].dropna().shape[0] else np.nan
    a_m, cls_m = _arrow_cls(m_val if pd.notna(m_val) else np.nan)
    a_a, cls_a = _arrow_cls(a_val if pd.notna(a_val) else np.nan)

    hdr_label_txt = "Nivel General" if str(hdr_label).strip().lower() == "nivel general" else hdr_label

    st.markdown(
        "\n".join([
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">🛒</div>',
            '    <div class="fx-title">IPC</div>',
            "  </div>",
            '  <div class="fx-card">',
            '    <div class="fx-row">',
            f'      <div class="fx-value">{_fmt_pct_es(last_val, 1)}%</div>',
            '      <div class="fx-meta">',
            f'        {hdr_label_txt}<span class="sep">|</span>{medida_txt_hdr}<span class="sep">|</span>{_mmmyy_es(last_period)}',
            "      </div>",
            '      <div class="fx-pills">',
            '        <div class="fx-pill red">',
            f'          <span class="fx-arrow {cls_m}">{a_m}</span>',
            f'          <span class="{cls_m}">{_fmt_pct_es(m_val, 1) if pd.notna(m_val) else "—"}%</span>',
            '          <span class="lab">mensual</span>',
            "        </div>",
            '        <div class="fx-pill green">',
            f'          <span class="fx-arrow {cls_a}">{a_a}</span>',
            f'          <span class="{cls_a}">{_fmt_pct_es(a_val, 1) if pd.notna(a_val) else "—"}%</span>',
            '          <span class="lab">anual</span>',
            "        </div>",
            "      </div>",
            "    </div>",
            "  </div>",
            "</div>",
        ]),
        unsafe_allow_html=True,
    )
    st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

    _apply_panel_wrap("ipc_panel_marker")

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("<div class='fx-panel-title'>Seleccioná la medida</div>", unsafe_allow_html=True)
        medida = st.selectbox(
            "",
            ["Mensual", "Anual", "Acumulado"],
            index=["Mensual", "Anual", "Acumulado"].index(medida_state),
            label_visibility="collapsed",
            key="ipc_medida",
        )

    with c2:
        st.markdown("<div class='fx-panel-title'>Seleccioná la variable</div>", unsafe_allow_html=True)
        selected = st.multiselect(
            "",
            options=options_ipc,
            format_func=lambda c: code_to_label.get(c, c),
            label_visibility="collapsed",
            key="ipc_vars",
        )

    if not selected:
        selected = DEFAULT_SELECTED
        st.session_state["ipc_vars"] = selected

    # rango IPC
    df_sel = ipc[ipc["Codigo_str"].isin(selected)].dropna(subset=["Periodo"]).copy()
    if df_sel.empty:
        st.warning("No hay datos para esa selección.")
        return

    min_m = pd.to_datetime(df_sel["Periodo"].min()).to_period("M").to_timestamp(how="start")
    max_m = pd.to_datetime(df_sel["Periodo"].max()).to_period("M").to_timestamp(how="start")
    months = pd.date_range(min_m, max_m, freq="MS")
    if len(months) < 2:
        st.warning("Rango insuficiente para armar selector mensual.")
        return

    default_start = months[max(0, len(months) - 24)]
    default_end = months[-1]

    st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
    start_m, end_m = st.select_slider(
        "",
        options=list(months),
        value=(default_start, default_end),
        format_func=lambda d: _mmmyy_es(d),
        label_visibility="collapsed",
        key="ipc_range_month",
    )
    start_m = pd.to_datetime(start_m).normalize()
    end_m = pd.to_datetime(end_m).normalize()
    end_exclusive = end_m + pd.DateOffset(months=1)

    df_plot = df_sel[(df_sel["Periodo"] >= start_m) & (df_sel["Periodo"] < end_exclusive)].copy()
    if df_plot.empty:
        st.warning("No hay datos en el rango seleccionado.")
        return

    if medida == "Mensual":
        y_axis_label = "Variación mensual (%)"
        def _get_y_ipc(one: pd.DataFrame) -> pd.Series:
            return one["v_m_IPC"]
    elif medida == "Anual":
        y_axis_label = "Variación anual (%)"
        def _get_y_ipc(one: pd.DataFrame) -> pd.Series:
            return one["v_i_a_IPC"]
    else:
        y_axis_label = "Variación acumulada (%)"
        def _get_y_ipc(one: pd.DataFrame) -> pd.Series:
            one = one.copy()
            one["acc_range"] = _range_accum_from_monthly_pct(one, "Periodo", "Codigo_str", "v_m_IPC", start_m)
            return one["acc_range"]

    x_min = start_m
    x_max = end_m + pd.DateOffset(months=1)
    all_ticks = pd.date_range(x_min, x_max, freq="MS")
    n_months = len(all_ticks)
    step = _tick_step_from_months(n_months)
    tickvals = list(all_ticks[::step])
    ticktext = [_mmmyy_es(d) for d in tickvals]

    fig = go.Figure()

    selected_sorted = list(selected)
    if ipc_code_general in selected_sorted:
        selected_sorted = [ipc_code_general] + [c for c in selected_sorted if c != ipc_code_general]

    for c in selected_sorted:
        s = df_plot[df_plot["Codigo_str"] == c].sort_values("Periodo")
        if s.empty:
            continue
        y = pd.to_numeric(_get_y_ipc(s), errors="coerce")
        s = s.assign(y=y).dropna(subset=["y"])
        if s.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=s["Periodo"],
                y=s["y"],
                name=code_to_label.get(c, c),
                mode="lines+markers",
                marker=dict(size=5),
                hovertemplate="%{y:.1f}%<extra></extra>",
            )
        )

    fig.update_layout(
        hovermode="x unified",
        height=520,
        margin=dict(l=10, r=20, t=10, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1.0),
        dragmode=False,
    )
    fig.update_yaxes(title_text=y_axis_label, ticksuffix="%", fixedrange=False)
    fig.update_xaxes(
        title_text="",
        range=[x_min, x_max],
        tickmode="array",
        tickvals=tickvals,
        ticktext=ticktext,
        tickangle=-90 if n_months >= 36 else 0,
        fixedrange=False,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
        key="ipc_chart",
    )

    st.markdown(
        "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:6px;'>Fuente: INDEC.</div>",
        unsafe_allow_html=True,
    )

    # =========================================================
    # 2) IPIM (INDEC) — PANEL (abajo) + Acumulado por rango
    # + DEFAULT: Nacionales → Industria → Nivel general
    # =========================================================
    st.divider()

    SERIES_TOP = {
        "ng_nivel_general": "Nivel general",
        "n_productos_nacionales": "Productos nacionales",
        "i_productos_importados": "Productos importados",
    }

    SERIES_NAC = {
        "1_primarios": "Primarios",
        "d_productos_manufacturados": "Industria (manufacturados)",
        "e_energia_electrica": "Energía eléctrica",
    }

    def _is_2digit_industry(code: str) -> bool:
        return bool(re.match(r"^\d{2}_", str(code)))

    def _industry_2digit_codes(df: pd.DataFrame) -> list[str]:
        codes = sorted({c for c in df["Apertura"].unique() if _is_2digit_industry(c)})
        out = []
        for c in codes:
            try:
                n = int(str(c)[:2])
                if 15 <= n <= 36:
                    out.append(c)
            except Exception:
                continue
        return out

    def _pretty_industry_label(code: str) -> str:
        s = str(code)
        rest = s[3:].replace("_", " ").strip()
        rest = rest[:1].upper() + rest[1:] if rest else rest
        return rest

    def _final_label(code: str) -> str:
        if code in SERIES_TOP:
            return SERIES_TOP[code]
        if code in SERIES_NAC:
            return SERIES_NAC[code]
        if code == "d_productos_manufacturados":
            return "Nivel general (industria)"
        if _is_2digit_industry(code):
            return _pretty_industry_label(code)
        return code

    # defaults IPIM pedido
    if "ipim_medida_simple" not in st.session_state:
        st.session_state["ipim_medida_simple"] = "Mensual"
    if "ipim_var_simple" not in st.session_state:
        st.session_state["ipim_var_simple"] = "n_productos_nacionales"
    if "ipim_nac_group" not in st.session_state:
        st.session_state["ipim_nac_group"] = "d_productos_manufacturados"
    if "ipim_industry_multi" not in st.session_state:
        st.session_state["ipim_industry_multi"] = ["__nivel_general_industria__"]

    if st.session_state["ipim_medida_simple"] not in ["Mensual", "Anual", "Acumulado"]:
        st.session_state["ipim_medida_simple"] = "Mensual"

    _apply_panel_wrap("ipim_panel_marker")

    # HEADER fijo IPIM (nivel general)
    HEADER_CODE = "d_productos_manufacturados"
    medida_state = st.session_state.get("ipim_medida_simple", "Mensual")
    if medida_state not in ["Mensual", "Anual", "Acumulado"]:
        medida_state = "Mensual"

    if medida_state == "Mensual":
        y_col_hdr = "v_m"
        medida_txt_hdr = "Variación mensual"
    elif medida_state == "Anual":
        y_col_hdr = "v_i_a"
        medida_txt_hdr = "Variación anual"
    else:
        y_col_hdr = "v_m"
        medida_txt_hdr = "Variación acumulada"

    hdr = ipim[ipim["Apertura"] == HEADER_CODE].dropna(subset=[y_col_hdr]).sort_values("Periodo")
    if hdr.empty:
        st.warning("IPIM: sin datos para armar el header (Nivel general).")
        return

    last_period = pd.to_datetime(hdr["Periodo"].iloc[-1])
    last_val = float(hdr[y_col_hdr].iloc[-1])

    at = ipim[ipim["Apertura"] == HEADER_CODE].sort_values("Periodo")
    m_val = at["v_m"].dropna().iloc[-1] if at["v_m"].dropna().shape[0] else np.nan
    a_val = at["v_i_a"].dropna().iloc[-1] if at["v_i_a"].dropna().shape[0] else np.nan

    a_m, cls_m = _arrow_cls(m_val if pd.notna(m_val) else np.nan)
    a_a, cls_a = _arrow_cls(a_val if pd.notna(a_val) else np.nan)

    st.markdown(
        "\n".join([
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">🏭</div>',
            '    <div class="fx-title">IPIM</div>',
            "  </div>",
            '  <div class="fx-card">',
            '    <div class="fx-row">',
            f'      <div class="fx-value">{_fmt_pct_es(last_val, 1)}%</div>',
            '      <div class="fx-meta">',
            f'         IPIM manufacturas<span class="sep">|</span>{medida_txt_hdr}<span class="sep">|</span>{_mmmyy_es(last_period)}',
            "      </div>",
            '      <div class="fx-pills">',
            '        <div class="fx-pill red">',
            f'          <span class="fx-arrow {cls_m}">{a_m}</span>',
            f'          <span class="{cls_m}">{_fmt_pct_es(m_val, 1) if pd.notna(m_val) else "—"}%</span>',
            '          <span class="lab">mensual</span>',
            "        </div>",
            '        <div class="fx-pill green">',
            f'          <span class="fx-arrow {cls_a}">{a_a}</span>',
            f'          <span class="{cls_a}">{_fmt_pct_es(a_val, 1) if pd.notna(a_val) else "—"}%</span>',
            '          <span class="lab">anual</span>',
            "        </div>",
            "      </div>",
            "    </div>",
            "  </div>",
            "</div>",
        ]),
        unsafe_allow_html=True,
    )
    st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

    # CONTROLES IPIM fila 1
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("<div class='fx-panel-title'>Seleccioná la medida</div>", unsafe_allow_html=True)
        st.selectbox(
            "",
            ["Mensual", "Anual", "Acumulado"],
            label_visibility="collapsed",
            key="ipim_medida_simple",
        )

    with c2:
        st.markdown("<div class='fx-panel-title'>Seleccioná la variable</div>", unsafe_allow_html=True)
        vars_codes = list(SERIES_TOP.keys())
        st.selectbox(
            "",
            vars_codes,
            format_func=lambda c: SERIES_TOP.get(c, c),
            label_visibility="collapsed",
            key="ipim_var_simple",
        )

    # CONTROLES IPIM fila 2
    c3, c4 = st.columns(2, gap="large")

    medida_now = st.session_state["ipim_medida_simple"]
    var_now = st.session_state["ipim_var_simple"]
    final_code = var_now  # str o list[str]

    with c3:
        if var_now == "n_productos_nacionales":
            st.markdown("<div class='fx-panel-title'>Apertura (nacionales)</div>", unsafe_allow_html=True)
            nac_codes = list(SERIES_NAC.keys())

            nac_state = st.session_state.get("ipim_nac_group", "d_productos_manufacturados")
            if nac_state not in SERIES_NAC:
                nac_state = "d_productos_manufacturados"
                st.session_state["ipim_nac_group"] = nac_state

            st.selectbox(
                "",
                nac_codes,
                format_func=lambda c: SERIES_NAC.get(c, c),
                label_visibility="collapsed",
                key="ipim_nac_group",
            )
            final_code = st.session_state["ipim_nac_group"]

    with c4:
        if var_now == "n_productos_nacionales" and st.session_state.get("ipim_nac_group") == "d_productos_manufacturados":
            st.markdown("<div class='fx-panel-title'>Industria (manufacturados)</div>", unsafe_allow_html=True)

            ind2 = _industry_2digit_codes(ipim)
            ind_options = ["__nivel_general_industria__"] + ind2

            # limpiar selección actual según opciones vigentes
            cur_sel = st.session_state.get("ipim_industry_multi", ["__nivel_general_industria__"])
            cur_sel = [x for x in cur_sel if x in ind_options]
            if not cur_sel:
                cur_sel = ["__nivel_general_industria__"]
            st.session_state["ipim_industry_multi"] = cur_sel

            st.multiselect(
                "",
                ind_options,
                format_func=lambda c: ("Nivel general (industria)" if c == "__nivel_general_industria__" else _pretty_industry_label(c)),
                label_visibility="collapsed",
                key="ipim_industry_multi",
            )

            sel_multi = st.session_state.get("ipim_industry_multi", ["__nivel_general_industria__"])
            final_code = ["d_productos_manufacturados" if x == "__nivel_general_industria__" else x for x in sel_multi]

    codes_to_plot = final_code if isinstance(final_code, list) else [final_code]


    df_sel = ipim[ipim["Apertura"].isin(codes_to_plot)].dropna(subset=["Periodo"]).copy()
    if df_sel.empty:
        st.info("No hay datos para esa selección.")
        return

    min_dt = pd.to_datetime(df_sel["Periodo"].min()).to_period("M").to_timestamp(how="start")
    max_dt = pd.to_datetime(df_sel["Periodo"].max()).to_period("M").to_timestamp(how="start")
    months = pd.date_range(min_dt, max_dt, freq="MS")
    if len(months) < 2:
        st.warning("Rango insuficiente para armar selector mensual.")
        return

    default_start = months[max(0, len(months) - 24)]
    default_end = months[-1]

    st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
    start_m, end_m = st.select_slider(
        "",
        options=list(months),
        value=(default_start, default_end),
        format_func=lambda d: _mmmyy_es(d),
        label_visibility="collapsed",
        key="ipim_range_months_simple",
    )
    start_m = pd.to_datetime(start_m).normalize()
    end_m = pd.to_datetime(end_m).normalize()
    end_exclusive = end_m + pd.DateOffset(months=1)

    x_min = start_m
    x_max = end_m + pd.DateOffset(months=1)
    all_ticks = pd.date_range(x_min, x_max, freq="MS")
    n_months = len(all_ticks)
    step = _tick_step_from_months(n_months)
    tickvals = list(all_ticks[::step])
    ticktext = [_mmmyy_es(d) for d in tickvals]

    if medida_now == "Mensual":
        y_axis_label = "Variación mensual (%)"
        def _get_y_ipim(one: pd.DataFrame) -> pd.Series:
            return one["v_m"]
    elif medida_now == "Anual":
        y_axis_label = "Variación anual (%)"
        def _get_y_ipim(one: pd.DataFrame) -> pd.Series:
            return one["v_i_a"]
    else:
        y_axis_label = "Variación acumulada (%)"
        def _get_y_ipim(one: pd.DataFrame) -> pd.Series:
            one = one.copy()
            one["acc_range"] = _range_accum_from_index(one, "Periodo", "Apertura", "Indice", start_m)
            return one["acc_range"]

    fig = go.Figure()

    for code in codes_to_plot:
        df_one = ipim[ipim["Apertura"] == code].copy()
        df_one = df_one[(df_one["Periodo"] >= start_m) & (df_one["Periodo"] < end_exclusive)].sort_values("Periodo")
        if df_one.empty:
            continue

        y = pd.to_numeric(_get_y_ipim(df_one), errors="coerce")
        df_one = df_one.assign(y=y).dropna(subset=["y"])
        if df_one.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=df_one["Periodo"],
                y=df_one["y"],
                name=_final_label(code),
                mode="lines+markers",
                marker=dict(size=5),
                hovertemplate="%{y:.1f}%<extra></extra>",
            )
        )

    if len(fig.data) == 0:
        st.warning("No hay datos en el rango seleccionado.")
        return

    fig.update_layout(
        hovermode="x unified",
        height=520,
        margin=dict(l=10, r=10, t=10, b=60),
        showlegend=(len(codes_to_plot) > 1),
        dragmode=False,
        legend=dict(
            x=0.99, y=0.99,
            xanchor="right", yanchor="top",
            orientation="v",
            bgcolor="rgba(255,255,255,0.0)",
            borderwidth=0,
            itemsizing="constant",
        ),
    )
    fig.update_yaxes(title_text=y_axis_label, ticksuffix="%", fixedrange=False)
    fig.update_xaxes(
        title_text="",
        range=[x_min, x_max],
        tickmode="array",
        tickvals=tickvals,
        ticktext=ticktext,
        tickangle=-90 if n_months >= 36 else 0,
        fixedrange=False,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
        key="ipim_simple_chart",
    )

    st.markdown(
        "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:6px;'>Fuente: INDEC.</div>",
        unsafe_allow_html=True,
    )

        # ============================================================
    # Calculadora de actualización por inflación
    # ============================================================
    st.divider()

    st.markdown(
        """
        <div class="fx-panel-title">Calculadora de actualización por inflación</div>
        """,
        unsafe_allow_html=True,
    )

    # IPC Nivel General - Nacional
    ipc_calc = ipc[ipc["Codigo_str"] == ipc_code_general].copy()
    ipc_calc = ipc_calc.dropna(subset=["Periodo", "Indice_IPC"]).sort_values("Periodo")

    if ipc_calc.empty:
        st.warning("No hay datos suficientes de IPC Nivel General para calcular la actualización.")
    else:
        ipc_calc["Periodo"] = pd.to_datetime(ipc_calc["Periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp(how="start")
        ipc_calc["Indice_IPC"] = pd.to_numeric(ipc_calc["Indice_IPC"], errors="coerce")
        ipc_calc = ipc_calc.dropna(subset=["Periodo", "Indice_IPC"]).sort_values("Periodo")

        meses_calc = list(ipc_calc["Periodo"].drop_duplicates())

        col_monto, col_desde, col_hasta = st.columns([1.2, 1, 1], gap="large")

        with col_monto:
            monto_inicial = st.number_input(
                "Monto inicial ($)",
                min_value=0.0,
                value=100000.0,
                step=1000.0,
                format="%.2f",
                key="calc_inflacion_monto",
            )

        with col_desde:
            periodo_inicial = st.selectbox(
                "Período inicial",
                options=meses_calc,
                index=max(0, len(meses_calc) - 13),
                format_func=lambda d: _mmmyy_es(d),
                key="calc_inflacion_periodo_inicial",
            )

        with col_hasta:
            periodo_final = st.selectbox(
                "Período final",
                options=meses_calc,
                index=len(meses_calc) - 1,
                format_func=lambda d: _mmmyy_es(d),
                key="calc_inflacion_periodo_final",
            )

        periodo_inicial = pd.to_datetime(periodo_inicial).to_period("M").to_timestamp(how="start")
        periodo_final = pd.to_datetime(periodo_final).to_period("M").to_timestamp(how="start")

        if periodo_final < periodo_inicial:
            st.warning("El período final debe ser igual o posterior al período inicial.")
        else:
            idx_ini = ipc_calc.loc[ipc_calc["Periodo"] == periodo_inicial, "Indice_IPC"]
            idx_fin = ipc_calc.loc[ipc_calc["Periodo"] == periodo_final, "Indice_IPC"]

            if idx_ini.empty or idx_fin.empty:
                st.warning("No se encontraron índices para los períodos seleccionados.")
            else:
                idx_ini = float(idx_ini.iloc[-1])
                idx_fin = float(idx_fin.iloc[-1])

                if idx_ini <= 0:
                    st.warning("El índice inicial no es válido.")
                else:
                    factor = idx_fin / idx_ini
                    inflacion_acum = (factor - 1) * 100
                    monto_actualizado = monto_inicial * factor

                    st.markdown(
                        f"""
                        <div class="fx-card" style="margin-top: 14px;">
                            <div style="font-size:13px; font-weight:800; color:#2b4660; margin-bottom:6px;">
                                Resultado
                            </div>
                            <div style="font-size:34px; font-weight:950; color:#14324f; line-height:1.1;">
                                ${monto_actualizado:,.2f}
                            </div>
                            <div style="font-size:13px; color:#64748b; margin-top:6px;">
                                ${monto_inicial:,.2f} de {_mmmyy_es(periodo_inicial)} equivalen a 
                                ${monto_actualizado:,.2f} en {_mmmyy_es(periodo_final)}.
                            </div>
                            <div style="font-size:13px; color:#64748b; margin-top:4px;">
                                Inflación acumulada del período: <b>{_fmt_pct_es(inflacion_acum, 1)}%</b>
                            </div>
                        </div>
                        """.replace(",", "X").replace(".", ",").replace("X", "."),
                        unsafe_allow_html=True,
                    )

                    st.markdown(
                        "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:6px;'>"
                        "Fuente: INDEC. Cálculo realizado con IPC Nacional Nivel General."
                        "</div>",
                        unsafe_allow_html=True,
                    )
