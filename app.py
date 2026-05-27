# ============================================================
# Monitor CEU–UIA (UNIFICADO)
# Estructura modular para trabajar en equipo (GitHub)
# Secciones:
# - Home
# - Macroeconomía (FX / Tasa / Precios)
# - Empleo Privado (SIPA)
# - Producción Industrial (IPI INDEC)
# ============================================================

import warnings
import streamlit as st

from ui.theme import apply_global_styles
from ui.common import get_section, go_to, topbar_logo

from pages.home import render_main_home
from pages.macro_home import render_macro_home
from pages.macro_fx import render_macro_fx
from pages.macro_tasa import render_macro_tasa
from pages.macro_precios import render_macro_precios
from pages.finanzas import render_finanzas
from pages.empleo import render_empleo
from pages.ipi import render_ipi
from pages.macro_pbi_emae import render_macro_pbi_emae
from pages.comex import render_comex
from pages.morosidad import render_morosidad

# ----------------------------
# Warnings (limpia consola)
# ----------------------------
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


# ----------------------------
# Configuración Streamlit
# ----------------------------
st.set_page_config(
    page_title="Monitor CEU–UIA",
    page_icon="assets/okok.png",
    layout="wide",
    initial_sidebar_state="collapsed",
)

apply_global_styles()
from utils.auth import check_login, logout_button

if not check_login():
    st.stop()

logout_button()

# ----------------------------
# Cortina anti-ghost (solo al cambiar de sección)
# ----------------------------
def _nav_curtain_if_needed(current_sec: str):
    prev = st.session_state.get("_prev_section")
    if prev == current_sec:
        return None  # reruns normales: no mostramos cortina

    ph = st.empty()
    ph.markdown(
        """
        <style>
        .nav-banner{
            position: fixed;
            top: 14px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 999999;

            background: #0f172a;
            color: #ffffff !important;
            border: 1px solid rgba(255,255,255,0.10);

            border-radius: 999px;
            padding: 10px 22px;

            font-family: Inter, "Segoe UI", Arial, sans-serif;
            font-weight: 900;
            font-size: 14px;
            letter-spacing: 0.02em;

            box-shadow: 0 10px 24px rgba(15,23,42,0.25);
        }

        .nav-banner *{
            color: #ffffff !important;
        }
        </style>

        <div class="nav-banner">⏳ Cargando…</div>
        """,
        unsafe_allow_html=True,
    )

    return ph


# ----------------------------
# Router
# ----------------------------
sec = get_section(default="home")

# Mostrar cortina SOLO si cambió la sección (evita "fantasmas" del DOM anterior)
curtain = _nav_curtain_if_needed(sec)

# Logo en todas las secciones salvo Home
if sec != "home":
    topbar_logo()

if sec == "home":
    render_main_home(go_to)

elif sec == "macro_home":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_macro_home(go_to)

elif sec == "macro_fx":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_macro_fx(go_to)

elif sec == "macro_tasa":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_macro_tasa(go_to)

elif sec == "macro_precios":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_macro_precios(go_to)

elif sec == "finanzas":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_finanzas(go_to)

elif sec == "empleo":
    render_empleo(go_to)

elif sec == "ipi":
    render_ipi(go_to)

elif sec == "macro_pbi_emae":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_macro_pbi_emae(go_to)

elif sec == "comex":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_comex(go_to)
    
elif sec == "morosidad":
    if st.button("← Volver a secciones"):
        go_to("home")
    render_morosidad(go_to)

else:
    st.warning("Sección desconocida. Volviendo al inicio.")
    go_to("home")


# Apagar cortina y registrar sección actual
if curtain is not None:
    curtain.empty()
st.session_state["_prev_section"] = sec
