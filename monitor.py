"""
Inbox Monitor page — start/stop the Outlook polling agent, show live log.
"""

import streamlit as st
import pandas as pd
import time

from utils.state import init_state, get_agent_status, set_agent_status, log_event, get_log
from utils.excel_io import XLSX_PATH


def render():
    init_state()
    st.title("📬 Inbox Monitor")
    st.caption("Automatically watch your Outlook inbox for new Sales Quote emails.")

    api_key  = st.session_state.get("groq_api_key", "")
    email    = st.session_state.get("outlook_email", "")
    password = st.session_state.get("outlook_password", "")
    model    = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)
    poll_interval = st.session_state.get("poll_interval", 60)

    # ── Config check ──────────────────────────────────────────────────────────
    missing = []
    if not api_key:   missing.append("Groq API key")
    if not email:     missing.append("Outlook email")
    if not password:  missing.append("Outlook password")
    if missing:
        st.warning(f"⚠️ Missing config: {', '.join(missing)}. Go to **⚙️ Settings**.")
        return

    status = get_agent_status()

    # ── Status + controls ─────────────────────────────────────────────────────
    col_status, col_btn = st.columns([3, 1])
    with col_status:
        if status == "running":
            st.success(f"🟢 Monitor is **running** — polling every {poll_interval}s")
        else:
            st.error("🔴 Monitor is **stopped**")

    with col_btn:
        if status == "running":
            if st.button("⏹ Stop monitor", type="secondary", use_container_width=True):
                stop_event = st.session_state.get("agent_stop_event")
                if stop_event:
                    from utils.monitor import stop_monitor
                    stop_monitor(stop_event)
                set_agent_status("stopped")
                log_event("SYSTEM", "info", "Monitor stopped by user")
                st.rerun()
        else:
            if st.button("▶️ Start monitor", type="primary", use_container_width=True):
                from utils.monitor import start_monitor
                thread, stop_event = start_monitor(
                    email_addr=email,
                    password=password,
                    api_key=api_key,
                    model=model,
                    output_xlsx=output_path,
                    poll_interval=poll_interval,
                    processed_ids=st.session_state["processed_ids"],
                    log_fn=log_event,
                    status_fn=set_agent_status,
                )
                st.session_state["agent_thread"] = thread
                st.session_state["agent_stop_event"] = stop_event
                set_agent_status("starting")
                log_event("SYSTEM", "info", f"Monitor started → {email}")
                st.rerun()

    st.markdown("---")

    # ── Configuration summary ─────────────────────────────────────────────────
    with st.expander("Current configuration", expanded=False):
        st.markdown(f"""
| Setting | Value |
|---|---|
| Outlook inbox | `{email}` |
| Groq model | `{model}` |
| Poll interval | `{poll_interval}s` |
| Output file | `{output_path}` |
| Processed IDs cached | `{len(st.session_state['processed_ids'])}` |
""")

    # ── Live log ──────────────────────────────────────────────────────────────
    st.subheader("Activity log")
    log_entries = get_log()
    if not log_entries:
        st.caption("No activity yet. Start the monitor or upload files manually.")
    else:
        status_icons = {"success": "✅", "error": "❌", "warning": "⚠️", "processing": "⏳",
                        "skipped": "⏭️", "info": "ℹ️", "starting": "🔄"}
        log_df = pd.DataFrame(log_entries)
        log_df["status"] = log_df["status"].map(lambda s: f"{status_icons.get(s, '')} {s}")
        st.dataframe(log_df, use_container_width=True, height=400,
                     column_config={
                         "time":    st.column_config.TextColumn("Time", width="small"),
                         "quote":   st.column_config.TextColumn("Quote #", width="small"),
                         "status":  st.column_config.TextColumn("Status", width="small"),
                         "message": st.column_config.TextColumn("Message"),
                     })

    # ── Auto-refresh when running ─────────────────────────────────────────────
    if status == "running":
        st.caption("⟳ Auto-refreshing every 10 seconds…")
        time.sleep(10)
        st.rerun()
