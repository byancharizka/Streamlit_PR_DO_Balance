import os
import logging
from io import BytesIO
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import plotly.express as px
import pytz
import requests
import streamlit as st
import plotly.graph_objects as go
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

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
# 3) APP CONFIG
# =========================================================
TIMEZONE = pytz.timezone("Asia/Jakarta")
# Ambil tanggal hari ini
today = date.today()

# Default: tanggal 1 bulan aktif sampai hari ini
DEFAULT_START_DATE = date(today.year, today.month, 1)
DEFAULT_END_DATE = today
REQUEST_TIMEOUT = int(os.getenv("SIBIMA_API_TIMEOUT", "120"))


BASE_URL = {
    "outstanding": "https://erp.sibima.id/api/dashboard/",
    "erp": "https://erp.sibima.id/api/",
    "brp": "https://brp.sibima.id/api/"
}

API_TOKEN = os.getenv("SIBIMA_API_TOKEN", "3bd1c8f44fa6ba220af7382c57c547a9673b0f6f5ada977b850d7f5215e6")

# Pastikan setiap URL diakhiri dengan "/"
for key in BASE_URL:
    if not BASE_URL[key].endswith("/"):
        BASE_URL[key] += "/"

def create_session():
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[502, 503, 504, 429],
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

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

def normalize_pic_columns(
    df: pd.DataFrame,
    columns=("PIC Procurement", "PIC Purchasing", "PIC", "item_pic_procurement_name"),
) -> pd.DataFrame:
    """
    Normalisasi nama PIC agar variasi penulisan dianggap sebagai PIC yang sama.

    Canonical rules:
    - seluruh nama PIC menjadi uppercase;
    - "FAQIH RAMADHAN" digabung menjadi "FAQIH".
    """
    working = df.copy() if df is not None else pd.DataFrame()

    aliases = {
        "FAQIH RAMADHAN": "FAQIH",
    }

    for col in columns:
        if col in working.columns:
            s = (
                working[col]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.upper()
            )

            null_like = s.str.lower().isin({"", "nan", "none", "null", "<na>"})
            s = s.replace(aliases)
            s.loc[null_like] = ""

            working[col] = s

    return working



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


def calculate_total_pr_strict(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Canonical Total PR used by BOTH API and PostgreSQL dashboards.

    Grain: one PR detail row (transaction_number + item_id).
    Formula (same as legacy API):
        disc_per_unit = item_price * item_discount / 100
        tax_unit       = (item_price - disc_per_unit) * item_tax1_percentage / 100
        net_price_unit = item_price - disc_per_unit + tax_unit
        total_pr_row   = item_quantity * net_price_unit

    Important parity rules:
    - numeric NULL/invalid -> 0, identical on both files;
    - duplicate PR-detail rows are collapsed before summing;
    - no transaction_total/header-total fallback;
    - Tax2 is intentionally excluded because the legacy API Total PR excludes it.
    """
    out = df.copy() if df is not None else pd.DataFrame()
    if out.empty:
        for c in ["disc_per_unit", "tax_unit", "net_price_unit", "total_pr_row"]:
            out[c] = pd.Series(dtype="float64")
        return out, 0.0

    # The endpoint and DB reader can differ in dtype (1 vs 1.0 vs '1').
    # Normalize only for de-duplication; business fields remain untouched.
    def _canon(v):
        if v is None or pd.isna(v):
            return ""
        s = str(v).strip()
        if not s or s.lower() in {"nan", "none", "null", "<na>"}:
            return ""
        try:
            f = float(s)
            if f.is_integer():
                return str(int(f))
        except Exception:
            pass
        return s

    doc = out.get("transaction_number", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    detail = out.get("item_id", pd.Series("", index=out.index)).map(_canon)
    product = out.get("item_product_id", pd.Series("", index=out.index)).map(_canon)
    name = out.get("item_item_name", pd.Series("", index=out.index)).fillna("").astype(str).str.strip().str.lower()
    qty_key = pd.to_numeric(out.get("item_quantity", pd.Series(0, index=out.index)), errors="coerce").fillna(0)

    # item_id is the strongest 1:1 key observed on API and DB. Fallback protects old rows.
    strong = detail.ne("")
    out["__total_pr_key"] = ""
    out.loc[strong, "__total_pr_key"] = "PRD|" + doc.loc[strong] + "|" + detail.loc[strong]
    weak = ~strong
    out.loc[weak, "__total_pr_key"] = (
        "FB|" + doc.loc[weak] + "|" + product.loc[weak] + "|" + name.loc[weak]
        + "|" + qty_key.loc[weak].astype(str)
    )

    # Keep a single current-state row for every PR detail.
    out = out.drop_duplicates("__total_pr_key", keep="last").copy()

    for c in ["item_price", "item_discount", "item_quantity", "item_tax1_percentage"]:
        if c not in out.columns:
            out[c] = 0.0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    out["disc_per_unit"] = out["item_price"] * (out["item_discount"] / 100.0)
    out["tax_unit"] = (out["item_price"] - out["disc_per_unit"]) * (out["item_tax1_percentage"] / 100.0)
    out["net_price_unit"] = out["item_price"] - out["disc_per_unit"] + out["tax_unit"]
    out["total_pr_row"] = out["item_quantity"] * out["net_price_unit"]

    return out, float(out["total_pr_row"].sum())



def build_total_pr_day_snapshot(df: pd.DataFrame, target_date, source_label: str = "API") -> pd.DataFrame:
    """Canonical Total PR rows for exactly one transaction date."""
    work = df.copy() if df is not None else pd.DataFrame()
    if work.empty or "transaction_date" not in work.columns:
        return pd.DataFrame()
    work["transaction_date"] = pd.to_datetime(work["transaction_date"], errors="coerce")
    target = pd.Timestamp(target_date).date()
    work = work.loc[work["transaction_date"].dt.date.eq(target)].copy()
    work, _ = calculate_total_pr_strict(work)
    if work.empty:
        return work
    work["Forensic Source"] = source_label
    keep = [c for c in [
        "Forensic Source","transaction_number","transaction_date","item_id","item_product_id",
        "item_item_name","item_quantity","item_price","item_discount","item_tax1_percentage",
        "disc_per_unit","tax_unit","net_price_unit","total_pr_row","Status","PIC Procurement","__total_pr_key"
    ] if c in work.columns]
    return work[keep].copy()

# =========================================================
# 6) API FETCHING
# =========================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_api_data_old(endpoint: str, source: str = "outstanding", start_date=None, end_date=None):
    base_url = BASE_URL.get(source, BASE_URL["outstanding"])
    url = f"{base_url}{endpoint}"
    params = {"date_start": start_date, "date_end": end_date}

    try:
        logger.info("Fetching endpoint=%s from source=%s params=%s", endpoint, source, params)

        # 🔹 Gunakan session dengan retry
        session = create_session()
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, dict):
            data_layer = payload.get("data", {})
            if isinstance(data_layer, dict):
                rows = data_layer.get("data", [])
                if isinstance(rows, list):
                    df = pd.DataFrame(rows)
                    df = safe_to_datetime(df, "transaction_date")
                    return df
        return pd.DataFrame()

    except Exception as e:
        st.warning(f"Gagal mengambil data dari endpoint {endpoint} ({source}): {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def get_api_data_new(
    endpoint: str,
    source: str = "erp",
    start_date=None,
    end_date=None,
    silent: bool = False,
    per_page: int = 200,
    max_pages: int = 500,
):
    """Fetch ERP API dengan pagination penuh dan flatten item detail.

    Perbaikan penting:
    - tidak lagi berhenti di page pertama;
    - aman bila endpoint mengabaikan parameter page (signature guard);
    - menerima payload data=list maupun data={data:[...], current_page, last_page};
    - satu output row per item/detail;
    - dipakai oleh purchase-requests, purchase-orders, delivery-orders, sales-orders.
    """
    base_url = BASE_URL.get(source, BASE_URL["erp"])
    url = f"{base_url}{endpoint}"

    session = create_session()
    all_rows = []
    previous_signature = None

    def _extract_page(payload, requested_page):
        rows = []
        current_page = requested_page
        last_page = None

        if not isinstance(payload, dict):
            return rows, current_page, last_page

        data_layer = payload.get("data", [])
        meta_candidates = []

        if isinstance(data_layer, dict):
            nested = data_layer.get("data", [])
            if isinstance(nested, list):
                rows = nested
            elif isinstance(data_layer.get("items"), list):
                rows = data_layer.get("items", [])
            meta_candidates.append(data_layer)
        elif isinstance(data_layer, list):
            rows = data_layer

        for key in ("meta", "pagination", "paginate"):
            obj = payload.get(key)
            if isinstance(obj, dict):
                meta_candidates.append(obj)
        meta_candidates.append(payload)

        for meta in meta_candidates:
            if not isinstance(meta, dict):
                continue
            if current_page == requested_page:
                current_page = (
                    meta.get("current_page")
                    or meta.get("page")
                    or meta.get("currentPage")
                    or current_page
                )
            if last_page is None:
                last_page = (
                    meta.get("last_page")
                    or meta.get("total_pages")
                    or meta.get("lastPage")
                    or meta.get("page_count")
                )

        return rows if isinstance(rows, list) else [], current_page, last_page

    try:
        for page in range(1, max_pages + 1):
            params = {
                "date_start": start_date,
                "date_end": end_date,
                "token": API_TOKEN,
                "page": page,
                "per_page": per_page,
            }
            # Buang None agar API tidak menerima string/null yang tidak perlu.
            params = {k: v for k, v in params.items() if v is not None and v != ""}

            logger.info(
                "Fetching endpoint=%s source=%s page=%s params=%s",
                endpoint, source, page,
                {k: v for k, v in params.items() if k != "token"},
            )

            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()

            rows, current_page, last_page = _extract_page(payload, page)
            if not rows:
                break

            # Guard bila backend mengabaikan ?page= dan selalu mengembalikan page yang sama.
            signature = "|".join(
                str(
                    (r.get("id") if isinstance(r, dict) else "")
                    or (r.get("transaction_number") if isinstance(r, dict) else "")
                    or ""
                )
                for r in rows[:25]
            )
            if page > 1 and signature and signature == previous_signature:
                logger.warning(
                    "Endpoint %s mengembalikan page identik pada page=%s; stop untuk mencegah duplikasi.",
                    endpoint, page,
                )
                break
            previous_signature = signature

            for row in rows:
                if not isinstance(row, dict):
                    continue

                items = row.get("items", [])
                if isinstance(items, dict):
                    items = items.get("data", [])

                if isinstance(items, list) and items:
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        flat = {
                            **row,
                            **{f"item_{k}": v for k, v in item.items()},
                        }
                        # Hindari menyimpan nested list besar pada setiap detail row.
                        flat.pop("items", None)
                        all_rows.append(flat)
                else:
                    flat = row.copy()
                    flat.pop("items", None)
                    all_rows.append(flat)

            try:
                if last_page is not None and int(current_page) >= int(last_page):
                    break
            except Exception:
                pass

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        df = safe_to_datetime(df, "transaction_date")
        return df

    except requests.exceptions.HTTPError as e:
        logger.warning(
            "HTTP error endpoint=%s source=%s status=%s",
            endpoint,
            source,
            getattr(e.response, "status_code", "-"),
        )
        if not silent:
            st.warning(f"Gagal mengambil data dari endpoint {endpoint} ({source}).")
        return pd.DataFrame()

    except Exception:
        logger.exception("Gagal mengambil endpoint=%s source=%s", endpoint, source)
        if not silent:
            st.warning(f"Gagal mengambil data dari endpoint {endpoint} ({source}).")
        return pd.DataFrame()



def load_all_data(start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    endpoint_map = {
        "pr": ("pr-balance", {"Tgl. PR": "transaction_date"}),
        "po": ("po-balance", {"Tgl. PO": "transaction_date"}),
        "grn": ("grn-balance", {"Tgl. GRN": "transaction_date"}),
        "do": ("do-balance", {"Tgl. DO": "transaction_date"}),
        "npr": ("outstanding-npr", {"Tanggal": "transaction_date"}),
        #"pur": ("outstanding-pur", {"Tanggal": "transaction_date"})
    }

    result = {}
    for key, (endpoint, rename_map) in endpoint_map.items():
        df = get_api_data_old(endpoint, source="outstanding", start_date=start_date, end_date=end_date)

        if not df.empty:
            df = df.rename(columns=rename_map)
            df = safe_to_datetime(df, "transaction_date")
        result[key] = df

    return result





# =========================================================
# 6B) HISTORICAL / AS-OF PR BALANCE — API CANONICAL
# =========================================================
PR_BALANCE_START_DATE = date(2026, 1, 1)


def _api_first_series(df: pd.DataFrame, candidates, default="") -> pd.Series:
    """Ambil candidate column pertama yang tersedia sebagai Series sepanjang index."""
    for col in candidates:
        if col in df.columns:
            return df[col]
    return pd.Series(default, index=df.index)


def _api_clean_key(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )


def _api_numeric(df: pd.DataFrame, candidates, default=0.0) -> pd.Series:
    return pd.to_numeric(
        _api_first_series(df, candidates, default),
        errors="coerce",
    ).fillna(default)


def _normalize_pr_status_for_balance(value):
    if value is None or pd.isna(value):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        code = int(float(raw))
        return {
            0: "Draft",
            1: "Need Approve",
            2: "Approved",
            3: "In Progress",
            4: "Complete",
            5: "Close",
            7: "Approve 1",
            8: "Approve 2",
            9: "Approve 3",
        }.get(code, raw)
    except Exception:
        pass

    key = " ".join(raw.casefold().split())
    return {
        "draft": "Draft",
        "need approve": "Need Approve",
        "need approved": "Need Approve",
        "approved": "Approved",
        "approve": "Approved",
        "in progress": "In Progress",
        "complete": "Complete",
        "completed": "Complete",
        "close": "Close",
        "closed": "Close",
        "approve 1": "Approve 1",
        "approve 2": "Approve 2",
        "approve 3": "Approve 3",
    }.get(key, raw)


def _status_code_from_label(label: str):
    return {
        "Draft": 0,
        "Need Approve": 1,
        "Approved": 2,
        "In Progress": 3,
        "Complete": 4,
        "Close": 5,
        "Approve 1": 7,
        "Approve 2": 8,
        "Approve 3": 9,
    }.get(str(label or "").strip())


def _extract_nested_pr_status_asof(df: pd.DataFrame, cutoff) -> pd.DataFrame:
    """
    Coba baca history status yang ikut dibawa oleh endpoint purchase-requests.

    Beberapa deployment API mengembalikan history sebagai nested list pada header.
    Jika tersedia, ini menjadi sumber terbaik untuk status as-of. Jika tidak tersedia,
    caller akan fallback ke milestone date_approved/date_inprogress/date_complete.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["__pr_number", "Status As Of", "Status Code As Of", "Status As Of Timestamp"])

    history_cols = [c for c in [
        "histories", "history", "status_histories", "transaction_histories",
        "purchase_request_histories", "request_histories",
    ] if c in df.columns]
    if not history_cols:
        return pd.DataFrame(columns=["__pr_number", "Status As Of", "Status Code As Of", "Status As Of Timestamp"])

    cutoff_ts = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)
    rows = []
    docs = df.drop_duplicates("transaction_number", keep="last") if "transaction_number" in df.columns else df
    for _, r in docs.iterrows():
        pr_no = str(r.get("transaction_number") or "").strip()
        if not pr_no:
            continue
        for hc in history_cols:
            entries = r.get(hc)
            if not isinstance(entries, list):
                continue
            for h in entries:
                if not isinstance(h, dict):
                    continue
                status_val = h.get("status", h.get("status_id", h.get("status_code", h.get("status_description"))))
                status_label = _normalize_pr_status_for_balance(status_val)
                if not status_label:
                    status_label = _normalize_pr_status_for_balance(h.get("status_name", ""))
                ts = pd.to_datetime(
                    h.get("updated_at", h.get("created_at", h.get("date", h.get("timestamp")))),
                    errors="coerce",
                )
                if pd.isna(ts) or ts >= cutoff_ts:
                    continue
                rows.append({
                    "__pr_number": pr_no,
                    "Status As Of": status_label,
                    "Status Code As Of": _status_code_from_label(status_label),
                    "Status As Of Timestamp": ts,
                })

    if not rows:
        return pd.DataFrame(columns=["__pr_number", "Status As Of", "Status Code As Of", "Status As Of Timestamp"])

    out = pd.DataFrame(rows).sort_values(["__pr_number", "Status As Of Timestamp"])
    return out.drop_duplicates("__pr_number", keep="last").reset_index(drop=True)


def _infer_pr_status_asof_from_milestones(df: pd.DataFrame, cutoff) -> pd.DataFrame:
    """
    Fallback status as-of ketika endpoint tidak expose nested history.

    Urutan milestone mengikuti mapping ERP:
      Approved=2 -> In Progress=3 -> Complete=4.
    Untuk dokumen yang belum mencapai Approved pada cutoff, status fallback menjadi
    Need Approve kecuali current status memang Draft.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["__pr_number", "Status As Of", "Status Code As Of", "Status As Of Timestamp"])

    docs = df.copy()
    docs["__pr_number"] = _api_clean_key(_api_first_series(docs, ["transaction_number"], ""))
    docs = docs[docs["__pr_number"].ne("")].drop_duplicates("__pr_number", keep="last")
    cutoff_ts = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)

    current = _api_first_series(docs, ["status", "status_description", "Status"], "").map(_normalize_pr_status_for_balance)
    approved = pd.to_datetime(_api_first_series(docs, ["date_approved", "approved_at", "approved_date"], pd.NaT), errors="coerce")
    inprogress = pd.to_datetime(_api_first_series(docs, ["date_inprogress", "date_in_progress", "inprogress_at", "in_progress_at"], pd.NaT), errors="coerce")
    complete = pd.to_datetime(_api_first_series(docs, ["date_complete", "date_completed", "completed_at", "complete_at"], pd.NaT), errors="coerce")

    status = pd.Series("Need Approve", index=docs.index, dtype="object")
    status_at = pd.to_datetime(_api_first_series(docs, ["transaction_date", "date", "created_at"], pd.NaT), errors="coerce")

    draft_mask = current.eq("Draft") & approved.isna() & inprogress.isna() & complete.isna()
    status.loc[draft_mask] = "Draft"

    m = approved.notna() & approved.lt(cutoff_ts)
    status.loc[m] = "Approved"
    status_at.loc[m] = approved.loc[m]

    m = inprogress.notna() & inprogress.lt(cutoff_ts)
    status.loc[m] = "In Progress"
    status_at.loc[m] = inprogress.loc[m]

    m = complete.notna() & complete.lt(cutoff_ts)
    status.loc[m] = "Complete"
    status_at.loc[m] = complete.loc[m]

    out = pd.DataFrame({
        "__pr_number": docs["__pr_number"],
        "Status As Of": status,
        "Status Code As Of": status.map(_status_code_from_label),
        "Status As Of Timestamp": status_at,
    })
    return out.reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pr_history_cached(pr_no: str) -> dict:
    """Fetch FULL history satu PR dan cache 1 jam.

    Penting: cache TIDAK bergantung pada cutoff. History PR yang sama dapat dipakai ulang
    ketika user berpindah filter Jan -> Feb -> Mar -> Oct tanpa request HTTP ulang.
    """
    pr_no = str(pr_no or "").strip()
    if not pr_no:
        return {
            "__pr_number": "",
            "Fetch State": "EMPTY_PR_NUMBER",
            "history": [],
        }

    base_url = f"{BASE_URL['erp']}purchase-requests/history"
    params = {"transaction_number": pr_no}
    if API_TOKEN:
        params["token"] = API_TOKEN

    try:
        session = create_session()
        response = session.get(base_url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        history = data.get("history", []) if isinstance(data, dict) else []
        if not isinstance(history, list):
            history = []

        # Simpan hanya field yang diperlukan agar cache ringan dan stabil.
        parsed = []
        for h in history:
            if not isinstance(h, dict):
                continue
            status_label = _normalize_pr_status_for_balance(
                h.get("status", h.get("status_description", h.get("status_name", "")))
            )
            ts = pd.to_datetime(
                h.get("updated_at", h.get("created_at", h.get("date", h.get("timestamp")))),
                errors="coerce",
            )
            if pd.isna(ts):
                continue
            parsed.append({
                "status": status_label,
                "timestamp": ts,
            })

        return {
            "__pr_number": pr_no,
            "Fetch State": "OK",
            "history": parsed,
        }
    except Exception as exc:
        logger.warning("Gagal mengambil PR history %s: %s", pr_no, exc)
        return {
            "__pr_number": pr_no,
            "Fetch State": "ENDPOINT_ERROR",
            "history": [],
        }


@st.cache_data(ttl=300, show_spinner=False)
def _load_pr_history_status_map_api(transaction_numbers: tuple, cutoff) -> pd.DataFrame:
    """Bangun status historical per cutoff dari history PR yang dicache per dokumen.

    Optimasi performa:
    - history setiap PR dicache 1 jam oleh _fetch_pr_history_cached();
    - cutoff berbeda tidak memaksa download history yang sama;
    - first-load menggunakan maksimal 8 worker agar cepat tanpa membanjiri ERP API.
    """
    if not transaction_numbers:
        return pd.DataFrame(columns=[
            "__pr_number", "Status As Of", "Status Code As Of",
            "Status As Of Timestamp", "Status As Of Source",
            "History Endpoint State", "First History Timestamp",
        ])

    # Pastikan unique supaya satu PR tidak pernah dipanggil lebih dari sekali dalam satu run.
    transaction_numbers = tuple(sorted({
        str(v).strip() for v in transaction_numbers if str(v).strip()
    }))
    cutoff_ts = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)

    def resolve_one(pr_no: str):
        fetched = _fetch_pr_history_cached(pr_no)
        fetch_state = fetched.get("Fetch State", "ENDPOINT_ERROR")
        history = fetched.get("history", [])
        if not isinstance(history, list):
            history = []

        if fetch_state != "OK":
            return {
                "__pr_number": pr_no,
                "Status As Of": pd.NA,
                "Status Code As Of": pd.NA,
                "Status As Of Timestamp": pd.NaT,
                "Status As Of Source": "HISTORY_ENDPOINT_ERROR",
                "History Endpoint State": "ENDPOINT_ERROR",
                "First History Timestamp": pd.NaT,
            }

        parsed_history = []
        candidates = []
        for h in history:
            if not isinstance(h, dict):
                continue
            status_label = _normalize_pr_status_for_balance(h.get("status", ""))
            ts = pd.to_datetime(h.get("timestamp"), errors="coerce")
            if pd.isna(ts):
                continue
            parsed_history.append(ts)
            if status_label and ts < cutoff_ts:
                candidates.append((ts, status_label))

        if candidates:
            ts, status_label = max(candidates, key=lambda x: x[0])
            return {
                "__pr_number": pr_no,
                "Status As Of": status_label,
                "Status Code As Of": _status_code_from_label(status_label),
                "Status As Of Timestamp": ts,
                "Status As Of Source": "PURCHASE_REQUEST_HISTORY_ENDPOINT",
                "History Endpoint State": "HISTORY_BEFORE_CUTOFF",
                "First History Timestamp": min(parsed_history) if parsed_history else pd.NaT,
            }

        if parsed_history:
            return {
                "__pr_number": pr_no,
                "Status As Of": pd.NA,
                "Status Code As Of": pd.NA,
                "Status As Of Timestamp": pd.NaT,
                "Status As Of Source": "PURCHASE_REQUEST_HISTORY_ENDPOINT",
                "History Endpoint State": "HISTORY_AFTER_CUTOFF_ONLY",
                "First History Timestamp": min(parsed_history),
            }

        return {
            "__pr_number": pr_no,
            "Status As Of": pd.NA,
            "Status Code As Of": pd.NA,
            "Status As Of Timestamp": pd.NaT,
            "Status As Of Source": "PURCHASE_REQUEST_HISTORY_ENDPOINT",
            "History Endpoint State": "NO_HISTORY_RETURNED",
            "First History Timestamp": pd.NaT,
        }

    rows = []
    max_workers = min(8, max(1, len(transaction_numbers)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(resolve_one, pr_no): pr_no for pr_no in transaction_numbers}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                pr_no = futures[future]
                logger.warning("Gagal resolve cached PR history %s: %s", pr_no, exc)
                result = {
                    "__pr_number": pr_no,
                    "Status As Of": pd.NA,
                    "Status Code As Of": pd.NA,
                    "Status As Of Timestamp": pd.NaT,
                    "Status As Of Source": "HISTORY_ENDPOINT_ERROR",
                    "History Endpoint State": "ENDPOINT_ERROR",
                    "First History Timestamp": pd.NaT,
                }
            rows.append(result)

    if not rows:
        return pd.DataFrame(columns=[
            "__pr_number", "Status As Of", "Status Code As Of",
            "Status As Of Timestamp", "Status As Of Source",
            "History Endpoint State", "First History Timestamp",
        ])

    return (
        pd.DataFrame(rows)
        .drop_duplicates("__pr_number", keep="last")
        .reset_index(drop=True)
    )


def _build_pr_status_asof_api(
    pr_df: pd.DataFrame,
    cutoff,
    transaction_numbers=None,
) -> pd.DataFrame:
    """Resolve status PR exactly AS-OF the selected cutoff.

    Performance-safe parity rules:
    - Hanya PR candidate yang memang masih relevan untuk PR Balance yang dipanggil ke
      endpoint history. Caller dapat mengirim ``transaction_numbers`` agar PR tanpa SO,
      PR internal, dan row yang pasti tidak akan masuk balance tidak menambah request.
    - Historical cutoff: latest history status <= cutoff tetap authoritative.
    - Current/today cutoff: current payload dipakai sebagai baseline. History hanya
      dipanggil untuk dokumen non-terminal yang masih perlu verifikasi stale status.
      Complete/Close current tidak perlu request history karena keduanya pasti keluar.
    - Complete milestone tetap dapat mengalahkan status_description yang stale.

    Business logic status TIDAK berubah; yang berubah hanya jumlah request HTTP.
    """
    if pr_df is None or pr_df.empty:
        return pd.DataFrame(columns=[
            "__pr_number", "Status As Of", "Status Code As Of",
            "Status As Of Timestamp", "Status As Of Source",
            "History Endpoint State", "First History Timestamp",
        ])

    cutoff_date = pd.Timestamp(cutoff).date()
    is_current_cutoff = cutoff_date >= date.today()

    docs = pr_df.copy()
    docs["__pr_number"] = _api_clean_key(_api_first_series(docs, ["transaction_number"], ""))
    docs = docs[docs["__pr_number"].ne("")].drop_duplicates("__pr_number", keep="last")

    if transaction_numbers is not None:
        wanted = {str(v).strip() for v in transaction_numbers if str(v).strip()}
        docs = docs[docs["__pr_number"].isin(wanted)].copy()

    if docs.empty:
        return pd.DataFrame(columns=[
            "__pr_number", "Status As Of", "Status Code As Of",
            "Status As Of Timestamp", "Status As Of Source",
            "History Endpoint State", "First History Timestamp",
        ])

    current_status = _api_first_series(
        docs, ["status", "status_description", "Status"], ""
    ).map(_normalize_pr_status_for_balance)
    current_at = pd.to_datetime(
        _api_first_series(docs, ["updated_at", "modified_at", "created_at", "transaction_date"], pd.NaT),
        errors="coerce",
    )
    current_map = pd.DataFrame({
        "__pr_number": docs["__pr_number"],
        "__current_status": current_status,
        "__current_code": current_status.map(_status_code_from_label),
        "__current_ts": current_at,
    })

    # Milestone/nested data adalah local payload processing (murah, tanpa HTTP tambahan).
    nested = _extract_nested_pr_status_asof(docs, cutoff_date)
    inferred = _infer_pr_status_asof_from_milestones(docs, cutoff_date)

    # History endpoint adalah bagian paling mahal. Untuk current cutoff, current Complete/Close
    # sudah pasti keluar dari PR Balance sehingga tidak perlu diverifikasi lagi. Selain itu,
    # bila date_complete sudah ada, milestone sendiri sudah cukup membuktikan Complete.
    history_docs = docs.copy()
    if is_current_cutoff:
        complete_milestone = pd.to_datetime(
            _api_first_series(history_docs, ["date_complete", "date_completed", "completed_at", "complete_at"], pd.NaT),
            errors="coerce",
        ).notna()
        current_for_history = _api_first_series(
            history_docs, ["status", "status_description", "Status"], ""
        ).map(_normalize_pr_status_for_balance)
        need_history = ~current_for_history.isin(["Complete", "Close"]) & ~complete_milestone
        history_docs = history_docs.loc[need_history].copy()

    history_numbers = tuple(sorted(history_docs["__pr_number"].dropna().astype(str).unique().tolist()))
    if history_numbers:
        history_endpoint = _load_pr_history_status_map_api(history_numbers, cutoff_date)
    else:
        history_endpoint = pd.DataFrame(columns=[
            "__pr_number", "Status As Of", "Status Code As Of",
            "Status As Of Timestamp", "Status As Of Source",
            "History Endpoint State", "First History Timestamp",
        ])

    base = docs[["__pr_number"]].copy()
    base = base.merge(history_endpoint, how="left", on="__pr_number")
    base = base.merge(current_map, how="left", on="__pr_number")

    if nested is not None and not nested.empty:
        n = nested.rename(columns={
            "Status As Of": "__nested_status",
            "Status Code As Of": "__nested_code",
            "Status As Of Timestamp": "__nested_ts",
        })
        base = base.merge(
            n[[c for c in ["__pr_number", "__nested_status", "__nested_code", "__nested_ts"] if c in n.columns]],
            how="left", on="__pr_number",
        )
    else:
        base["__nested_status"] = pd.NA
        base["__nested_code"] = pd.NA
        base["__nested_ts"] = pd.NaT

    if inferred is not None and not inferred.empty:
        f = inferred.rename(columns={
            "Status As Of": "__fallback_status",
            "Status Code As Of": "__fallback_code",
            "Status As Of Timestamp": "__fallback_ts",
        })
        base = base.merge(
            f[[c for c in ["__pr_number", "__fallback_status", "__fallback_code", "__fallback_ts"] if c in f.columns]],
            how="left", on="__pr_number",
        )
    else:
        base["__fallback_status"] = pd.NA
        base["__fallback_code"] = pd.NA
        base["__fallback_ts"] = pd.NaT

    history_state = base.get("History Endpoint State", pd.Series("", index=base.index)).fillna("").astype(str)
    proven_not_existing = history_state.eq("HISTORY_AFTER_CUTOFF_ONLY")

    # Historical cutoff: history <= cutoff is source-of-truth. Only fallback when history
    # endpoint did not provide a usable status. Never use today's status to rewrite history.
    if not is_current_cutoff:
        endpoint_missing = (
            base["Status As Of"].isna()
            | base["Status As Of"].fillna("").astype(str).str.strip().eq("")
        )

        nested_present = base["__nested_status"].notna() & base["__nested_status"].astype(str).str.strip().ne("")
        use_nested = endpoint_missing & ~proven_not_existing & nested_present
        base.loc[use_nested, "Status As Of"] = base.loc[use_nested, "__nested_status"]
        base.loc[use_nested, "Status Code As Of"] = base.loc[use_nested, "__nested_code"]
        base.loc[use_nested, "Status As Of Timestamp"] = base.loc[use_nested, "__nested_ts"]
        base.loc[use_nested, "Status As Of Source"] = "NESTED_HISTORY_FALLBACK"

        still_missing = (
            base["Status As Of"].isna()
            | base["Status As Of"].fillna("").astype(str).str.strip().eq("")
        )
        fallback_present = base["__fallback_status"].notna() & base["__fallback_status"].astype(str).str.strip().ne("")
        use_fallback = still_missing & ~proven_not_existing & fallback_present
        base.loc[use_fallback, "Status As Of"] = base.loc[use_fallback, "__fallback_status"]
        base.loc[use_fallback, "Status Code As Of"] = base.loc[use_fallback, "__fallback_code"]
        base.loc[use_fallback, "Status As Of Timestamp"] = base.loc[use_fallback, "__fallback_ts"]
        base.loc[use_fallback, "Status As Of Source"] = "MILESTONE_FALLBACK"

        # Last fallback only when no historical evidence is available at all.
        still_missing = (
            base["Status As Of"].isna()
            | base["Status As Of"].fillna("").astype(str).str.strip().eq("")
        )
        use_current_fallback = still_missing & ~proven_not_existing & base["__current_status"].fillna("").astype(str).str.strip().ne("")
        base.loc[use_current_fallback, "Status As Of"] = base.loc[use_current_fallback, "__current_status"]
        base.loc[use_current_fallback, "Status Code As Of"] = base.loc[use_current_fallback, "__current_code"]
        base.loc[use_current_fallback, "Status As Of Timestamp"] = base.loc[use_current_fallback, "__current_ts"]
        base.loc[use_current_fallback, "Status As Of Source"] = "CURRENT_STATUS_LAST_RESORT_FALLBACK"

        base.loc[proven_not_existing, "Status As Of"] = pd.NA
        base.loc[proven_not_existing, "Status Code As Of"] = pd.NA
        base.loc[proven_not_existing, "Status As Of Timestamp"] = pd.NaT
        base.loc[proven_not_existing, "Status As Of Source"] = "HISTORY_PROVES_NOT_EXISTING_AT_CUTOFF"

    else:
        # Current cutoff: choose the best current-state evidence.
        # Start from current payload status, then override stale values with stronger terminal
        # evidence from latest history/milestone.
        base["Status As Of"] = base["__current_status"]
        base["Status Code As Of"] = base["__current_code"]
        base["Status As Of Timestamp"] = base["__current_ts"]
        base["Status As Of Source"] = "CURRENT_PURCHASE_REQUEST_STATUS"

        history_status = base.get("Status As Of", pd.Series(index=base.index, dtype="object"))
        # The merge above's history Status As Of was overwritten, so recover directly from
        # history_endpoint through a lightweight map keyed by PR number.
        hist_status_map = {}
        hist_code_map = {}
        hist_ts_map = {}
        if history_endpoint is not None and not history_endpoint.empty:
            hist_status_map = history_endpoint.set_index("__pr_number")["Status As Of"].to_dict()
            hist_code_map = history_endpoint.set_index("__pr_number")["Status Code As Of"].to_dict()
            hist_ts_map = history_endpoint.set_index("__pr_number")["Status As Of Timestamp"].to_dict()
        hist_status = base["__pr_number"].map(hist_status_map).fillna("").astype(str).str.strip()
        hist_code = base["__pr_number"].map(hist_code_map)
        hist_ts = pd.to_datetime(base["__pr_number"].map(hist_ts_map), errors="coerce")

        # Complete/Close are terminal for PR Balance membership and must override stale
        # Approved/In Progress descriptions on today's snapshot.
        hist_terminal = hist_status.isin(["Complete", "Close"])
        base.loc[hist_terminal, "Status As Of"] = hist_status.loc[hist_terminal]
        base.loc[hist_terminal, "Status Code As Of"] = hist_code.loc[hist_terminal]
        base.loc[hist_terminal, "Status As Of Timestamp"] = hist_ts.loc[hist_terminal]
        base.loc[hist_terminal, "Status As Of Source"] = "CURRENT_HISTORY_TERMINAL_STATUS"

        # Milestone Complete is also strong evidence when history endpoint is incomplete.
        fallback_status = base["__fallback_status"].fillna("").astype(str).str.strip()
        fallback_complete = fallback_status.eq("Complete")
        base.loc[fallback_complete, "Status As Of"] = "Complete"
        base.loc[fallback_complete, "Status Code As Of"] = _status_code_from_label("Complete")
        base.loc[fallback_complete, "Status As Of Timestamp"] = base.loc[fallback_complete, "__fallback_ts"]
        base.loc[fallback_complete, "Status As Of Source"] = "CURRENT_COMPLETE_MILESTONE"

        # If current payload is blank, use latest history, then inferred milestone status.
        current_blank = base["Status As Of"].fillna("").astype(str).str.strip().eq("")
        use_hist = current_blank & hist_status.ne("")
        base.loc[use_hist, "Status As Of"] = hist_status.loc[use_hist]
        base.loc[use_hist, "Status Code As Of"] = hist_code.loc[use_hist]
        base.loc[use_hist, "Status As Of Timestamp"] = hist_ts.loc[use_hist]
        base.loc[use_hist, "Status As Of Source"] = "CURRENT_HISTORY_FALLBACK"

        still_blank = base["Status As Of"].fillna("").astype(str).str.strip().eq("")
        use_inferred = still_blank & fallback_status.ne("")
        base.loc[use_inferred, "Status As Of"] = fallback_status.loc[use_inferred]
        base.loc[use_inferred, "Status Code As Of"] = base.loc[use_inferred, "__fallback_code"]
        base.loc[use_inferred, "Status As Of Timestamp"] = base.loc[use_inferred, "__fallback_ts"]
        base.loc[use_inferred, "Status As Of Source"] = "CURRENT_MILESTONE_FALLBACK"

    return base[[c for c in [
        "__pr_number", "Status As Of", "Status Code As Of",
        "Status As Of Timestamp", "Status As Of Source",
        "History Endpoint State", "First History Timestamp",
    ] if c in base.columns]].copy()


@st.cache_data(ttl=1800, show_spinner=False)
def _load_purchase_orders_until_api(end_date_val) -> pd.DataFrame:
    """PO detail kumulatif 1-Jan-2026 s/d cutoff untuk mengurangi PR Balance."""
    return get_api_data_new(
        "purchase-orders",
        source="erp",
        start_date=PR_BALANCE_START_DATE,
        end_date=end_date_val,
    )


def _build_po_qty_by_pr_detail_api(po_df: pd.DataFrame, cutoff) -> pd.DataFrame:
    if po_df is None or po_df.empty:
        return pd.DataFrame(columns=["__pr_detail_key", "PO Qty Linked", "PO Documents", "PO Statuses"])

    po = po_df.copy()
    pr_ref = _api_clean_key(_api_first_series(po, [
        "item_pr_detail_id", "item_purchase_request_detail_id", "item_purchase_request_details_id",
        "item_request_detail_id", "item_source_pr_detail_id", "pr_detail_id",
        "purchase_request_detail_id",
    ], ""))
    po["__pr_detail_key"] = pr_ref
    po = po[po["__pr_detail_key"].ne("")].copy()
    if po.empty:
        return pd.DataFrame(columns=["__pr_detail_key", "PO Qty Linked", "PO Documents", "PO Statuses"])

    po_date = pd.to_datetime(_api_first_series(po, ["transaction_date", "date", "created_at"], pd.NaT), errors="coerce")
    cutoff_ts = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)
    po = po.loc[po_date.notna() & po_date.lt(cutoff_ts)].copy()

    status_raw = _api_first_series(po, ["status", "status_description", "Status"], "")
    def po_status_code(v):
        if v is None or pd.isna(v):
            return None
        try:
            return int(float(str(v).strip()))
        except Exception:
            key = " ".join(str(v).casefold().split())
            # mapping sesuai backend PostgreSQL yang dipakai file parity
            name_map = {
                "menunggu persetujuan po": 1,
                "pembuatan bpv / barang dalam pengiriman": 2,
                "sebagian barang tersedia": 3,
                "barang siap lengkap": 4,
                "approved": 2,
                "in progress": 3,
                "complete": 4,
                "completed": 4,
                "need approve": 1,
            }
            return name_map.get(key)
    po["__po_status_code"] = status_raw.map(po_status_code)
    po = po[po["__po_status_code"].isin([1, 2, 3, 4])].copy()
    if po.empty:
        return pd.DataFrame(columns=["__pr_detail_key", "PO Qty Linked", "PO Documents", "PO Statuses"])

    po["__po_qty"] = _api_numeric(po, ["item_quantity", "quantity", "item_qty"], 0.0).clip(lower=0)
    po["__po_number"] = _api_clean_key(_api_first_series(po, ["transaction_number", "po_transaction_number", "number"], ""))
    po["__po_status_text"] = _api_first_series(po, ["status_description", "Status", "status"], "").fillna("").astype(str)

    # deduplicate one current API row per PO detail before sum
    po_detail_key = _api_clean_key(_api_first_series(po, ["item_id", "po_detail_id", "item_po_detail_id"], ""))
    po["__po_detail_key"] = po_detail_key
    has_key = po["__po_detail_key"].ne("")
    keyed = po.loc[has_key].drop_duplicates("__po_detail_key", keep="last")
    unkeyed = po.loc[~has_key]
    po = pd.concat([keyed, unkeyed], ignore_index=True, sort=False)

    agg = po.groupby("__pr_detail_key", dropna=False).agg(
        **{
            "PO Qty Linked": ("__po_qty", "sum"),
            "PO Documents": ("__po_number", lambda x: " | ".join(dict.fromkeys(v for v in x if v))),
            "PO Statuses": ("__po_status_text", lambda x: " | ".join(dict.fromkeys(str(v).strip() for v in x if str(v).strip()))),
        }
    ).reset_index()
    return agg


@st.cache_data(ttl=1800, show_spinner=False)
def _load_sales_orders_api_for_pricing(end_date_val) -> pd.DataFrame:
    """Ambil seluruh SO detail untuk lookup harga PR Balance.

    get_api_data_new() sudah pagination penuh. Horizon dimulai 2024 karena PR 2026
    dapat mereferensikan SO yang dibuat lebih lama. Jika dated fetch kosong, coba all-time.
    """
    df = get_api_data_new(
        "sales-orders",
        source="erp",
        start_date=date(2024, 1, 1),
        end_date=end_date_val,
        silent=True,
        per_page=200,
        max_pages=500,
    )

    if df is None or df.empty:
        df = get_api_data_new(
            "sales-orders",
            source="erp",
            start_date=None,
            end_date=None,
            silent=True,
            per_page=200,
            max_pages=500,
        )

    return df.reset_index(drop=True) if df is not None else pd.DataFrame()



def _normalize_match_text(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
        .str.replace(r"\s+", " ", regex=True)
    )


def _build_sales_order_pricing_maps_api(so_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Siapkan beberapa key lookup agar API tidak bergantung pada satu bentuk ID saja.

    Urutan kekuatan key:
      1) SO detail ID
      2) SO number + product identifier
      3) SO number + normalized item name (hanya bila unik)
    """
    if so_df is None or so_df.empty:
        return {"detail": pd.DataFrame(), "so_product": pd.DataFrame(), "so_name": pd.DataFrame()}

    so = so_df.copy()
    so["__so_detail_key"] = _api_clean_key(_api_first_series(so, [
        "item_id", "so_detail_id", "item_so_detail_id", "item_sales_order_detail_id"
    ], ""))
    so["__so_number_key"] = _api_clean_key(_api_first_series(so, [
        "transaction_number", "so_transaction_number", "number"
    ], ""))
    so["__so_product_key"] = _api_clean_key(_api_first_series(so, [
        "item_product_id", "item_product_code", "product_id", "product_code"
    ], ""))
    so["__so_name_key"] = _normalize_match_text(_api_first_series(so, [
        "item_item_name", "item_name", "name", "product_name"
    ], ""))

    so["__so_price"] = _api_numeric(so, [
        "item_price", "price", "item_sell_price", "item_selling_price", "selling_price"
    ], float("nan"))
    so["__so_discount"] = _api_numeric(so, [
        "item_discount", "discount", "discount_percentage"
    ], 0.0)
    so["__so_tax1_percentage"] = _api_numeric(so, [
        "item_tax1_percentage", "item_tax1_val", "tax1_percentage", "tax1_val", "tax1_value"
    ], 0.0)
    so["__so_tax2_percentage"] = _api_numeric(so, [
        "item_tax2_percentage", "item_tax2_val", "tax2_percentage", "tax2_val", "tax2_value"
    ], 0.0)

    cols = ["__so_price", "__so_discount", "__so_tax1_percentage", "__so_tax2_percentage"]
    valid = so[so["__so_price"].notna()].copy()

    detail = valid[valid["__so_detail_key"].ne("")][["__so_detail_key"] + cols].drop_duplicates(
        "__so_detail_key", keep="last"
    )

    sp = valid[valid["__so_number_key"].ne("") & valid["__so_product_key"].ne("")].copy()
    if not sp.empty:
        sp["__cnt"] = sp.groupby(["__so_number_key", "__so_product_key"])["__so_price"].transform("size")
        sp = sp[sp["__cnt"].eq(1)][["__so_number_key", "__so_product_key"] + cols].drop_duplicates(
            ["__so_number_key", "__so_product_key"], keep="last"
        )
        sp = sp.rename(columns={"__so_product_key": "__pr_product_key"})

    sn = valid[valid["__so_number_key"].ne("") & valid["__so_name_key"].ne("")].copy()
    if not sn.empty:
        sn["__cnt"] = sn.groupby(["__so_number_key", "__so_name_key"])["__so_price"].transform("size")
        sn = sn[sn["__cnt"].eq(1)][["__so_number_key", "__so_name_key"] + cols].drop_duplicates(
            ["__so_number_key", "__so_name_key"], keep="last"
        )

    return {"detail": detail, "so_product": sp, "so_name": sn}


def _attach_so_pricing_api(work: pd.DataFrame, pr_df: pd.DataFrame, end_date_val) -> pd.DataFrame:
    """Resolve authoritative SO pricing tanpa pernah fallback ke harga PR.

    Fungsi ini sengaja memakai beberapa key karena payload API antar endpoint dapat memakai
    internal ID, external product code, atau tidak expose SO detail ID pada sebagian row.
    """
    out = work.copy()
    idx = out.index

    # Canonical lookup keys on PR rows.
    out["__so_detail_key"] = _api_clean_key(_api_first_series(out, [
        "__so_detail_key", "item_so_detail_id", "so_detail_id", "item_sales_order_detail_id"
    ], ""))
    out["__so_number_key"] = _api_clean_key(_api_first_series(out, [
        "No. SO", "item_so_transaction_number", "so_transaction_number", "item_sales_order_number"
    ], ""))
    out["__pr_product_key"] = _api_clean_key(_api_first_series(out, [
        "item_product_id", "item_product_code", "product_id", "product_code"
    ], ""))
    out["__pr_name_key"] = _normalize_match_text(_api_first_series(out, [
        "item_item_name", "item_name", "Nama Barang"
    ], ""))

    # Direct SO fields carried by purchase-requests are strongest when present.
    out["__so_price"] = _api_numeric(out, [
        "item_so_price", "item_so_detail_price", "item_sales_order_price",
        "item_sales_order_detail_price", "item_sell_price", "item_selling_price", "so_price"
    ], float("nan"))
    out["__so_discount"] = _api_numeric(out, [
        "item_so_discount", "item_so_detail_discount", "item_sales_order_discount", "so_discount"
    ], float("nan"))
    out["__so_tax1_percentage"] = _api_numeric(out, [
        "item_so_tax1_percentage", "item_so_tax1_val", "item_sales_order_tax1_percentage", "so_tax1_percentage"
    ], float("nan"))
    out["__so_tax2_percentage"] = _api_numeric(out, [
        "item_so_tax2_percentage", "item_so_tax2_val", "item_sales_order_tax2_percentage", "so_tax2_percentage"
    ], float("nan"))
    out["SO Price Match Method"] = pd.Series("", index=out.index, dtype="object")
    direct_mask = out["__so_price"].notna()
    out.loc[direct_mask, "SO Price Match Method"] = "PURCHASE_REQUEST_DIRECT_SO_FIELD"

    so = _load_sales_orders_api_for_pricing(end_date_val)
    maps = _build_sales_order_pricing_maps_api(so)
    price_cols = ["__so_price", "__so_discount", "__so_tax1_percentage", "__so_tax2_percentage"]

    def fill_from_map(frame: pd.DataFrame, keys: list[str], method: str):
        nonlocal out
        if frame is None or frame.empty:
            return
        unresolved = out["__so_price"].isna()
        if not unresolved.any():
            return
        left = out.loc[unresolved, keys].copy()
        left["__orig_index"] = left.index
        merged = left.merge(frame, how="left", on=keys, suffixes=("", "__lookup"))
        merged = merged.set_index("__orig_index")
        lookup_price = pd.to_numeric(merged.get("__so_price"), errors="coerce")
        hit = lookup_price.notna()
        if not hit.any():
            return
        hit_idx = lookup_price.index[hit]
        for c in price_cols:
            vals = pd.to_numeric(merged.loc[hit_idx, c], errors="coerce") if c in merged.columns else pd.Series(index=hit_idx, dtype="float64")
            out.loc[hit_idx, c] = vals
        out.loc[hit_idx, "SO Price Match Method"] = method

    fill_from_map(maps.get("detail"), ["__so_detail_key"], "SO_DETAIL_ID")
    fill_from_map(maps.get("so_product"), ["__so_number_key", "__pr_product_key"], "SO_NUMBER_PRODUCT")

    # Rename product key for the right-side map before fallback merge.
    sn = maps.get("so_name")
    if sn is not None and not sn.empty:
        sn = sn.rename(columns={"__so_name_key": "__pr_name_key"})
    fill_from_map(sn, ["__so_number_key", "__pr_name_key"], "SO_NUMBER_ITEM_NAME")

    out["SO Price Found"] = pd.to_numeric(out["__so_price"], errors="coerce").notna()
    out.loc[~out["SO Price Found"], "SO Price Match Method"] = "UNRESOLVED"
    return out

@st.cache_data(ttl=300, show_spinner=False)
def load_pr_balance_historical_api(
    end_date_val,
    pr_df: pd.DataFrame | None = None,
    po_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Historical / AS-OF PR Balance berbasis ERP API.

    Disamakan dengan file PostgreSQL:
      - grain PR detail dari /api/purchase-requests;
      - PR sejak 1-Jan-2026 sampai end_date;
      - status PR = status pada cutoff (nested history bila tersedia, fallback milestone);
      - PO qty = cumulative PO detail sampai cutoff, status 1/2/3/4;
      - closed qty current hanya berlaku bila detail update <= cutoff;
      - outstanding = PR qty - PO qty as-of - closed qty as-of;
      - hanya outstanding > 0 dan PR terhubung SO;
      - nominal = outstanding x net selling price SO (price-discount+Tax1+Tax2);
      - TIDAK menggunakan /dashboard/pr-balance sebagai angka final.
    """
    cutoff = pd.Timestamp(end_date_val).date()
    cutoff_next = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)

    # Reuse purchase-requests yang sudah di-fetch oleh main() bila tersedia.
    # Ini menghilangkan fetch kedua untuk dataset yang sama.
    pr = pr_df.copy() if pr_df is not None else get_api_data_new(
        "purchase-requests",
        source="erp",
        start_date=PR_BALANCE_START_DATE,
        end_date=cutoff,
    )
    if pr is None or pr.empty:
        return pd.DataFrame(columns=[
            "No. PR", "transaction_number", "transaction_date", "Status", "PIC Procurement",
            "No. SO", "PR Qty", "PO Qty", "Qty Closed", "Balance Qty", "Balance Type", "Nominal"
        ])

    work = pr.copy()
    work["transaction_number"] = _api_clean_key(_api_first_series(work, ["transaction_number"], ""))
    work["transaction_date"] = pd.to_datetime(_api_first_series(work, ["transaction_date", "date", "created_at"], pd.NaT), errors="coerce")
    work = work[
        work["transaction_number"].ne("")
        & work["transaction_date"].notna()
        & work["transaction_date"].ge(pd.Timestamp(PR_BALANCE_START_DATE))
        & work["transaction_date"].lt(cutoff_next)
    ].copy()

    # Exclude internal/non-production PR SEBELUM history dipanggil.
    # Output bisnis sama dengan exclude_pr_numbers() di main, tetapi request history berkurang.
    number_upper = work["transaction_number"].str.upper()
    work = work.loc[
        ~number_upper.str.startswith(("SIBIMA.PR.", "SIBPRGA"), na=False)
        & ~number_upper.eq("#N/A")
    ].copy()

    # one row per PR detail
    work["__pr_detail_key"] = _api_clean_key(_api_first_series(work, ["item_id", "item_pr_detail_id", "pr_detail_id"], ""))
    fallback = (
        work["transaction_number"] + "|"
        + _api_clean_key(_api_first_series(work, ["item_product_id", "item_product_code"], "")) + "|"
        + _api_clean_key(_api_first_series(work, ["item_item_name", "item_name"], "")).str.lower() + "|"
        + _api_numeric(work, ["item_quantity", "quantity"], 0.0).astype(str)
    )
    work["__row_key"] = work["__pr_detail_key"].where(work["__pr_detail_key"].ne(""), fallback)
    work = work.drop_duplicates("__row_key", keep="last").copy()

    # PR Balance wajib terhubung ke SO Detail. Filter ini dilakukan SEBELUM history
    # agar PR tanpa SO tidak menghasilkan request history yang sia-sia.
    work["__so_detail_key"] = _api_clean_key(_api_first_series(work, [
        "item_so_detail_id", "so_detail_id", "item_sales_order_detail_id"
    ], ""))
    work["No. SO"] = _api_clean_key(_api_first_series(work, [
        "item_so_transaction_number", "so_transaction_number", "item_sales_order_number"
    ], ""))
    work = work.loc[work["__so_detail_key"].ne("")].copy()

    # Status historical/current AS-OF cutoff. Hanya dokumen candidate yang dipanggil ke history.
    candidate_pr_numbers = tuple(sorted(work["transaction_number"].dropna().astype(str).unique().tolist()))
    status_map = _build_pr_status_asof_api(pr, cutoff, transaction_numbers=candidate_pr_numbers)
    work["__pr_number"] = work["transaction_number"]
    work = work.merge(status_map, how="left", on="__pr_number")
    current_status = _api_first_series(work, ["status", "status_description", "Status"], "").map(_normalize_pr_status_for_balance)
    work["Status"] = work.get("Status As Of", pd.Series("", index=work.index)).fillna("")
    work.loc[work["Status"].eq(""), "Status"] = current_status.loc[work["Status"].eq("")]

    # Status membership sudah diselesaikan sepenuhnya oleh _build_pr_status_asof_api().
    # Historical cutoff tetap membutuhkan existence guard agar transaksi backdated yang
    # sebenarnya belum ada pada cutoff tidak ikut snapshot lama.
    if cutoff < date.today():
        # Historical existence guard.
        # transaction_date dapat dibackdate. Jangan masukkan PR ke snapshot lama bila bukti workflow
        # pertama (history endpoint atau milestone aktual) baru terjadi setelah cutoff.
        cutoff_ts = pd.Timestamp(cutoff).normalize() + pd.Timedelta(days=1)
        first_hist = pd.to_datetime(work.get("First History Timestamp", pd.Series(pd.NaT, index=work.index)), errors="coerce")

        workflow_candidates = []
        for candidate in [
            "created_at", "date_draft", "date_need_approve", "need_approve_at", "date_need_approved",
            "date_approved", "approved_at", "approved_date",
            "date_inprogress", "date_in_progress", "inprogress_at", "in_progress_at",
            "date_complete", "date_completed", "completed_at", "complete_at",
        ]:
            if candidate in work.columns:
                workflow_candidates.append(pd.to_datetime(work[candidate], errors="coerce"))

        first_workflow = first_hist.copy()
        if workflow_candidates:
            workflow_frame = pd.concat(workflow_candidates, axis=1)
            milestone_first = workflow_frame.min(axis=1)
            first_workflow = pd.concat([first_hist.rename("history"), milestone_first.rename("milestone")], axis=1).min(axis=1)

        work["First Workflow Timestamp"] = first_workflow
        history_state = work.get("History Endpoint State", pd.Series("", index=work.index)).fillna("").astype(str)
        proven_after = history_state.eq("HISTORY_AFTER_CUTOFF_ONLY") | (
            first_workflow.notna() & first_workflow.ge(cutoff_ts)
        )
        work["Historical Existence Check"] = "EXISTS_BY_CUTOFF"
        work.loc[proven_after, "Historical Existence Check"] = "NOT_YET_EXISTING_AT_CUTOFF"
        work = work.loc[~proven_after].copy()

    work = work[work["Status"].isin(["Need Approve", "Approved", "In Progress"])].copy()

    # Cumulative PO until cutoff; do NOT use current item_po_quantity as historical truth.
    po = po_df.copy() if po_df is not None else _load_purchase_orders_until_api(cutoff)
    po_map = _build_po_qty_by_pr_detail_api(po, cutoff)
    if not po_map.empty:
        work = work.merge(po_map, how="left", on="__pr_detail_key")
    else:
        work["PO Qty Linked"] = 0.0
        work["PO Documents"] = ""
        work["PO Statuses"] = ""

    work["PO Qty Linked"] = pd.to_numeric(work.get("PO Qty Linked", 0), errors="coerce").fillna(0.0).clip(lower=0)

    pr_qty = _api_numeric(work, ["item_quantity", "quantity"], 0.0).clip(lower=0)

    # Historical closed qty fallback = same idea as repaired PostgreSQL file.
    closed_current = _api_numeric(work, [
        "item_closed_quantity", "item_closed_qty", "item_close_quantity", "item_qty_closed",
        "closed_quantity", "close_quantity",
    ], 0.0).clip(lower=0)
    detail_updated = pd.to_datetime(_api_first_series(work, [
        "item_updated_at", "item_modified_at", "item_last_updated_at", "item_created_at"
    ], pd.NaT), errors="coerce")

    has_detail_timestamp = detail_updated.notna()
    closed_asof = pd.Series(0.0, index=work.index)
    closed_asof.loc[has_detail_timestamp & detail_updated.lt(cutoff_next)] = closed_current.loc[
        has_detail_timestamp & detail_updated.lt(cutoff_next)
    ]
    # Untuk cutoff hari ini/masa kini, bila API tidak expose detail timestamp, current value aman dipakai.
    if cutoff >= date.today():
        closed_asof.loc[~has_detail_timestamp] = closed_current.loc[~has_detail_timestamp]

    balance_qty = (pr_qty - work["PO Qty Linked"] - closed_asof).clip(lower=0)
    outstanding = pr_qty.gt(0) & balance_qty.gt(0)
    work = work.loc[outstanding].copy()
    pr_qty = pr_qty.loc[work.index]
    closed_asof = closed_asof.loc[work.index]
    balance_qty = balance_qty.loc[work.index]

    # SO pricing authoritative; no fallback ke harga PR. Resolve dengan beberapa key API.
    work = _attach_so_pricing_api(work, pr, cutoff)

    price = pd.to_numeric(work.get("__so_price"), errors="coerce")
    discount = pd.to_numeric(work.get("__so_discount", 0), errors="coerce").fillna(0.0)
    tax1 = pd.to_numeric(work.get("__so_tax1_percentage", 0), errors="coerce").fillna(0.0)
    tax2 = pd.to_numeric(work.get("__so_tax2_percentage", 0), errors="coerce").fillna(0.0)

    price_found = price.notna()
    # Tetap 0 untuk kalkulasi unresolved agar app tidak crash, tetapi row ditandai jelas
    # dan diagnostic di UI memperingatkan bahwa nominal belum parity.
    price = price.fillna(0.0)
    base = price * (1 - discount / 100.0)
    net_unit = base + (base * tax1 / 100.0) + (base * tax2 / 100.0)
    nominal = balance_qty * net_unit

    work["No. PR"] = work["transaction_number"]
    work["Tgl. PR"] = work["transaction_date"]
    work["PR Qty"] = pr_qty
    work["Qty Permintaan (PR)"] = pr_qty
    work["PO Qty"] = work["PO Qty Linked"]
    work["Effective PO Qty"] = work["PO Qty Linked"]
    work["Qty Sudah PO"] = work["PO Qty Linked"]
    work["Qty Closed"] = closed_asof
    work["Balance Qty"] = balance_qty
    work["Qty Outstanding"] = balance_qty
    work["Balance Type"] = "NO PO"
    work.loc[work["PO Qty Linked"].gt(0), "Balance Type"] = "PARTIAL PO"
    work["Unit Price"] = price
    work["Harga Jual"] = price
    work["Net Unit Price"] = net_unit
    work["Nominal"] = nominal
    work["SO Price Found"] = price_found
    work["Price Source"] = work.get("SO Price Match Method", pd.Series("UNRESOLVED", index=work.index))
    work["SO Base Price"] = price
    work["SO Discount %"] = discount
    work["SO Tax1 %"] = tax1
    work["SO Tax2 %"] = tax2
    work["PR Balance Start Date"] = pd.Timestamp(PR_BALANCE_START_DATE)
    work["PR Balance End Date"] = pd.Timestamp(cutoff)
    work["PR Balance Snapshot Date"] = pd.Timestamp(cutoff)
    work["PR Balance Source"] = "API purchase-requests + PR history endpoint + purchase-orders + sales-orders AS-OF"

    work["PIC Procurement"] = _api_first_series(work, [
        "item_pic_procurement_name", "pic_procurement_name", "PIC Procurement"
    ], "").fillna("").astype(str).str.strip()
    work["Nama Barang"] = _api_first_series(work, ["item_item_name", "item_name", "Nama Barang"], "")
    work["ID Produk"] = _api_first_series(work, ["item_product_id", "item_product_code", "ID Produk"], "")
    work["Product ID"] = work["ID Produk"]
    work["Item Name"] = work["Nama Barang"]
    work["SO Number"] = work["No. SO"]
    work["No. PO"] = work.get("PO Documents", pd.Series("", index=work.index)).fillna("")
    work["PO Quantity Source"] = "PURCHASE_ORDERS_API_AS_OF"
    work["Closed Quantity Source"] = "PR_DETAIL_CURRENT_IF_UPDATED_BEFORE_CUTOFF"

    # Current status diagnostic must be derived AFTER all merges/filters so index alignment
    # cannot attach another PR's status to this row. Never use this field for historical membership.
    work["Current Status"] = _api_first_series(
        work, ["status", "status_description"], ""
    ).map(_normalize_pr_status_for_balance)

    return work.drop(columns=["__row_key", "__pr_number"], errors="ignore").reset_index(drop=True)


def load_all_data_new(start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    # Mapping endpoint baru sesuai API kamu
    endpoint_map_new = {
        "pr": ("purchase-requests",{}),
        "po": ("purchase-orders", {"date" : "transaction_date"}),
        "do": ("delivery-orders",{})
    }

    result_new = {}
    for key, (endpoint, rename_map_new) in endpoint_map_new.items():
        df = get_api_data_new(endpoint, source="erp", start_date=start_date, end_date=end_date)

        if not df.empty:
            df = df.rename(columns=rename_map_new)
            df = safe_to_datetime(df, "transaction_date")
        result_new[key] = df

    return result_new





NPR_STATUS_MAP = {
    0: "Request",
    1: "Process",
    2: "Complete",
}

NPR_VALID_STATUSES = tuple(NPR_STATUS_MAP.values())

def _normalize_npr_text_status(value):
    if value is None or pd.isna(value):
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # Numeric status from x4_product_requests / API full NPR
    try:
        n = int(float(s))
        if n in NPR_STATUS_MAP:
            return NPR_STATUS_MAP[n]
    except Exception:
        pass

    key = " ".join(s.casefold().split())
    text_map = {
        "request": "Request",
        "requested": "Request",
        "process": "Process",
        "processed": "Process",
        "proses": "Process",
        "in process": "Process",
        "complete": "Complete",
        "completed": "Complete",
        "selesai": "Complete",
    }
    return text_map.get(key, s)

def _npr_empty_frame():
    return pd.DataFrame(columns=[
        "No. Transaksi", "Nama NPR", "Sales", "Status",
        "Tanggal Deadline", "Catatan", "User Input", "transaction_date"
    ])



@st.cache_data(ttl=300, show_spinner=False)
def load_full_npr_from_api(start_date=None, end_date=None) -> pd.DataFrame:
    """
    Full NPR from ERP API. outstanding-npr is intentionally not used.

    Candidate endpoint order is tried safely because the exact full-NPR route
    can differ by deployment. Payload is accepted only when it contains
    SIBNPR transactions and statuses mapped to Request/Process/Complete.
    """
    candidates = [
        "product-requests",
        "new-product-requests",
        "new-product-request",
        "product-request",
        "nprs",
        "npr",
    ]

    for endpoint in candidates:
        try:
            raw = get_api_data_new(
                endpoint,
                source="erp",
                start_date=start_date,
                end_date=end_date,
                silent=True,
            )
        except Exception:
            continue

        if raw is None or raw.empty:
            continue

        cols = {str(c).casefold(): c for c in raw.columns}

        def col(*names):
            for n in names:
                c = cols.get(str(n).casefold())
                if c is not None:
                    return c
            return None

        no_col = col("transaction_number", "no_npr", "npr_number", "number", "code")
        status_col = col("status_description", "status_name", "status")
        if not no_col or not status_col:
            continue

        out = pd.DataFrame(index=raw.index)
        out["No. Transaksi"] = raw[no_col].fillna("").astype(str).str.strip()
        out["Nama NPR"] = raw[col("name", "nama_npr", "npr_name", "title", "description")].fillna("").astype(str).str.strip() if col("name", "nama_npr", "npr_name", "title", "description") else ""

        pic_col = col("pic_name", "pic", "penanggung_jawab", "responsible_name", "assignee_name")
        if pic_col:
            out["Sales"] = raw[pic_col].fillna("").astype(str).str.strip()
        else:
            pic_id_col = col("pic_id")
            out["Sales"] = raw[pic_id_col].fillna("").astype(str).str.strip() if pic_id_col else ""

        out["Status"] = raw[status_col].map(_normalize_npr_text_status)

        deadline_col = col("due_date", "deadline", "deadline_date", "tanggal_deadline")
        out["Tanggal Deadline"] = pd.to_datetime(raw[deadline_col], errors="coerce") if deadline_col else pd.NaT

        notes_col = col("notes", "note", "catatan", "remarks", "remark")
        out["Catatan"] = raw[notes_col].fillna("").astype(str).str.strip() if notes_col else ""

        creator_col = col("created_by_name", "user_input", "creator_name", "created_by", "user_name")
        out["User Input"] = raw[creator_col].fillna("").astype(str).str.strip() if creator_col else ""

        date_col = col("created_at", "transaction_date", "date", "request_date")
        out["transaction_date"] = pd.to_datetime(raw[date_col], errors="coerce") if date_col else pd.NaT

        out = out[
            out["No. Transaksi"].str.upper().str.startswith("SIBNPR", na=False)
            & out["Status"].isin(NPR_VALID_STATUSES)
        ].copy()

        if out.empty:
            continue

        out = (
            out.sort_values("transaction_date", na_position="last")
            .drop_duplicates("No. Transaksi", keep="last")
            .reset_index(drop=True)
        )
        return out

    return _npr_empty_frame()


# =========================================================
# 7) FILTERS & TRANSFORM
# =========================================================

def exclude_pr_numbers(df: pd.DataFrame, transaction_col: str) -> pd.DataFrame:
    """
    Exclude nomor PR yang tidak boleh masuk dashboard production:
    - prefix SIBIMA.PR.
    - prefix SIBPRGA
    - exact value #N/A

    Case-insensitive. Fungsi ini murni bekerja pada dataframe hasil API.
    """
    if df is None or df.empty or transaction_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    working = df.copy()
    number = (
        working[transaction_col]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    excluded_prefixes = ("SIBIMA.PR.", "SIBPRGA")
    excluded_exact = {"#N/A"}

    mask_excluded = (
        number.str.startswith(excluded_prefixes, na=False)
        | number.isin(excluded_exact)
    )

    return working.loc[~mask_excluded].copy()


def exclude_close_from_pr_balance(df: pd.DataFrame) -> pd.DataFrame:
    """
    PR berstatus Close bukan outstanding PR sehingga tidak masuk PR Balance.

    Sumber status tetap dari payload endpoint API pr-balance.
    Tidak ada query PostgreSQL / DB pada fungsi ini.
    """
    if df is None or df.empty or "Status" not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    working = df.copy()
    status = (
        working["Status"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    return working.loc[~status.eq("close")].copy()


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
        working[col] = (
            working[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        null_like = working[col].str.lower().isin({"", "nan", "none", "null", "<na>"})
        working.loc[~null_like, col] = working.loc[~null_like, col].str.upper()

        # Canonical alias: FAQIH RAMADHAN digabung ke FAQIH.
        working.loc[working[col].eq("FAQIH RAMADHAN"), col] = "FAQIH"
        working.loc[null_like, col] = "Unassigned"

        if col == "PIC Procurement":
            unassigned_names = {
                "LUBNA",
                "MIRZA",
                "TRIAS",
                "ADIN",
                "ALFAN",
                "KEYACCOUNT.REG02@SIBIMA.ID",
            }

            mask = (
                working[col]
                .str.upper()
                .isin(unassigned_names)
            )

            working.loc[mask, col] = "Unassigned"

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
    Hitung aging dengan aman.

    - Selalu mengembalikan kolom ``Aging`` meskipun dataframe kosong atau
      ``date_col`` tidak tersedia.
    - Prioritas tanggal dapat menggunakan approved / inprogress / complete.
    - Jika tanggal prioritas tidak tersedia, aging tetap menggunakan
      hari ini - transaction_date.
    """
    working = df.copy() if df is not None else pd.DataFrame()

    # Penting: caller berikutnya memanggil categorize_aging(), jadi kolom ini
    # harus selalu tersedia agar tidak terjadi KeyError: 'Aging'.
    if "Aging" not in working.columns:
        working["Aging"] = pd.Series(pd.NA, index=working.index, dtype="Int64")

    if working.empty or date_col not in working.columns:
        return working

    working = safe_to_datetime(working, date_col)

    # safe_to_datetime aman jika kolom tidak ada, namun kita tetap membuat
    # placeholder supaya blok preferensi di bawah selalu aman.
    for c in ["date_approved", "date_inprogress", "date_complete"]:
        if c not in working.columns:
            working[c] = pd.NaT
        working = safe_to_datetime(working, c)

    today = pd.Timestamp.today().normalize()

    base_date = pd.to_datetime(working[date_col], errors="coerce")
    working["Aging"] = (today - base_date).dt.days.astype("Int64")

    prefer_map = {
        "approved": "date_approved",
        "inprogress": "date_inprogress",
        "complete": "date_complete",
    }
    prefer_col = prefer_map.get(str(prefer).strip().lower())

    if prefer_col and prefer_col in working.columns:
        prefer_date = pd.to_datetime(working[prefer_col], errors="coerce")
        mask = prefer_date.notna() & base_date.notna()
        if mask.any():
            working.loc[mask, "Aging"] = (
                prefer_date.loc[mask] - base_date.loc[mask]
            ).dt.days.astype("Int64")

    return working


def categorize_aging(df: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan kategori aging tanpa gagal saat dataframe/kolom Aging kosong."""
    working = df.copy() if df is not None else pd.DataFrame()

    if "Aging" not in working.columns:
        working["Aging"] = pd.Series(pd.NA, index=working.index, dtype="Int64")

    aging_numeric = pd.to_numeric(working["Aging"], errors="coerce")

    bins = [-1, 30, 60, 90, float("inf")]
    labels = ["0-30 hari", "31-60 hari", "61-90 hari", ">90 hari"]

    working["Aging Category"] = pd.cut(
        aging_numeric,
        bins=bins,
        labels=labels,
        right=True,
    )
    return working


def render_aging_bar(df: pd.DataFrame, doc_col: str, chart_key: str = "aging_bar"):
    if df.empty or "Aging Category" not in df.columns or doc_col not in df.columns:
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


def render_pr_balance_aging_per_pic(df: pd.DataFrame):
    """
    Stacked bar PR Balance per PIC berdasarkan kategori aging.

    Tujuan:
    - menunjukkan workload outstanding per PIC;
    - sekaligus menunjukkan umur backlog;
    - lebih informatif dibanding rata-rata aging saja.
    """
    if (
        df is None
        or df.empty
        or "PIC Procurement" not in df.columns
        or "Aging Category" not in df.columns
        or "No. PR" not in df.columns
    ):
        st.info("Data PR Balance Aging per PIC tidak tersedia.")
        return

    working = assign_unassigned(df, "PIC Procurement")

    # Hanya PR Balance aktif/outstanding.
    working = working[
        ~working["Status"].fillna("").astype(str).str.strip().isin(
            ["Complete", "Draft", "Close"]
        )
    ].copy()

    if working.empty:
        st.info("Tidak ada PR Balance aktif untuk dianalisis.")
        return

    summary = (
        working.groupby(
            ["PIC Procurement", "Aging Category"],
            observed=False,
            dropna=False,
        )["No. PR"]
        .nunique()
        .reset_index(name="Jumlah PR")
    )

    # Buang bucket kosong.
    summary = summary[summary["Jumlah PR"] > 0].copy()

    if summary.empty:
        st.info("Data PR Balance Aging per PIC tidak tersedia.")
        return

    # Urutkan PIC dari backlog paling banyak.
    pic_order = (
        summary.groupby("PIC Procurement", as_index=False)["Jumlah PR"]
        .sum()
        .sort_values("Jumlah PR", ascending=False)["PIC Procurement"]
        .tolist()
    )

    aging_order = [
        "0-30 hari",
        "31-60 hari",
        "61-90 hari",
        ">90 hari",
    ]

    aging_colors = {
        "0-30 hari": "#2F80ED",
        "31-60 hari": "#7ABBEE",
        "61-90 hari": "#FCA27F",
        ">90 hari": "#EB5757",
    }

    fig = px.bar(
        summary,
        x="PIC Procurement",
        y="Jumlah PR",
        color="Aging Category",
        text="Jumlah PR",
        color_discrete_map=aging_colors,
        category_orders={
            "PIC Procurement": pic_order,
            "Aging Category": aging_order,
        },
        title="PR Balance Aging per PIC Procurement",
    )

    # Jumlah tiap bucket di dalam bar.
    fig.update_traces(
        textposition="inside",
        textfont=dict(size=10, color="white"),
    )

    # Tambahkan total PR Balance di atas tiap PIC.
    totals = (
        summary.groupby("PIC Procurement", as_index=False)["Jumlah PR"]
        .sum()
    )

    for _, row in totals.iterrows():
        fig.add_annotation(
            x=row["PIC Procurement"],
            y=row["Jumlah PR"],
            text=f"{int(row['Jumlah PR'])}",
            showarrow=False,
            font=dict(size=12, color="black"),
            yshift=10,
        )

    fig.update_layout(
        barmode="stack",
        uniformtext_mode="hide",
        yaxis_title="Jumlah PR Balance",
        xaxis_title="PIC Procurement",
        legend_title_text="Aging",
        margin=dict(l=40, r=40, t=60, b=100),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key="pr_balance_aging_per_pic",
    )

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
    st.caption("Data source: ERP API only — tanpa PostgreSQL. PR Balance direkonstruksi historical/as-of dari purchase-requests + purchase-orders + sales-orders; PR internal excluded.")

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

    with st.spinner("Mengambil data dashboard..."):
        # =========================================================
        # LAZY LOAD BY DOCUMENT TYPE
        # =========================================================
        # Hanya endpoint yang dibutuhkan oleh jenis dokumen aktif yang dipanggil.
        # Business calculation tidak berubah; ini hanya menghapus request yang sebelumnya
        # selalu dijalankan walaupun user sedang melihat dokumen lain.
        # Skeleton columns menjaga transform existing tetap aman walaupun source lain
        # sengaja tidak di-fetch pada document type yang sedang aktif.
        df_pr = pd.DataFrame(columns=[
            "Nominal", "No. PR", "Status", "PIC Procurement", "transaction_date"
        ])
        df_po = pd.DataFrame(columns=["Nominal", "transaction_date"])
        df_grn = pd.DataFrame(columns=["Nominal", "transaction_date"])
        df_do = pd.DataFrame(columns=[
            "Nominal", "No. DO", "Status", "PIC Procurement", "PIC Purchasing", "transaction_date"
        ])
        df_npr = pd.DataFrame(columns=[
            "No. Transaksi", "Status", "Sales", "transaction_date"
        ])
        df_pr_final = pd.DataFrame(columns=[
            "transaction_number", "transaction_date", "Status", "PIC Procurement",
            "item_price", "item_discount", "item_quantity",
            "item_tax1_percentage", "item_tax2_percentage"
        ])
        df_do_final = pd.DataFrame(columns=[
            "transaction_number", "transaction_date", "Status", "PIC Procurement",
            "item_price", "item_discount", "item_quantity",
            "item_tax1_percentage", "item_tax2_percentage"
        ])

        if selected_doc_type == "PR":
            # Purchase Requests diambil SATU KALI dari 1-Jan-2026 s/d cutoff lalu direuse:
            # - PR Balance memakai seluruh horizon kumulatif
            # - Total PR memakai realization filter start_date..end_date di bawah
            pr_all = get_api_data_new(
                "purchase-requests",
                source="erp",
                start_date=PR_BALANCE_START_DATE,
                end_date=end_date,
            )
            df_pr = load_pr_balance_historical_api(
                end_date,
                pr_df=pr_all,
            )
            df_pr_final = pr_all.copy()

        elif selected_doc_type == "DO":
            # DO balance + DO transaction saja. Tidak fetch PR/PO/SO/NPR.
            df_do = get_api_data_old(
                "do-balance",
                source="outstanding",
                start_date=None,
                end_date=end_date,
            )
            if not df_do.empty:
                df_do = df_do.rename(columns={"Tgl. DO": "transaction_date"})
                df_do = safe_to_datetime(df_do, "transaction_date")

            df_do_final = get_api_data_new(
                "delivery-orders",
                source="erp",
                start_date=start_date,
                end_date=end_date,
            )

        elif selected_doc_type == "NPR":
            # Full NPR saja.
            df_npr = load_full_npr_from_api(
                start_date=start_date,
                end_date=end_date,
            )

        else:
            # PUR belum memiliki source aktif pada script existing.
            pass

    # ---------- ASSIGN / CLEAN DATAFRAME ----------
    if selected_doc_type == "PR":
        df_pr = exclude_pr_numbers(df_pr, "No. PR")
        df_pr_final = exclude_pr_numbers(df_pr_final, "transaction_number")

    # Untuk NPR, data sudah di-load di lazy-load block; jangan fetch kedua kali.
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

    # =====================================================
    # NORMALISASI PIC - CASE INSENSITIVE + ALIAS
    # =====================================================
    # FAQIH dan FAQIH RAMADHAN digabung menjadi canonical PIC "FAQIH".
    # Dijalankan sebelum dropdown/filter/groupby agar seluruh metric dan export konsisten.
    df_pr = normalize_pic_columns(df_pr)
    df_pr_final = normalize_pic_columns(df_pr_final)
    df_do = normalize_pic_columns(df_do)
    df_do_final = normalize_pic_columns(df_do_final)

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
    df_npr_f = df_npr_f[df_npr_f["Status"].isin(NPR_VALID_STATUSES)].copy()
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


    # TOTAL PR STRICT PARITY: one canonical formula + one PR-detail grain.
    df_pr_final_real, total_pr = calculate_total_pr_strict(df_pr_final_real)

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

    # =========================================================
    # PR BALANCE API PARITY DIAGNOSTIC
    # =========================================================
    with st.expander("🧪 Diagnostic PR Balance API Historical", expanded=False):
        if df_pr_f.empty:
            st.info("PR Balance kosong pada cutoff ini.")
        else:
            so_found = df_pr_f.get("SO Price Found", pd.Series(False, index=df_pr_f.index)).fillna(False).astype(bool)
            resolved_rows = int(so_found.sum())
            unresolved_rows = int((~so_found).sum())
            resolved_nominal = float(pd.to_numeric(df_pr_f.loc[so_found, "Nominal"], errors="coerce").fillna(0).sum()) if resolved_rows else 0.0
            unresolved_balance_qty = float(pd.to_numeric(df_pr_f.loc[~so_found, "Balance Qty"], errors="coerce").fillna(0).sum()) if unresolved_rows else 0.0

            q1, q2, q3, q4 = st.columns(4)
            q1.metric("PR Balance Items", f"{len(df_pr_f):,}")
            q2.metric("SO Price Resolved", f"{resolved_rows:,}")
            q3.metric("SO Price Unresolved", f"{unresolved_rows:,}")
            q4.metric("Resolved Nominal", f"Rp {resolved_nominal:,.0f}".replace(",", "."))

            if unresolved_rows > 0:
                st.error(
                    f"Ada {unresolved_rows:,} item PR Balance yang belum berhasil menemukan harga SO. "
                    "Nominal card belum dapat dianggap parity dengan PostgreSQL sampai angka ini = 0."
                )
                unresolved = df_pr_f.loc[~so_found].copy()
                cols = [c for c in [
                    "No. PR", "Tgl. PR", "No. SO", "__so_detail_key", "ID Produk", "Nama Barang",
                    "PR Qty", "PO Qty", "Qty Closed", "Balance Qty", "Status",
                    "SO Price Match Method", "Current Status", "Status As Of", "Status As Of Source"
                ] if c in unresolved.columns]
                st.caption(f"Total Balance Qty pada item unresolved: {unresolved_balance_qty:,.2f}")
                st.dataframe(unresolved[cols].head(500), use_container_width=True, hide_index=True)

            if "SO Price Match Method" in df_pr_f.columns:
                match_summary = (
                    df_pr_f.groupby("SO Price Match Method", dropna=False)
                    .agg(
                        Items=("No. PR", "size"),
                        PR_Documents=("No. PR", "nunique"),
                        Nominal=("Nominal", "sum"),
                    )
                    .reset_index()
                    .sort_values("Items", ascending=False)
                )
                st.markdown("**SO pricing resolution method**")
                st.dataframe(match_summary, use_container_width=True, hide_index=True)

            membership_diag = pd.DataFrame([{
                "Report End": report_end_date,
                "PR Balance Documents": safe_unique_count(df_pr_f, "No. PR"),
                "PR Balance Items": len(df_pr_f),
                "PR Balance Nominal": float(total_pr_unpr),
                "SO Price Resolved Items": resolved_rows,
                "SO Price Unresolved Items": unresolved_rows,
                "NO PO Items": int((df_pr_f.get("Balance Type", pd.Series(index=df_pr_f.index, dtype="object")) == "NO PO").sum()),
                "PARTIAL PO Items": int((df_pr_f.get("Balance Type", pd.Series(index=df_pr_f.index, dtype="object")) == "PARTIAL PO").sum()),
            }])
            st.download_button(
                "⬇️ Download Diagnostic PR Balance API.xlsx",
                data=(lambda: (
                    (lambda out: (
                        (lambda writer: None)(None), out
                    ))(BytesIO())
                ))() if False else to_excel_bytes(df_pr_f, sheet_name="PR_BALANCE_ROWS"),
                file_name=f"Diagnostic_PR_Balance_API_{pd.Timestamp(report_end_date).strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_pr_balance_api_diag",
                use_container_width=True,
            )

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
    df_pr_valid = df_pr_f[
    ~df_pr_f["Status"].isin(["Complete", "Draft", "Close"])
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

            #with st.container(border=True):
                #st.subheader("⏳ Distribusi Aging PR Balance")
                #render_aging_bar(df_pr_valid, "No. PR", chart_key="aging_pr_outstanding")

                pic_aging_summary = summarize_pic_aging(df_pr_valid, "PIC Procurement", "No. PR")
                pic_aging_summary_final = summarize_pic_aging(df_pr_final_valid, "PIC Procurement", "transaction_number")

            with st.container(border=True):
                st.subheader("👥 Rata-rata Proses PR")
                #st.dataframe(pic_aging_summary, use_container_width=True, hide_index=True)
                render_pic_aging_bar(pic_aging_summary_final)

            with st.container(border=True):
                st.subheader("📊 PR Balance Aging per PIC Procurement")
                st.caption(
                    "Menampilkan jumlah PR Balance aktif per PIC dan distribusi umurnya "
                    "(0-30, 31-60, 61-90, >90 hari). Total backlog ditampilkan di atas setiap PIC."
                )
                render_pr_balance_aging_per_pic(df_pr_valid)

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
                render_sla_gauge(df_pr_final_valid, threshold=2, title="SLA Compliance PR")

            #with st.container(border=True):
                #st.subheader("📏 SLA Compliance PR Balance")
                #render_sla_gauge(df_pr_valid, threshold=2, title="SLA Compliance PR Balance")

            pic_sla_summary = summarize_pic_sla(df_pr_final_valid, "PIC Procurement", "transaction_number", threshold=2)

            with st.container(border=True):
                st.subheader("📏 SLA Compliance per PIC Procurement")
                #st.dataframe(pic_sla_summary, use_container_width=True, hide_index=True)
                render_pic_sla_bar(pic_sla_summary)

            with st.container(border=True):
                st.subheader("📈 Trend SLA")
                render_sla_trend(df_pr_final_valid, threshold=2, date_col="transaction_date")


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
                render_sla_gauge(df_do_final_valid, 2, title="SLA Compliance DO")

            with st.container(border=True):
                st.subheader("📏 SLA Compliance DO Balance")
                render_sla_gauge(df_do_valid, threshold=2, title="SLA Compliance DO Balance")

            pic_sla_summary_do = summarize_pic_sla(df_do_final_valid, "PIC Procurement", "transaction_number", threshold=2)

            with st.container(border=True):
                st.subheader("📏 SLA Compliance per PIC Procurement")
                #st.dataframe(pic_sla_summary, use_container_width=True, hide_index=True)
                render_pic_sla_bar(pic_sla_summary_do)

            with st.container(border=True):
                st.subheader("📈 Trend SLA")
                render_sla_trend(df_do_final_valid, threshold=2, date_col="transaction_date")


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
                        "Process": "#F2C94C",
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
- **Base URL:** `{BASE_URL}`
- **Timeout Request:** `{REQUEST_TIMEOUT}` detik
- **Tanggal report sampai:** `{selected_report_date}`
- **Mode filter tanggal:** kumulatif (semua data sampai tanggal akhir)
- **Cache API:** 600 detik
            """
        )


if __name__ == "__main__":
    main()