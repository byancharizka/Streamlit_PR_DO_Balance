import os
import logging
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import plotly.express as px
import pytz
import requests
import streamlit as st

# =========================================================
# 1) PAGE CONFIG - WAJIB PALING ATAS
# =========================================================
st.set_page_config(
    layout="wide",
    page_title="SIBIMA Performance Dashboard",
    initial_sidebar_state="expanded"
)

# =========================================================
# 2) LOGGING CONFIG
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# =========================================================
# 3) APP CONFIG
# =========================================================
TIMEZONE = pytz.timezone("Asia/Jakarta")
TODAY = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

DEFAULT_START_DATE = date(2026, 1, 1)
REQUEST_TIMEOUT = int(os.getenv("SIBIMA_API_TIMEOUT", "30"))

# API TANPA TOKEN
BASE_URL = "https://eas.sibima.id/api/dashboard/"

if not BASE_URL.endswith("/"):
    BASE_URL += "/"

# =========================================================
# 4) CSS CUSTOM
# =========================================================
st.markdown("""
<style>
/* ====== TITLE UTAMA ====== */
h1 {
    font-size: 2rem !important;   /* paling besar */
    font-weight: 800;
    color: #222;
}

/* ====== SUBTITLE & SUBHEADER ====== */
h2, h3, h4, h5, h6 {
    font-size: 1rem !important;   /* lebih kecil dari h1 */
    font-weight: 600;
    color: #444;
}

/* ====== LAYOUT CONTAINER ====== */
.block-container {
    padding-top: 2rem;
    padding-bottom: 1rem;
    padding-left: 2rem;
    padding-right: 2rem;
    max-width: 100%;
}

/* ====== METRIC COMPONENTS ====== */
[data-testid="stMetricLabel"] {
    font-size: 0.7rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 0.8rem !important;
}

/* ====== CUSTOM METRIC CARD ====== */
.metric-card {
    background-color: #f4f4f4;
    border: 1px solid #dcdcdc;
    border-radius: 12px;
    padding: 16px;
    box-shadow: 1px 2px 8px rgba(0,0,0,0.05);
    text-align: center;
    margin: 8px 0;
    font-size: 0.75rem;
}
            
.metric-card div {
    font-size: 0.75rem !important;
}            

/* ====== SMALL NOTES ====== */
.small-note {
    color: #666;
    font-size: 0.70rem;
}
            
h3, h4, h5 {
    margin-bottom: 0.1rem !important;
}

/* Kurangi jarak antar komponen container */
div[data-testid="stVerticalBlock"] {
    margin-top: 0.1rem !important;
    margin-bottom: 0.1rem !important;
}

/* Kurangi padding default di dalam container */
div[data-testid="stContainer"] {
    padding-top: 0.1rem !important;
    padding-bottom: 0.1rem !important;
}
                        
</style>
""", unsafe_allow_html=True)


# =========================================================
# 5) UTILITIES
# =========================================================
def metric_card(label: str, value: str):
    st.markdown(
        f"""
        <div class="metric-card">
            <div style="color: #666; font-size: 0.95rem;">{label}</div>
            <div style="font-size: 0.9rem; font-weight: 700; color: #222;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Pastikan semua kolom ada agar operasi berikutnya aman."""
    if df.empty:
        for col in columns:
            if col not in df.columns:
                df[col] = pd.Series(dtype="object")
        return df

    for col in columns:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def safe_to_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Konversi kolom ke numerik dengan aman."""
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def safe_to_datetime(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Konversi kolom tanggal dengan aman dan hilangkan timezone."""
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")
        try:
            df[col] = df[col].dt.tz_localize(None)
        except Exception:
            pass
    return df


def normalize_text_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Normalisasi string agar aman untuk pencarian."""
    for col in columns:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def safe_unique_count(df: pd.DataFrame, col: str) -> int:
    if df.empty or col not in df.columns:
        return 0
    return df[col].nunique(dropna=True)


def safe_mean(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return float(df[col].mean()) if not df[col].dropna().empty else 0.0


def safe_sum(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return float(df[col].sum())


def to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


# =========================================================
# 6) API FETCHING
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def get_api_data(endpoint: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """
    Ambil data dari endpoint dashboard API.
    Robust terhadap:
    - network timeout
    - response non-200
    - format JSON yang tidak sesuai
    """
    actual_start = start_date if start_date else DEFAULT_START_DATE.strftime("%Y-%m-%d")
    actual_end = end_date if end_date else TODAY

    url = f"{BASE_URL}{endpoint}"
    params = {
        "date_start": actual_start,
        "date_end": actual_end
    }

    try:
        logger.info("Fetching endpoint=%s params=%s", endpoint, params)
        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        payload = response.json()

        # Expected format: {"data": {"data": [...]}}
        if isinstance(payload, dict):
            data_layer = payload.get("data", {})
            if isinstance(data_layer, dict):
                rows = data_layer.get("data", [])
                if isinstance(rows, list):
                    return pd.DataFrame(rows)

        logger.warning("Unexpected JSON structure for endpoint=%s", endpoint)
        return pd.DataFrame()

    except requests.Timeout:
        logger.exception("Timeout when fetching endpoint=%s", endpoint)
        st.warning(f"Timeout saat mengambil data dari endpoint: {endpoint}")
        return pd.DataFrame()

    except requests.RequestException as e:
        logger.exception("Request error for endpoint=%s", endpoint)
        st.warning(f"Gagal mengambil data dari endpoint {endpoint}: {e}")
        return pd.DataFrame()

    except ValueError:
        logger.exception("Invalid JSON for endpoint=%s", endpoint)
        st.warning(f"Response bukan JSON valid untuk endpoint {endpoint}")
        return pd.DataFrame()

    except Exception as e:
        logger.exception("Unexpected error for endpoint=%s", endpoint)
        st.warning(f"Error tidak terduga saat mengambil {endpoint}: {e}")
        return pd.DataFrame()


def load_all_data() -> dict[str, pd.DataFrame]:
    endpoint_map = {
        "pr": ("pr-balance", {"Tgl. PR": "transaction_date"}),
        "po": ("po-balance", {"Tgl. PO": "transaction_date"}),
        "do": ("do-balance", {"Tgl. DO": "transaction_date"}),
        "npr": ("outstanding-npr", {"Tanggal": "transaction_date"}),
        "pur": ("outstanding-pur", {"Tanggal": "transaction_date"}),
    }

    result = {}
    for key, (endpoint, rename_map) in endpoint_map.items():
        df = get_api_data(endpoint)
        if not df.empty:
            df = df.rename(columns=rename_map)
        result[key] = df

    return result


# =========================================================
# 7) FILTERS & TRANSFORM
# =========================================================
def apply_cumulative_filter(df: pd.DataFrame, end_date_val) -> pd.DataFrame:
    """
    Ambil SEMUA data dari awal hingga end_date.
    """
    if df.empty or "transaction_date" not in df.columns:
        return df.copy()

    working = df.copy()
    working = safe_to_datetime(working, "transaction_date")

    upper_limit = pd.to_datetime(end_date_val).replace(hour=23, minute=59, second=59)
    return working[
        working["transaction_date"].notna() &
        (working["transaction_date"] <= upper_limit)
    ].copy()


def apply_search_filter(
    df: pd.DataFrame,
    search_number: str = "",
    search_status: str = "",
    search_pic: str = ""
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    working = df.copy()
    working = normalize_text_columns(
        working,
        ["Status", "PIC Procurement", "PIC Purchasing", "PIC", "No. PR", "No. DO", "No. PUR", "No. Transaksi"]
    )

    # Filter nomor dokumen: mencari di semua kolom string
    if search_number:
        pattern = search_number.strip().lower()
        string_cols = working.select_dtypes(include=["object"]).columns.tolist()
        if string_cols:
            mask_number = working[string_cols].apply(
                lambda col: col.str.lower().str.contains(pattern, na=False)
            ).any(axis=1)
            working = working[mask_number]

    # Filter status
    if search_status and "Status" in working.columns:
        working = working[
            working["Status"].str.contains(search_status.strip(), case=False, na=False)
        ]

    # Filter PIC -> OR logic, bukan AND
    if search_pic:
        pic_cols = [col for col in ["PIC Procurement", "PIC Purchasing", "PIC"] if col in working.columns]
        if pic_cols:
            mask_pic = working[pic_cols].apply(
                lambda col: col.str.contains(search_pic.strip(), case=False, na=False)
            ).any(axis=1)
            working = working[mask_pic]

    return working.copy()


def assign_unassigned(df: pd.DataFrame, col: str) -> pd.DataFrame:
    working = df.copy()
    if col in working.columns:
        working[col] = working[col].fillna("Unassigned").astype(str).str.strip()
        working.loc[working[col] == "", col] = "Unassigned"
    return working


def get_top_pic(df: pd.DataFrame, pic_col: str, doc_col: str) -> str:
    if df.empty or pic_col not in df.columns or doc_col not in df.columns:
        return "Tidak ada"

    working = assign_unassigned(df, pic_col)
    working = working[working[pic_col] != "Unassigned"]

    if working.empty:
        return "Tidak ada"

    grouped = (
        working.groupby(pic_col, dropna=False)[doc_col]
        .nunique()
        .sort_values(ascending=False)
    )

    return grouped.index[0] if not grouped.empty else "Tidak ada"


def summarize_status(df: pd.DataFrame, doc_col: str, nominal_col: str = "Nominal") -> pd.DataFrame:
    if df.empty or "Status" not in df.columns:
        return pd.DataFrame(columns=["Status", "Total_Doc", "Total_Amount"])

    working = df.copy()
    working = ensure_columns(working, [doc_col, nominal_col, "Status"])
    working = safe_to_numeric(working, [nominal_col])

    summary = (
        working.groupby("Status", dropna=False)
        .agg(
            Total_Doc=(doc_col, "nunique"),
            Total_Amount=(nominal_col, "sum")
        )
        .reset_index()
    )
    return summary


def summarize_pic_status(df: pd.DataFrame, pic_col: str, doc_col: str) -> pd.DataFrame:
    if df.empty or pic_col not in df.columns or "Status" not in df.columns or doc_col not in df.columns:
        return pd.DataFrame(columns=[pic_col, "Status", "Jumlah_Doc"])

    working = assign_unassigned(df, pic_col)

    summary = (
        working.groupby([pic_col, "Status"], dropna=False)
        .agg(Jumlah_Doc=(doc_col, "nunique"))
        .reset_index()
        .sort_values(by="Jumlah_Doc", ascending=False)
    )
    return summary


# =========================================================
# 8) CHART HELPERS
# =========================================================
STATUS_COLORS = {
    "Complete": "#00CC96",
    "In Progress": "#F2C94C",
    "Approved": "#F2994A",
    "Rejected": "#EB5757",
    "Pending": "#56CCF2",
}

def render_status_pie(summary_df: pd.DataFrame, title: str):
    if summary_df.empty:
        st.info("Data status tidak tersedia.")
        return

    fig = px.pie(
        summary_df,
        values="Total_Amount",
        names="Status",
        color="Status",
        color_discrete_map=STATUS_COLORS,
        hole=0.45,
    )
    

    fig.update_traces(
        textinfo="percent+value",
        texttemplate="%{percent:.1%}<br>(Rp %{value:,.0f})"
    )
    st.plotly_chart(fig, use_container_width=True)


def render_status_bar(summary_df: pd.DataFrame, title: str):
    if summary_df.empty:
        st.info("Data status tidak tersedia.")
        return

    fig = px.bar(
        summary_df,
        x="Status",
        y="Total_Amount",
        color="Status",
        color_discrete_map=STATUS_COLORS,
        title=title
    )

    fig.update_traces(
        texttemplate="Rp %{y:,.0f}",
        textposition="outside"
    )
    fig.update_layout(
        showlegend=False,
        yaxis=dict(
            tickformat=",.0f",
            title="Total Nominal (Rp)"
        )
    )
    st.plotly_chart(fig, use_container_width=True)


def render_pic_bar(summary_df: pd.DataFrame, x_col: str, y_col: str, color_col: str | None, title: str):
    if summary_df.empty:
        st.info("Data PIC tidak tersedia.")
        return

    kwargs = {
        "data_frame": summary_df,
        "x": x_col,
        "y": y_col,
    }

    if color_col and color_col in summary_df.columns:
        kwargs["color"] = color_col
        kwargs["color_discrete_map"] = STATUS_COLORS

    fig = px.bar(**kwargs)
    fig.update_traces(
        texttemplate="%{y}",
        textposition="inside",
        textfont_size=10,
        textangle=0
    )
    fig.update_layout(
        uniformtext_mode="hide",
        uniformtext_minsize=8
    )
    st.plotly_chart(fig, use_container_width=True)


def render_pic_heatmap(df: pd.DataFrame, pic_col: str, date_col: str, title: str):
    """Menampilkan heatmap aktivitas PIC berdasarkan jumlah dokumen unik per bulan."""
    if df.empty or pic_col not in df.columns or date_col not in df.columns:
        st.info("Data tidak tersedia untuk heatmap aktivitas PIC.")
        return

    working = df.copy()
    working[date_col] = pd.to_datetime(working[date_col], errors="coerce")
    working[pic_col] = working[pic_col].fillna("Unassigned")

    bulan_map = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }
    working["Bulan"] = working[date_col].dt.month.map(bulan_map)
    bulan_order = list(bulan_map.values())
    working["Bulan"] = pd.Categorical(working["Bulan"], categories=bulan_order, ordered=True)

    working["No. PR"] = working["No. PR"].astype(str).str.strip().str.upper()
    summary = (
        working.groupby([pic_col, "Bulan"])["No. PR"]
        .nunique()
        .reset_index(name="Jumlah Dokumen")
        .sort_values("Bulan")
    )

    # 🔥 Buat heatmap
    fig = px.density_heatmap(
        summary,
        x="Bulan",
        y=pic_col,
        z="Jumlah Dokumen",
        color_continuous_scale=["#56CCF2", "#F2994A", "#EB5757"],
    )

    # 🔧 Atur layout agar legenda di bawah dan heatmap lebih lebar
    fig.update_layout(
        xaxis_title="Bulan",
        yaxis_title="PIC",
        coloraxis_colorbar=dict(
            title="Jumlah Dokumen",
            orientation="h",          # horizontal
            yanchor="bottom",
            y=-0.25,                  # posisi di bawah grafik
            xanchor="center",
            x=0.5
        ),
        margin=dict(l=100, r=40, t=60, b=80),
        height=500
    )

    st.plotly_chart(fig, use_container_width=True)

    # Tambahkan keterangan di bawah heatmap
    st.markdown(
        "<div style='text-align:center; font-size:0.8rem; color:#6f6f6f;'>"
        "📝 <b>Keterangan:</b> " \
        "Kotak dengan warna mendekati merah artinya punya outstanding PR yang lebih banyak sedangkan " \
        "kotak dengan warna mendekati biru artinya outstanding PRnya lebih sedikit"
        "</div>",
        unsafe_allow_html=True
    )





# =========================================================
# 9) MAIN APP
# =========================================================
def main():
    st.title("SIBIMA Performance Dashboard")

    # ---------- LOAD DATA ----------
    with st.spinner("Mengambil data dashboard..."):
        data = load_all_data()

    df_pr = data["pr"]
    df_po = data["po"]
    df_do = data["do"]
    df_npr = data["npr"]
    df_pur = data["pur"]

    # ---------- TOP FILTERS ----------
    col_head1, col_head2, col_head3, col_head4 = st.columns([1, 1, 1, 1])

    with col_head1:
        selected_date_range = st.date_input(
            "Select Date Range 📅",
            value=(DEFAULT_START_DATE, date.today()),
            max_value=date.today()
        )

    with col_head2:
        search_number = st.text_input(
            "Cari Nomor Dokumen 🔍",
            placeholder="No. PR / No. DO / No. NPR / No. PUR"
        )

    with col_head3:
        search_status = st.text_input(
            "Cari Status 🔍",
            placeholder="Complete / In Progress / Approved"
        )

    with col_head4:
        search_pic = st.text_input(
            "Cari PIC 🔍",
            placeholder="PIC Procurement / PIC Purchasing / PIC PUR"
        )

    # ---------- DEFAULT SAFE COPY ----------
    df_pr_f = df_pr.copy()
    df_po_f = df_po.copy()
    df_do_f = df_do.copy()
    df_npr_f = df_npr.copy()
    df_pur_f = df_pur.copy()

    # ---------- DATE FILTER (CUMULATIVE) ----------
    if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2:
        report_end_date = selected_date_range[1]
        df_pr_f = apply_cumulative_filter(df_pr_f, report_end_date)
        df_po_f = apply_cumulative_filter(df_po_f, report_end_date)
        df_do_f = apply_cumulative_filter(df_do_f, report_end_date)
        df_npr_f = apply_cumulative_filter(df_npr_f, report_end_date)
        df_pur_f = apply_cumulative_filter(df_pur_f, report_end_date)

    # ---------- SEARCH FILTER ----------
    df_pr_f = apply_search_filter(df_pr_f, search_number, search_status, search_pic)
    df_po_f = apply_search_filter(df_po_f, search_number, search_status, search_pic)
    df_do_f = apply_search_filter(df_do_f, search_number, search_status, search_pic)
    df_npr_f = apply_search_filter(df_npr_f, search_number, search_status, search_pic)
    df_pur_f = apply_search_filter(df_pur_f, search_number, search_status, search_pic)

    # ---------- ENSURE IMPORTANT COLUMNS ----------
    df_pr_f = ensure_columns(df_pr_f, ["Nominal", "No. PR", "Status", "PIC Procurement"])
    df_po_f = ensure_columns(df_po_f, ["Nominal"])
    df_do_f = ensure_columns(df_do_f, ["Nominal", "No. DO", "PIC Purchasing"])
    df_npr_f = ensure_columns(df_npr_f, ["No. Transaksi"])
    df_pur_f = ensure_columns(df_pur_f, ["No. PUR", "PIC", "Status"])

    df_pr_f = safe_to_numeric(df_pr_f, ["Nominal"])
    df_po_f = safe_to_numeric(df_po_f, ["Nominal"])
    df_do_f = safe_to_numeric(df_do_f, ["Nominal"])

    # ---------- METRICS ----------
    total_pr_unpr = safe_sum(df_pr_f, "Nominal")
    total_po_unpr = safe_sum(df_po_f, "Nominal")
    total_do_unpr = safe_sum(df_do_f, "Nominal")

    total_pr_count = safe_unique_count(df_pr_f, "No. PR")
    total_pr_rows = len(df_pr_f)
    total_do_count = safe_unique_count(df_do_f, "No. DO")
    total_do_rows = len(df_do_f)
    total_npr_count = safe_unique_count(df_npr_f, "No. Transaksi")
    total_npr_rows = len(df_npr_f)

    avg_nominal_do = safe_mean(df_do_f, "Nominal")

    top_pic_pr = get_top_pic(df_pr_f, "PIC Procurement", "No. PR")
    top_pic_do = get_top_pic(df_do_f, "PIC Purchasing", "No. DO")
    top_pic_pur = get_top_pic(df_pur_f, "PIC", "No. PUR")

    # ---------- LAYOUT ----------
    col_kiri, col_tengah, col_kanan = st.columns([1, 1, 1])

    # =====================================================
    # LEFT - PR & DO
    # =====================================================
    with col_kiri:
        with st.container(border=True):
            st.subheader("📊 Detail Outstanding PR & DO")

            c1, c2 = st.columns(2)
            with c1:
                metric_card("PR Balance", f"Rp {total_pr_unpr:,.0f}")
            with c2:
                metric_card("PO Balance", f"Rp {total_po_unpr:,.0f}")

            c1, c2, c3 = st.columns(3)
            with c1:
                metric_card("Total Dokumen PR", f"{total_pr_count:,}")
            with c2:
                metric_card("Total Item PR", f"{total_pr_rows:,}")
            with c3:
                metric_card("PIC Terbanyak", top_pic_pr)

        pr_summary = summarize_status(df_pr_f, doc_col="No. PR", nominal_col="Nominal")

        with st.container(border=True):
            st.subheader("🍩 Proporsi Nominal PR per Status")
            render_status_pie(pr_summary, "Persentase Distribusi Nominal PR")

        with st.container(border=True):
            st.subheader("🔍 Analisis Status PR")

            if not pr_summary.empty:
                pr_summary_display = pr_summary.copy()
                pr_summary_display["Total_Amount"] = pr_summary_display["Total_Amount"].apply(lambda x: f"Rp {x:,.0f}")
                st.dataframe(pr_summary_display, use_container_width=True, hide_index=True)
            else:
                st.info("Tidak ada data status PR untuk ditampilkan.")

            render_status_bar(pr_summary, "Distribusi Nominal PR per Status")

        pic_summary_pr = summarize_pic_status(df_pr_f, "PIC Procurement", "No. PR")
        with st.container(border=True):
            st.subheader("👤 Analisis PIC Procurement per Status")
            render_pic_bar(
                summary_df=pic_summary_pr,
                x_col="PIC Procurement",
                y_col="Jumlah_Doc",
                color_col="Status",
                title="Jumlah PR per PIC Procurement"
            )
        
        with st.container(border=True):
            st.subheader("🔥 Heatmap Aktivitas PIC Procurement")
            render_pic_heatmap(
                df_pr_f,
                pic_col="PIC Procurement",
                date_col="transaction_date",
                title="Heatmap Aktivitas PIC Procurement per Bulan"
            )


        # Download per PIC PR
        with st.container(border=True):
            st.subheader("📥 Download Data PR per PIC")

            if not df_pr_f.empty and "PIC Procurement" in df_pr_f.columns:
                options = sorted(df_pr_f["PIC Procurement"].fillna("Unassigned").astype(str).unique().tolist())
                selected_pic = st.selectbox("Pilih PIC Procurement:", options, key="pr_pic_select")

                filtered = df_pr_f[df_pr_f["PIC Procurement"].fillna("Unassigned").astype(str) == selected_pic].copy()
                st.download_button(
                    label=f"Download Data {selected_pic}.xlsx",
                    data=to_excel_bytes(filtered, sheet_name="Data_PR"),
                    file_name=f"Data_PR_{selected_pic}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.info("Data tidak tersedia untuk fitur download PR per PIC.")

        # Download PR by status
        with st.container(border=True):
            st.subheader("📥 Download Data PR (Periode & Status)")

            if not df_pr_f.empty and "Status" in df_pr_f.columns:
                all_statuses = sorted([s for s in df_pr_f["Status"].dropna().astype(str).unique().tolist() if s.strip()])
                selected_statuses = st.multiselect(
                    "Pilih Status untuk di-download:",
                    all_statuses,
                    default=all_statuses,
                    key="pr_status_export"
                )

                df_download = df_pr_f[df_pr_f["Status"].isin(selected_statuses)].copy()

                if not df_download.empty:
                    st.download_button(
                        label=f"Download {len(df_download):,} Baris Data (Filtered).xlsx",
                        data=to_excel_bytes(df_download, sheet_name="Data_PR"),
                        file_name=f"Data_PR_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.caption(f"Menampilkan {len(df_download):,} baris data yang akan di-download.")
                else:
                    st.warning("Tidak ada data yang sesuai dengan filter yang dipilih.")
            else:
                st.info("Data PR tidak tersedia untuk export.")

        # DO Metrics
        with st.container(border=True):
            st.subheader("📊 Detail Outstanding DO")

            c1, c2 = st.columns(2)
            with c1:
                metric_card("DO Balance", f"Rp {total_do_unpr:,.0f}")
            with c2:
                metric_card("Rata-rata Nominal", f"Rp {avg_nominal_do:,.0f}")

            c1, c2, c3 = st.columns(3)
            with c1:
                metric_card("Total Dokumen DO", f"{total_do_count:,}")
            with c2:
                metric_card("Total Item DO", f"{total_do_rows:,}")
            with c3:
                metric_card("PIC Terbanyak", top_pic_do)

    # =====================================================
    # RIGHT - NPR & PUR
    # =====================================================
    with col_kanan:
        with st.container(border=True):
            st.subheader("📊 Detail Outstanding NPR & PUR")
            c1, c2 = st.columns(2)
            with c1:
                metric_card("NPR Balance (Item)", f"{total_npr_rows:,}")
            with c2:
                metric_card("PIC PUR Terbanyak", top_pic_pur)

            st.markdown(
                f"<div class='small-note'>Total dokumen NPR unik: {total_npr_count:,}</div>",
                unsafe_allow_html=True
            )

        pic_summary_pur = summarize_pic_status(df_pur_f, "PIC", "No. PUR")
        with st.container(border=True):
            st.subheader("👤 Analisis PIC PUR")
            render_pic_bar(
                summary_df=pic_summary_pur,
                x_col="PIC",
                y_col="Jumlah_Doc",
                color_col="Status" if "Status" in pic_summary_pur.columns else None,
                title="Jumlah PUR per PIC PUR"
            )

        with st.container(border=True):
            st.subheader("📥 Download Data PUR per PIC")

            if not df_pur_f.empty and "PIC" in df_pur_f.columns:
                options = sorted(df_pur_f["PIC"].fillna("Unassigned").astype(str).unique().tolist())
                selected_pic3 = st.selectbox("Pilih PIC PUR:", options, key="pur_pic_select")

                filtered3 = df_pur_f[df_pur_f["PIC"].fillna("Unassigned").astype(str) == selected_pic3].copy()
                st.download_button(
                    label=f"Download Data {selected_pic3}.xlsx",
                    data=to_excel_bytes(filtered3, sheet_name="Data_PUR"),
                    file_name=f"Data_PUR_{selected_pic3}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.info("Data PUR tidak tersedia untuk fitur download.")

    # ---------- FOOTER INFO ----------
    with st.expander("ℹ️ Informasi Teknis Dashboard"):
        selected_report_date = (
            selected_date_range[1]
            if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2
            else date.today()
        )

        st.markdown(
            f"""
- **Base URL:** `{BASE_URL}`
- **Timeout Request:** `{REQUEST_TIMEOUT}` detik
- **Tanggal report sampai:** `{selected_report_date}`
- **Mode filter tanggal:** kumulatif (semua data sampai tanggal akhir)
- **Cache API:** 600 detik
            """
        )


if __name__ == "__main__":
    main()
