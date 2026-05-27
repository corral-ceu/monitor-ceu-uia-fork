import hmac
import streamlit as st


def check_login():
    """
    Login simple para fase de prueba.
    Valida número de socio + clave contra st.secrets["usuarios"].
    """

    if "logueado" not in st.session_state:
        st.session_state["logueado"] = False

    if st.session_state["logueado"]:
        return True

    st.markdown(
        """
        <style>
        .login-container {
            max-width: 420px;
            margin: 5rem auto 2rem auto;
            padding: 2rem;
            border-radius: 18px;
            background: #ffffff;
            box-shadow: 0 8px 24px rgba(0,0,0,0.08);
            border: 1px solid #e8e8e8;
        }
        .login-title {
            font-size: 1.8rem;
            font-weight: 700;
            color: #12355B;
            margin-bottom: 0.3rem;
            text-align: center;
        }
        .login-subtitle {
            font-size: 0.95rem;
            color: #666;
            margin-bottom: 1.5rem;
            text-align: center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="login-container">
            <div class="login-title">Monitor CEU-UIA</div>
            <div class="login-subtitle">Ingreso para usuarios de prueba</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        socio = st.text_input("Número de socio")
        clave = st.text_input("Clave", type="password")
        ingresar = st.form_submit_button("Ingresar")

    if ingresar:
        socio = socio.strip()

        try:
            usuarios = st.secrets["usuarios"]
        except Exception:
            st.error("No se encontró la configuración de usuarios en secrets.toml.")
            return False

        if socio in usuarios:
            clave_correcta = str(usuarios[socio].get("clave", ""))

            if hmac.compare_digest(clave, clave_correcta):
                st.session_state["logueado"] = True
                st.session_state["socio"] = socio
                st.session_state["nombre_usuario"] = usuarios[socio].get("nombre", "")
                st.rerun()

        st.error("Número de socio o clave incorrectos.")

    return False


def logout_button():
    nombre = st.session_state.get("nombre_usuario", "")
    socio = st.session_state.get("socio", "")

    if nombre:
        st.sidebar.caption(f"Usuario: {nombre}")
    elif socio:
        st.sidebar.caption(f"Socio: {socio}")

    if st.sidebar.button("Cerrar sesión", key="logout_button"):
        st.session_state.clear()
        st.rerun()