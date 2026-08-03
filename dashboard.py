import streamlit as st
import pandas as pd
import plotly.express as px
from utils.state import init_state, get_log
from utils.excel_io import load_as_dataframe, get_stats, XLSX_PATH
import os


def render():
    init_state()
    st.title("📊 Dashboard")

    output_path = st.session_state.get("output_xlsx", XLSX_PATH)
    stats = get_stats(output_path)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Rows",        f"{stats['total_rows']:,}")
    c2.metric("Unique Quotes",     f"{stats['unique_quotes']:,}")
    c3.metric("Unique Customers",  f"{stats['unique_customers']:,}")
    c4.metric("Total Sales Value", f"${stats['total_sales']:,.2f}")

    st.markdown("---")
    df = load_as_dataframe(output_path)

    if df.empty:
        st.info("No quotes processed yet. Go to **Upload Quotes** to get started.")
        return

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Sales by customer")
        if "Company" in df.columns and "TotalSales" in df.columns:
            chart_df = df.copy()
            chart_df["TotalSales"] = pd.to_numeric(chart_df["TotalSales"], errors="coerce").fillna(0)
            grouped = (chart_df[chart_df.get("item_id", pd.Series()) != "Tax"]
                       .groupby("Company")["TotalSales"].sum().reset_index()
                       .sort_values("TotalSales", ascending=False).head(10))
            fig = px.bar(grouped, x="TotalSales", y="Company", orientation="h",
                         color="TotalSales", color_continuous_scale="Blues",
                         labels={"TotalSales": "Total ($)", "Company": ""},
                         height=340)
            fig.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Quotes by salesperson")
        if "ReferralEmail" in df.columns and "QuoteNumber" in df.columns:
            sp_df = df.drop_duplicates(subset=["QuoteNumber"])[["ReferralEmail", "QuoteNumber"]].copy()
            sp_count = sp_df.groupby("ReferralEmail")["QuoteNumber"].count().reset_index()
            sp_count.columns = ["Salesperson", "Count"]
            fig2 = px.pie(sp_count, names="Salesperson", values="Count",
                          color_discrete_sequence=px.colors.sequential.Blues_r, height=300)
            fig2.update_traces(textposition="inside", textinfo="percent+label")
            fig2.update_layout(showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")
    st.subheader("All processed quotes")

    f1, f2, f3 = st.columns(3)
    with f1:
        companies = ["All"] + sorted(df["Company"].dropna().unique().tolist()) if "Company" in df.columns else ["All"]
        sel_company = st.selectbox("Filter by customer", companies)
    with f2:
        quotes = ["All"] + sorted(df["QuoteNumber"].dropna().unique().tolist()) if "QuoteNumber" in df.columns else ["All"]
        sel_quote = st.selectbox("Filter by quote #", quotes)
    with f3:
        search = st.text_input("Search item", placeholder="e.g. HS.163")

    filtered = df.copy()
    if sel_company != "All":
        filtered = filtered[filtered["Company"] == sel_company]
    if sel_quote != "All":
        filtered = filtered[filtered["QuoteNumber"] == sel_quote]
    if search:
        mask = (
            filtered.get("item_id", pd.Series(dtype=str)).astype(str).str.contains(search, case=False, na=False) |
            filtered.get("item_desc", pd.Series(dtype=str)).astype(str).str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]

    st.dataframe(filtered, width="stretch", height=400)
    st.caption(f"Showing {len(filtered):,} of {len(df):,} rows")

    if os.path.exists(output_path):
        with open(output_path, "rb") as f:
            st.download_button("⬇️ Download cadre_quotes.xlsx", f.read(),
                               "cadre_quotes.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.markdown("---")
    st.subheader("Processing log")
    log_entries = get_log()
    if not log_entries:
        st.caption("No activity yet.")
    else:
        icons = {"success": "✅", "error": "❌", "warning": "⚠️", "processing": "⏳", "skipped": "⏭️", "info": "ℹ️"}
        log_df = pd.DataFrame(log_entries)
        log_df["status"] = log_df["status"].map(lambda s: f"{icons.get(s,'')} {s}")
        st.dataframe(log_df, width="stretch", height=250)
