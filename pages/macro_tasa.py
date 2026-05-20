import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import numpy as np
import random
import textwrap
import streamlit.components.v1 as components
from services.macro_data import get_monetaria_serie
from ui.common import safe_pct   # 👈 ESTA LÍNEA
from services.macro_data import get_calidad_cartera_long


# ============================================================
# Frases (loading)
# ============================================================
INDU_LOADING_PHRASES = [
    "La industria aporta más del 18% del valor agregado de la economía argentina.",
    "La industria es el segundo mayor empleador privado del país.",
    "Por cada empleo industrial directo se generan casi dos empleos indirectos.",
    "Los salarios industriales son 23% más altos que el promedio privado.",
    "Dos tercios de las exportaciones argentinas provienen de la industria.",
]


# ============================================================
# Helpers
# ============================================================
def _fmt_pct_es(x: float, dec: int = 1) -> str:
    try:
        return f"{float(x):.{dec}f}".replace(".", ",")
    except Exception:
        return "—"


def _fmt_pp_es(x: float, dec: int = 1) -> str:
    try:
        return f"{float(x):.{dec}f}".replace(".", ",") + " pp"
    except Exception:
        return "—"


def _arrow_cls(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ("", "")
    return ("▲", "fx-up") if v >= 0 else ("▼", "fx-down")


def _asof(df_: pd.DataFrame, target: pd.Timestamp, col: str):
    t = df_.dropna(subset=["Date", col]).sort_values("Date")
    t = t[t["Date"] <= target]
    if t.empty:
        return None
    return float(t[col].iloc[-1])


def _rem29_to_daily(df_m: pd.DataFrame) -> pd.DataFrame:
    """REM mensual -> diario (valor mensual repetido y ffill)."""
    if df_m is None or df_m.empty:
        return pd.DataFrame(columns=["Date", "value"])

    df_m = df_m.copy()
    df_m["Date"] = pd.to_datetime(df_m["Date"], errors="coerce").dt.normalize()
    df_m["value"] = pd.to_numeric(df_m["value"], errors="coerce")
    df_m = df_m.dropna(subset=["Date", "value"]).sort_values("Date")

    df_m["Period"] = df_m["Date"].dt.to_period("M")
    df_m = df_m.drop_duplicates("Period", keep="last")

    start = df_m["Period"].min().to_timestamp(how="start")
    end = df_m["Period"].max().to_timestamp(how="end")

    cal = pd.DataFrame({"Date": pd.date_range(start, end, freq="D")})
    cal["Period"] = cal["Date"].dt.to_period("M")

    out = cal.merge(df_m[["Period", "value"]], on="Period", how="left").drop(columns=["Period"])
    out["value"] = out["value"].ffill()
    return out


def _extend_daily_ffill(df_daily: pd.DataFrame, end_date: pd.Timestamp) -> pd.DataFrame:
    """Extiende REM diario hasta end_date con ffill del último dato."""
    if df_daily is None or df_daily.empty:
        return pd.DataFrame({"Date": [end_date], "value": [np.nan]})

    df_daily = df_daily.copy()
    df_daily["Date"] = pd.to_datetime(df_daily["Date"], errors="coerce").dt.normalize()
    df_daily["value"] = pd.to_numeric(df_daily["value"], errors="coerce")
    df_daily = df_daily.dropna(subset=["Date"]).sort_values("Date")

    last_date = df_daily["Date"].max()
    if end_date <= last_date:
        return df_daily

    cal = pd.DataFrame({"Date": pd.date_range(df_daily["Date"].min(), end_date, freq="D")})
    out = cal.merge(df_daily, on="Date", how="left")
    out["value"] = out["value"].ffill()
    return out


# ============================================================
# Main
# ============================================================
def render_macro_tasa(go_to):

    # =========================
    # Volver (afuera del panel)
    # =========================
    if st.button("← Volver"):
        go_to("macro_home")

    # =========================
    # CSS (idéntico look & feel FX)
    # =========================
    st.markdown(
        textwrap.dedent(
            """
        <style>
          /* ===== HEADER ===== */
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

          .fx-title{
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
            font-size: 46px;
            font-weight: 950;
            letter-spacing: -0.02em;
            color: #14324f;
            line-height: 0.95;
          }

          .fx-meta{
            font-size: 13px;
            color: #2b4660;
            font-weight: 700;
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
            font-weight: 700;
            box-shadow: 0 6px 10px rgba(15,55,100,0.08);
          }

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

          /* ===============================
             PANEL GRANDE REAL (aplicado por JS al contenedor de Streamlit)
             =============================== */
          .fx-panel-wrap{
            background: rgba(230, 243, 255, 0.55);
            border: 1px solid rgba(15, 55, 100, 0.10);
            border-radius: 22px;
            padding: 16px 16px 26px 16px;
            box-shadow: 0 10px 18px rgba(15,55,100,0.06);
            margin-top: 10px;
          }

          /* Evitar “cortes” visuales dentro del panel */
          .fx-panel-wrap div[data-testid="stSelectbox"],
          .fx-panel-wrap div[data-testid="stMultiSelect"],
          .fx-panel-wrap div[data-testid="stSlider"],
          .fx-panel-wrap div[data-testid="stPlotlyChart"]{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
          }

          /* Estilo combobox base (default) */
          .fx-panel-wrap div[role="combobox"]{
            border-radius: 16px !important;
            border: 1px solid rgba(15,23,42,0.10) !important;
            background: rgba(255,255,255,0.94) !important;
            box-shadow: 0 10px 18px rgba(15, 55, 100, 0.08) !important;
          }

          /* Selectbox medida estilo chip (oscuro + texto azul) */
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

          /* Tags multiselect (texto BLANCO garantizado) */
          .fx-panel-wrap span[data-baseweb="tag"]{
            background: #0b2a55 !important;
            border-radius: 10px !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
          }
          .fx-panel-wrap span[data-baseweb="tag"] *{
            color: #ffffff !important;
            fill: #ffffff !important;
            font-weight: 800 !important;
          }

          @media (max-width: 900px){
            .fx-row{ grid-template-columns: 1fr; row-gap: 10px; }
            .fx-meta{ white-space: normal; }
            .fx-pills{ justify-content: flex-start; }
          }
        </style>
        """
        ),
        unsafe_allow_html=True,
    )

    # =========================
    # Series
    # =========================
    SERIES_TASAS = {
        13: {"nombre": "Adelantos a Empresas"},
        12: {"nombre": "Plazo Fijo"},
        14: {"nombre": "Préstamos Personales"},
    }
    OPT_INFL = "Inflación esperada (REM 12m)"
    ID_REM = 29

    # =========================
    # Load data
    # =========================
    rem29 = _rem29_to_daily(get_monetaria_serie(ID_REM))

    series_data = {}
    for sid in SERIES_TASAS:
        df = get_monetaria_serie(sid)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["Date", "value"]).sort_values("Date")
        series_data[sid] = df

    if not series_data:
        st.warning("Sin datos para las tasas.")
        return

    max_rate_date = max(df["Date"].max() for df in series_data.values())
    rem29 = _extend_daily_ffill(rem29, max_rate_date)


    # =========================
    # Defaults (state) - SIEMPRE antes de crear widgets
    # =========================
    if "tasa_medida" not in st.session_state:
        st.session_state["tasa_medida"] = "Tasa nominal anual"
    if "tasa_vars" not in st.session_state:
        st.session_state["tasa_vars"] = [SERIES_TASAS[13]["nombre"]]

    # =========================
    # Master DF
    # =========================
    min_date = min(df["Date"].min() for df in series_data.values())
    cal = pd.DataFrame({"Date": pd.date_range(min_date, max_rate_date, freq="D")})
    df_master = cal.copy()

    for sid, meta in SERIES_TASAS.items():
        name = meta["nombre"]

        df_sid = series_data.get(sid)
        if df_sid is None or df_sid.empty:
            # opcional: warning suave (o solo continue para no ensuciar)
            # st.warning(f"Serie BCRA faltante o vacía: {name} (id={sid})")
            continue

        # asegurar columnas y limpieza (por si vino raro)
        if "Date" not in df_sid.columns or "value" not in df_sid.columns:
            # st.warning(f"Serie BCRA con columnas inesperadas: {name} (id={sid}) cols={list(df_sid.columns)}")
            continue

        tmp = df_sid[["Date", "value"]].rename(columns={"value": name})
        df_master = df_master.merge(tmp, on="Date", how="left")

        last = df_sid["Date"].max()
        df_master[name] = pd.to_numeric(df_master[name], errors="coerce").ffill()
        df_master.loc[df_master["Date"] > last, name] = np.nan


    df_master = df_master.merge(rem29.rename(columns={"value": OPT_INFL}), on="Date", how="left")
    df_master[OPT_INFL] = df_master[OPT_INFL].ffill()

    # =========================================================
    # Panel grande: TODO adentro de un container
    # (esto evita que el header quede “abajo”)
    # =========================================================
    with st.container():

        # --- marker + JS PRIMERO en el container ---
        st.markdown("<span id='tasa_panel_marker'></span>", unsafe_allow_html=True)
        components.html(
            """
            <script>
            (function() {
              function applyPanelClass() {
                const marker = window.parent.document.getElementById('tasa_panel_marker');
                if (!marker) return;
                const block = marker.closest('div[data-testid="stVerticalBlock"]');
                if (block) block.classList.add('fx-panel-wrap');
              }

              applyPanelClass();

              let tries = 0;
              const t = setInterval(() => {
                applyPanelClass();
                tries += 1;
                if (tries >= 10) clearInterval(t);
              }, 150);

              const obs = new MutationObserver(() => applyPanelClass());
              obs.observe(window.parent.document.body, { childList: true, subtree: true });
              setTimeout(() => obs.disconnect(), 3000);
            })();
            </script>
            """,
            height=0,
        )

        # =========================
        # Controles (arriba del header? NO: header primero como FX)
        # =========================
        # Primero: calcular opciones vigentes según medida (para limpiar selección)
        base_opts = [v["nombre"] for v in SERIES_TASAS.values()]
        medida_now = st.session_state.get("tasa_medida", "Tasa nominal anual")
        opts_now = base_opts if medida_now != "Tasa nominal anual" else base_opts + [OPT_INFL]

        # limpiar selección inválida ANTES de dibujar widgets
        st.session_state["tasa_vars"] = [v for v in st.session_state.get("tasa_vars", []) if v in opts_now]

        # si quedó vacía, dejamos vacía (para que se vea el warning “Seleccioná una variable”)
        # (no seteamos default acá, porque querés poder borrar todas sin error)

        # =========================
        # Header (usa 1ra variable seleccionada si existe; si no, muestra header “vacío”)
        # =========================
        vars_sel = st.session_state.get("tasa_vars", [])
        base_name = vars_sel[0] if vars_sel else base_opts[0]

        s = df_master[["Date", base_name, OPT_INFL]].copy().sort_values("Date")
        s = s.dropna(subset=[base_name]).copy()

        if st.session_state["tasa_medida"] == "Tasa nominal anual":
            s["VAL"] = pd.to_numeric(s[base_name], errors="coerce")
            unidad = "% TNA"
        else:
            s["VAL"] = (
                (1 + pd.to_numeric(s[base_name], errors="coerce") / 100)
                / (1 + pd.to_numeric(s[OPT_INFL], errors="coerce") / 100)
                - 1
            ) * 100
            unidad = "% real"

        s = s.dropna(subset=["VAL"])

        # si por lo que sea no hay datos, igual dibujamos header sin reventar
        if s.empty:
            last_date = pd.Timestamp(df_master["Date"].max())
            last_val = np.nan
            vm_pp = None
            va_pp = None
        else:
            last_date = pd.to_datetime(s["Date"].iloc[-1])
            last_val = float(s["VAL"].iloc[-1])

            v_m = _asof(s, last_date - pd.Timedelta(days=30), "VAL")
            v_y = _asof(s, last_date - pd.Timedelta(days=365), "VAL")

            vm_pp = None if v_m is None else (last_val - v_m)
            va_pp = None if v_y is None else (last_val - v_y)

        a_vm, cls_vm = _arrow_cls(vm_pp)
        a_va, cls_va = _arrow_cls(va_pp)

        header_lines = [
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">📈</div>',
            '    <div class="fx-title">Tasa de interés</div>',
            "  </div>",
            '  <div class="fx-card">',
            '    <div class="fx-row">',
            f'      <div class="fx-value">{_fmt_pct_es(last_val, 1)}%</div>' if pd.notna(last_val) else '      <div class="fx-value">—</div>',
            '      <div class="fx-meta">',
            f'        {base_name}<span class="sep">|</span>{unidad}<span class="sep">|</span>{pd.to_datetime(last_date).strftime("%d/%m/%Y")}',
            "      </div>",
            '      <div class="fx-pills">',
            '        <div class="fx-pill red">',
            f'          <span class="fx-arrow {cls_vm}">{a_vm}</span>',
            f'          <span class="{cls_vm}">{_fmt_pp_es(vm_pp, 1)}</span>',
            '          <span class="lab">mensual</span>',
            "        </div>",
            '        <div class="fx-pill green">',
            f'          <span class="fx-arrow {cls_va}">{a_va}</span>',
            f'          <span class="{cls_va}">{_fmt_pp_es(va_pp, 1)}</span>',
            '          <span class="lab">interanual</span>',
            "        </div>",
            "      </div>",
            "    </div>",
            "  </div>",
            "</div>",
        ]
        st.markdown("\n".join(header_lines), unsafe_allow_html=True)

        st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

        # =========================
        # Controles (debajo del header, como FX)
        # =========================
        c1, c2 = st.columns(2, gap="large")

        with c1:
            st.markdown("<div class='fx-panel-title'>Seleccioná la medida</div>", unsafe_allow_html=True)
            st.selectbox(
                "",
                ["Tasa nominal anual", "Tasa real (ex-ante, REM 12m)"],
                key="tasa_medida",
                label_visibility="collapsed",
            )

        # recomputar opts según la medida (porque el selectbox puede cambiarla)
        base_opts = [v["nombre"] for v in SERIES_TASAS.values()]
        medida_now = st.session_state.get("tasa_medida", "Tasa nominal anual")
        opts_now = base_opts if medida_now != "Tasa nominal anual" else base_opts + [OPT_INFL]

        # limpiar nuevamente (por si el usuario cambió medida recién)
        st.session_state["tasa_vars"] = [v for v in st.session_state.get("tasa_vars", []) if v in opts_now]

        with c2:
            st.markdown("<div class='fx-panel-title'>Seleccioná la variable</div>", unsafe_allow_html=True)
            st.multiselect(
                "",
                options=opts_now,
                key="tasa_vars",
                label_visibility="collapsed",
            )

        # =========================
        # Guard: sin variables seleccionadas
        # =========================
        vars_sel = st.session_state.get("tasa_vars", [])
        if not vars_sel:
            st.warning("Seleccioná una variable.")
            return

        # =========================
        # Slider (default 2025, min 2004)
        # =========================
        min_slider = max(df_master["Date"].min().date(), pd.Timestamp("2004-01-01").date())
        max_slider = df_master["Date"].max().date()
        start_def = max(pd.Timestamp("2025-01-01").date(), min_slider)

        st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
        start_d, end_d = st.slider(
            "",
            min_value=min_slider,
            max_value=max_slider,
            value=(start_def, max_slider),
            label_visibility="collapsed",
            key="tasa_range",
        )

        df_plot = df_master[
            (df_master["Date"] >= pd.Timestamp(start_d)) & (df_master["Date"] <= pd.Timestamp(end_d))
        ].copy()

        # =========================
        # Plot
        # =========================
        fig = go.Figure()

        for v in vars_sel:
            if v == OPT_INFL:
                # inflación solo en nominal
                if st.session_state["tasa_medida"] == "Tasa nominal anual":
                    fig.add_trace(
                        go.Scatter(
                            x=df_plot["Date"],
                            y=df_plot[OPT_INFL],
                            name=OPT_INFL,
                            mode="lines",
                            line=dict(dash="dot"),
                        )
                    )
                continue

            if st.session_state["tasa_medida"] == "Tasa nominal anual":
                y = df_plot[v]
            else:
                y = ((1 + df_plot[v] / 100) / (1 + df_plot[OPT_INFL] / 100) - 1) * 100

            fig.add_trace(go.Scatter(x=df_plot["Date"], y=y, name=v, mode="lines"))

        # línea 0% (gris oscura)
        fig.add_hline(y=0, line_width=1, line_dash="solid", line_color="rgba(80,80,80,0.7)")

        fig.update_layout(
            height=520,
            hovermode="x",
            margin=dict(l=10, r=10, t=10, b=50),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            dragmode=False,
        )
        fig.update_yaxes(ticksuffix="%")

        # --- Aire a la derecha (10 días)
        x_max = pd.to_datetime(df_plot["Date"].max())
        x_min = pd.to_datetime(df_plot["Date"].min())
        fig.update_xaxes(range=[x_min, x_max + pd.Timedelta(days=10)])



        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
            key="chart_tasas",
        )


        st.markdown(
            "<div style='color:rgba(20,50,79,0.70); font-size:12px;'>"
            "Fuente: Banco Central de la República Argentina."
            "</div>",
            unsafe_allow_html=True,
        )

    # ============================================================
    # RESERVAS INTERNACIONALES BRUTAS (BCRA) — PANEL NUEVO
    # id serie = 1 | millones de USD
    # ============================================================
    st.divider()

    with st.spinner("Cargando reservas internacionales..."):
        reservas = get_monetaria_serie(1)

    if reservas is None or reservas.empty:
        st.warning("Sin datos de Reservas Internacionales Brutas.")
        return

    reservas = reservas.copy()
    reservas["Date"] = pd.to_datetime(reservas["Date"], errors="coerce").dt.normalize()
    reservas["value"] = pd.to_numeric(reservas["value"], errors="coerce")
    reservas = reservas.dropna(subset=["Date", "value"]).sort_values("Date").reset_index(drop=True)

    # 👇 CLAVE: todo adentro de un container, como FX
    with st.container():

        # --- marker + JS PRIMERO ---
        st.markdown("<span id='res_panel_marker'></span>", unsafe_allow_html=True)
        components.html(
            """
            <script>
            (function() {
            function applyResPanelClass() {
                const marker = window.parent.document.getElementById('res_panel_marker');
                if (!marker) return;
                const block = marker.closest('div[data-testid="stVerticalBlock"]');
                if (block) block.classList.add('fx-panel-wrap');
            }

            applyResPanelClass();

            let tries = 0;
            const t = setInterval(() => {
                applyResPanelClass();
                tries += 1;
                if (tries >= 10) clearInterval(t);
            }, 150);

            const obs = new MutationObserver(() => applyResPanelClass());
            obs.observe(window.parent.document.body, { childList: true, subtree: true });
            setTimeout(() => obs.disconnect(), 3000);
            })();
            </script>
            """,
            height=0,
        )

        # -------------------------
        # Header (nivel + % mensual e interanual)
        # -------------------------
        last_date = pd.to_datetime(reservas["Date"].iloc[-1])
        last_val = float(reservas["value"].iloc[-1])

        val_m = _asof_reservas = None
        # usamos helper local rápido con la misma lógica que ya tenés
        def _asof_res(df_: pd.DataFrame, target: pd.Timestamp):
            tt = df_.dropna(subset=["Date", "value"]).sort_values("Date")
            tt = tt[tt["Date"] <= target]
            if tt.empty:
                return None
            return float(tt["value"].iloc[-1])

        val_m = _asof_res(reservas, last_date - pd.Timedelta(days=30))
        val_y = _asof_res(reservas, last_date - pd.Timedelta(days=365))

        vm = None if val_m is None else (last_val / val_m - 1) * 100
        va = None if val_y is None else (last_val / val_y - 1) * 100

        a_vm, cls_vm = _arrow_cls(vm)   # 👈 usa tu helper GLOBAL (NO redefinir)
        a_va, cls_va = _arrow_cls(va)

        vm_txt = safe_pct(vm, 1)
        va_txt = safe_pct(va, 1)

        def _fmt_musd(x: float) -> str:
            try:
                return f"{int(round(float(x))):,}".replace(",", ".")
            except Exception:
                return "—"

        header_lines = [
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">🏦</div>',
            '    <div class="fx-title">Reservas internacionales</div>',
            "  </div>",
            '  <div class="fx-card">',
            '    <div class="fx-row">',
            f'      <div class="fx-value">{_fmt_musd(last_val)}</div>',
            '      <div class="fx-meta">',
            f'        Reservas Internacionales Brutas<span class="sep">|</span>Millones de USD<span class="sep">|</span>{last_date.strftime("%d/%m/%Y")}',
            "      </div>",
            '      <div class="fx-pills">',
            '        <div class="fx-pill red">',
            f'          <span class="fx-arrow {cls_vm}">{a_vm}</span>',
            f'          <span class="{cls_vm}">{vm_txt}</span>',
            '          <span class="lab">mensual</span>',
            "        </div>",
            '        <div class="fx-pill green">',
            f'          <span class="fx-arrow {cls_va}">{a_va}</span>',
            f'          <span class="{cls_va}">{va_txt}</span>',
            '          <span class="lab">interanual</span>',
            "        </div>",
            "      </div>",
            "    </div>",
            "  </div>",
            "</div>",
        ]
        st.markdown("\n".join(header_lines), unsafe_allow_html=True)

        st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

        # -------------------------
        # Controles
        # -------------------------
        if "res_medida" not in st.session_state:
            st.session_state["res_medida"] = "Nivel"

        if "res_var" not in st.session_state:
            st.session_state["res_var"] = "Reservas"

        c1, c2 = st.columns(2, gap="large")

        with c1:
            st.markdown("<div class='fx-panel-title'>Seleccioná la medida</div>", unsafe_allow_html=True)
            res_medida = st.selectbox(
                "",
                ["Nivel", "Variación acumulada"],
                key="res_medida",
                label_visibility="collapsed",
            )

        with c2:
            st.markdown("<div class='fx-panel-title'>Seleccioná la variable</div>", unsafe_allow_html=True)
            res_var = st.selectbox(
                "",
                ["Reservas"],
                key="res_var",
                label_visibility="collapsed",
            )

        # -------------------------
        # Rango de fechas
        # -------------------------
        min_d = reservas["Date"].min().date()
        max_d = reservas["Date"].max().date()
        default_start = max(min_d, pd.Timestamp("2021-01-01").date())

        st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
        start_d, end_d = st.slider(
            "",
            min_value=min_d,
            max_value=max_d,
            value=(default_start, max_d),
            label_visibility="collapsed",
            key="res_rangebar",
        )

        df_plot = reservas[(reservas["Date"] >= pd.Timestamp(start_d)) & (reservas["Date"] <= pd.Timestamp(end_d))].copy()

        # -------------------------
        # Plot
        # -------------------------
        fig = go.Figure()

        y0 = df_plot["value"].copy()

        if res_medida == "Variación acumulada":
            base_series = y0.dropna()
            base = float(base_series.iloc[0]) if not base_series.empty else np.nan

            y = (y0 / base - 1) * 100

            fig.add_trace(
                go.Scatter(
                    x=df_plot["Date"],
                    y=y,
                    name="Reservas (var. acum.)",
                    mode="lines",
                    hovertemplate="%{x|%d/%m/%Y}<br>Variación acumulada: %{y:.2f}%<extra></extra>",
                )
            )

            # línea 0 gris oscura
            fig.add_hline(y=0, line_width=1, line_dash="solid", line_color="rgba(80,80,80,0.7)")

            # eje en %
            fig.update_yaxes(ticksuffix="%")

        else:
            fig.add_trace(
                go.Scatter(
                    x=df_plot["Date"],
                    y=y0,
                    name="Reservas",
                    mode="lines",
                    hovertemplate="%{x|%d/%m/%Y}<br>Millones USD: %{y:,.0f}<extra></extra>"
                    .replace(",", "X").replace(".", ",").replace("X", "."),
                )
            )

            # --- Formato argentino del eje Y SOLO en NIVEL (20.000, 30.000, etc.) ---
            y_min = float(np.nanmin(y0.values))
            y_max = float(np.nanmax(y0.values))

            step = 5000  # ajustable (10000 si querés menos ticks)
            ticks = np.arange(
                np.floor(y_min / step) * step,
                np.ceil(y_max / step) * step + step,
                step
            )

            fig.update_yaxes(
                tickmode="array",
                tickvals=ticks,
                ticktext=[f"{int(t):,}".replace(",", ".") for t in ticks],
            )

        fig.update_layout(
            height=520,
            hovermode="x",
            margin=dict(l=10, r=10, t=10, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            dragmode=False,
        )

        fig.update_yaxes(ticksuffix="%")

        # --- Aire a la derecha (10 días)
        x_max = pd.to_datetime(df_plot["Date"].max())
        x_min = pd.to_datetime(df_plot["Date"].min())
        fig.update_xaxes(range=[x_min, x_max + pd.Timedelta(days=10)])

        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
            key="chart_reservas",
        )



        # -------------------------
        # CSV + Fuente
        # -------------------------
        export = df_plot.rename(columns={"value": "reservas_musd"}).copy()
        st.download_button(
            "⬇️ Descargar CSV",
            export.to_csv(index=False).encode("utf-8"),
            file_name=f"reservas_{pd.Timestamp(end_d).strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
            key="dl_reservas_csv",
        )

        st.markdown(
            "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:10px;'>"
            "Fuente: CEU-UIA en base a BCRA."
            "</div>",
            unsafe_allow_html=True,
        )

    # ============================================================
    # CALIDAD DE CARTERA BCRA — RATIO DE IRREGULARIDAD
    # ============================================================
    st.divider()

    cartera = get_calidad_cartera_long()

    if cartera is None or cartera.empty:
        st.warning("Sin datos de calidad de cartera.")
        return

    conceptos_dict = {
        "Total": [
            "Cartera irregular total",
            "Adelantos",
            "Documentos",
            "Con garantía hipotecaria",
            "Con garantía prendaria",
            "Personales",
            "Tarjetas de crédito",
            "Prefinanciación y financiación de exportaciones",
            "Otros",
        ],
        "Familias": [
            "Cartera irregular total",
            "Personales",
            "Con garantía hipotecaria",
            "Con garantía prendaria",
            "Tarjetas de crédito",
            "Otros",
        ],
        "Empresas": [
            "Cartera irregular total",
            "Adelantos",
            "Documentos",
            "Con garantía hipotecaria",
            "Con garantía prendaria",
            "Prefinanciación y financiación de exportaciones",
            "Otros",
        ],
    }

    if "cart_agente" not in st.session_state:
        st.session_state["cart_agente"] = "Total"

    if "cart_concepto" not in st.session_state:
        st.session_state["cart_concepto"] = "Cartera irregular total"

    with st.container():

        # --- marker + JS PRIMERO ---
        st.markdown("<span id='cart_panel_marker'></span>", unsafe_allow_html=True)
        components.html(
            """
            <script>
            (function() {
              function applyCartPanelClass() {
                const marker = window.parent.document.getElementById('cart_panel_marker');
                if (!marker) return;
                const block = marker.closest('div[data-testid="stVerticalBlock"]');
                if (block) block.classList.add('fx-panel-wrap');
              }

              applyCartPanelClass();

              let tries = 0;
              const t = setInterval(() => {
                applyCartPanelClass();
                tries += 1;
                if (tries >= 10) clearInterval(t);
              }, 150);

              const obs = new MutationObserver(() => applyCartPanelClass());
              obs.observe(window.parent.document.body, { childList: true, subtree: true });
              setTimeout(() => obs.disconnect(), 3000);
            })();
            </script>
            """,
            height=0,
        )

        # -------------------------
        # Estado limpio
        # -------------------------
        agente_now = st.session_state.get("cart_agente", "Total")

        if agente_now not in conceptos_dict:
            st.session_state["cart_agente"] = "Total"
            agente_now = "Total"

        if st.session_state.get("cart_concepto") not in conceptos_dict[agente_now]:
            st.session_state["cart_concepto"] = conceptos_dict[agente_now][0]

        # -------------------------
        # Header
        # -------------------------
        agente_header = st.session_state.get("cart_agente", "Total")
        concepto_header = st.session_state.get("cart_concepto", "Cartera irregular total")

        s = cartera[
            (cartera["agente"] == agente_header)
            &
            (cartera["concepto"] == concepto_header)
        ].copy()

        s["Date"] = pd.to_datetime(s["Date"], errors="coerce")
        s["value"] = pd.to_numeric(s["value"], errors="coerce")
        s = s.dropna(subset=["Date", "value"]).sort_values("Date")

        if s.empty:
            last_date = pd.Timestamp(cartera["Date"].max())
            last_val = np.nan
            vm_pp = None
            va_pp = None
        else:
            last_date = pd.to_datetime(s["Date"].iloc[-1])
            last_val = float(s["value"].iloc[-1])

            val_m = _asof(
                s.rename(columns={"value": "VAL"}),
                last_date - pd.DateOffset(months=1),
                "VAL",
            )
            val_y = _asof(
                s.rename(columns={"value": "VAL"}),
                last_date - pd.DateOffset(years=1),
                "VAL",
            )

            vm_pp = None if val_m is None else last_val - val_m
            va_pp = None if val_y is None else last_val - val_y

        a_vm, cls_vm = _arrow_cls(vm_pp)
        a_va, cls_va = _arrow_cls(va_pp)

        header_lines = [
            '<div class="fx-wrap">',
            '  <div class="fx-title-row">',
            '    <div class="fx-icon-badge">🏦</div>',
            '    <div class="fx-title">Tasa de Morosidad</div>',
            "  </div>",
            '  <div class="fx-card">',
            '    <div class="fx-row">',
            f'      <div class="fx-value">{_fmt_pct_es(last_val, 1)}%</div>' if pd.notna(last_val) else '      <div class="fx-value">—</div>',
            '      <div class="fx-meta">',
            f'        {concepto_header}<span class="sep">|</span>{agente_header}<span class="sep">|</span>{last_date.strftime("%d/%m/%Y")}',
            "      </div>",
            '      <div class="fx-pills">',
            '        <div class="fx-pill red">',
            f'          <span class="fx-arrow {cls_vm}">{a_vm}</span>',
            f'          <span class="{cls_vm}">{_fmt_pp_es(vm_pp, 1)}</span>',
            '          <span class="lab">mensual</span>',
            "        </div>",
            '        <div class="fx-pill green">',
            f'          <span class="fx-arrow {cls_va}">{a_va}</span>',
            f'          <span class="{cls_va}">{_fmt_pp_es(va_pp, 1)}</span>',
            '          <span class="lab">interanual</span>',
            "        </div>",
            "      </div>",
            "    </div>",
            "  </div>",
            "</div>",
        ]

        st.markdown("\n".join(header_lines), unsafe_allow_html=True)

        st.markdown("<div class='fx-panel-gap'></div>", unsafe_allow_html=True)

        # -------------------------
        # Controles
        # -------------------------
        c1, c2 = st.columns(2, gap="large")

        with c1:
            st.markdown("<div class='fx-panel-title'>Seleccioná el agente económico</div>", unsafe_allow_html=True)
            agente = st.selectbox(
                "",
                ["Total", "Familias", "Empresas"],
                key="cart_agente",
                label_visibility="collapsed",
            )

        if st.session_state.get("cart_concepto") not in conceptos_dict[agente]:
            st.session_state["cart_concepto"] = conceptos_dict[agente][0]

        with c2:
            st.markdown("<div class='fx-panel-title'>Seleccioná el concepto</div>", unsafe_allow_html=True)
            concepto = st.selectbox(
                "",
                conceptos_dict[agente],
                key="cart_concepto",
                label_visibility="collapsed",
            )

        # -------------------------
        # Filtro
        # -------------------------
        df_plot = cartera[
            (cartera["agente"] == agente)
            &
            (cartera["concepto"] == concepto)
        ].copy()

        df_plot["Date"] = pd.to_datetime(df_plot["Date"], errors="coerce")
        df_plot["value"] = pd.to_numeric(df_plot["value"], errors="coerce")
        df_plot = df_plot.dropna(subset=["Date", "value"]).sort_values("Date")

        if df_plot.empty:
            st.warning("Sin datos para la selección.")
            return

        # -------------------------
        # Rango de fechas
        # -------------------------
        min_d = df_plot["Date"].min().date()
        max_d = df_plot["Date"].max().date()
        default_start = max(min_d, pd.Timestamp("2021-01-01").date())

        st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
        start_d, end_d = st.slider(
            "",
            min_value=min_d,
            max_value=max_d,
            value=(default_start, max_d),
            label_visibility="collapsed",
            key="cart_rangebar",
        )

        df_plot = df_plot[
            (df_plot["Date"] >= pd.Timestamp(start_d))
            &
            (df_plot["Date"] <= pd.Timestamp(end_d))
        ].copy()

        # -------------------------
        # Plot
        # -------------------------
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=df_plot["Date"],
                y=df_plot["value"],
                name=concepto,
                mode="lines",
                hovertemplate="%{x|%d/%m/%Y}<br>Ratio de irregularidad: %{y:.1f}%<extra></extra>",
            )
        )

        fig.add_hline(
            y=0,
            line_width=1,
            line_dash="solid",
            line_color="rgba(80,80,80,0.7)",
        )

        fig.update_layout(
            height=520,
            hovermode="x",
            margin=dict(l=10, r=10, t=10, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            dragmode=False,
        )

        fig.update_yaxes(ticksuffix="%")

        x_max = pd.to_datetime(df_plot["Date"].max())
        x_min = pd.to_datetime(df_plot["Date"].min())
        fig.update_xaxes(range=[x_min, x_max + pd.Timedelta(days=10)])

        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
            key="chart_calidad_cartera",
        )

        st.markdown(
            "<div style='color:rgba(20,50,79,0.70); font-size:12px; margin-top:10px;'>"
            "Fuente: CEU-UIA en base a BCRA."
            "</div>",
            unsafe_allow_html=True,
        )
