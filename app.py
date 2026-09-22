import logging
import re
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import streamlit as st
from sqlalchemy import URL, create_engine, text
from sshtunnel import SSHTunnelForwarder

# =========================================================
# 1) PAGE CONFIG - WAJIB PALING ATAS
# =========================================================
st.set_page_config(
    layout="wide",
    page_title="SIBIMA Performance Dashboard - PROCUREMENT",
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
# 3) APP CONFIG + DATABASE CONNECTION
# =========================================================
TIMEZONE = pytz.timezone("Asia/Jakarta")
today = date.today()

DEFAULT_START_DATE = date(today.year, today.month, 1)
DEFAULT_END_DATE = today
DB_CACHE_TTL = 600

# Cache pembacaan database.
# Tidak ada HTTP/API call pada versi ini.


@st.cache_resource
def get_erp_database_connection():
    """
    Koneksi ERP PostgreSQL melalui SSH tunnel.

    Secrets yang dipakai sama dengan script database SIBIMA sebelumnya:

    [ssh]
    host = "..."
    port = 22
    username = "..."
    password = "..."

    [postgres]
    host = "..."
    port = 5432
    username = "..."
    password = "..."
    database = "..."
    """
    ssh = st.secrets["ssh"]
    postgres = st.secrets["postgres"]

    tunnel_kwargs = {
        "ssh_username": ssh["username"],
        "remote_bind_address": (
            postgres["host"],
            int(postgres.get("port", 5432)),
        ),
        "local_bind_address": ("127.0.0.1", 0),
    }
    if ssh.get("password"):
        tunnel_kwargs["ssh_password"] = ssh["password"]
    if ssh.get("private_key"):
        tunnel_kwargs["ssh_pkey"] = ssh["private_key"]

    tunnel = SSHTunnelForwarder(
        (ssh["host"], int(ssh.get("port", 22))),
        **tunnel_kwargs,
    )
    tunnel.start()

    database_url = URL.create(
        drivername="postgresql+psycopg",
        username=postgres["username"],
        password=postgres["password"],
        host="127.0.0.1",
        port=tunnel.local_bind_port,
        database=postgres["database"],
    )

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": 15,
            "application_name": "sibima_procurement_dashboard_database",
        },
    )
    return engine, tunnel


# Mapping tabel ERP. Nama tabel dan relasi mengikuti mapping DB yang sudah
# tervalidasi pada script Weekly Monitoring.
DB_STAGES = {
    "so": {
        "header_table": "public.x4_sales_order",
        "detail_table": "public.x4_sales_order_detail",
        "header_date": "date",
        "header_fk_candidates": ["sales_order_id", "so_id"],
        "detail_id_candidates": ["id", "sales_order_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "item_name_candidates": ["item_name", "product_name", "name"],
    },
    "pr": {
        "header_table": "public.x4_purchase_requests",
        "detail_table": "public.x4_purchase_request_details",
        "header_date": "date",
        "header_fk_candidates": ["purchase_request_id", "pr_id"],
        "detail_id_candidates": ["id", "pr_detail_id", "purchase_request_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "ref_so_candidates": ["so_detail_id", "sales_order_detail_id"],
    },
    "po": {
        "header_table": "public.x4_purchase_orders",
        "detail_table": "public.x4_purchase_order_details",
        "header_date": "date",
        "header_fk_candidates": ["purchase_order_id", "po_id"],
        "detail_id_candidates": ["id", "po_detail_id", "purchase_order_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "ref_pr_candidates": ["pr_detail_id", "purchase_request_detail_id"],
    },
    "grn": {
        "header_table": "public.x4_goods_receipt_note",
        "detail_table": "public.x4_goods_receipt_note_detail",
        "header_date": "transaction_date",
        "header_fk_candidates": ["goods_receipt_note_id", "grn_id", "goods_receipt_id"],
        "detail_id_candidates": ["id", "grn_detail_id", "goods_receipt_note_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "ref_po_candidates": ["purchase_order_detail_id", "po_detail_id"],
    },
    "do": {
        "header_table": "public.x4_delivery_orders",
        "detail_table": "public.x4_delivery_order_details",
        "header_date": "transaction_date",
        "header_fk_candidates": ["delivery_order_id", "do_id"],
        "detail_id_candidates": ["id", "do_detail_id", "delivery_order_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "ref_so_candidates": ["so_detail_id", "sales_order_detail_id"],
        # Tetap dibaca bila kolom memang tersedia, tetapi main dashboard juga
        # mempunyai jalur SO -> DO direct sebagai fallback.
        "ref_grn_candidates": [
            "grn_detail_id",
            "goods_receipt_note_detail_id",
            "goods_receipt_detail_id",
            "receipt_detail_id",
        ],
    },
    "si": {
        "header_table": "public.x4_sales_invoices",
        "detail_table": "public.x4_sales_invoice_details",
        "header_date": "transaction_date",
        "header_fk_candidates": ["sales_invoice_id", "invoice_id", "si_id"],
        "detail_id_candidates": ["id", "si_detail_id", "sales_invoice_detail_id"],
        "product_candidates": ["item_id", "product_id", "product_detail_id"],
        "ref_do_candidates": ["do_detail_id", "delivery_order_detail_id"],
        "item_name_candidates": ["item_name", "product_name", "name"],
    },
}

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
# 6) DATABASE READING - API-FREE
# =========================================================
def _split_table_name(full_name: str) -> tuple[str, str]:
    if "." in full_name:
        schema, table = full_name.split(".", 1)
    else:
        schema, table = "public", full_name
    return schema, table


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


@st.cache_data(ttl=DB_CACHE_TTL, show_spinner=False)
def get_table_columns(full_name: str) -> list[str]:
    """Ambil nama kolom aktual dari PostgreSQL agar reader toleran terhadap variasi schema."""
    engine, _ = get_erp_database_connection()
    schema, table = _split_table_name(full_name)
    q = text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :schema
          AND table_name = :table
        ORDER BY ordinal_position
    """)
    with engine.connect() as conn:
        rows = conn.execute(q, {"schema": schema, "table": table}).scalars().all()
    return [str(v) for v in rows]


def _first_existing(columns: list[str], candidates: list[str]) -> str | None:
    lookup = {str(c).lower(): c for c in columns}
    for candidate in candidates:
        if str(candidate).lower() in lookup:
            return lookup[str(candidate).lower()]
    return None


def _select_expr(alias: str, column: str | None, output_alias: str, sql_type: str | None = None) -> str:
    """Bangun SELECT expression; bila source tidak ada tetap hasilkan kolom NULL."""
    out = _quote_ident(output_alias)
    if column:
        return f'{alias}.{_quote_ident(column)} AS {out}'
    if sql_type:
        return f'NULL::{sql_type} AS {out}'
    return f'NULL AS {out}'


def _coalesce_text_expr(
    first_alias: str, first_column: str | None,
    second_alias: str, second_column: str | None,
    output_alias: str,
) -> str:
    """COALESCE dua source text tanpa memaksa salah satu harus ada."""
    out = _quote_ident(output_alias)
    parts = []
    if first_column:
        parts.append(f'{first_alias}.{_quote_ident(first_column)}::text')
    if second_column:
        parts.append(f'{second_alias}.{_quote_ident(second_column)}::text')
    if not parts:
        return f'NULL::text AS {out}'
    if len(parts) == 1:
        return f'{parts[0]} AS {out}'
    return f'COALESCE({", ".join(parts)}) AS {out}'


@st.cache_data(ttl=DB_CACHE_TTL, show_spinner=False)
def read_stage_from_database(stage: str, start_date=None, end_date=None) -> pd.DataFrame:
    """
    Membaca header + detail langsung dari PostgreSQL dan mengeluarkan bentuk
    DataFrame yang kompatibel dengan hasil API baru lama.

    Penting:
    - item_id            = primary key detail (compat API)
    - item_product_id    = product/item identity dari detail.item_id/product_id
    - transaction_number = nomor dokumen header
    - transaction_date   = tanggal dokumen header
    """
    if stage not in DB_STAGES:
        raise KeyError(f"Stage database tidak dikenal: {stage}")

    cfg = DB_STAGES[stage]
    header_table = cfg["header_table"]
    detail_table = cfg["detail_table"]

    header_cols = get_table_columns(header_table)
    detail_cols = get_table_columns(detail_table)
    if not header_cols:
        raise RuntimeError(f"Tabel header tidak ditemukan / tidak terbaca: {header_table}")
    if not detail_cols:
        raise RuntimeError(f"Tabel detail tidak ditemukan / tidak terbaca: {detail_table}")

    header_id = _first_existing(header_cols, ["id"] + cfg.get("header_id_candidates", []))
    detail_header_fk = _first_existing(detail_cols, cfg["header_fk_candidates"])
    detail_id = _first_existing(detail_cols, cfg["detail_id_candidates"])
    product_id = _first_existing(detail_cols, cfg["product_candidates"])

    if not header_id or not detail_header_fk or not detail_id:
        raise RuntimeError(
            f"Mapping key {stage.upper()} belum lengkap. "
            f"header_id={header_id}, detail_header_fk={detail_header_fk}, detail_id={detail_id}"
        )

    tx_number = _first_existing(
        header_cols,
        ["transaction_number", "number", "document_number"],
    )
    header_date = _first_existing(header_cols, [cfg["header_date"], "transaction_date", "date"])
    status = _first_existing(
        header_cols,
        ["status_description", "status", "realization_status", "item_status_description"],
    )
    sales_pic = _first_existing(
        header_cols,
        ["pic_sales_name", "sales_pic_name", "sales_name", "sales_person_name", "sales_person", "pic_sales"],
    )
    customer = _first_existing(
        header_cols,
        ["customer_name", "customer", "client_name", "partner_name", "customer_id"],
    )
    vendor = _first_existing(
        header_cols,
        ["vendor_name", "supplier_name", "supplier", "vendor", "vendor_id", "supplier_id"],
    )
    header_pic_procurement = _first_existing(
        header_cols,
        ["item_pic_procurement_name", "pic_procurement_name", "pic_procurement_id", "pic_name"],
    )
    header_total = _first_existing(
        header_cols,
        ["transaction_total", "grand_total", "total_amount", "net_total", "total"],
    )

    date_approved = _first_existing(header_cols, ["date_approved", "approved_date", "approved_at"])
    date_inprogress = _first_existing(header_cols, ["date_inprogress", "inprogress_date", "in_progress_date", "in_progress_at"])
    date_complete = _first_existing(header_cols, ["date_complete", "complete_date", "completed_date", "completed_at"])

    item_name = _first_existing(detail_cols, cfg.get("item_name_candidates", ["item_name", "product_name", "name"]))
    item_price = _first_existing(detail_cols, ["price", "unit_price", "selling_price", "sales_price", "unit_value", "rate"])
    item_discount = _first_existing(detail_cols, ["discount", "discount_percentage", "discount_percent", "disc"])
    item_quantity = _first_existing(detail_cols, ["quantity", "item_quantity", "qty", "ordered_quantity", "invoice_quantity"])
    item_tax1_pct = _first_existing(detail_cols, ["tax1_percentage", "tax_1_percentage", "tax_percentage", "vat_percentage"])
    item_tax2_pct = _first_existing(detail_cols, ["tax2_percentage", "tax_2_percentage"])
    item_subtotal = _first_existing(
        detail_cols,
        ["sub_total", "subtotal", "line_total", "item_total", "net_amount", "total_amount", "amount", "total_value"],
    )
    detail_pic_procurement = _first_existing(
        detail_cols,
        ["item_pic_procurement_name", "pic_procurement_name", "pic_procurement_id"],
    )
    detail_sales_pic = _first_existing(
        detail_cols,
        ["pic_sales_name", "sales_pic_name", "sales_name", "sales_person_name", "sales_person", "pic_sales"],
    )
    detail_vendor = _first_existing(
        detail_cols,
        ["vendor_name", "supplier_name", "supplier", "vendor", "vendor_id", "supplier_id"],
    )

    ref_so = _first_existing(detail_cols, cfg.get("ref_so_candidates", []))
    ref_pr = _first_existing(detail_cols, cfg.get("ref_pr_candidates", []))
    ref_po = _first_existing(detail_cols, cfg.get("ref_po_candidates", []))
    ref_grn = _first_existing(detail_cols, cfg.get("ref_grn_candidates", []))
    ref_do = _first_existing(detail_cols, cfg.get("ref_do_candidates", []))

    # Header/item aliases sengaja meniru payload API lama agar seluruh logic dashboard
    # setelah bagian LOAD DATA tidak perlu ditulis ulang besar-besaran.
    select_parts = [
        _select_expr("h", header_id, "header_id"),
        _select_expr("h", tx_number, "transaction_number", "text"),
        _select_expr("h", header_date, "transaction_date", "timestamp"),
        _select_expr("h", status, "status_description", "text"),
        _coalesce_text_expr("d", detail_sales_pic, "h", sales_pic, "pic_sales_name"),
        _select_expr("h", customer, "customer_name", "text"),
        _coalesce_text_expr("d", detail_vendor, "h", vendor, "vendor_name"),
        _select_expr("h", header_total, "transaction_total", "numeric"),
        _select_expr("h", date_approved, "date_approved", "timestamp"),
        _select_expr("h", date_inprogress, "date_inprogress", "timestamp"),
        _select_expr("h", date_complete, "date_complete", "timestamp"),
        _select_expr("d", detail_id, "item_id"),
        _select_expr("d", product_id, "item_product_id"),
        _select_expr("d", item_name, "item_item_name", "text"),
        _select_expr("d", item_price, "item_price", "numeric"),
        _select_expr("d", item_discount, "item_discount", "numeric"),
        _select_expr("d", item_quantity, "item_quantity", "numeric"),
        _select_expr("d", item_tax1_pct, "item_tax1_percentage", "numeric"),
        _select_expr("d", item_tax2_pct, "item_tax2_percentage", "numeric"),
        _select_expr("d", item_subtotal, "item_sub_total", "numeric"),
        _select_expr("d", ref_so, "item_so_detail_id"),
        _select_expr("d", ref_pr, "item_pr_detail_id"),
        _select_expr("d", ref_po, "item_po_detail_id"),
        _select_expr("d", ref_grn, "item_grn_detail_id"),
        _select_expr("d", ref_do, "item_do_detail_id"),
    ]

    # PIC Procurement: detail lebih prioritas, header menjadi fallback.
    select_parts.append(
        _coalesce_text_expr(
            "d", detail_pic_procurement,
            "h", header_pic_procurement,
            "item_pic_procurement_name",
        )
    )

    params = {}
    where_parts = []
    if header_date and end_date is not None:
        params["next_date"] = pd.Timestamp(end_date) + pd.Timedelta(days=1)
        where_parts.append(f'h.{_quote_ident(header_date)} < :next_date')
    if header_date and start_date is not None:
        params["start_date"] = pd.Timestamp(start_date)
        where_parts.append(f'h.{_quote_ident(header_date)} >= :start_date')

    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    query = text(f"""
        SELECT
            {', '.join(select_parts)}
        FROM {header_table} h
        JOIN {detail_table} d
          ON h.{_quote_ident(header_id)} = d.{_quote_ident(detail_header_fk)}
        {where_sql}
    """)

    engine, _ = get_erp_database_connection()
    with engine.connect() as conn:
        df = pd.read_sql_query(query, conn, params=params)

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = safe_to_datetime(df, "transaction_date")
    for col in ["date_approved", "date_inprogress", "date_complete"]:
        df = safe_to_datetime(df, col)
    return df


def _api_compat_nominal(df: pd.DataFrame) -> pd.Series:
    """Hitung nominal baris dari field detail DB dengan fallback subtotal."""
    if df.empty:
        return pd.Series(dtype="float64")

    qty = pd.to_numeric(df.get("item_quantity", 0), errors="coerce").fillna(0)
    price = pd.to_numeric(df.get("item_price", 0), errors="coerce").fillna(0)
    discount = pd.to_numeric(df.get("item_discount", 0), errors="coerce").fillna(0)
    tax1 = pd.to_numeric(df.get("item_tax1_percentage", 0), errors="coerce").fillna(0)
    direct = pd.to_numeric(df.get("item_sub_total", 0), errors="coerce").fillna(0)

    disc_per_unit = price * (discount / 100)
    tax_per_unit = (price - disc_per_unit) * (tax1 / 100)
    computed = qty * (price - disc_per_unit + tax_per_unit)

    value = direct.copy()
    use_computed = (computed != 0) & ((value == 0) | value.isna())
    value.loc[use_computed] = computed.loc[use_computed]
    return value.fillna(0)


def _build_balance_view(stage: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Compatibility view pengganti endpoint *-balance.

    Catatan: API lama dapat memiliki business rule server-side yang tidak terlihat
    di script ini. Karena rule endpoint tersebut tidak tersedia di source code,
    view ini hanya memproyeksikan data transaksi DB ke nama kolom yang digunakan UI.
    Tidak ada HTTP/API call lagi.
    """
    doc_labels = {
        "so": "No. SO",
        "pr": "No. PR",
        "po": "No. PO",
        "grn": "No. GRN",
        "do": "No. DO",
    }
    pic_labels = {
        "so": "PIC Sales",
        "pr": "PIC Procurement",
        "po": "PIC Procurement",
        "grn": "PIC Procurement",
        "do": "PIC Procurement",
    }

    if df is None or df.empty:
        status_col = "Status DO" if stage == "do" else "Status"
        cols = [doc_labels.get(stage, "No. Transaksi"), pic_labels.get(stage, "PIC"), status_col, "Nominal", "transaction_date"]
        return pd.DataFrame(columns=cols)

    out = pd.DataFrame(index=df.index)
    out[doc_labels.get(stage, "No. Transaksi")] = df.get("transaction_number")
    if stage == "so":
        out[pic_labels[stage]] = df.get("pic_sales_name")
    else:
        out[pic_labels.get(stage, "PIC Procurement")] = df.get("item_pic_procurement_name")

    status_name = "Status DO" if stage == "do" else "Status"
    out[status_name] = df.get("status_description")
    out["Nominal"] = _api_compat_nominal(df)
    out["transaction_date"] = pd.to_datetime(df.get("transaction_date"), errors="coerce")

    # Fallback header total hanya bila seluruh detail dokumen tidak memiliki nilai.
    # Header total diletakkan sekali saja agar tidak double count per item.
    if "transaction_total" in df.columns:
        doc_col = doc_labels.get(stage, "No. Transaksi")
        header_total = pd.to_numeric(df["transaction_total"], errors="coerce").fillna(0)
        tmp = pd.DataFrame({"doc": out[doc_col], "line": out["Nominal"], "header": header_total}, index=out.index)
        for _, idx in tmp.groupby("doc", dropna=False).groups.items():
            idx = list(idx)
            if not idx:
                continue
            line_sum = float(tmp.loc[idx, "line"].sum())
            hv = float(tmp.loc[idx, "header"].iloc[0]) if len(idx) else 0.0
            if line_sum == 0 and hv != 0:
                out.loc[idx, "Nominal"] = 0.0
                out.loc[idx[0], "Nominal"] = hv

    return out.reset_index(drop=True)


def _build_npr_view(data_new: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """DB-native NPR proxy: item SO yang belum memiliki PR strict detail+product."""
    so = data_new.get("so", pd.DataFrame()).copy()
    pr = data_new.get("pr", pd.DataFrame()).copy()
    if so.empty:
        return pd.DataFrame(columns=["No. Transaksi", "Status", "Nominal", "transaction_date"])

    def canon(v):
        if pd.isna(v):
            return None
        s = str(v).strip()
        if not s or s.lower() in {"nan", "none", "null", "<na>"}:
            return None
        try:
            f = float(s)
            if f.is_integer():
                return str(int(f))
        except Exception:
            pass
        return s

    so["__detail"] = so.get("item_id", pd.Series(index=so.index, dtype="object")).map(canon)
    so["__product"] = so.get("item_product_id", pd.Series(index=so.index, dtype="object")).map(canon)
    if pr.empty:
        pending = so.copy()
    else:
        pr_keys = set(zip(
            pr.get("item_so_detail_id", pd.Series(index=pr.index, dtype="object")).map(canon),
            pr.get("item_product_id", pd.Series(index=pr.index, dtype="object")).map(canon),
        ))
        mask = [
            (d, p) not in pr_keys
            for d, p in zip(so["__detail"], so["__product"])
        ]
        pending = so.loc[mask].copy()

    return pd.DataFrame({
        "No. Transaksi": pending.get("transaction_number"),
        "Status": pending.get("status_description"),
        "Nominal": _api_compat_nominal(pending),
        "transaction_date": pd.to_datetime(pending.get("transaction_date"), errors="coerce"),
    }).reset_index(drop=True)


@st.cache_data(ttl=DB_CACHE_TTL, show_spinner=False)
def load_all_data_new(start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    """
    Pengganti API baru: semua stage dibaca langsung dari ERP PostgreSQL.

    Semantik tanggal dipertahankan seperti request API lama:
    date_start/date_end diterapkan pada dataset transaksi yang diminta.
    """
    target_end = end_date if end_date is not None else date.today()

    result = {}
    for stage in ["so", "pr", "po", "grn", "do", "si"]:
        try:
            result[stage] = read_stage_from_database(
                stage,
                start_date=start_date,
                end_date=target_end,
            )
        except Exception as exc:
            logger.exception("Gagal membaca stage %s dari database", stage)
            st.warning(f"Gagal membaca {stage.upper()} dari database: {exc}")
            result[stage] = pd.DataFrame()
    return result


@st.cache_data(ttl=DB_CACHE_TTL, show_spinner=False)
def load_all_data(start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    """Pengganti endpoint balance/outstanding lama; tetap 100% database."""
    target_end = end_date if end_date is not None else date.today()
    source = load_all_data_new(start_date=start_date, end_date=target_end)

    result = {
        "pr": _build_balance_view("pr", source.get("pr", pd.DataFrame())),
        "po": _build_balance_view("po", source.get("po", pd.DataFrame())),
        "grn": _build_balance_view("grn", source.get("grn", pd.DataFrame())),
        "do": _build_balance_view("do", source.get("do", pd.DataFrame())),
        "npr": _build_npr_view(source),
    }
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
    search_status: str = "Semua Status",
    search_pic: str = "Semua PIC"
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    working = df.copy()
    working = normalize_text_columns(
        working,
        ["Status", "Status_so", "PIC Procurement", "PIC Purchasing", "PIC", "No. PR", "No. DO", "No. PUR", "No. Transaksi"]
    )

    # Filter nomor transaksi
    if search_number:
        pattern = search_number.strip().lower()
        string_cols = working.select_dtypes(include=["object"]).columns.tolist()
        if string_cols:
            mask_number = working[string_cols].apply(
                lambda col: col.str.lower().str.contains(pattern, na=False)
            ).any(axis=1)
            working = working[mask_number]

    # Filter Status khusus SO saja
    if search_status and search_status != "Semua Status":
        if "Status_so" in working.columns:
            working = working[
                working["Status_so"].str.strip().str.lower() == search_status.strip().lower()
            ]

    # Filter PIC Procurement via Dropdown
    if search_pic and search_pic != "Semua PIC":
        pic_cols = [col for col in ["PIC Procurement", "item_pic_procurement_name", "PIC Purchasing", "PIC"] if col in working.columns]
        if pic_cols:
            mask_pic = working[pic_cols].apply(
                lambda col: col.str.strip().str.lower() == search_pic.strip().lower()
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
    if df.empty or pic_col not in df.columns or doc_col not in df.columns or "Status" not in df.columns:
        return "Tidak ada"

    working = assign_unassigned(df, pic_col)
    working = working[working[pic_col] != "Unassigned"]

    if working.empty:
        return "Tidak ada"

    # 🔹 Urutan prioritas status (semakin tinggi nilainya, semakin pending)
    status_priority = {
        "Need Approve": 4,
        "Approved": 3,
        "In Progress": 2,
        "Complete": 1
    }

    working["Status_Score"] = working["Status"].map(status_priority).fillna(0)

    summary = (
        working.groupby(pic_col)
        .agg(
            Total_Doc=(doc_col, "nunique"),
            Avg_Status_Score=("Status_Score", "mean")
        )
        .reset_index()
    )

    # 🔹 Urutkan berdasarkan jumlah dokumen dan tingkat pending (semakin tinggi skor, semakin pending)
    summary = summary.sort_values(["Total_Doc", "Avg_Status_Score"], ascending=[False, False])

    return summary.iloc[0][pic_col] if not summary.empty else "Tidak ada"


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


def calculate_aging(df: pd.DataFrame, date_col: str, prefer: str = "approved") -> pd.DataFrame:
    """
    Hitung aging dengan prioritas tanggal sesuai preferensi.
    prefer bisa: "approved", "inprogress", "complete"
    Default: "approved"
    """
    if df.empty or date_col not in df.columns:
        return df.copy()

    working = df.copy()
    working = safe_to_datetime(working, date_col)
    working = safe_to_datetime(working, "date_inprogress")
    working = safe_to_datetime(working, "date_complete")
    working = safe_to_datetime(working, "date_approved")

    today = pd.Timestamp.today().normalize()

    # Default aging = today - transaction_date
    working["Aging"] = (today - working[date_col]).dt.days

    # Terapkan prioritas sesuai prefer
    if prefer == "approved":
        mask = working["date_approved"].notna()
        working.loc[mask, "Aging"] = (
            working.loc[mask, "date_approved"] - working.loc[mask, date_col]
        ).dt.days

    elif prefer == "inprogress":
        mask = working["date_inprogress"].notna()
        working.loc[mask, "Aging"] = (
            working.loc[mask, "date_inprogress"] - working.loc[mask, date_col]
        ).dt.days

    elif prefer == "complete":
        mask = working["date_complete"].notna()
        working.loc[mask, "Aging"] = (
            working.loc[mask, "date_complete"] - working.loc[mask, date_col]
        ).dt.days

    return working


def categorize_aging(df: pd.DataFrame) -> pd.DataFrame:
    bins = [-1, 30, 60, 90, float("inf")]   # 🔹 ubah dari 0 → -1
    labels = ["0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
    df["Aging Category"] = pd.cut(df["Aging"], bins=bins, labels=labels, right=True)
    return df


def render_aging_bar(df: pd.DataFrame, doc_col: str, chart_key: str = "aging_bar"):
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
            "0-30 hari": "#2F80ED",
            "31-60 hari": "#7ABBEE",
            "61-90 hari": "#FCA27F",
            ">90 hari": "#EB5757"
        },
        text="Jumlah Transaksi"
    )
    fig.update_traces(textposition="outside")

    # ✅ tambahkan key unik di sini
    st.plotly_chart(fig, use_container_width=True, key=chart_key)


def summarize_pic_aging(df: pd.DataFrame, pic_col: str, doc_col: str) -> pd.DataFrame:
    if df.empty or pic_col not in df.columns or "Aging" not in df.columns or "Status" not in df.columns:
        return pd.DataFrame(columns=[pic_col, "Average Aging", "Total_Doc", "Outstanding_Doc", "Completed_Doc", "Over90Pct"])

    working = assign_unassigned(df, pic_col)

    summary = (
        working.groupby(pic_col).agg(
            Average_Aging=("Aging", "mean"),   # 🔹 ubah nama kolom di sini
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
    y="Average_Aging",   # 🔹 gunakan nama baru
    text="Average_Aging",
    color="Average_Aging",
    color_continuous_scale=[(0.0, "#56CCF2"), (0.5, "#F2C94C"), (1.0, "#EB5757")]
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

def render_sla_gauge(df: pd.DataFrame, threshold: int = 5, title: str = "SLA Compliance"):
    if df.empty or "Aging" not in df.columns:
        st.info("Data aging tidak tersedia untuk SLA.")
        return

    sla_compliance = (df["Aging"] <= threshold).mean() * 100

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=sla_compliance,
        number={'suffix': '%', 'font': {'size': 48, 'color': "#000"}},  # 🔹 tambahkan ini
        title={'text': f"{title} (≤{threshold} hari)"},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': "blue"},
            'steps': [
                {'range': [0, 50], 'color': "red"},
                {'range': [50, 80], 'color': "yellow"},
                {'range': [80, 100], 'color': "green"}
            ]
        }
    ))
    st.plotly_chart(fig, use_container_width=True)


def summarize_pic_sla(df: pd.DataFrame, pic_col: str, doc_col: str, threshold: int = 5) -> pd.DataFrame:
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


def render_sla_trend(df: pd.DataFrame, threshold: int = 5, date_col: str = "transaction_date"):
    if df.empty or "Aging" not in df.columns or date_col not in df.columns:
        st.info("Data tidak tersedia untuk trend SLA.")
        return

    # Pastikan kolom tanggal dalam format datetime
    df = safe_to_datetime(df, date_col)

    # Tambahkan kolom bulan (Period)
    df["Bulan"] = df[date_col].dt.to_period("M").dt.to_timestamp()

    # Hitung SLA compliance per bulan
    sla_trend = (
        df.groupby("Bulan")
        .agg(SLA_Compliance=("Aging", lambda x: (x <= threshold).mean() * 100))
        .reset_index()
    )

    # Buat line chart
    fig = px.line(
        sla_trend,
        x="Bulan",
        y="SLA_Compliance",
        markers=True,
        title=f"Trend SLA Compliance per Bulan (≤{threshold} hari)"
    )
    fig.update_traces(
        texttemplate="%{y:.1f}%",
        textposition="top center"
    )
    fig.update_layout(
        yaxis=dict(title="SLA Compliance (%)", range=[0, 100])
    )

    st.plotly_chart(fig, use_container_width=True)


# =========================================================
# 9) MAIN APP
# =========================================================

def main():
    st.title("SIBIMA Performance Dashboard - PROCUREMENT")

    # ---------- TOP FILTERS ----------
    today = date.today()
    default_start = date(today.year, today.month, 1)

    col_head1, col_head2, col_head3, col_head4, col_head5 = st.columns([1, 1, 1, 1, 1])

    with col_head1:
        selected_date_range = st.date_input(
            "Select Date Range 📅",
            value=(default_start, today),
            max_value=today
        )

    with col_head2:
        selected_doc_type = st.selectbox("Pilih Jenis Dokumen 📑", ["PR", "DO", "NPR", "PUR"])

    with col_head3:
        search_number = st.text_input("Cari Nomor Transaksi 🔍", placeholder="No. PR / No. DO / No. NPR / No. PUR")

    with col_head4:
        search_status = st.text_input("Cari Status 🔍", placeholder="Complete / In Progress / Approved / Need Approve")

    # ---------- LOAD DATA ----------
    if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2:
        start_date, end_date = selected_date_range
    else:
        start_date, end_date = default_start, today

    with st.spinner("Membaca data langsung dari PostgreSQL ERP..."):
        data_old = load_all_data()
        data_new = load_all_data_new(start_date=start_date, end_date=end_date)

    # ---------- ASSIGN DATAFRAME ----------
    df_pr = data_old["pr"]
    df_po = data_old["po"]
    df_grn = data_old["grn"]
    df_do = data_old["do"]
    df_npr = data_old["npr"]
    #df_pur = data_old["pur"]

    df_pr_final = data_new["pr"]
    df_do_final = data_new["do"]
    #df_npr_final = data_new["npr"]

    # Pastikan kolom PIC dan Status sesuai
    #PR
    df_pr_final = df_pr_final.rename(columns={
        "item_pic_procurement_name": "PIC Procurement",
        "status_description": "Status"
    })
    #DO
    df_do_final = df_do_final.rename(columns={
        "item_pic_procurement_name": "PIC Procurement",
        "status_description": "Status"
    })

    df_do = df_do.rename(columns={
        "Status DO": "Status"
    })

    # Pastikan kolom tanggal sudah dalam format datetime
    #PR
    df_pr_final = safe_to_datetime(df_pr_final, "transaction_date")
    df_pr_final = safe_to_datetime(df_pr_final, "date_approved")
    df_pr_final = safe_to_datetime(df_pr_final, "date_inprogress")
    df_pr_final = safe_to_datetime(df_pr_final, "date_complete")
    #DO
    df_do_final = safe_to_datetime(df_do_final, "transaction_date")
    df_do_final = safe_to_datetime(df_do_final, "date_approved")
    df_do_final = safe_to_datetime(df_do_final, "date_inprogress")
    df_do_final = safe_to_datetime(df_do_final, "date_complete")
    #NPR
    #df_npr_final = safe_to_datetime(df_npr_final, "transaction_date")
    #df_npr_final = safe_to_datetime(df_npr_final, "date_approved")
    #df_npr_final = safe_to_datetime(df_npr_final, "date_inprogress")
    #df_npr_final = safe_to_datetime(df_npr_final, "date_complete")

        # ---------- EXTRACT UNIQUE PIC LIST ----------
    # Ambil list PIC Procurement unik dari df_pr_final (dan dataframe lain jika perlu)
    pic_list = []
    if "PIC Procurement" in df_pr_final.columns:
        pic_list = df_pr_final["PIC Procurement"].dropna().astype(str).str.strip()
        pic_list = [pic for pic in pic_list.unique() if pic != "" and pic.lower() != "nan"]
        pic_list.sort()

    # Tambahkan opsi 'Semua PIC' di urutan pertama
    pic_options = ["Semua PIC"] + pic_list

    # ---------- TOP FILTERS (Tahap 2: Dropdown PIC) ----------
    with col_head5:
        search_pic = st.selectbox(
            "Pilih PIC Procurement 👤",
            options=pic_options,
            index=0
        )

    # ---------- DEFAULT SAFE COPY ----------
    df_pr_f = df_pr.copy()
    df_po_f = df_po.copy()
    df_grn_f = df_grn.copy()
    df_do_f = df_do.copy()
    df_npr_f = df_npr.copy()
    #df_pur_f = df_pur.copy()
    df_pr_final_f = df_pr_final.copy()
    df_do_final_f = df_do_final.copy()
    #df_npr_final_f = df_pr_final.copy()

    # ---------- DATE FILTER ----------
    if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2:
        report_start_date, report_end_date = selected_date_range
        df_pr_f = apply_cumulative_filter(df_pr_f, report_end_date)
        df_po_f = apply_cumulative_filter(df_po_f, report_end_date)
        df_grn_f = apply_cumulative_filter(df_grn_f, report_end_date)
        df_do_f = apply_cumulative_filter(df_do_f, report_end_date)
        df_npr_f = apply_cumulative_filter(df_npr_f, report_end_date)
        #df_pur_f = apply_cumulative_filter(df_pur_f, report_end_date)
        df_pr_final_f = apply_cumulative_filter(df_pr_final_f, report_end_date)
        df_do_final_f = apply_cumulative_filter(df_do_final_f, report_end_date)
        #df_npr_final_f = apply_cumulative_filter(df_npr_final_f, report_end_date)

        # 🔹 Dataset baru (PR Final) pakai realisasi
        df_pr_f_real = apply_realization_filter(df_pr_f, report_start_date, report_end_date)
        df_pr_final_real = apply_realization_filter(df_pr_final, report_start_date, report_end_date)
        df_do_final_real = apply_realization_filter(df_do_final, report_start_date, report_end_date)
        #df_npr_final_real = apply_realization_filter(df_npr_final, report_start_date, report_end_date)

    # ---------- SEARCH FILTER ----------
    df_pr_f = apply_search_filter(df_pr_f, search_number, search_status, search_pic)
    df_pr_final_f = apply_search_filter(df_pr_final_f, search_number, search_status, search_pic)
    df_pr_final_real = apply_search_filter(df_pr_final_real, search_number, search_status, search_pic)
    df_po_f = apply_search_filter(df_po_f, search_number, search_status, search_pic)
    df_grn_f = apply_search_filter(df_grn_f, search_number, search_status, search_pic)
    df_do_f = apply_search_filter(df_do_f, search_number, search_status, search_pic)
    df_npr_f = apply_search_filter(df_npr_f, search_number, search_status, search_pic)
    #df_pur_f = apply_search_filter(df_pur_f, search_number, search_status, search_pic)
    #df_pr_final_real = apply_search_filter(df_pr_final_real, search_number, search_status, search_pic)


    # ---------- ENSURE IMPORTANT COLUMNS ----------
    df_pr_f = ensure_columns(df_pr_f, ["Nominal", "No. PR", "Status", "PIC Procurement"])
    df_po_f = ensure_columns(df_po_f, ["Nominal"])
    df_grn_f = ensure_columns(df_grn_f, ["Nominal"])
    df_do_f = ensure_columns(df_do_f, ["Nominal", "No. DO", "PIC Purchasing"])
    df_npr_f = ensure_columns(df_npr_f, ["Status", "Sales"])
    #df_pur_f = ensure_columns(df_pur_f, ["No. PUR", "PIC", "Status"])
    df_pr_final_real = ensure_columns(df_pr_final_real, ["PIC Procurement", "transaction_number","Status", "price", "quantity", "discount", "transaction_total", "tax1_percentage", "tax2_percentage"])
    df_do_final_real = ensure_columns(df_do_final_real, ["PIC Procurement", "transaction_number","Status", "price", "quantity", "discount", "transaction_total", "tax1_percentage", "tax2_percentage"])

    df_pr_f = safe_to_numeric(df_pr_f, ["Nominal"])
    df_po_f = safe_to_numeric(df_po_f, ["Nominal"])
    df_grn_f = safe_to_numeric(df_grn_f, ["Nominal"])
    df_do_f = safe_to_numeric(df_do_f, ["Nominal"])
    #df_pr_final_real = safe_to_numeric(df_pr_final_real, ["price", "discount", "quantity", "tax1_percentage", "tax2_percentage"])
    df_pr_final_real= safe_to_numeric(df_pr_final_real, ["item_price", "item_discount", "item_quantity", "item_tax1_percentage", "item_tax2_percentage"])
    df_do_final_real= safe_to_numeric(df_do_final_real, ["item_price", "item_discount", "item_quantity", "item_tax1_percentage", "item_tax2_percentage"])
    
    # ---------- METRICS ----------
    total_pr_unpr = safe_sum(df_pr_f, "Nominal")
    total_po_unpr = safe_sum(df_po_f, "Nominal")
    total_grn_unpr = safe_sum(df_grn_f, "Nominal")
    total_do_unpr = safe_sum(df_do_f, "Nominal")
    #total_pr = safe_sum(df_pr_final_real, "transaction_total")

    df_pr_final_real = normalize_text_columns(df_pr_final_real, ["item_PIC_Procurement"])
    df_do_final_real = normalize_text_columns(df_do_final_real, ["item_PIC_Procurement"])


    df_pr_final_real["disc_per_unit"] = df_pr_final_real["item_price"] * (df_pr_final_real["item_discount"] / 100)
    df_pr_final_real["tax_unit"] = (df_pr_final_real["item_price"] - df_pr_final_real["disc_per_unit"]) * (df_pr_final_real["item_tax1_percentage"] / 100)
    df_pr_final_real["net_price_unit"] = df_pr_final_real["item_price"] - df_pr_final_real["disc_per_unit"] + df_pr_final_real["tax_unit"]
    df_pr_final_real["total_pr_row"] = df_pr_final_real["item_quantity"] * df_pr_final_real["net_price_unit"]
    total_pr = df_pr_final_real["total_pr_row"].sum()

    df_do_final_real["disc_per_unit"] = df_do_final_real["item_price"] * (df_do_final_real["item_discount"] / 100)
    df_do_final_real["tax_unit"] = (df_do_final_real["item_price"] - df_do_final_real["disc_per_unit"]) * (df_do_final_real["item_tax1_percentage"] / 100)
    df_do_final_real["net_price_unit"] = df_do_final_real["item_price"] - df_do_final_real["disc_per_unit"] + df_do_final_real["tax_unit"]
    df_do_final_real["total_do_row"] = df_do_final_real["item_quantity"] * df_do_final_real["net_price_unit"]
    total_do = df_do_final_real["total_do_row"].sum()


    #df_npr_final_real["disc_per_unit"] = df_npr_final_real["item_price"] * (df_npr_final_real["item_discount"] / 100)
    #df_npr_final_real["tax_unit"] = (df_npr_final_real["item_price"] - df_npr_final_real["disc_per_unit"]) * (df_npr_final_real["item_tax1_percentage"] / 100)
    #df_npr_final_real["net_price_unit"] = df_npr_final_real["item_price"] - df_npr_final_real["disc_per_unit"] + df_npr_final_real["tax_unit"]
    #df_npr_final_real["total_pr_row"] = df_npr_final_real["item_quantity"] * df_npr_final_real["net_price_unit"]
    #total_npr = df_npr_final_real["total_pr_row"].sum()

    #df_do_final_real["disc_per_unit"] = df_do_final_real["item_price"] * (df_do_final_real["item_discount"] / 100)
    #df_do_final_real["tax_unit"] = (df_do_final_real["item_price"] - df_do_final_real["disc_per_unit"]) * (df_do_final_real["item_tax1_percentage"] / 100)
    #df_do_final_real["tax_unit"] = df_do_final_real["item_tax1_value"] + df_do_final_real["item_tax1_value"]
    #df_do_final_real["net_price_unit"] = df_do_final_real["item_price"] - df_do_final_real["disc_per_unit"] + df_do_final_real["tax_unit"]
    #df_do_final_real["net_price_unit"] = df_do_final_real["item_price"] - df_do_final_real["disc_per_unit"]
    #df_do_final_real["total_do_row"] = df_do_final_real["item_quantity"] * df_do_final_real["net_price_unit"]
    total_do = df_do_final_real["total_do_row"].sum()

    total_pr_count = safe_unique_count(df_pr_final_real, "transaction_number")
    total_pr_balance_count = safe_unique_count(df_pr_f, "No. PR")
    total_pr_rows = len(df_pr_final_real)
    total_pr_balance_rows = len(df_pr_f)
    total_do_count = safe_unique_count(df_do_final_real, "transaction_number")
    total_do_balance_count = safe_unique_count(df_do_f, "No. DO")
    total_do_rows = len(df_do_final_real)
    total_do_balance_rows = len(df_do_f)
    #total_npr_count = safe_unique_count(df_npr_f, "No. Transaksi")
    #total_npr_rows = len(df_npr_f)

    avg_nominal_do = safe_mean(df_do_f, "Nominal")

    top_pic_pr = get_top_pic(df_pr_f, "PIC Procurement", "No. PR")
    top_pic_do = get_top_pic(df_do_f, "PIC Procurement", "No. DO")
    #top_pic_pur = get_top_pic(df_pur_f, "PIC", "No. PUR")

    # ---------- LAYOUT ----------
    col_kiri, col_tengah, col_kanan = st.columns([1, 1, 1], gap="small")
    # 🔹 Filter hanya PR yang sudah punya tanggal inprogress atau complete
    #Aging PR
    #df_pr_final_valid = df_pr_final_real[
    #df_pr_final_real["date_inprogress"].notna() | df_pr_final_real["date_complete"].notna()
    #].copy()
    #PR
    df_pr_final_valid = df_pr_final_real[
    df_pr_final_real["Status"].isin(["Approved", "In Progress", "Complete"])
    ].copy()
    df_pr_final_valid = apply_search_filter(df_pr_final_valid, search_number, search_status, search_pic)

    #Aging PR Balance
    # Filter PR Balance hanya untuk status aktif (exclude Complete & Draft)
    df_pr_valid = df_pr_final_f[
    ~df_pr_final_f["Status"].isin(["Complete", "Draft"])
    ].copy()
    df_pr_valid = apply_search_filter(df_pr_valid, search_number, search_status, search_pic)


    #DO
    # 🔹 Filter hanya DO yang sudah punya tanggal inprogress atau complete
    #Aging DO
    df_do_final_valid = df_do_final_real[
    df_do_final_real["Status"].isin(["Approved", "In Progress", "Complete"])
    ].copy()
    df_do_final_valid = apply_search_filter(df_do_final_valid, search_number, search_status, search_pic)

    #Aging DO Balance
    # Filter DO Balance hanya untuk status aktif (exclude Complete & Draft)
    df_do_valid = df_do_final_f[
    ~df_do_final_f["Status"].isin(["Complete", "Draft"])
    ].copy()
    df_do_valid = apply_search_filter(df_do_valid, search_number, search_status, search_pic)


    # Lanjutkan proses aging hanya untuk DO yang valid
    #Aging DO
    df_do_final_valid = calculate_aging(df_do_final_valid, "transaction_date", prefer="approved")
    df_do_final_valid = categorize_aging(df_do_final_valid)
    #Aging DO Balance
    df_do_valid = calculate_aging(df_do_valid, "transaction_date", prefer="approved")
    df_do_valid = categorize_aging(df_do_valid)

    # Filter hanya DO valid aktif, exclude Draft
    df_do_final_valid = df_do_final_valid[
    ~df_do_final_valid["Status"].str.contains("Draft", case=False, na=False)
    ].copy()

    
    # =====================================================
    # LEFT - PR
    # =====================================================
    if selected_doc_type == "PR":
        with col_kiri:
            with st.container(border=True):
                st.subheader("📊 Detail PR")

                c1, c2 = st.columns(2)
                with c1:
                    metric_card("Total PR", f"Rp {total_pr:,.0f}".replace(",", "."))
                with c2:
                    metric_card("PR Balance", f"Rp {total_pr_unpr:,.0f}".replace(",", "."))

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

            # Download PR Balance by status
            with st.container(border=True):
                st.subheader("📥 Download Data PR Balance (Periode & Status)")

                if not df_pr_f.empty and "Status" in df_pr_f.columns:
                    all_statuses = sorted([s for s in df_pr_f["Status"].dropna().astype(str).unique().tolist() if s.strip()])
                    selected_statuses = st.multiselect(
                        "Pilih Status untuk di-download:",
                        all_statuses,
                        default=all_statuses,
                        key="pr_balance_status_export"
                    )

                    df_download_pr_balance = df_pr_f[df_pr_f["Status"].isin(selected_statuses)].copy()

                    if not df_download_pr_balance.empty:
                        st.download_button(
                            label=f"⬇️Download {len(df_download_pr_balance):,} Baris Data (Filtered).xlsx",
                            data=to_excel_bytes(df_download_pr_balance, sheet_name="Data_PR"),
                            file_name=f"Data_PR_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                        st.caption(f"Menampilkan {len(df_download_pr_balance):,} baris data yang akan di-download.")
                    else:
                        st.warning("Tidak ada data yang sesuai dengan filter yang dipilih.")
                else:
                    st.info("Data PR Balance tidak tersedia untuk export.")

            # Download per PIC PR Balance
            with st.container(border=True):
                st.subheader("📥 Download Data PR Balance per PIC")

                if not df_pr_f.empty and "PIC Procurement" in df_pr_f.columns:
                    # Filter status hanya Need Approve, Approved, In Progress
                    df_filtered_status = df_pr_f.copy()
                    #[
                        #df_pr_valid["Status"].isin(["Need Approve", "Approved", "In Progress"])
                    #].copy()

                    # Tambahkan opsi "Semua"
                    options = ["Semua"] + sorted(
                        df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str).unique().tolist()
                    )

                    selected_pic = st.selectbox("Pilih PIC Procurement:", options, key="pr_balance_pic_select")

                    # Jika pilih "Semua", ambil semua data sesuai status
                    if selected_pic == "Semua":
                        filtered = df_filtered_status.copy()
                    else:
                        filtered = df_filtered_status[
                            df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str) == selected_pic
                        ].copy()

                    st.download_button(
                        label=f"⬇️Download Data {selected_pic}.xlsx",
                        data=to_excel_bytes(filtered, sheet_name="Data_PR_Balance"),
                        file_name=f"Data_PR_balance_{selected_pic}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.caption(f"Menampilkan {len(filtered):,} baris data yang akan di-download.")
                else:
                    st.info("Data tidak tersedia untuk fitur download PR Balance per PIC.")

    # =====================================================
    # MID PR
    # =====================================================
        with col_tengah:


            # Lanjutkan proses aging hanya untuk PR yang valid
            #Aging PR
            #df_pr_final_valid = calculate_aging(df_pr_final_valid, "transaction_date")
            df_pr_final_valid = calculate_aging(df_pr_final_valid, "transaction_date", prefer="approved")
            df_pr_final_valid = categorize_aging(df_pr_final_valid)
            #Aging PR Balance
            #df_pr_valid = calculate_aging(df_pr_valid, "transaction_date")
            df_pr_valid = calculate_aging(df_pr_valid, "transaction_date", prefer="approved")
            df_pr_valid = categorize_aging(df_pr_valid)
            #df_pr_valid = df_pr_valid.drop_duplicates(subset=["transaction_number"])

            # Filter hanya PR valid aktif, exclude Draft
            df_pr_final_valid = df_pr_final_valid[
            ~df_pr_final_valid["Status"].str.contains("Draft", case=False, na=False)
            ].copy()
            # Filter hanya PR aktif, exclude Draft
            #df_pr_valid = df_pr_valid[
            #~df_pr_valid["Status"].str.contains("Draft", case=False, na=False)
            #].copy()

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging PR")
                render_aging_bar(df_pr_final_valid, "transaction_number", chart_key="aging_pr")

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging PR Balance")
                render_aging_bar(df_pr_valid, "transaction_number", chart_key="aging_pr_outstanding")

                pic_aging_summary = summarize_pic_aging(df_pr_valid, "PIC Procurement", "transaction_number")
                pic_aging_summary_final = summarize_pic_aging(df_pr_final_valid, "PIC Procurement", "transaction_number")

            with st.container(border=True):
                st.subheader("👥 Rata-rata Proses PR")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary_final)

            with st.container(border=True):
                st.subheader("👥 Rata-rata Proses PR Balance")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary)

            # Download per Category PR Aging
            with st.container(border=True):
                st.subheader("📥 Download Data per Categori Aging PR")

                if not df_pr_final_valid.empty:
                    # Filter data aging PR berdasarkan kategori yang dipilih
                    selected_category_pr = st.selectbox(
                        "Pilih kategori aging PR untuk diunduh 📂",
                        ["Semua", "0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
                    )

                    # Jika bukan 'Semua', filter sesuai kategori
                    if selected_category_pr != "Semua":
                        df_pr_filtered = df_pr_final_valid[
                            df_pr_final_valid["Aging Category"] == selected_category_pr
                        ].copy()
                    else:
                        df_pr_filtered = df_pr_final_valid.copy()

                    # Tombol download
                    st.download_button(
                    label=f"⬇️Download Data PR Aging ({selected_category_pr})",
                    data=to_excel_bytes(df_pr_filtered, sheet_name="PR Aging"),
                    file_name=f"PR_Aging_{selected_category_pr.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download_pr_aging_{selected_category_pr}"   # 🔹 key unik
                    )

                else:
                    st.info("Data tidak tersedia untuk fitur download per Category Aging PR.")

            # Download per Category PR Balance Aging
            with st.container(border=True):
                st.subheader("📥 Download Data per Categori Aging PR Balance")

                if not df_pr_valid.empty:
                    # Filter data aging PR berdasarkan kategori yang dipilih
                    selected_category_balance = st.selectbox(
                        "Pilih kategori aging PR Balance untuk diunduh 📂",
                        ["Semua", "0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
                    )

                    # Jika bukan 'Semua', filter sesuai kategori
                    if selected_category_balance != "Semua":
                        df_balance_filtered = df_pr_valid[
                            df_pr_valid["Aging Category"] == selected_category_balance
                        ].copy()
                    else:
                        df_balance_filtered = df_pr_valid.copy()

                    # Tombol download
                    st.download_button(
                    label=f"⬇️Download Data PR Balance Aging ({selected_category_balance})",
                    data=to_excel_bytes(df_balance_filtered, sheet_name="PR Balance Aging"),
                    file_name=f"PR_Balance_Aging_{selected_category_balance.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download_pr_balance_{selected_category_balance}"   # 🔹 key unik
                    )

                else:
                    st.info("Data tidak tersedia untuk fitur download per Category Aging PR Balance.")


    # =====================================================
    # RIGHT - PR
    # =====================================================
        with col_kanan:
            with st.container(border=True):
                st.subheader("📏 SLA Compliance PR")
                render_sla_gauge(df_pr_final_valid, threshold=5, title="SLA Compliance PR")

            with st.container(border=True):
                st.subheader("📏 SLA Compliance PR Balance")
                render_sla_gauge(df_pr_valid, threshold=5, title="SLA Compliance PR Balance")

            pic_sla_summary = summarize_pic_sla(df_pr_final_valid, "PIC Procurement", "transaction_number", threshold=5)

            with st.container(border=True):
                st.subheader("📏 SLA Compliance per PIC Procurement")
                #st.dataframe(pic_sla_summary, use_container_width=True, hide_index=True)
                render_pic_sla_bar(pic_sla_summary)

            with st.container(border=True):
                st.subheader("📈 Trend SLA")
                render_sla_trend(df_pr_final_valid, threshold=5, date_col="transaction_date")


            # Download PR by period & status
            with st.container(border=True):
                st.subheader("📥 Download Data PR (Periode & Status)")

                if not df_pr_final_real.empty and "Status" in df_pr_final_real.columns:
                    all_statuses = sorted([s for s in df_pr_final_real["Status"].dropna().astype(str).unique().tolist() if s.strip()])
                    selected_statuses = st.multiselect(
                        "Pilih Status untuk di-download:",
                        all_statuses,
                        default=all_statuses,
                        key="pr_status_export"
                    )

                    df_download = df_pr_final_real[df_pr_final_real["Status"].isin(selected_statuses)].copy()

                    if not df_download.empty:
                        st.download_button(
                            label=f"⬇️Download {len(df_download):,} Baris Data (Filtered).xlsx",
                            data=to_excel_bytes(df_download, sheet_name="Data_PR"),
                            file_name=f"Data_PR_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"download {len(df_download):,} Baris Data (Filtered)"   # 🔹 key unik
                        )
                        st.caption(f"Menampilkan {len(df_download):,} baris data yang akan di-download.")
                    else:
                        st.warning("Tidak ada data yang sesuai dengan filter yang dipilih.")
                else:
                    st.info("Data PR tidak tersedia untuk export.")

            # Download per PIC PR
            with st.container(border=True):
                st.subheader("📥 Download Data PR per PIC")

                if not df_pr_final_real.empty and "PIC Procurement" in df_pr_final_real.columns:
                    # Filter status hanya Need Approve, Approved, In Progress
                    df_filtered_status = df_pr_final_real.copy()
                    #[
                        #df_pr_final_real["Status"].isin(["Approved", "In Progress", "Complete"])
                    #].copy()

                    # Tambahkan opsi "Semua"
                    options = ["Semua"] + sorted(
                        df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str).unique().tolist()
                    )

                    selected_pic = st.selectbox("Pilih PIC Procurement:", options, key="pr_pic_select")

                    # Jika pilih "Semua", ambil semua data sesuai status
                    if selected_pic == "Semua":
                        filtered = df_filtered_status.copy()
                    else:
                        filtered = df_filtered_status[
                            df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str) == selected_pic
                        ].copy()

                    st.download_button(
                        label=f"⬇️Download Data {selected_pic}.xlsx",
                        data=to_excel_bytes(filtered, sheet_name="Data_PR"),
                        file_name=f"Data_PR_{selected_pic}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.caption(f"Menampilkan {len(filtered):,} baris data yang akan di-download.")
                else:
                    st.info("Data tidak tersedia untuk fitur download PR Balance per PIC.")


    # =====================================================
    # LEFT - DO
    # =====================================================
    elif selected_doc_type == "DO":
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
                    color_col="Status",
                )

            with st.container(border=True):
                st.subheader("🔥 Heatmap DO Balance - Aktivitas PIC Procurement")
                render_pic_heatmap(df_do_f, "PIC Procurement", "transaction_date", "No. DO", "Heatmap Aktivitas PIC Procurement per Bulan")

            # Download DO Balance by status
            with st.container(border=True):
                st.subheader("📥 Download Data DO Balance (Periode & Status)")

                if not df_do_valid.empty and "Status" in df_do_valid.columns:
                    all_statuses = sorted([s for s in df_do_valid["Status"].dropna().astype(str).unique().tolist() if s.strip()])
                    selected_statuses = st.multiselect(
                        "Pilih Status untuk di-download:",
                        all_statuses,
                        default=all_statuses,
                        key="do_balance_status_export"
                    )

                    df_download_do_balance = df_do_valid[df_do_valid["Status"].isin(selected_statuses)].copy()

                    if not df_download_do_balance.empty:
                        st.download_button(
                            label=f"⬇️Download {len(df_download_do_balance):,} Baris Data (Filtered).xlsx",
                            data=to_excel_bytes(df_download_do_balance, sheet_name="Data_PR"),
                            file_name=f"Data_DO_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                        st.caption(f"Menampilkan {len(df_download_do_balance):,} baris data yang akan di-download.")
                    else:
                        st.warning("Tidak ada data yang sesuai dengan filter yang dipilih.")
                else:
                    st.info("Data DO Balance tidak tersedia untuk export.")

            # Download per PIC PR Balance
            with st.container(border=True):
                st.subheader("📥 Download Data DO Balance per PIC")

                if not df_do_valid.empty and "PIC Procurement" in df_do_valid.columns:
                    # Filter status hanya Need Approve, Approved, In Progress
                    df_filtered_status = df_do_valid.copy()

                    # Tambahkan opsi "Semua"
                    options = ["Semua"] + sorted(
                        df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str).unique().tolist()
                    )

                    selected_pic = st.selectbox("Pilih PIC Procurement:", options, key="do_balance_pic_select")

                    # Jika pilih "Semua", ambil semua data sesuai status
                    if selected_pic == "Semua":
                        filtered = df_filtered_status.copy()
                    else:
                        filtered = df_filtered_status[
                            df_filtered_status["PIC Procurement"].fillna("Unassigned").astype(str) == selected_pic
                        ].copy()

                    st.download_button(
                        label=f"⬇️Download Data {selected_pic}.xlsx",
                        data=to_excel_bytes(filtered, sheet_name="Data_DO_Balance"),
                        file_name=f"Data_DO_balance_{selected_pic}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.caption(f"Menampilkan {len(filtered):,} baris data yang akan di-download.")
                else:
                    st.info("Data tidak tersedia untuk fitur download DO Balance per PIC.")

    # =====================================================
    # MID DO
    # =====================================================
        with col_tengah:

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging DO")
                render_aging_bar(df_do_final_valid, "transaction_number", chart_key="aging_do")

            with st.container(border=True):
                st.subheader("⏳ Distribusi Aging DO Balance")
                render_aging_bar(df_do_valid, "transaction_number", chart_key="aging_do_outstanding")


                pic_aging_summary_do = summarize_pic_aging(df_do_valid, "PIC Procurement", "transaction_number")
                pic_aging_summary_final_do = summarize_pic_aging(df_do_final_valid, "PIC Procurement", "transaction_number")

            with st.container(border=True):
                st.subheader("👥 Rata-rata Proses DO")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary_final_do)

            with st.container(border=True):
                st.subheader("👥 Rata-rata Proses DO Balance")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary_do)


            # Download per Category DO Aging
            with st.container(border=True):
                st.subheader("📥 Download Data per Categori Aging DO")

                if not df_do_final_valid.empty:
                    # Filter data aging DO berdasarkan kategori yang dipilih
                    selected_category_do = st.selectbox(
                        "Pilih kategori aging DO untuk diunduh 📂",
                        ["Semua", "0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
                    )

                    # Jika bukan 'Semua', filter sesuai kategori
                    if selected_category_do != "Semua":
                        df_do_filtered = df_do_final_valid[
                            df_do_final_valid["Aging Category"] == selected_category_do
                        ].copy()
                    else:
                        df_do_filtered = df_do_final_valid.copy()

                    # Tombol download
                    st.download_button(
                    label=f"⬇️Download Data DO Aging ({selected_category_do})",
                    data=to_excel_bytes(df_do_filtered, sheet_name="DO Aging"),
                    file_name=f"DO_Aging_{selected_category_do.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download_do_aging_{selected_category_do}"   # 🔹 key unik
                    )

                else:
                    st.info("Data tidak tersedia untuk fitur download per Category Aging DO.")

            # Download per Category DO Balance Aging
            with st.container(border=True):
                st.subheader("📥 Download Data per Categori Aging DO Balance")

                if not df_do_valid.empty:
                    # Filter data aging DO berdasarkan kategori yang dipilih
                    selected_category_do_balance = st.selectbox(
                        "Pilih kategori aging DO Balance untuk diunduh 📂",
                        ["Semua", "0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]
                    )

                    # Jika bukan 'Semua', filter sesuai kategori
                    if selected_category_do_balance != "Semua":
                        df_balance_do_filtered = df_do_valid[
                            df_do_valid["Aging Category"] == selected_category_do_balance
                        ].copy()
                    else:
                        df_balance_do_filtered = df_do_valid.copy()

                    # Tombol download
                    st.download_button(
                    label=f"⬇️Download Data DO Balance Aging ({selected_category_do_balance})",
                    data=to_excel_bytes(df_balance_do_filtered, sheet_name="DO Balance Aging"),
                    file_name=f"DO_Balance_Aging_{selected_category_do_balance.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download_do_balance_{selected_category_do_balance}"   # 🔹 key unik
                    )

                else:
                    st.info("Data tidak tersedia untuk fitur download per Category Aging DO Balance.")

    # =====================================================
    # RIGHT - DO
    # =====================================================
        with col_kanan:
            with st.container(border=True):
                st.subheader("📏 SLA Compliance DO")
                render_sla_gauge(df_do_final_valid, threshold=5, title="SLA Compliance DO")

            with st.container(border=True):
                st.subheader("📏 SLA Compliance DO Balance")
                render_sla_gauge(df_do_valid, threshold=5, title="SLA Compliance DO Balance")

            pic_sla_summary_do = summarize_pic_sla(df_do_final_valid, "PIC Procurement", "transaction_number", threshold=5)

            with st.container(border=True):
                st.subheader("📏 SLA Compliance per PIC Procurement")
                #st.dataframe(pic_sla_summary, use_container_width=True, hide_index=True)
                render_pic_sla_bar(pic_sla_summary_do)

            with st.container(border=True):
                st.subheader("📈 Trend SLA")
                render_sla_trend(df_do_final_valid, threshold=5, date_col="transaction_date")


            # Download DO by period & status
            with st.container(border=True):
                st.subheader("📥 Download Data DO (Periode & Status)")

                if not df_do_final_real.empty and "Status" in df_do_final_real.columns:
                    all_statuses_do = sorted([s for s in df_do_final_real["Status"].dropna().astype(str).unique().tolist() if s.strip()])
                    selected_statuses_do = st.multiselect(
                        "Pilih Status untuk di-download:",
                        all_statuses_do,
                        default=all_statuses_do,
                        key="do_status_export"
                    )

                    df_download_do = df_do_final_real[df_do_final_real["Status"].isin(selected_statuses_do)].copy()

                    if not df_download_do.empty:
                        st.download_button(
                            label=f"⬇️Download {len(df_download_do):,} Baris Data (Filtered).xlsx",
                            data=to_excel_bytes(df_download_do, sheet_name="Data_DO"),
                            file_name=f"Data_DO_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"download {len(df_download_do):,} Baris Data (Filtered)"   # 🔹 key unik
                        )
                        st.caption(f"Menampilkan {len(df_download_do):,} baris data yang akan di-download.")
                    else:
                        st.warning("Tidak ada data yang sesuai dengan filter yang dipilih.")
                else:
                    st.info("Data DO tidak tersedia untuk export.")

            # Download per PIC DO
            with st.container(border=True):
                st.subheader("📥 Download Data DO per PIC")

                if not df_do_final_real.empty and "PIC Procurement" in df_do_final_real.columns:
                    # Filter status hanya Need Approve, Approved, In Progress
                    df_filtered_status_do = df_do_final_real.copy()
                    #[
                        #df_do_final_real["Status"].isin(["Approve", "In Progress", "Complete"])
                    #].copy()

                    # Tambahkan opsi "Semua"
                    options = ["Semua"] + sorted(
                        df_filtered_status_do["PIC Procurement"].fillna("Unassigned").astype(str).unique().tolist()
                    )

                    selected_pic_do = st.selectbox("Pilih PIC Procurement:", options, key="pr_pic_select")

                    # Jika pilih "Semua", ambil semua data sesuai status
                    if selected_pic_do == "Semua":
                        filtered_do = df_filtered_status_do.copy()
                    else:
                        filtered_do = df_filtered_status_do[
                            df_filtered_status_do["PIC Procurement"].fillna("Unassigned").astype(str) == selected_pic_do
                        ].copy()

                    st.download_button(
                        label=f"⬇️Download Data {selected_pic_do}.xlsx",
                        data=to_excel_bytes(filtered_do, sheet_name="Data_DO"),
                        file_name=f"Data_DO_{selected_pic}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.caption(f"Menampilkan {len(filtered_do):,} baris data yang akan di-download.")
                else:
                    st.info("Data tidak tersedia untuk fitur download DO Balance per PIC.")


    # =====================================================
    # LEFT - NPR
    # =====================================================
    if selected_doc_type == "NPR":
        with col_kiri:
            with st.container(border=True):
                st.subheader("📊 Detail NPR per Status")

                if df_npr_f.empty:
                    st.info("Data NPR tidak tersedia.")
                else:
                    status_summary = (
                        df_npr_f.groupby("Status")["No. Transaksi"]
                        .nunique()
                        .reset_index(name="Jumlah_Doc")
                    )

                    # Warna status konsisten dengan PR
                    STATUS_COLORS = {
                        "Complete": "#00CC96",
                        "In Progress": "#F2C94C",
                        "Approved": "#F2994A",
                        "Need Approve": "#EB5757",
                        "Request": "#56CCF2"  # tambahan untuk NPR
                    }

                    # Pie chart berdasarkan jumlah dokumen
                    fig = px.pie(
                        status_summary,
                        values="Jumlah_Doc",
                        names="Status",
                        color="Status",
                        color_discrete_map=STATUS_COLORS,
                        hole=0.45,
                        title="Proporsi Jumlah NPR per Status"
                    )

                    # Tampilkan persentase dan jumlah
                    fig.update_traces(
                        textinfo="percent+value",
                        texttemplate="%{percent:.1%}<br>(%{value} dokumen)"
                    )

                    st.plotly_chart(fig, use_container_width=True)




    # =====================================================
    # MID NPR
    # =====================================================

        with col_tengah:
            with st.container(border=True):
                st.subheader("👥 Analisis Aktivitas NPR per Sales & per Status")

                if df_npr_f.empty:
                    st.info("Data NPR tidak tersedia.")
                else:
                    # Pastikan kolom Status dan Sales ada
                    df_npr_f = assign_unassigned(df_npr_f, "Sales")
                    df_npr_f = assign_unassigned(df_npr_f, "Status")

                    # Ringkas jumlah NPR per Sales dan Status
                    sales_summary = (
                        df_npr_f.groupby(["Sales", "Status"])
                        .agg(Jumlah_Doc=("No. Transaksi", "nunique"))
                        .reset_index()
                        .sort_values(by="Jumlah_Doc", ascending=False)
                    )

                    # Warna status sama seperti PR
                    STATUS_COLORS = {
                        "Complete": "#00CC96",
                        "Pro": "#F2C94C",
                        "Approved": "#F2994A",
                        "Need Approve": "#EB5757",
                        "Request": "#56CCF2"  # tambahan untuk NPR
                    }

                    # Buat stacked bar chart
                    fig = px.bar(
                        sales_summary,
                        x="Sales",
                        y="Jumlah_Doc",
                        color="Status",
                        color_discrete_map=STATUS_COLORS,
                        text="Jumlah_Doc",
                        title="Analisis Transaksi NPR per Sales & per Status"
                    )

                    # Tambahkan label total di atas tiap Sales
                    totals = sales_summary.groupby("Sales")["Jumlah_Doc"].sum().reset_index()
                    for _, row in totals.iterrows():
                        fig.add_annotation(
                            x=row["Sales"],
                            y=row["Jumlah_Doc"],
                            text=f"{row['Jumlah_Doc']}",
                            showarrow=False,
                            font=dict(size=12, color="black"),
                            yshift=10
                        )

                    fig.update_traces(
                        textposition="inside",
                        textfont=dict(size=10, color="white")
                    )
                    fig.update_layout(
                        uniformtext_mode="hide",
                        yaxis_title="Jumlah NPR",
                        xaxis_title="Sales",
                        margin=dict(l=40, r=40, t=60, b=80)
                    )

                    st.plotly_chart(fig, use_container_width=True)


    # =====================================================
    # 📈 RIGHT - NPR Timeline
    # =====================================================
        with col_kanan:
            with st.container(border=True):
                st.subheader("📈 Timeline NPR per Tanggal Transaksi")

                if df_npr_f.empty:
                    st.info("Data NPR tidak tersedia.")
                else:
                    # Pastikan kolom tanggal valid
                    df_npr_f['transaction_date'] = pd.to_datetime(df_npr_f['transaction_date'], errors='coerce')

                    # Hitung jumlah NPR unik per tanggal
                    timeline = (
                        df_npr_f.groupby(df_npr_f['transaction_date'].dt.date)['No. Transaksi']
                        .nunique()
                        .reset_index(name='Jumlah_Doc')
                    )

                    # Line chart timeline NPR
                    fig = px.line(
                        timeline,
                        x='transaction_date',
                        y='Jumlah_Doc',
                        markers=True,
                        title='Timeline NPR (Jumlah NPR Unik per Hari)',
                        line_shape='linear',
                        color_discrete_sequence=['#56CCF2']
                    )

                    fig.update_traces(
                        textposition="top center",
                        textfont=dict(size=10, color="black")
                    )
                    fig.update_layout(
                        yaxis_title="Jumlah NPR",
                        xaxis_title="Tanggal Transaksi",
                        margin=dict(l=40, r=40, t=60, b=80)
                    )

                    st.plotly_chart(fig, use_container_width=True)


    # ---------- FOOTER INFO ----------
    with st.expander("ℹ️ Informasi Teknis Dashboard"):
        selected_report_date = (
            selected_date_range[1]
            if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2
            else date.today()
        )

        st.markdown(
            f"""
- **Data Source:** `PostgreSQL ERP`
- **Connection:** `SSH Tunnel → SQLAlchemy → PostgreSQL`
- **Tanggal report sampai:** `{selected_report_date}`
- **Mode filter tanggal:** kumulatif (semua data sampai tanggal akhir)
- **Cache Database Query:** `{DB_CACHE_TTL}` detik
- **API/HTTP Request:** `DISABLED / TIDAK DIGUNAKAN`
            """
        )


if __name__ == "__main__":
    main()