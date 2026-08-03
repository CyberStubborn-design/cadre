import streamlit as st
import pandas as pd
from io import BytesIO
import zipfile
import gc

import cadre


def rerun_app():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass


st.set_page_config(page_title="Cadre Quote Automation", layout="wide")
st.title("Cadre Quote Automation")

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

st.sidebar.divider()
if st.sidebar.button("Delete raw files / clear screen"):
    st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
    for key in list(st.session_state.keys()):
        if key != "uploader_key":
            del st.session_state[key]
    gc.collect()
    st.sidebar.success("Raw files and session data cleared.")
    rerun_app()

st.sidebar.divider()
if st.sidebar.button("Clear session data"):
    st.session_state.clear()
    gc.collect()
    st.sidebar.success("Session data cleared.")

uploaded_files = st.file_uploader(
    "Upload Outlook MSG or PDF",
    type=["msg", "pdf"],
    accept_multiple_files=True,
    key=f"file_uploader_{st.session_state['uploader_key']}",
)

if uploaded_files:
    all_rows = []
    all_pdfs = []
    errors = []

    for uploaded_file in uploaded_files:
        try:
            result = cadre.process_uploaded_file(uploaded_file)
            all_rows.extend(result["rows"])
            all_pdfs.extend(result["pdfs"])
        except Exception as exc:
            errors.append(f"{uploaded_file.name}: {exc}")

    if errors:
        st.error("Some files could not be processed")
        for err in errors:
            st.write(err)

    if all_rows:
        df = pd.DataFrame(all_rows).astype(str)

        st.success("Processing complete")
        st.subheader("Extracted Data")
        st.dataframe(df, use_container_width=True)

        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Quotes")
            ws = writer.sheets["Quotes"]
            for col in ws.columns:
                col_letter = col[0].column_letter
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col_letter].width = min(max(max_len + 2, 14), 70)

        st.download_button(
            label="Download Excel",
            data=excel_buffer.getvalue(),
            file_name="cadre_quotes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("cadre_quotes.xlsx", excel_buffer.getvalue())
            for pdf_name, pdf_bytes in all_pdfs:
                zf.writestr(pdf_name, pdf_bytes)

        st.download_button(
            label="Download ZIP: Excel + PDFs",
            data=zip_buffer.getvalue(),
            file_name="cadre_quote_bundle.zip",
            mime="application/zip",
        )

        if st.button("Delete raw files and start new batch"):
            st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
            for key in list(st.session_state.keys()):
                if key != "uploader_key":
                    del st.session_state[key]
            gc.collect()
            st.success("Cleared.")
            rerun_app()
