import streamlit as st
import pandas as pd
from utils.state import init_state, log_event
from utils.extractor import process_msg_bytes, pdf_bytes_to_text
from utils.excel_io import append_rows, quote_exists, XLSX_PATH


def render():
    init_state()
    st.title("📤 Upload Quotes")

    api_key     = st.session_state.get("groq_api_key", "")
    model       = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)

    if not api_key:
        st.error("⚠️ Go to **⚙️ Settings** tab, paste your Groq API key and click Save.")
        return

    # type=None — no extension filter, works on Streamlit Cloud
    uploaded = st.file_uploader(
        "Upload .msg files",
        type=None,
        accept_multiple_files=True,
    )

    if not uploaded:
        st.info("Upload your Outlook `.msg` files above.\n\n"
                "**Tip:** If the file picker blocks .msg files, rename the file to `.pdf` before uploading — "
                "the agent detects the real format automatically.")
        return

    if st.button("🚀 Process", type="primary"):
        progress = st.progress(0)
        all_results = []

        for i, file in enumerate(uploaded):
            progress.progress((i + 1) / len(uploaded), text=f"Processing {file.name}…")
            file.seek(0)
            raw = file.read()

            # Detect by magic bytes, not filename
            is_pdf = raw[:4] == b"%PDF"
            with st.spinner(f"Extracting {file.name}…"):
                if is_pdf:
                    result = _process_pdf(raw, api_key, model)
                else:
                    result = process_msg_bytes(raw, api_key, model)
            result["filename"] = file.name
            all_results.append(result)

        progress.empty()

        for result in all_results:
            fname  = result["filename"]
            data   = result.get("data", {})
            rows   = result.get("rows", [])
            issues = result.get("issues", [])
            q_num  = data.get("quote_number", "UNKNOWN")
            items  = [r for r in rows if r.get("item_id") != "Tax"]

            with st.expander(f"📄 {fname} — Quote #{q_num} ({len(items)} items)", expanded=True):
                for iss in issues:
                    st.warning(iss)

                if rows:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Company",      data.get("company", "—"))
                    c2.metric("Quote #",      q_num)
                    c3.metric("Valid through", data.get("quote_valid_through", "—"))
                    c4.metric("Total",        f"${data.get('grand_total', 0):,.2f}")

                    st.dataframe(
                        pd.DataFrame([{"item_id": r.get("item_id"),
                                       "item_desc": r.get("item_desc"),
                                       "Unit Price": r.get("Unit Price"),
                                       "TotalSales": r.get("TotalSales")} for r in rows]),
                        use_container_width=True, height=200,
                    )

                    if quote_exists(q_num, output_path):
                        st.info(f"Quote #{q_num} already exists.")
                        col1, col2 = st.columns(2)
                        if col1.button("Overwrite", key=f"ow_{q_num}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Overwritten from {fname}")
                            st.success("Overwritten ✅")
                        if col2.button("Skip", key=f"sk_{q_num}"):
                            st.info("Skipped.")
                    else:
                        if st.button(f"💾 Save quote #{q_num}", key=f"save_{q_num}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Saved {len(rows)} rows from {fname}")
                            st.success(f"✅ {len(rows)} rows saved")
                else:
                    st.error("No data extracted.")
                    if result.get("pdf_text"):
                        with st.expander("Debug — raw PDF text"):
                            st.text(result["pdf_text"][:2000])


def _process_pdf(pdf_bytes, api_key, model):
    from utils.extractor import extract_quote_with_groq, build_rows, validate_rows
    try:
        text   = pdf_bytes_to_text(pdf_bytes)
        data   = extract_quote_with_groq(text, api_key, model)
        q_num  = data.get("quote_number", "UNKNOWN")
        rows   = build_rows(data, None, q_num)
        issues = validate_rows(data, rows)
        return {"rows": rows, "data": data, "issues": issues, "pdf_text": text}
    except Exception as e:
        return {"rows": [], "data": {}, "issues": [str(e)], "pdf_text": ""}
