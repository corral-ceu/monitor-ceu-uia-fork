import base64
import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse


# ============================================================
# Logo helper
# ============================================================
def _img_to_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ============================================================
# NEWS TICKER (RSS + scoring)
# ============================================================
NEWS_FEEDS = [
    "https://www.ambito.com/rss/pages/economia.xml",
]

NEWS_WEIGHTS = {

    # ---- Macro base ----
    "bcra": 4,
    "indec": 10,
    "inflación": 4,
    "inflacion": 4,
    "ipc": 4,
    "ipim": 3,
    "emae": 4,
    "pbi": 4,
    "fmi": 4,
    "deuda": 5,
    "empleo": 5,
    "salarios": 5,
    "china": 10,
    "bono": 5,
    "bonos": 5,
    "riesgo país": 10,
    "riesgo pais": 10,
    "reservas": 3,
    "dólar": 3,
    "dolar": 3,
    "monetaria": 3,
    "cambiaria": 2,
    "fiscal": 2,
    "merval": 10,
    "actividad": 5,

    # ---- Industria núcleo CEU ----
    "industria": 20,
    "industrial": 20,
    "manufactura": 18,
    "producción": 15,
    "capacidad": 18,
    "planta": 15,
    "suspensiones": 16,
    "despidos": 16,
    "cierres": 20,
    "recesión": 14,
    "pyme": 15,
    "pymes": 15,
    "automotriz": 16,
    "metalúrgica": 16,
    "textil": 14,
    "construcción": 14,

    # ---- Comercio exterior estructural ----
    "importaciones": 10,
    "exportaciones": 10,
    "balanza": 15,
    "déficit": 12,
    "superávit": 12,
    "dumping": 20,
    "antidumping": 22,
    "arancel": 15,
    "mercosur": 15,
    "brasil": 12,

    # ---- Inversión productiva ----
    "inversión": 16,
    "capital": 14,
    "crédito": 14,
    "financiamiento": 14,

    # ---- Política ----
    "caputo": 10,
    "milei": 10,
    "quirno": 10,
    "uia": 50,
    "gobierno": 1,
    "economía": 1,
    "economia": 1,

    # ---- Penalizaciones ----
    "supermercado": -4,
    "descuentos": -3,
    "verano": -3,
    "hamaca": -4,
    "ofertas": -3,
    "oferta": -5,
    "turismo": -3,
    "vacaciones": -4,
    "restaurante": -3,
}


def _news_score_title(title: str) -> int:
    t = str(title).lower()
    score = 0
    for k, w in NEWS_WEIGHTS.items():
        if k in t:
            score += w
    if "argentina" in t:
        score += 1
    return int(score)


def _parse_rss(xml_bytes: bytes, feed_url: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        channel = root.find(".//channel")
    if channel is None:
        return []

    out = []
    for it in channel.findall("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        if not title or not link:
            continue

        dt = None
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
            except Exception:
                dt = None

        out.append(
            {
                "title": title,
                "link": link,
                "published": dt,
                "source": urlparse(feed_url).netloc.replace("www.", ""),
            }
        )
    return out


@st.cache_data(ttl=900, show_spinner=False)
def _load_news_scored():
    items = []
    for url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            items.extend(_parse_rss(r.content, url))
        except Exception:
            continue

    df = pd.DataFrame(items)
    if df.empty:
        return df

    df = df.drop_duplicates(subset=["link"]).copy()
    df["published"] = pd.to_datetime(df["published"], errors="coerce")
    df["score"] = df["title"].apply(_news_score_title)

    df = df.sort_values(["score", "published"], ascending=[False, False])
    return df.head(50)


def _build_news_ticker_html(df_news: pd.DataFrame, top_n: int = 12) -> str:
    if df_news is None or df_news.empty:
        return ""

    df_top = df_news[df_news["score"] > 0].head(top_n)
    if df_top.empty:
        df_top = df_news.head(min(top_n, 8))

    parts = []
    for _, r in df_top.iterrows():
        parts.append(
            "<span class='tk-item'>"
            f"<a class='tk-link' href='{r['link']}' target='_blank'>"
            f"📌 {r['title']} <span class='tk-src'>— {r['source']}</span>"
            "</a></span>"
        )

    return "<span class='tk-sep'>•</span>".join(parts)


# ============================================================
# HOME
# ============================================================
def render_main_home(go_to):

    logo_b64 = _img_to_b64("assets/okok.png")

    st.markdown(
        """
        <style>
          .home-shell{
            max-width:1200px;
            margin:0px auto 5px auto;
            padding:0 16px;
            display:flex;
            flex-direction:column;
            align-items:center;
          }

          .home-title{
            font-size:46px;
            font-weight:900;
            letter-spacing:-0.8px;
            color:#0f172a;
            margin:16px 0 24px 0;
            text-align:center;
          }

          /* =======================
            TICKER PRO (igual macro)
          ======================= */
          .ticker-wrap{
            width: 82%;
            margin: 10px auto 12px auto;
            background:#0b0b0b;
            border-radius:12px;
            overflow:hidden;
            border: 1px solid rgba(255,255,255,.10);
          }

          .ticker-viewport{ padding: 9px 0; }

          .ticker-track{
            display:inline-block;
            white-space:nowrap;
            animation:tickerScroll 75s linear infinite;
            will-change: transform;
          }
          @keyframes tickerScroll{
            from{ transform:translateX(0%); }
            to{ transform:translateX(-50%); }
          }

          /* ===== Bloomberg-ish ===== */
          .tk-item{
            display:inline-block;
            padding: 0 8px;
            font-family: Inter, "Segoe UI", -apple-system, BlinkMacSystemFont, Arial, sans-serif;
            font-size: 13.5px;
            font-weight: 500;
            letter-spacing: 0.15px;
            color: rgba(255,255,255,0.92) !important;
          }
          .tk-sep{
            margin: 0 6px;
            color: rgba(255,255,255,.28) !important;
          }
          .tk-link{
            color: rgba(255,255,255,0.92) !important;
            text-decoration:none !important;
          }
          .tk-link:hover{
            text-decoration: none !important;
            color: rgba(170,215,255,0.98) !important;
          }
          .tk-src{
            opacity: 0.60;
            font-weight: 400;
          }


          /* =======================
            CARDS
          ======================= */
          .home-cards div.stButton > button{
            width:100% !important;
            height:156px !important;
            border-radius:22px !important;
            background:#ffffff !important;
            border:1px solid rgba(15,23,42,0.14) !important;
            box-shadow:0 12px 28px rgba(15,23,42,0.12) !important;

            font-weight:900 !important;
            font-size:24px !important;
            color:#0f172a !important;

            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
            text-align:center !important;

            transition:transform 140ms ease, box-shadow 140ms ease, border-color 140ms ease !important;
          }

          .home-cards div.stButton > button:hover{
            transform:translateY(-3px);
            box-shadow:0 18px 36px rgba(15,23,42,0.16) !important;
            border-color:rgba(15,23,42,0.22) !important;
          }

        </style>
        """,
        unsafe_allow_html=True,
    )


    st.markdown("<div class='home-shell'>", unsafe_allow_html=True)

    ticker_ph = st.empty()

    # ---- TICKER ARRIBA ----
    df_news = _load_news_scored()
    news_line = _build_news_ticker_html(df_news)

    if news_line:
        ticker_ph.markdown(
            f"""
            <div class="ticker-wrap">
              <div class="ticker-viewport">
                <div class="ticker-track">
                  {news_line}<span class='tk-sep'>•</span>{news_line}
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        ticker_ph.markdown(
            """
            <div class="ticker-wrap">
              <div class="ticker-viewport" style="padding:10px 14px; color:#cbd5e1; font-weight:700; font-family: Inter, 'Segoe UI', Arial, sans-serif;">
                📌 Sin titulares disponibles (reintenta en unos minutos)
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ---- HEADER ----
    st.markdown(
        "<div class='home-title'>Monitor CEU–UIA</div>",
        unsafe_allow_html=True
    )

    
    # ---- CARDS ----
    st.markdown("<div class='home-cards'>", unsafe_allow_html=True)

    # Fila 1: 2 columnas
    r1 = st.columns(2, gap="large")

    row1_sections = [
        ("🏭 Producción Industrial", "ipi"),
        ("📈 Actividad Económica", "macro_pbi_emae"),
    ]

    for col, (label, target) in zip(r1, row1_sections):
        with col:
            if st.button(label, use_container_width=True):
                go_to(target)
                
    st.markdown(
        "<div style='height:18px'></div>",
        unsafe_allow_html=True
    )
    
    # Fila 2: 3 columnas
    r2 = st.columns(3, gap="large")

    row2_sections = [
        ("📊 Macroeconomía", "macro_home"),
        ("💼 Empleo Privado", "empleo"),
        ("🚢 Comercio Exterior", "comex"),
    ]

    for col, (label, target) in zip(r2, row2_sections):
        with col:
            if st.button(label, use_container_width=True):
                go_to(target)

    st.markdown("</div>", unsafe_allow_html=True)

    # LOGO
    st.markdown(
        f"""
        <div style="margin-top:30px;text-align:center;">
          <img src="data:image/png;base64,{logo_b64}" width="96"/>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)
