import streamlit as st
import os
from utils.state import init_state

def render():
    init_state()
    st.title("⚙️ Settings")

    # Auto-load from Streamlit secrets or env var
    if not st.session_state.get("groq_api_key"):
        try:
            st.session_state["groq_api_key"] = st.secrets["groq"]["api_key"]
        except Exception:
            st.session_state["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")

    api_key = st.text_input(
        "Groq API key",
        value=st.session_state.get("groq_api_key", ""),
        type="password",
        placeholder="gsk_...",
    )

    if st.button("💾 Save", type="primary"):
        st.session_state["groq_api_key"] = api_key
        st.session_state["groq_model"]   = "llama-3.3-70b-versatile"
        st.session_state["output_xlsx"]  = "data/cadre_quotes.xlsx"
        st.success("✅ Saved — go to Upload Quotes tab")
