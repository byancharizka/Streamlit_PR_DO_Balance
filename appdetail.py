import os
import logging
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import plotly.express as px
import pytz
import requests
import streamlit as st
import plotly.graph_objects as go

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

BASE_URL = {
    "outstanding": "https://eas.sibima.id/api/dashboard/",
    "eas": "https://eas.sibima.id/api/",
    "brp": "https://brp.sibima.id/api/"
}

API_TOKEN = os.getenv("SIBIMA_API_TOKEN", "44b71f38c25ddd02cd31b409f85e9f3aca4f337f02f2fa90237afc2a0736")

# Pastikan setiap URL diakhiri dengan "/"
for key in BASE_URL:
    if not BASE_URL[key].endswith("/"):
        BASE_URL[key] += "/"


# =========================================================
# 4) CSS CUSTOM
# =========================================================
st.markdown("""
<style>
/* ====== TITLE UTAMA ====== */
h1 {
    font-size: 1.5rem !important;   /* paling besar */
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
    font-size: 0.5rem !important;
}

/* ====== CUSTOM METRIC CARD ====== */
.metric-card {
    background-color: #f4f4f4;
    border: 1px solid #dcdcdc;
    border-radius: 12px;
    padding: 2px;
    box-shadow: 1px 2px 8px rgba(0,0,0,0.05);
    text-align: center;
    margin-top: 3px;
    margin-bottom: 7px;
    margin-left: 2.5px;
    font-size: 0.75rem;
}
            
.metric-card div {
    font-size: 0.67rem !important;
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
            

/* ====== FILTER INPUTS ====== */
div[data-testid="stDateInput"], 
div[data-testid="stTextInput"] {
    font-size: 0.7rem !important;   /* ukuran teks lebih kecil */
}

label, .stTextInput label, .stDateInput label {
    font-size: 0.7rem !important;   /* label input lebih kecil */
    color: #555 !important;
}

/* Kurangi tinggi box input agar lebih ramping */
input, textarea {
    font-size: 0.7rem !important;
    padding: 4px 6px !important;
}
            
@media (max-width: 768px) {
    h1 { font-size: 1.2rem !important; }
    h2, h3, h4 { font-size: 0.9rem !important; }
    .metric-card {
        font-size: 0.65rem !important;
        padding: 4px !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 0.7rem !important;
    }
    .block-container {
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }
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

def safe_sum(df: pd.DataFrame, col: str) -> float:
    if df.empty:
        return 0.0
    if col not in df.columns:
        # fallback ke kolom lain yang mirip
        for alt in ["Nominal", "discount", "price"]:
            if alt in df.columns:
                col = alt
                break
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())



def to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


# =========================================================
# 6) API FETCHING
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
@st.cache_data(ttl=600, show_spinner=False)
def get_api_data_old(endpoint: str, source: str = "outstanding", start_date=None, end_date=None):
    base_url = BASE_URL.get(source, BASE_URL["outstanding"])
    url = f"{base_url}{endpoint}"
    params = {"date_start": start_date, "date_end": end_date}

    try:
        logger.info("Fetching endpoint=%s from source=%s params=%s", endpoint, source, params)
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, dict):
            data_layer = payload.get("data", {})
            if isinstance(data_layer, dict):
                rows = data_layer.get("data", [])
                if isinstance(rows, list):
                    return pd.DataFrame(rows)
        return pd.DataFrame()

    except Exception as e:
        st.warning(f"Gagal mengambil data dari endpoint {endpoint} ({source}): {e}")
        return pd.DataFrame()
    
def get_api_data_new(endpoint: str, source: str = "eas", start_date=None, end_date=None):
    base_url = BASE_URL.get(source, BASE_URL["eas"])
    url = f"{base_url}{endpoint}"
    params = {
        "date_start": start_date,
        "date_end": end_date,
        "token": API_TOKEN,
        "page": 1
    }

    all_rows = []
    while True:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        rows = payload.get("data", [])
        if isinstance(rows, list):
            for row in rows:
                items = row.get("items", [])
                if items:
                    for item in items:
                        # aman: prefix item
                        flat = {**row, **{f"item_{k}": v for k, v in item.items()}}
                        all_rows.append(flat)
                else:
                    all_rows.append(row)

        meta = payload.get("meta", {})
        if meta.get("current_page", 1) >= meta.get("last_page", 1):
            break
        params["page"] += 1

    # ✅ konversi ke DataFrame dan pastikan kolom tanggal jadi datetime
    df = pd.DataFrame(all_rows)
    df = safe_to_datetime(df, "transaction_date")
    return df


def load_all_data() -> dict[str, pd.DataFrame]:
    endpoint_map = {
        "pr": ("pr-balance", {"Tgl. PR": "transaction_date"}),
        "po": ("po-balance", {"Tgl. PO": "transaction_date"}),
        "grn": ("grn-balance", {"Tgl. GRN": "transaction_date"}),
        "do": ("do-balance", {"Tgl. DO": "transaction_date"}),
        "npr": ("outstanding-npr", {"Tanggal": "transaction_date"}),
        "pur": ("outstanding-pur", {"Tanggal": "transaction_date"})
    }

    result = {}
    for key, (endpoint, rename_map) in endpoint_map.items():
        # 🔹 gunakan fungsi yang benar
        df = get_api_data_old(endpoint, source="outstanding")

        if not df.empty:
            df = df.rename(columns=rename_map)
        result[key] = df

    return result


def load_all_data_new(start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    endpoint_map_new = {
        "do": "delivery-orders",
        "pr": "purchase-requests",
        "po": "purchase-orders",
    }

    result_new = {}
    for key, endpoint in endpoint_map_new.items():
        df = get_api_data_new(endpoint, source="eas", start_date=start_date, end_date=end_date)
        result_new[key] = df
    return result_new



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

def apply_realization_filter(df: pd.DataFrame, start_date_val, end_date_val) -> pd.DataFrame:
    """
    Ambil data hanya dalam rentang tanggal tertentu (start_date sampai end_date).
    Contoh: 1 Mei 2026 s/d 31 Mei 2026.
    """
    if df.empty or "transaction_date" not in df.columns:
        return df.copy()

    working = df.copy()
    working = safe_to_datetime(working, "transaction_date")

    lower_limit = pd.to_datetime(start_date_val).replace(hour=0, minute=0, second=0)
    upper_limit = pd.to_datetime(end_date_val).replace(hour=23, minute=59, second=59)

    return working[
        working["transaction_date"].notna() &
        (working["transaction_date"] >= lower_limit) &
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

    # Filter nomor transaksi: mencari di semua kolom string
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

def summarize_status_do(df: pd.DataFrame, doc_col: str, nominal_col: str = "Nominal") -> pd.DataFrame:
    if df.empty or "Status DO" not in df.columns:
        return pd.DataFrame(columns=["Status", "Total_Doc", "Total_Amount"])  # ubah ke Status

    working_do = df.copy()
    working_do = ensure_columns(working_do, [doc_col, nominal_col, "Status DO"])
    working_do = safe_to_numeric(working_do, [nominal_col])

    summary_do = (
        working_do.groupby("Status DO", dropna=False)
        .agg(
            Total_Doc=(doc_col, "nunique"),
            Total_Amount=(nominal_col, "sum")
        )
        .reset_index()
        .rename(columns={"Status DO": "Status"})  # tambahkan ini
    )
    return summary_do


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

def summarize_pic_status_do(df: pd.DataFrame, pic_col: str, doc_col: str) -> pd.DataFrame:
    if df.empty or pic_col not in df.columns or "Status DO" not in df.columns or doc_col not in df.columns:
        return pd.DataFrame(columns=[pic_col, "Status DO", "Jumlah_Doc"])

    working_do = assign_unassigned(df, pic_col)

    summary_do = (
        working_do.groupby([pic_col, "Status DO"], dropna=False)
        .agg(Jumlah_Doc=(doc_col, "nunique"))
        .reset_index()
        .sort_values(by="Jumlah_Doc", ascending=False)
    )
    return summary_do
# =========================================================
# 8) CHART HELPERS
# =========================================================
STATUS_COLORS = {
    "Complete": "#00CC96",
    "In Progress": "#F2C94C",
    "Approved": "#F2994A",
    "Need Approve": "#EB5757",
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


def render_pic_bar(summary_df: pd.DataFrame, x_col: str, y_col: str, color_col: str | None):
    if summary_df.empty:
        st.info("Data PIC tidak tersedia.")
        return

    # Hitung total transaksi per PIC
    summary_df["Total_Doc"] = summary_df.groupby(x_col)[y_col].transform("sum")

    kwargs = {
        "data_frame": summary_df,
        "x": x_col,
        "y": y_col,
    }

    if color_col and color_col in summary_df.columns:
        kwargs["color"] = color_col
        kwargs["color_discrete_map"] = STATUS_COLORS

    fig = px.bar(**kwargs)

    # 🔹 Label per status (segmen warna) → di dalam bar
    fig.update_traces(
        texttemplate="%{y}",          # angka per status
        textposition="inside",
        textfont=dict(size=10, color="white")
    )

    # 🔹 Tambahkan angka total per PIC → di atas bar
    totals = summary_df.groupby(x_col)[y_col].sum().reset_index()
    for _, row in totals.iterrows():
        fig.add_annotation(
            x=row[x_col],             # posisi di sumbu X (PIC)
            y=row[y_col],             # tinggi bar total
            text=f"{row[y_col]}",     # angka total
            showarrow=False,
            font=dict(size=12, color="black"),
            yshift=10                 # geser sedikit ke atas
        )

    fig.update_layout(
        uniformtext_mode="hide",
        uniformtext_minsize=8,
    )

    st.plotly_chart(fig, use_container_width=True)


def render_pic_heatmap(df: pd.DataFrame, pic_col: str, date_col: str, doc_col: str, title: str):
    if df.empty or pic_col not in df.columns or date_col not in df.columns or doc_col not in df.columns:
        st.info("Data tidak tersedia untuk heatmap aktivitas PIC.")
        return

    working = df.copy()
    working[date_col] = pd.to_datetime(working[date_col], errors="coerce")
    working[pic_col] = working[pic_col].fillna("Unassigned")

    bulan_map = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    working["Bulan"] = working[date_col].dt.month.map(bulan_map)
    bulan_order = list(bulan_map.values())
    working["Bulan"] = pd.Categorical(working["Bulan"], categories=bulan_order, ordered=True)

    # gunakan doc_col dinamis
    working[doc_col] = working[doc_col].astype(str).str.strip().str.upper()
    summary = (
        working.groupby([pic_col, "Bulan"])[doc_col]
        .nunique()
        .reset_index(name="Jumlah Transaksi")
        .sort_values("Bulan")
    )

    fig = px.density_heatmap(summary, x="Bulan", y=pic_col, z="Jumlah Transaksi",
                             color_continuous_scale=["#138207","#F2994A","#A80B0B"], text_auto=True)
    
    # tambahkan pengaturan layout di sini
    fig.update_layout(
        coloraxis_showscale=False,   # 🔹 sembunyikan color bar
        coloraxis_colorbar=dict(title=None),  # 🔹 hilangkan teks "sum of Jumlah Transaksi"
        xaxis_title="Bulan",
        yaxis_title="PIC Procurement",
        margin=dict(l=100, r=40, t=60, b=120),
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


def calculate_aging(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df.copy()
    working = df.copy()
    working = safe_to_datetime(working, date_col)
    today = pd.to_datetime(TODAY)
    working["Aging"] = (today - working[date_col]).dt.days
    return working

def categorize_aging(df: pd.DataFrame) -> pd.DataFrame:
    bins = [0, 30, 60, 90, float("inf")]
    labels = ["0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
    df["Aging Category"] = pd.cut(df["Aging"], bins=bins, labels=labels, right=True)
    return df


def render_aging_bar(df: pd.DataFrame, doc_col: str):
    if df.empty or "Aging Category" not in df.columns:
        st.info("Data aging tidak tersedia.")
        return

    summary = (
        df.groupby("Aging Category")[doc_col]
        .nunique()
        .reset_index(name="Jumlah Transaksi")
    )

    fig = px.bar(
    summary,
    x="Aging Category",
    y="Jumlah Transaksi",
    color="Aging Category",
    color_discrete_map={
        "0-30 hari": "#2F80ED",   # biru tua
        "31-60 hari": "#7ABBEE",  # biru muda
        "61-90 hari": "#FCA27F",     # oranye
        ">90 hari": "#EB5757"  # merah
    },
    text="Jumlah Transaksi"
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

def summarize_pic_aging(df: pd.DataFrame, pic_col: str, doc_col: str) -> pd.DataFrame:
    if df.empty or pic_col not in df.columns or "Aging" not in df.columns or "Status" not in df.columns:
        return pd.DataFrame(columns=[pic_col, "Avg_Aging", "Total_Doc", "Outstanding_Doc", "Completed_Doc", "Over90Pct"])

    working = assign_unassigned(df, pic_col)

    summary = (
        working.groupby(pic_col).agg(
            Avg_Aging=("Aging", "mean"),
            Total_Doc=(doc_col, "nunique"),
            Outstanding_Doc=(doc_col, lambda x: (working.loc[x.index, "Status"] != "Complete").sum()),
            Completed_Doc=(doc_col, lambda x: (working.loc[x.index, "Status"] == "Complete").sum()),
            Over90Pct=("Aging", lambda x: (x > 90).sum() / len(x) * 100 if len(x) > 0 else 0)
        )
        .reset_index()
    )
    return summary

def render_pic_aging_bar(summary_df: pd.DataFrame):
    if summary_df.empty:
        st.info("Data aging per PIC tidak tersedia.")
        return
    color_continuous_scale=[
    (0.0, "#56CCF2"),   # hijau muda untuk aging rendah
    (0.5, "#F2994A"),   # kuning untuk sedang
    (1.0, "#EB5757")    # merah untuk aging tinggi
    ]

    fig = px.bar(
        summary_df,
        x="PIC Procurement",
        y="Avg_Aging",
        text="Avg_Aging",
        color="Avg_Aging",
        color_continuous_scale=[
            (0.0, "#56CCF2"),
            (0.5, "#F2C94C"),
            (1.0, "#EB5757")
    ],
    )
    # Tambahkan pengaturan ukuran teks
    fig.update_traces(
    texttemplate="%{text:.1f} hari",
    textposition="outside",
    textfont=dict(
        size=20,          # ubah sesuai kebutuhan (misalnya 18 atau 20)
        color="black",    # warna teks agar kontras
        family="Arial"    # jenis font agar lebih jelas
        )
    )

    fig.update_layout(
    coloraxis_showscale=False  # sembunyikan color scale di sisi kanan
    )


    st.plotly_chart(fig, use_container_width=True)

def render_sla_gauge(df: pd.DataFrame, threshold: int = 30, title: str = "SLA Compliance"):
    if df.empty or "Aging" not in df.columns:
        st.info("Data aging tidak tersedia untuk SLA.")
        return

    sla_compliance = (df["Aging"] <= threshold).mean() * 100

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=sla_compliance,
        number={'suffix': '%', 'font': {'size': 48, 'color': '#555'}},  # 🔹 tambahkan ini
        title={'text': f"{title} (≤{threshold} hari)"},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': "green"},
            'steps': [
                {'range': [0, 50], 'color': "red"},
                {'range': [50, 80], 'color': "yellow"},
                {'range': [80, 100], 'color': "green"}
            ]
        }
    ))
    st.plotly_chart(fig, use_container_width=True)


def summarize_pic_sla(df: pd.DataFrame, pic_col: str, doc_col: str, threshold: int = 30) -> pd.DataFrame:
    if df.empty or pic_col not in df.columns or "Aging" not in df.columns:
        return pd.DataFrame(columns=[pic_col, "Total_Doc", "SLA_Compliance"])

    working = assign_unassigned(df, pic_col)

    summary = (
        working.groupby(pic_col).agg(
            Total_Doc=(doc_col, "nunique"),
            SLA_Compliance=(doc_col, lambda x: (working.loc[x.index, "Aging"] <= threshold).sum() / len(x) * 100 if len(x) > 0 else 0)
        )
        .reset_index()
    )
    return summary

def render_pic_sla_bar(summary_df: pd.DataFrame):
    if summary_df.empty:
        st.info("Data SLA per PIC tidak tersedia.")
        return

    fig = px.bar(
        summary_df,
        x="PIC Procurement",
        y="SLA_Compliance",
        text="SLA_Compliance",
        color="SLA_Compliance",
        color_continuous_scale=["#EB5757", "#F2C94C", "#6FCF97"],  # merah → kuning → hijau
    )
    fig.update_traces(
        texttemplate="%{text:.1f}%",
        textposition="outside",
        textfont=dict(size=14, color="black")
    )
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

# =========================================================
# 9) MAIN APP
# =========================================================

data_new = load_all_data_new(start_date=DEFAULT_START_DATE, end_date=date.today())


data_old = load_all_data()
#data_new = load_all_data_new()

def main():
    st.title("SIBIMA Performance Dashboard")

    # ---------- LOAD DATA ----------
    with st.spinner("Mengambil data dashboard..."):
        data = load_all_data()

    df_pr = data_old["pr"]
    df_po = data_old["po"]
    df_grn = data_old["grn"]
    df_do = data_old["do"]
    df_npr = data_old["npr"]
    df_pur = data_old["pur"]
    df_pr_final = data_new["pr"]
    df_do_final = data_new["do"]

    # Pastikan kolom PIC dan Status sesuai
    df_pr_final = df_pr_final.rename(columns={
    "item_pic_procurement_name": "PIC Procurement",
    "status_description": "Status"
    })

    # ---------- TOP FILTERS ----------
    col_head1, col_head2, col_head3, col_head4, col_head5 = st.columns([1, 1, 1, 1, 1])

    with col_head1:
        selected_date_range = st.date_input(
            "Select Date Range 📅",
            value=(DEFAULT_START_DATE, date.today()),
            max_value=date.today()
        )

    with col_head2:
        selected_doc_type = st.selectbox(
            "Pilih Jenis Dokumen 📑",
            ["PR", "PO", "GRN", "DO", "NPR", "PUR"]
    )

    with col_head3:
        search_number = st.text_input(
            "Cari Nomor Transaksi 🔍",
            placeholder="No. PR / No. DO / No. NPR / No. PUR"
        )

    with col_head4:
        search_status = st.text_input(
            "Cari Status 🔍",
            placeholder="Complete / In Progress / Approved / Need Approve"
        )

    with col_head5:
        search_pic = st.text_input(
            "Cari PIC 🔍",
            placeholder="PIC Procurement / PIC Purchasing / PIC PUR"
        )

    # ---------- DEFAULT SAFE COPY ----------
    df_pr_f = df_pr.copy()
    df_po_f = df_po.copy()
    df_grn_f = df_grn.copy()
    df_do_f = df_do.copy()
    df_npr_f = df_npr.copy()
    df_pur_f = df_pur.copy()
    df_pr_final_f = df_pr_final.copy()
    df_do_final_f = df_do_final.copy()

    # ---------- DATE FILTER ----------
    if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2:
        report_start_date, report_end_date = selected_date_range
        df_pr_f = apply_cumulative_filter(df_pr_f, report_end_date)
        df_po_f = apply_cumulative_filter(df_po_f, report_end_date)
        df_grn_f = apply_cumulative_filter(df_grn_f, report_end_date)
        df_do_f = apply_cumulative_filter(df_do_f, report_end_date)
        df_npr_f = apply_cumulative_filter(df_npr_f, report_end_date)
        df_pur_f = apply_cumulative_filter(df_pur_f, report_end_date)
        #df_pr_final_f = apply_cumulative_filter(df_pr_final_f, report_end_date)

            # 🔹 Dataset baru (PR Final) pakai realisasi
        df_pr_final_f_real = apply_realization_filter(df_pr_final_f, report_start_date, report_end_date)
        df_do_final_f_real = apply_realization_filter(df_do_final_f, report_start_date, report_end_date)


    # ---------- SEARCH FILTER ----------
    df_pr_f = apply_search_filter(df_pr_f, search_number, search_status, search_pic)
    df_po_f = apply_search_filter(df_po_f, search_number, search_status, search_pic)
    df_grn_f = apply_search_filter(df_grn_f, search_number, search_status, search_pic)
    df_do_f = apply_search_filter(df_do_f, search_number, search_status, search_pic)
    df_npr_f = apply_search_filter(df_npr_f, search_number, search_status, search_pic)
    df_pur_f = apply_search_filter(df_pur_f, search_number, search_status, search_pic)

    # ---------- ENSURE IMPORTANT COLUMNS ----------
    df_pr_f = ensure_columns(df_pr_f, ["Nominal", "No. PR", "Status", "PIC Procurement"])
    df_po_f = ensure_columns(df_po_f, ["Nominal"])
    df_grn_f = ensure_columns(df_grn_f, ["Nominal"])
    df_do_f = ensure_columns(df_do_f, ["Nominal", "No. DO", "PIC Purchasing"])
    df_npr_f = ensure_columns(df_npr_f, ["No. Transaksi"])
    df_pur_f = ensure_columns(df_pur_f, ["No. PUR", "PIC", "Status"])
    df_pr_final_f_real = ensure_columns(df_pr_final_f_real, ["PIC Procurement", "transaction_number","Status", "price", "quantity", "discount", "transaction_total", "tax1_percentage", "tax2_percentage"])
    df_do_final_f_real = ensure_columns(df_do_final_f_real, ["transaction_number", "price", "quantity", "discount", "transaction_total", "tax1_value", "tax2_value"])

    df_pr_f = safe_to_numeric(df_pr_f, ["Nominal"])
    df_po_f = safe_to_numeric(df_po_f, ["Nominal"])
    df_grn_f = safe_to_numeric(df_grn_f, ["Nominal"])
    df_do_f = safe_to_numeric(df_do_f, ["Nominal"])
    #df_pr_final_f_real = safe_to_numeric(df_pr_final_f_real, ["price", "discount", "quantity", "tax1_percentage", "tax2_percentage"])
    df_pr_final_f_real= safe_to_numeric(df_pr_final_f_real, ["item_price", "item_discount", "item_quantity", "item_tax1_percentage", "item_tax2_percentage"])
    df_do_final_f_real= safe_to_numeric(df_do_final_f_real, ["item_price", "item_discount", "item_quantity", "item_tax1_value", "item_tax2_value"])
    
    # ---------- METRICS ----------
    total_pr_unpr = safe_sum(df_pr_f, "Nominal")
    total_po_unpr = safe_sum(df_po_f, "Nominal")
    total_grn_unpr = safe_sum(df_grn_f, "Nominal")
    total_do_unpr = safe_sum(df_do_f, "Nominal")
    #total_pr = safe_sum(df_pr_final_f_real, "transaction_total")

    df_pr_final_f_real = normalize_text_columns(df_pr_final_f_real, ["item_PIC_Procurement"])


    df_pr_final_f_real["disc_per_unit"] = df_pr_final_f_real["item_price"] * (df_pr_final_f_real["item_discount"] / 100)
    df_pr_final_f_real["tax_unit"] = (df_pr_final_f_real["item_price"] - df_pr_final_f_real["disc_per_unit"]) * (df_pr_final_f_real["item_tax1_percentage"] / 100)
    df_pr_final_f_real["net_price_unit"] = df_pr_final_f_real["item_price"] - df_pr_final_f_real["disc_per_unit"] + df_pr_final_f_real["tax_unit"]
    df_pr_final_f_real["total_pr_row"] = df_pr_final_f_real["item_quantity"] * df_pr_final_f_real["net_price_unit"]
    total_pr = df_pr_final_f_real["total_pr_row"].sum()

    df_do_final_f_real["disc_per_unit"] = df_do_final_f_real["item_price"] * (df_do_final_f_real["item_discount"] / 100)
    #df_do_final_f_real["tax_unit"] = (df_do_final_f_real["item_price"] - df_do_final_f_real["disc_per_unit"]) * (df_do_final_f_real["item_tax1_percentage"] / 100)
    df_do_final_f_real["tax_unit"] = df_do_final_f_real["item_tax1_value"] + df_do_final_f_real["item_tax1_value"]
    #df_do_final_f_real["net_price_unit"] = df_do_final_f_real["item_price"] - df_do_final_f_real["disc_per_unit"] + df_do_final_f_real["tax_unit"]
    df_do_final_f_real["net_price_unit"] = df_do_final_f_real["item_price"] - df_do_final_f_real["disc_per_unit"]
    df_do_final_f_real["total_do_row"] = df_do_final_f_real["item_quantity"] * df_do_final_f_real["net_price_unit"]
    total_do = df_do_final_f_real["total_do_row"].sum()

    total_pr_count = safe_unique_count(df_pr_final_f_real, "transaction_number")
    total_pr_balance_count = safe_unique_count(df_pr_f, "No. PR")
    total_pr_rows = len(df_pr_final_f_real)
    total_pr_balance_rows = len(df_pr_f)
    total_do_count = safe_unique_count(df_do_final_f_real, "transaction_number")
    total_do_balance_count = safe_unique_count(df_do_f, "No. DO")
    total_do_rows = len(df_do_f)
    total_do_balance_rows = len(df_do_f)
    total_npr_count = safe_unique_count(df_npr_f, "No. Transaksi")
    total_npr_rows = len(df_npr_f)

    avg_nominal_do = safe_mean(df_do_f, "Nominal")

    top_pic_pr = get_top_pic(df_pr_f, "PIC Procurement", "No. PR")
    top_pic_do = get_top_pic(df_do_f, "PIC Purchasing", "No. DO")
    top_pic_pur = get_top_pic(df_pur_f, "PIC", "No. PUR")

    # ---------- LAYOUT ----------
    col_kiri, col_tengah, col_kanan = st.columns([1, 1, 1], gap="small")

    # =====================================================
    # LEFT - PR
    # =====================================================
    if selected_doc_type == "PR":
        with col_kiri:
            with st.container(border=True):
                st.subheader("📊 Detail PR")

                c1, c2 = st.columns(2)
                with c1:
                    metric_card("Total PR", f"Rp {total_pr:,.0f}")
                with c2:
                    metric_card("PR Balance", f"Rp {total_pr_unpr:,.0f}")

                c1, c2 = st.columns(2)
                with c1:
                    metric_card("Total Transaksi PR", f"{total_pr_count:,}")
                with c2:
                    metric_card("Total Transaksi PR Balance", f"{total_pr_balance_count:,}")


                #st.write("Kolom:", df_pr_final_f.columns)
                #st.write("Contoh tanggal:", df_pr_final_f["transaction_date"].head())
                #st.write(df_pr_final_f[["item_price", "item_discount", "item_quantity"]].head())

                c1, c2, c3 = st.columns(3)
                with c1:
                    metric_card("Total Item PR", f"{total_pr_rows:,}")
                with c2:
                    metric_card("Total Item PR Balance", total_pr_balance_rows)
                with c3:
                    metric_card("PIC Terbanyak", top_pic_pr)


                pr_summary = summarize_status(df_pr_f, doc_col="No. PR", nominal_col="Nominal")

                with st.container(border=True):
                    st.subheader("🍩 Proporsi Nominal PR Balance per Status")
                    render_status_pie(pr_summary, "Persentase Distribusi Nominal PR Balance")

            pic_summary_pr = summarize_pic_status(df_pr_f, "PIC Procurement", "No. PR")
            with st.container(border=True):
                st.subheader("👤 Analisis Transaksi PR Balance per PIC Procurement & per Status")
                render_pic_bar(
                    summary_df=pic_summary_pr,
                    x_col="PIC Procurement",
                    y_col="Jumlah_Doc",
                    color_col="Status",
                )

            with st.container(border=True):
                st.subheader("🔥 Heatmap PR Balance - Aktivitas PIC Procurement")
                render_pic_heatmap(df_pr_f, "PIC Procurement", "transaction_date", "No. PR", "Heatmap Aktivitas PIC Procurement per Bulan")





    # =====================================================
    # MID
    # =====================================================
        with col_tengah:

            df_pr_f_aging = calculate_aging(df_pr_f, "transaction_date")
            df_pr_f_aging = categorize_aging(df_pr_f_aging)
            # Hitung aging
            df_pr_final_f_real_aging = calculate_aging(df_pr_final_f_real, "transaction_date")
            df_pr_final_f_real_aging = categorize_aging(df_pr_final_f_real_aging)

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging PR")
                render_aging_bar(df_pr_final_f_real_aging, "transaction_number")

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging PR Balance")
                render_aging_bar(df_pr_f_aging, "No. PR")



                pic_aging_summary = summarize_pic_aging(df_pr_f_aging, "PIC Procurement", "No. PR")
                pic_aging_summary_final = summarize_pic_aging(df_pr_final_f_real_aging, "PIC Procurement", "transaction_number")

            with st.container(border=True):
                st.subheader("👥 Analisis PR Balance - Kinerja PIC Procurement")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary)

            with st.container(border=True):
                st.subheader("👥 Analisis PR - Kinerja PIC Procurement")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary_final)


    # =====================================================
    # RIGHT
    # =====================================================
    with col_kanan:
            with st.container(border=True):
                st.subheader("📏 SLA Compliance PR")
                render_sla_gauge(df_pr_final_f_real_aging, threshold=30, title="SLA Compliance PR")

            with st.container(border=True):
                st.subheader("📏 SLA Compliance PR Balance")
                render_sla_gauge(df_pr_f_aging, threshold=30, title="SLA Compliance PR Balance")

            pic_sla_summary = summarize_pic_sla(df_pr_final_f_real_aging, "PIC Procurement", "transaction_number", threshold=30)

            with st.container(border=True):
                st.subheader("📏 SLA Compliance per PIC Procurement")
                #st.dataframe(pic_sla_summary, use_container_width=True, hide_index=True)
                render_pic_sla_bar(pic_sla_summary)


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
    
    # ---------- DO ----------
    if selected_doc_type == "DO":
        with col_kiri:
            with st.container(border=True):
                st.subheader("📊 Detail DO")

                c1, c2 = st.columns(2)
                with c1:
                    metric_card("Total DO", f"Rp {total_do:,.0f}")
                with c2:
                    metric_card("DO Balance", f"Rp {total_do_unpr:,.0f}")

                c1, c2 = st.columns(2)
                with c1:
                    metric_card("Total Transaksi DO", f"{total_do_count:,}")
                with c2:
                    metric_card("Total Transaksi DO Balance", f"{total_do_balance_count:,}")


                #st.write("Kolom:", df_pr_final_f.columns)
                #st.write("Contoh tanggal:", df_pr_final_f["transaction_date"].head())
                #st.write(df_pr_final_f[["item_price", "item_discount", "item_quantity"]].head())

                c1, c2, c3 = st.columns(3)
                with c1:
                    metric_card("Total Item DO", f"{total_do_rows:,}")
                with c2:
                    metric_card("Total Item DO Balance", total_do_balance_rows)
                with c3:
                    metric_card("PIC Terbanyak", top_pic_do)

                do_summary = summarize_status(df_do_f, doc_col="No. DO", nominal_col="Nominal")

                with st.container(border=True):
                    st.subheader("🍩 Proporsi Nominal DO Balance per Status")
                    render_status_pie(do_summary, "Persentase Distribusi Nominal DO Balance")

            pic_summary_do = summarize_pic_status(df_do_f, "PIC Procurement", "No. DO")
            with st.container(border=True):
                st.subheader("👤 Analisis Transaksi DO Balance per PIC Procurement & per Status")
                render_pic_bar(
                    summary_df=pic_summary_do,
                    x_col="PIC Procurement",
                    y_col="Jumlah_Doc",
                    color_col="Status DO",
                )

            with st.container(border=True):
                st.subheader("🔥 Heatmap DO Balance - Aktivitas PIC Procurement")
                render_pic_heatmap(df_do_f, "PIC Procurement", "transaction_date", "No. DO", "Heatmap Aktivitas PIC Procurement per Bulan")

            df_pr_f_aging = calculate_aging(df_pr_f, "transaction_date")
            df_pr_f_aging = categorize_aging(df_pr_f_aging)
            #df_pr_final_f_real_aging = calculate_aging(df_pr_final_f_real, "transaction_date")
            #df_pr_final_f_real_aging = categorize_aging(df_pr_final_f_real_aging)
            # Hitung aging untuk PR
            df_pr_final_f_real_aging = calculate_aging(df_pr_final_f_real, "transaction_date")
            df_pr_final_f_real_aging = categorize_aging(df_pr_final_f_real_aging)

    
    
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
