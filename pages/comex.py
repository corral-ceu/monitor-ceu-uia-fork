import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from services.metrics import calc_var, fmt, obtener_nombre_mes
from services.comex_data import fetch_ica

MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fmt_mes_es(d):
    ts = pd.Timestamp(d)
    return f"{MESES_ES[ts.month - 1]}-{str(ts.year)[-2:]}"


def fmt_es(x, dec=1):
    if pd.isna(x):
        return "s/d"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _chip_cls(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "cx-chip-neu"
    return "cx-chip-pos" if x > 0 else "cx-chip-neg"

def _arrow_cls_cx(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "cx-arrow-neu"
    return "cx-arrow-up" if x > 0 else "cx-arrow-dn"

def _arrow_dir_cx(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "→"
    return "↑" if x > 0 else "↓"

def _val_cls(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "cx-val-neu"
    return "cx-val-pos" if x > 0 else "cx-val-neg"

def _fmt_pct(x, dec=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "s/d"
    return f"{float(x):.{dec}f}%".replace(".", ",")

def _calc_ytd_pct(df_, col):
    if col not in df_.columns:
        return np.nan
    last = df_["fecha"].iloc[-1]
    y, m = last.year, last.month
    cur  = df_[(df_["fecha"].dt.year == y)   & (df_["fecha"].dt.month <= m)][col].sum()
    prev = df_[(df_["fecha"].dt.year == y-1) & (df_["fecha"].dt.month <= m)][col].sum()
    if prev == 0:
        return np.nan
    return (cur / prev - 1) * 100


def _cx_card(title, tipo, yoy, ytd):
    bar_cls   = "cx-card-bar-expo" if tipo == "expo" else "cx-card-bar-impo"
    badge_cls = "cx-badge-expo"    if tipo == "expo" else "cx-badge-impo"
    badge_txt = "EXPO"             if tipo == "expo" else "IMPO"
    chip_yoy = (f'<div class="cx-chip {_chip_cls(yoy)}">'
                f'<div class="cx-chip-top">'
                f'<span class="cx-chip-label">Interanual</span>'
                f'<span class="cx-chip-arrow {_arrow_cls_cx(yoy)}">{_arrow_dir_cx(yoy)}</span>'
                f'</div>'
                f'<div class="cx-chip-val {_val_cls(yoy)}">{_fmt_pct(yoy)}</div>'
                f'</div>')
    chip_ytd = (f'<div class="cx-chip {_chip_cls(ytd)}">'
                f'<div class="cx-chip-top">'
                f'<span class="cx-chip-label">Acumulada</span>'
                f'<span class="cx-chip-arrow {_arrow_cls_cx(ytd)}">{_arrow_dir_cx(ytd)}</span>'
                f'</div>'
                f'<div class="cx-chip-val {_val_cls(ytd)}">{_fmt_pct(ytd)}</div>'
                f'</div>')
    return (f'<div class="cx-card">'
            f'<div class="{bar_cls}"></div>'
            f'<div class="cx-card-body">'
            f'<div class="cx-card-header">'
            f'<div class="cx-card-title">{title}</div>'
            f'<span class="{badge_cls}">{badge_txt}</span>'
            f'</div>'
            f'<div class="cx-metrics">{chip_yoy}{chip_ytd}</div>'
            f'</div>'
            f'</div>')


EXP_ROWS = [
    ("Productos primarios (PP)",    "expo_pp",   "expo"),
    ("MOA",                          "expo_moa",  "expo"),
    ("MOI",                          "expo_moi",  "expo"),
    ("Combustibles y energia (CyE)", "expo_cye",  "expo"),
]
IMP_ROWS = [
    ("Bienes de capital (BK)",         "impo_bk",    "impo"),
    ("Bienes intermedios (BI)",         "impo_bi",    "impo"),
    ("Combustibles y lubricantes (CL)", "impo_cl",    "impo"),
    ("Piezas y accesorios p/ BK",       "impo_pabc",  "impo"),
    ("Bienes de consumo (BC)",          "impo_bc",    "impo"),
    ("Vehiculos automotores (VAP)",     "impo_vap",   "impo"),
    ("Resto",                           "impo_resto", "impo"),
]
ALL_ROWS = EXP_ROWS + IMP_ROWS

EVOL_SERIES = {
    "Expo - Productos primarios (PP)": "expo_pp",
    "Expo - MOA":                       "expo_moa",
    "Expo - MOI":                       "expo_moi",
    "Expo - Combustibles y energia":    "expo_cye",
    "Impo - Bienes de capital (BK)":    "impo_bk",
    "Impo - Bienes intermedios (BI)":   "impo_bi",
    "Impo - Combustibles y lubric.":    "impo_cl",
    "Impo - Piezas y accesorios p/BK":  "impo_pabc",
    "Impo - Bienes de consumo (BC)":    "impo_bc",
    "Impo - Vehiculos automotores":     "impo_vap",
    "Impo - Resto":                     "impo_resto",
}


def _bar_fig(df, rows, mode, bar_color_pos, bar_color_neg):
    data = []
    for label, key, _ in rows:
        if key not in df.columns:
            continue
        val = calc_var(df[key], 12) if mode == "anual" else _calc_ytd_pct(df, key)
        if val is None or np.isnan(val):
            continue
        data.append({"Rubro": label, "val": float(val)})
    if not data:
        return None
    df_b = pd.DataFrame(data).sort_values("val", ascending=False).reset_index(drop=True)
    x = df_b["val"].values
    y = df_b["Rubro"].tolist()
    colors = [bar_color_pos if v >= 0 else bar_color_neg for v in x]
    pad = 0.18 * max(abs(x.min()), abs(x.max()), 1e-6)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=y, orientation="h",
        marker=dict(color=colors),
        text=[f"{v:.1f}%".replace(".", ",") for v in x],
        textposition="outside", texttemplate="%{text}",
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        height=max(300, 40 * len(df_b) + 80),
        margin=dict(l=8, r=8, t=8, b=30),
        hovermode="closest", showlegend=False,
        dragmode=False, template="plotly_white",
    )
    fig.update_xaxes(
        ticksuffix="%",
        range=[min(0.0, float(x.min())) - pad, max(0.0, float(x.max())) + pad],
        zeroline=True, zerolinewidth=1,
        zerolinecolor="rgba(120,120,120,0.65)",
        showgrid=True, gridcolor="rgba(120,120,120,0.22)",
    )
    fig.update_yaxes(autorange="reversed")
    return fig


def render_comex(go_to):


    st.markdown("""<style>
.com-wrap{background:linear-gradient(180deg,#f7fbff 0%,#eef6ff 100%);border:1px solid #dfeaf6;border-radius:22px;padding:14px;box-shadow:0 10px 24px rgba(15,55,100,0.16),inset 0 0 0 1px rgba(255,255,255,0.55);margin-top:8px;}
.com-title-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;padding-left:4px;}
.com-title-left{display:flex;align-items:center;gap:12px;}
.com-icon-badge{width:64px;height:52px;border-radius:14px;background:linear-gradient(180deg,#e7eef6 0%,#dfe7f1 100%);border:1px solid rgba(15,23,42,0.10);display:flex;align-items:center;justify-content:center;box-shadow:0 8px 14px rgba(15,55,100,0.12);font-size:30px;flex:0 0 auto;}
.com-title{font-size:23px;font-weight:900;letter-spacing:-0.01em;color:#14324f;margin:0;line-height:1.0;}
.com-subtitle{font-size:14px;font-weight:800;color:rgba(20,50,79,0.78);margin-top:2px;}
.com-card{background:rgba(255,255,255,0.94);border:1px solid rgba(15,23,42,0.10);border-radius:18px;padding:14px 14px 12px 14px;box-shadow:0 10px 18px rgba(15,55,100,0.10);}
.com-kpi-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:26px;align-items:start;margin-top:4px;}
.com-meta{font-size:16px;color:#2b4660;font-weight:800;margin-bottom:6px;}
.com-value{font-size:44px;font-weight:950;letter-spacing:-0.02em;color:#14324f;line-height:0.95;}
.com-badge{display:inline-flex;align-items:center;justify-content:center;padding:6px 12px;border-radius:999px;border:1px solid rgba(15,23,42,0.10);font-size:14px;font-weight:800;margin-top:8px;width:fit-content;}
.com-badge.red{background:rgba(220,38,38,0.07);}
.com-badge.green{background:rgba(22,163,74,0.08);}
.fx-panel-title{font-size:12px;font-weight:900;color:rgba(20,50,79,0.78);margin:0 0 6px 2px;letter-spacing:0.01em;}
.com-panel-wrap{background:rgba(230,243,255,0.55);border:1px solid rgba(15,55,100,0.10);border-radius:22px;padding:16px 16px 26px 16px;box-shadow:0 10px 18px rgba(15,55,100,0.06);margin-top:10px;}
.com-panel-wrap div[data-testid="stSelectbox"],.com-panel-wrap div[data-testid="stMultiSelect"],.com-panel-wrap div[data-testid="stSlider"],.com-panel-wrap div[data-testid="stPlotlyChart"],.com-panel-wrap div[data-testid="stDownloadButton"]{background:transparent !important;border:none !important;box-shadow:none !important;padding:0 !important;margin:0 !important;}
.cx-card{background:#ffffff;border:1px solid #dde6f0;border-radius:14px;overflow:hidden;box-shadow:0 2px 10px rgba(15,55,100,.07);margin-bottom:4px;}
.cx-card-bar-expo{height:5px;width:100%;background:#2563eb;}
.cx-card-bar-impo{height:5px;width:100%;background:#ea580c;}
.cx-card-body{padding:14px 14px 12px 14px;}
.cx-card-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px;gap:8px;}
.cx-card-title{font-weight:800;font-size:13px;color:#1e3a5f;line-height:1.3;flex:1;}
.cx-badge-expo{background:rgba(37,99,235,0.09);color:#2563eb;border:1px solid rgba(37,99,235,0.20);border-radius:999px;font-size:10px;font-weight:900;padding:2px 8px;white-space:nowrap;flex-shrink:0;letter-spacing:.3px;}
.cx-badge-impo{background:rgba(234,88,12,0.09);color:#ea580c;border:1px solid rgba(234,88,12,0.20);border-radius:999px;font-size:10px;font-weight:900;padding:2px 8px;white-space:nowrap;flex-shrink:0;letter-spacing:.3px;}
.cx-metrics{display:flex;gap:8px;}
.cx-chip{flex:1;border-radius:10px;padding:9px 10px;display:flex;flex-direction:column;gap:5px;}
.cx-chip-pos{background:#f0fdf4 !important;border:1px solid #bbf7d0 !important;}
.cx-chip-neg{background:#fff1f2 !important;border:1px solid #fecdd3 !important;}
.cx-chip-neu{background:#f8fafc !important;border:1px solid #e2e8f0 !important;}
.cx-chip-top{display:flex;align-items:center;justify-content:space-between;}
.cx-chip-label{font-size:10px;font-weight:700;color:#7a90a8 !important;text-transform:uppercase;letter-spacing:.3px;}
.cx-chip-arrow{font-size:13px;font-weight:900;line-height:1;}
.cx-arrow-up{color:#16a34a !important;}
.cx-arrow-dn{color:#e11d48 !important;}
.cx-arrow-neu{color:#64748b !important;}
.cx-chip-val{font-size:22px;font-weight:800;line-height:1;}
.cx-val-pos{color:#16a34a !important;}
.cx-val-neg{color:#e11d48 !important;}
.cx-val-neu{color:#64748b !important;}
.cx-section-header{display:flex;align-items:center;gap:12px;margin-bottom:16px;}
.cx-section-icon{width:44px;height:44px;border-radius:12px;background:linear-gradient(180deg,#e7eef6 0%,#dfe7f1 100%);border:1px solid rgba(15,23,42,0.10);display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 6px 12px rgba(15,55,100,0.10);flex-shrink:0;}
.cx-section-title{font-size:18px;font-weight:900;color:#14324f;line-height:1.0;margin:0;}
.cx-section-sub{font-size:12px;font-weight:700;color:rgba(20,50,79,0.65);margin-top:2px;}
.cx-chart-header{display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:10px 14px;border-radius:12px;}
.cx-chart-header-expo{background:rgba(37,99,235,0.07);border:1px solid rgba(37,99,235,0.18);}
.cx-chart-header-impo{background:rgba(234,88,12,0.07);border:1px solid rgba(234,88,12,0.18);}
.cx-chart-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;}
.cx-chart-dot-expo{background:#2563eb;}
.cx-chart-dot-impo{background:#ea580c;}
.cx-chart-label{font-size:13px;font-weight:900;}
.cx-chart-label-expo{color:#2563eb;}
.cx-chart-label-impo{color:#ea580c;}
@media(max-width:900px){.com-kpi-grid{grid-template-columns:1fr;gap:14px;}}span[data-baseweb="tag"]{background:#1e3a5f !important;border-radius:8px !important;}span[data-baseweb="tag"] span{color:#ffffff !important;font-weight:700 !important;}span[data-baseweb="tag"] [role="presentation"]{color:#ffffff !important;fill:#ffffff !important;}
</style>""", unsafe_allow_html=True)

    with st.spinner("Cargando ICA (INDEC)..."):
        df = fetch_ica()

    if df is None or df.empty or "fecha" not in df.columns:
        st.error("No se pudieron cargar los datos de ICA.")
        return

    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"]).sort_values("fecha")

    ult_f   = df["fecha"].iloc[-1]
    mes_txt = obtener_nombre_mes(ult_f)

    def _cls(x):
        return "green" if (x is not None and not pd.isna(x) and x >= 0) else "red"
    def _arrow(x):
        return "\u25b2" if (x is not None and not pd.isna(x) and x >= 0) else "\u25bc"

    expo  = df.get("expo_total")
    impo  = df.get("impo_total")
    saldo = df.get("saldo")

    expo_i   = calc_var(expo, 12)      if expo  is not None else np.nan
    impo_i   = calc_var(impo, 12)      if impo  is not None else np.nan
    saldo_di = saldo.diff(12).iloc[-1] if saldo is not None else np.nan
    expo_last  = expo.iloc[-1]  if expo  is not None else np.nan
    impo_last  = impo.iloc[-1]  if impo  is not None else np.nan
    saldo_last = saldo.iloc[-1] if saldo is not None else np.nan

    st.markdown(
        f'<div class="com-wrap">'
        f'<div class="com-title-row"><div class="com-title-left">'
        f'<div class="com-icon-badge">\U0001f6a2</div>'
        f'<div><div class="com-title">Comercio Exterior \u2014 ICA (INDEC) \u00b7 {mes_txt}</div>'
        f'<div class="com-subtitle">Millones de USD \u00b7 Exportaciones, Importaciones y Saldo</div></div>'
        f'</div></div>'
        f'<div class="com-card"><div class="com-kpi-grid">'
        f'<div><div class="com-meta">Exportaciones</div><div class="com-value">{fmt_es(expo_last,0)}</div>'
        f'<div class="com-badge {_cls(expo_i)}">{_arrow(expo_i)}{fmt_es(expo_i,1)}% interanual</div></div>'
        f'<div><div class="com-meta">Importaciones</div><div class="com-value">{fmt_es(impo_last,0)}</div>'
        f'<div class="com-badge {_cls(impo_i)}">{_arrow(impo_i)}{fmt_es(impo_i,1)}% interanual</div></div>'
        f'<div><div class="com-meta">Saldo comercial</div><div class="com-value">{fmt_es(saldo_last,0)}</div>'
        f'<div class="com-badge {_cls(saldo_di)}">{_arrow(saldo_di)}USD {fmt_es(saldo_di,0)}</div></div>'
        f'</div></div></div>',
        unsafe_allow_html=True)

    st.markdown("<span id='com_panel_marker'></span>", unsafe_allow_html=True)
    components.html("""<script>
(function(){
  function apply(){var m=window.parent.document.getElementById('com_panel_marker');if(!m)return;var b=m.closest('div[data-testid="stVerticalBlock"]');if(b)b.classList.add('com-panel-wrap');}
  apply();var n=0,t=setInterval(function(){apply();if(++n>=10)clearInterval(t);},150);
  var o=new MutationObserver(apply);o.observe(window.parent.document.body,{childList:true,subtree:true});setTimeout(function(){o.disconnect();},2500);
})();
</script>""", height=0)

    max_real = pd.to_datetime(df["fecha"].max())
    min_real = pd.to_datetime(df["fecha"].min())
    months   = pd.date_range(min_real.to_period("M").to_timestamp(), max_real.to_period("M").to_timestamp(), freq="MS")
    months_d  = [m.date() for m in months]
    end_idx   = len(months_d) - 1
    start_idx = max(0, end_idx - 12)

    st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
    start_d, end_d = st.select_slider("", options=months_d, value=(months_d[start_idx], months_d[end_idx]),
        format_func=fmt_mes_es, label_visibility="collapsed", key="comex_range")
    start_ts = pd.Timestamp(start_d).to_period("M").to_timestamp()
    end_ts   = pd.Timestamp(end_d).to_period("M").to_timestamp()
    dff = df[(df["fecha"] >= start_ts) & (df["fecha"] <= end_ts)].copy()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dff["fecha"], y=dff["expo_total"], name="Exportaciones", mode="lines"))
    fig.add_trace(go.Scatter(x=dff["fecha"], y=dff["impo_total"], name="Importaciones", mode="lines"))
    fig.add_trace(go.Bar(x=dff["fecha"], y=dff["saldo"], name="Saldo", opacity=0.35, yaxis="y2"))
    fig.update_layout(template="plotly_white", height=520, hovermode="x",
        margin=dict(l=10, r=10, t=10, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
        yaxis=dict(title="Millones USD"),
        yaxis2=dict(title="Saldo (Millones USD)", overlaying="y", side="right", showgrid=False),
        dragmode=False)
    _fig_dates  = pd.date_range(start_ts, end_ts, freq="MS")
    _tick_dates = [d for d in _fig_dates if d.month in (1, 4, 7, 10)]
    _last_dt    = pd.Timestamp(end_ts)
    if not _tick_dates or _tick_dates[-1] != _last_dt:
        _tick_dates = [d for d in _tick_dates if d != _last_dt] + [_last_dt]
    fig.update_xaxes(
        tickvals=[d.to_pydatetime() for d in _tick_dates],
        ticktext=[f"{MESES_ES[d.month-1]}-{str(d.year)[-2:]}" for d in _tick_dates],
        tickangle=-90,
        tickfont=dict(size=11),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False,"scrollZoom":False,"doubleClick":False})

    export_cols = [c for c in ["fecha","expo_total","impo_total","saldo"] if c in dff.columns]
    st.download_button("\u2b07\ufe0f Descargar CSV",
        dff[export_cols].copy().rename(columns={"fecha":"date"}).to_csv(index=False).encode("utf-8"),
        file_name="comex_ica.csv", mime="text/csv", use_container_width=False, key="dl_comex_csv")
    st.markdown("<div style='color:rgba(20,50,79,0.70);font-size:12px;margin-top:10px;'>Fuente: INDEC \u2014 Intercambio Comercial Argentino (ICA).</div>", unsafe_allow_html=True)

    # ── Composicion cards ────────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div class="com-wrap" style="margin-bottom:16px;">'
        '<div class="cx-section-header">'
        '<div class="cx-section-icon">\U0001f4e6</div>'
        '<div><div class="cx-section-title">Desglose por Rubros y Usos</div>'
        '<div class="cx-section-sub">Variaci\u00f3n interanual y acumulada anual por rubro'
        '<span style="margin-left:10px;padding:2px 8px;border-radius:999px;background:rgba(37,99,235,0.09);color:#2563eb;border:1px solid rgba(37,99,235,0.2);font-size:10px;font-weight:900;">EXPO</span>'
        '<span style="margin-left:4px;padding:2px 8px;border-radius:999px;background:rgba(234,88,12,0.09);color:#ea580c;border:1px solid rgba(234,88,12,0.2);font-size:10px;font-weight:900;">IMPO</span>'
        '</div></div></div></div>',
        unsafe_allow_html=True)

    available = [r for r in ALL_ROWS if r[1] in df.columns]
    for start in range(0, len(available), 3):
        chunk = available[start : start + 3]
        while len(chunk) < 3:
            chunk.append(None)
        cols = st.columns(3, gap="small")
        for j, item in enumerate(chunk):
            if item is None:
                continue
            label, key, tipo = item
            yoy = calc_var(df[key], 12)
            ytd = _calc_ytd_pct(df, key)
            cols[j].markdown(_cx_card(label, tipo, yoy, ytd), unsafe_allow_html=True)

    # ── Graficos paralelos ───────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div class="com-wrap" style="margin-bottom:14px;">'
        '<div class="cx-section-header">'
        '<div class="cx-section-icon">\U0001f4ca</div>'
        '<div><div class="cx-section-title">Comparaci\u00f3n por Rubros</div>'
        '<div class="cx-section-sub">Exportaciones e Importaciones en paralelo</div>'
        '</div></div></div>',
        unsafe_allow_html=True)

    MODE_OPTS = {"anual": "Variaci\u00f3n interanual", "acum": "Variaci\u00f3n acumulada anual"}
    st.markdown("<div class='fx-panel-title'>Tipo de comparaci\u00f3n</div>", unsafe_allow_html=True)
    mode = st.selectbox("", list(MODE_OPTS.keys()), format_func=lambda k: MODE_OPTS[k],
        key="cx_bar_mode", label_visibility="collapsed")

    last_f = df["fecha"].iloc[-1]
    last_m = MESES_ES[last_f.month - 1]
    subtitle = f"Variaci\u00f3n interanual \u00b7 {fmt_mes_es(last_f)}" if mode == "anual" else f"Acumulada ene\u2013{last_m} \u00b7 {last_f.year} vs {last_f.year - 1}"
    st.markdown(f"<div class='fx-panel-title' style='margin-bottom:12px;'>{subtitle}</div>", unsafe_allow_html=True)

    col_expo, col_impo = st.columns(2, gap="large")
    with col_expo:
        st.markdown('<div class="cx-chart-header cx-chart-header-expo"><div class="cx-chart-dot cx-chart-dot-expo"></div><span class="cx-chart-label cx-chart-label-expo">Exportaciones</span></div>', unsafe_allow_html=True)
        fig_expo = _bar_fig(df, EXP_ROWS, mode, "rgba(37,99,235,0.65)", "rgba(37,99,235,0.35)")
        if fig_expo:
            st.plotly_chart(fig_expo, use_container_width=True, config={"displayModeBar":False,"scrollZoom":False,"doubleClick":False}, key="cx_chart_expo")
        else:
            st.info("Sin datos de exportaciones.")

    with col_impo:
        st.markdown('<div class="cx-chart-header cx-chart-header-impo"><div class="cx-chart-dot cx-chart-dot-impo"></div><span class="cx-chart-label cx-chart-label-impo">Importaciones</span></div>', unsafe_allow_html=True)
        fig_impo = _bar_fig(df, IMP_ROWS, mode, "rgba(234,88,12,0.65)", "rgba(234,88,12,0.35)")
        if fig_impo:
            st.plotly_chart(fig_impo, use_container_width=True, config={"displayModeBar":False,"scrollZoom":False,"doubleClick":False}, key="cx_chart_impo")
        else:
            st.info("Sin datos de importaciones.")

    # ── Evolucion de rubros ──────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div class="com-wrap" style="margin-bottom:14px;">'
        '<div class="cx-section-header">'
        '<div class="cx-section-icon">\U0001f4c8</div>'
        '<div><div class="cx-section-title">Evoluci\u00f3n de Rubros</div>'
        '<div class="cx-section-sub">Seguimiento mensual por rubro \u2014 nivel, variaci\u00f3n interanual o acumulada</div>'
        '</div></div></div>',
        unsafe_allow_html=True)

    avail_evol = {k: v for k, v in EVOL_SERIES.items() if v in df.columns}

    r1, r2 = st.columns(2, gap="large")
    with r1:
        st.markdown("<div class='fx-panel-title'>Seleccion\u00e1 rubros</div>", unsafe_allow_html=True)
        sel_rubros = st.multiselect("", options=list(avail_evol.keys()),
            default=["Impo - Bienes de consumo (BC)"],
            key="cx_evol_rubros", label_visibility="collapsed")
    with r2:
        st.markdown("<div class='fx-panel-title'>Modo</div>", unsafe_allow_html=True)
        evol_mode = st.selectbox("",
            ["Nivel (millones USD)", "Variaci\u00f3n interanual (%)", "Variaci\u00f3n acumulada anual (%)"],
            key="cx_evol_mode", label_visibility="collapsed")

    st.markdown("<div class='fx-panel-title'>Rango de fechas</div>", unsafe_allow_html=True)
    evol_start, evol_end = st.select_slider("", options=months_d,
        value=(months_d[max(0, end_idx - 48)], months_d[end_idx]),
        format_func=fmt_mes_es, label_visibility="collapsed", key="cx_evol_range")
    evol_start_ts = pd.Timestamp(evol_start).to_period("M").to_timestamp()
    evol_end_ts   = pd.Timestamp(evol_end).to_period("M").to_timestamp()
    df_evol = df[(df["fecha"] >= evol_start_ts) & (df["fecha"] <= evol_end_ts)].copy()

    if not sel_rubros:
        st.warning("Seleccion\u00e1 al menos un rubro.")
    else:
        fig_evol = go.Figure()
        ytitle = "Millones USD"

        for rubro in sel_rubros:
            col = avail_evol[rubro]
            if col not in df_evol.columns:
                continue
            serie = df_evol[["fecha", col]].copy().sort_values("fecha")
            serie[col] = pd.to_numeric(serie[col], errors="coerce")
            color = "#2563eb" if rubro.startswith("Expo") else "#ea580c"

            if evol_mode == "Nivel (millones USD)":
                y_vals = serie[col]
                ytitle = "Millones USD"
                hover  = "%{y:,.0f} M USD"
            elif evol_mode == "Variaci\u00f3n interanual (%)":
                full = df[["fecha", col]].copy().sort_values("fecha")
                full[col] = pd.to_numeric(full[col], errors="coerce")
                full["yoy"] = (full[col] / full[col].shift(12) - 1) * 100
                merged = full[["fecha","yoy"]].merge(serie[["fecha"]], on="fecha", how="inner")
                y_vals = merged["yoy"]
                serie  = merged
                ytitle = "Variaci\u00f3n interanual (%)"
                hover  = "%{y:.1f}%"
            else:
                # Variación acumulada dinámica:
                # base = valor del mes anterior al inicio del slider
                # cada punto = (valor_mes / base_val - 1) * 100
                full = df[["fecha", col]].copy().sort_values("fecha")
                full[col] = pd.to_numeric(full[col], errors="coerce")
                base_month = evol_start_ts - pd.DateOffset(months=1)
                base_row   = full[full["fecha"] == base_month][col]
                if base_row.empty or base_row.iloc[0] == 0 or pd.isna(base_row.iloc[0]):
                    base_row = full[full["fecha"] == evol_start_ts][col]
                base_val = float(base_row.iloc[0]) if not base_row.empty else np.nan
                rng = full[(full["fecha"] >= evol_start_ts) & (full["fecha"] <= evol_end_ts)].copy()
                if not pd.isna(base_val) and base_val != 0:
                    rng["acum_pct"] = (rng[col] / base_val - 1) * 100
                else:
                    rng["acum_pct"] = np.nan
                serie  = rng[["fecha", "acum_pct"]].copy()
                y_vals = serie["acum_pct"]
                base_lbl = f"{MESES_ES[base_month.month-1]}-{str(base_month.year)[-2:]}"
                ytitle = f"Variaci\u00f3n vs {base_lbl} (%)"
                hover  = "%{y:.1f}%"

            fig_evol.add_trace(go.Scatter(
                x=serie["fecha"], y=y_vals, mode="lines+markers", name=rubro,
                line=dict(color=color, width=2), marker=dict(size=4),
                hovertemplate=f"{rubro}<br>{hover}<extra></extra>"))

        if evol_mode != "Nivel (millones USD)":
            fig_evol.add_hline(y=0, line_width=1, line_dash="solid", line_color="rgba(100,100,100,0.5)")
            fig_evol.update_yaxes(ticksuffix="%")

        # Eje X: tickvals cada 3 meses en formato mmm-aa español, último dato siempre presente
        all_dates = pd.date_range(evol_start_ts, evol_end_ts, freq="MS")
        tick_dates = [d for d in all_dates if d.month in (1, 4, 7, 10)]
        last_date  = pd.Timestamp(evol_end_ts)
        # Asegurar que el último dato aparezca
        if not tick_dates or tick_dates[-1] != last_date:
            tick_dates = [d for d in tick_dates if d != last_date] + [last_date]
        tick_vals  = [d.to_pydatetime() for d in tick_dates]
        tick_texts = [f"{MESES_ES[d.month-1]}-{str(d.year)[-2:]}" for d in tick_dates]

        fig_evol.update_layout(
            template="plotly_white", height=480, hovermode="x unified",
            margin=dict(l=10, r=10, t=10, b=80),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(title=ytitle), dragmode=False)
        fig_evol.update_xaxes(
            tickvals=tick_vals,
            ticktext=tick_texts,
            tickangle=-90,
            tickfont=dict(size=11),
        )
        st.plotly_chart(fig_evol, use_container_width=True,
            config={"displayModeBar":False,"scrollZoom":False,"doubleClick":False},
            key="cx_evol_chart")

    st.markdown("<div style='color:rgba(20,50,79,0.70);font-size:12px;margin-top:8px;'>Fuente: INDEC \u2014 Intercambio Comercial Argentino (ICA).</div>", unsafe_allow_html=True)
