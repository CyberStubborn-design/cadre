import streamlit as st
from datetime import datetime
import os


def init_state():
    # Always try to load API key from secrets/env first
    if not st.session_state.get("groq_api_key"):
        try:
            st.session_state["groq_api_key"] = st.secrets["groq"]["api_key"]
        except Exception:
            st.session_state["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")

    defaults = {
        "agent_status":     "stopped",
        "agent_thread":     None,
        "processing_log":   [],
        "groq_model":       "llama-3.3-70b-versatile",
        "outlook_email":    "",
        "outlook_password": "",
        "output_xlsx":      "data/cadre_quotes.xlsx",
        "poll_interval":    60,
        "processed_ids":    set(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_agent_status() -> str:
    init_state()
    return st.session_state.get("agent_status", "stopped")


def set_agent_status(status: str):
    init_state()
    st.session_state["agent_status"] = status


def log_event(quote_number: str, status: str, message: str):
    init_state()
    st.session_state["processing_log"].append({
        "time":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quote":   quote_number,
        "status":  status,
        "message": message,
    })
    if len(st.session_state["processing_log"]) > 200:
        st.session_state["processing_log"] = st.session_state["processing_log"][-200:]


def get_log() -> list[dict]:
    init_state()
    return list(reversed(st.session_state["processing_log"]))
