import warnings
import streamlit as st

from ui.theme import apply_global_styles
from ui.common import topbar_logo
from pages.morosidad import render_morosidad

warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

st.set_page_config(
    page_title="Monitor de Morosidad CEU–UIA",
    page_icon="assets/okok.png",
    layout="wide",
    initial_sidebar_state="collapsed",
)

apply_global_styles()
topbar_logo()

render_morosidad(lambda section: None)
