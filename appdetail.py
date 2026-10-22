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

# v11.2: use existing environment/secrets, never embed an API credential.
API_TOKEN = os.getenv("SIBIMA_API_TOKEN", "").strip()
if not API_TOKEN:
    try:
        API_TOKEN = str(st.secrets.get("SIBIMA_API_TOKEN", "") or
                        st.secrets.get("api", {}).get("token", "") or "").strip()
    except Exception:
        API_TOKEN = ""

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


def _exclude_pr_balance_non_item_rows_api(df: pd.DataFrame) -> pd.DataFrame:
    """Exclude baris yang secara bisnis tidak boleh masuk PR Balance.

    Rule eksplisit yang disepakati:
    - JASA / BIAYA PENGIRIMAN tidak termasuk PR Balance;
    - baris lain tetap harus lolos rule linkage SO Detail pada builder utama.

    Pencocokan dibuat toleran terhadap spasi dan tanda slash agar variasi penulisan
    tidak menyebabkan baris jasa pengiriman lolos tanpa sengaja.
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()
    name = _api_first_series(out, ["item_item_name", "item_name", "Nama Barang"], "")
    norm = (
        name.fillna("")
        .astype(str)
        .str.upper()
        .str.replace(r"[^A-Z0-9]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    excluded = norm.str.contains(r"\bJASA\s+BIAYA\s+PENGIRIMAN\b", regex=True, na=False)
    return out.loc[~excluded].copy()


def _flatten_sales_order_payload_api(payload) -> pd.DataFrame:
    """Flatten response satu / beberapa Sales Order menjadi grain SO detail.

    Dibuat toleran terhadap beberapa bentuk payload ERP:
    - data=list
    - data={data:[...]}
    - data={items:[...]}
    - data={details:[...]}
    - data={sales_order_details:[...]}
    - nested payload satu level lebih dalam.
    """
    if payload is None:
        return pd.DataFrame()

    def _extract_orders(obj):
        if isinstance(obj, list):
            return obj

        if not isinstance(obj, dict):
            return []

        data = obj.get("data", obj)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                return data.get("data", [])

            direct_detail_keys = (
                "items", "details", "sales_order_details",
                "salesOrderDetails", "order_details",
            )
            if any(k in data for k in direct_detail_keys):
                return [data]

            for k in ("sales_order", "salesOrder", "order", "result"):
                nested = data.get(k)
                if isinstance(nested, dict):
                    return [nested]
                if isinstance(nested, list):
                    return nested

            return [data]

        return []

    orders = _extract_orders(payload)
    rows = []

    for order in orders:
        if not isinstance(order, dict):
            continue

        items = None
        for detail_key in (
            "items",
            "details",
            "sales_order_details",
            "salesOrderDetails",
            "order_details",
        ):
            candidate = order.get(detail_key)
            if candidate is not None:
                items = candidate
                break

        if isinstance(items, dict):
            for k in ("data", "items", "details"):
                if isinstance(items.get(k), list):
                    items = items.get(k)
                    break

        if isinstance(items, list) and items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                flat = {**order, **{f"item_{k}": v for k, v in item.items()}}
                for k in (
                    "items", "details", "sales_order_details",
                    "salesOrderDetails", "order_details",
                ):
                    flat.pop(k, None)
                rows.append(flat)
        else:
            clean = order.copy()
            for k in (
                "items", "details", "sales_order_details",
                "salesOrderDetails", "order_details",
            ):
                clean.pop(k, None)
            rows.append(clean)

    return pd.DataFrame(rows)


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_sales_order_by_id_api(so_id) -> pd.DataFrame:
    """Fetch satu SO secara langsung berdasarkan header ID.

    Ini penting untuk historical PR Balance: PR Februari dapat membawa referensi
    SO yang tidak ikut dated bulk fetch sampai cutoff Februari. Pricing tetap harus
    mengikuti SO Detail yang direferensikan PR tersebut.
    """
    key = _api_clean_key(pd.Series([so_id])).iloc[0]
    if not key:
        return pd.DataFrame()

    url = f"{BASE_URL['erp']}sales-orders/{key}"
    params = {"token": API_TOKEN} if API_TOKEN else {}
    try:
        session = create_session()
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return _flatten_sales_order_payload_api(response.json())
    except Exception as exc:
        logger.info("Direct SO fetch gagal untuk id=%s: %s", key, exc)
        return pd.DataFrame()



def _frame_has_so_detail_api(df: pd.DataFrame, target_detail_id) -> bool:
    """True bila dataframe SO benar-benar memuat SO Detail ID yang diminta."""
    if df is None or df.empty:
        return False

    target = _api_clean_key(pd.Series([target_detail_id])).iloc[0]
    if not target:
        return False

    detail = _api_clean_key(_api_first_series(
        df,
        ["item_id", "so_detail_id", "item_so_detail_id", "item_sales_order_detail_id"],
        "",
    ))
    return bool(detail.eq(target).any())


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_sales_order_targeted_api(so_id="", so_number="", so_detail_id="") -> pd.DataFrame:
    """Targeted SO fetch untuk pricing historical tanpa date cutoff."""
    so_id_key = _api_clean_key(pd.Series([so_id])).iloc[0]
    so_no_key = str(so_number or "").strip()
    detail_key = _api_clean_key(pd.Series([so_detail_id])).iloc[0]

    session = create_session()
    base = f"{BASE_URL['erp']}sales-orders"
    attempts = []

    if so_id_key:
        attempts.append((f"{base}/{so_id_key}", {}))

    if so_no_key:
        attempts.extend([
            (base, {"transaction_number": so_no_key}),
            (base, {"number": so_no_key}),
            (base, {"search": so_no_key}),
        ])

    if detail_key:
        attempts.extend([
            (base, {"so_detail_id": detail_key}),
            (base, {"detail_id": detail_key}),
        ])

    for url, extra_params in attempts:
        params = dict(extra_params)
        if API_TOKEN:
            params["token"] = API_TOKEN

        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            frame = _flatten_sales_order_payload_api(response.json())

            if _frame_has_so_detail_api(frame, detail_key):
                return frame

        except Exception as exc:
            logger.info(
                "Targeted SO fetch gagal url=%s params=%s target_detail=%s: %s",
                url,
                extra_params,
                detail_key,
                exc,
            )

    return pd.DataFrame()


def _load_targeted_sales_orders_for_unresolved_api(rows: pd.DataFrame) -> pd.DataFrame:
    """Fetch targeted SO hanya untuk kombinasi SO/detail yang masih unresolved."""
    if rows is None or rows.empty:
        return pd.DataFrame()

    tmp = rows.copy()
    tmp["__target_so_id"] = _api_clean_key(_api_first_series(
        tmp, ["item_so_id", "so_id", "sales_order_id", "item_sales_order_id"], ""
    ))
    tmp["__target_so_number"] = _api_clean_key(_api_first_series(
        tmp, ["No. SO", "item_so_transaction_number", "so_transaction_number", "item_sales_order_number"], ""
    ))
    tmp["__target_so_detail"] = _api_clean_key(_api_first_series(
        tmp, ["__so_detail_key", "item_so_detail_id", "so_detail_id", "item_sales_order_detail_id"], ""
    ))

    targets = (
        tmp[["__target_so_id", "__target_so_number", "__target_so_detail"]]
        .drop_duplicates()
    )
    targets = targets[targets["__target_so_detail"].ne("")]

    if targets.empty:
        return pd.DataFrame()

    frames = []
    max_workers = min(8, max(1, len(targets)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for _, row in targets.iterrows():
            future = executor.submit(
                _fetch_sales_order_targeted_api,
                row["__target_so_id"],
                row["__target_so_number"],
                row["__target_so_detail"],
            )
            futures[future] = (
                row["__target_so_id"],
                row["__target_so_number"],
                row["__target_so_detail"],
            )

        for future in as_completed(futures):
            try:
                frame = future.result()
            except Exception as exc:
                logger.info("Targeted unresolved SO fetch error target=%s: %s", futures[future], exc)
                continue

            if frame is not None and not frame.empty:
                frames.append(frame)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True, sort=False)
    detail = _api_clean_key(_api_first_series(
        out,
        ["item_id", "so_detail_id", "item_so_detail_id", "item_sales_order_detail_id"],
        "",
    ))
    out["__target_detail_dedupe"] = detail

    keyed = out[out["__target_detail_dedupe"].ne("")].drop_duplicates(
        "__target_detail_dedupe", keep="last"
    )
    unkeyed = out[out["__target_detail_dedupe"].eq("")]

    return pd.concat([keyed, unkeyed], ignore_index=True, sort=False).drop(
        columns=["__target_detail_dedupe"], errors="ignore"
    )


def _load_referenced_sales_orders_api(rows: pd.DataFrame) -> pd.DataFrame:
    """Fetch hanya SO header yang direferensikan row unresolved, paralel + cached."""
    if rows is None or rows.empty:
        return pd.DataFrame()

    ids = _api_clean_key(_api_first_series(rows, [
        "item_so_id", "so_id", "sales_order_id", "item_sales_order_id"
    ], ""))
    ids = sorted({v for v in ids.tolist() if v})
    if not ids:
        return pd.DataFrame()

    frames = []
    max_workers = min(8, len(ids))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_sales_order_by_id_api, so_id): so_id for so_id in ids}
        for future in as_completed(futures):
            try:
                frame = future.result()
            except Exception as exc:
                logger.info("Referenced SO fetch error id=%s: %s", futures[future], exc)
                continue
            if frame is not None and not frame.empty:
                frames.append(frame)

    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def _load_sales_orders_api_all_time_fallback() -> pd.DataFrame:
    """Last-resort pricing fallback bila endpoint detail-by-ID tidak tersedia."""
    return get_api_data_new(
        "sales-orders", source="erp", start_date=None, end_date=None,
        silent=True, per_page=200, max_pages=500,
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
    out["__so_id_key"] = _api_clean_key(_api_first_series(out, [
        "item_so_id", "so_id", "sales_order_id", "item_sales_order_id"
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

    # Direct fetch hanya untuk memperkuat pricing row yang SUDAH lolos
    # historical SO existence guard. Future SO relationship tidak boleh
    # dipakai untuk membentuk historical membership.
    unresolved = out["__so_price"].isna()
    if unresolved.any():
        referenced_so = _load_referenced_sales_orders_api(out.loc[unresolved].copy())
        if referenced_so is not None and not referenced_so.empty:
            direct_maps = _build_sales_order_pricing_maps_api(referenced_so)
            fill_from_map(direct_maps.get("detail"), ["__so_detail_key"], "SO_DETAIL_ID_DIRECT_FETCH")
            fill_from_map(direct_maps.get("so_product"), ["__so_number_key", "__pr_product_key"], "SO_NUMBER_PRODUCT_DIRECT_FETCH")
            direct_name = direct_maps.get("so_name")
            if direct_name is not None and not direct_name.empty:
                direct_name = direct_name.rename(columns={"__so_name_key": "__pr_name_key"})
            fill_from_map(direct_name, ["__so_number_key", "__pr_name_key"], "SO_NUMBER_ITEM_NAME_DIRECT_FETCH")

    # Targeted unresolved fetch hanya sebagai pricing recovery untuk row yang
    # sudah terbukti SO Detail-nya eksis pada cutoff. Ini tidak boleh menambah
    # future-SO row kembali ke historical membership.
    unresolved = out["__so_price"].isna()
    if unresolved.any():
        targeted_so = _load_targeted_sales_orders_for_unresolved_api(
            out.loc[unresolved].copy()
        )
        if targeted_so is not None and not targeted_so.empty:
            targeted_maps = _build_sales_order_pricing_maps_api(targeted_so)

            fill_from_map(
                targeted_maps.get("detail"),
                ["__so_detail_key"],
                "SO_DETAIL_ID_TARGETED_NO_CUTOFF",
            )
            fill_from_map(
                targeted_maps.get("so_product"),
                ["__so_number_key", "__pr_product_key"],
                "SO_NUMBER_PRODUCT_TARGETED_NO_CUTOFF",
            )

            targeted_name = targeted_maps.get("so_name")
            if targeted_name is not None and not targeted_name.empty:
                targeted_name = targeted_name.rename(
                    columns={"__so_name_key": "__pr_name_key"}
                )

            fill_from_map(
                targeted_name,
                ["__so_number_key", "__pr_name_key"],
                "SO_NUMBER_ITEM_NAME_TARGETED_NO_CUTOFF",
            )

    # Last resort hanya bila masih unresolved. Tidak dijalankan pada normal path.
    unresolved = out["__so_price"].isna()
    if unresolved.any():
        all_time_so = _load_sales_orders_api_all_time_fallback()
        if all_time_so is not None and not all_time_so.empty:
            all_maps = _build_sales_order_pricing_maps_api(all_time_so)
            fill_from_map(all_maps.get("detail"), ["__so_detail_key"], "SO_DETAIL_ID_ALL_TIME_FALLBACK")
            fill_from_map(all_maps.get("so_product"), ["__so_number_key", "__pr_product_key"], "SO_NUMBER_PRODUCT_ALL_TIME_FALLBACK")
            all_name = all_maps.get("so_name")
            if all_name is not None and not all_name.empty:
                all_name = all_name.rename(columns={"__so_name_key": "__pr_name_key"})
            fill_from_map(all_name, ["__so_number_key", "__pr_name_key"], "SO_NUMBER_ITEM_NAME_ALL_TIME_FALLBACK")

    out["SO Price Found"] = pd.to_numeric(out["__so_price"], errors="coerce").notna()
    out.loc[~out["SO Price Found"], "SO Price Match Method"] = "UNRESOLVED"
    return out


def _filter_pr_rows_by_so_asof_api(work: pd.DataFrame, cutoff) -> pd.DataFrame:
    """
    Historical relation guard PR Detail -> SO Detail.

    Business rule:
    - Pada historical cutoff, PR detail hanya dianggap "terhubung ke SO"
      bila SO Detail yang direferensikan sudah eksis pada/before cutoff.
    - Current relation ke SO yang baru dibuat setelah cutoff tidak boleh
      berlaku mundur ke snapshot lama.
    - Pricing tetap berasal dari SO Detail yang sama setelah row lolos guard.

    Contoh:
      PR April sekarang menunjuk ke SO Mei.
      Snapshot 30-Apr -> relation SO tersebut belum eksis -> row keluar.
    """
    if work is None or work.empty:
        return work.copy() if work is not None else pd.DataFrame()

    out = work.copy()

    if "__so_detail_key" not in out.columns:
        out["__so_detail_key"] = _api_clean_key(_api_first_series(
            out,
            ["item_so_detail_id", "so_detail_id", "item_sales_order_detail_id"],
            "",
        ))

    # Source-of-truth existence: sales-orders yang memang sudah ada <= cutoff.
    so_asof = _load_sales_orders_api_for_pricing(cutoff)
    if so_asof is None or so_asof.empty:
        out["Historical SO Exists By Cutoff"] = False
        out["Historical SO Cutoff"] = pd.Timestamp(cutoff)
        return out.iloc[0:0].copy()

    so_keys = _api_clean_key(_api_first_series(
        so_asof,
        ["item_id", "so_detail_id", "item_so_detail_id", "item_sales_order_detail_id"],
        "",
    ))
    valid_so_details = set(v for v in so_keys.tolist() if v)

    out["Historical SO Exists By Cutoff"] = out["__so_detail_key"].isin(valid_so_details)
    out["Historical SO Cutoff"] = pd.Timestamp(cutoff)

    return out.loc[out["Historical SO Exists By Cutoff"]].copy()


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

    # Historical SO relation AS-OF:
    # current PR detail dapat menunjuk ke SO yang baru terbentuk setelah cutoff.
    # Row seperti itu belum dianggap terhubung SO pada snapshot historis.
    work = _filter_pr_rows_by_so_asof_api(work, cutoff)
    if work.empty:
        return pd.DataFrame(columns=[
            "No. PR", "transaction_number", "transaction_date", "Status", "PIC Procurement",
            "No. SO", "PR Qty", "PO Qty", "Qty Closed", "Balance Qty", "Balance Type", "Nominal"
        ])

    # Business exclusion: JASA / BIAYA PENGIRIMAN tidak termasuk PR Balance
    # walaupun mempunyai referensi SO Detail.
    work = _exclude_pr_balance_non_item_rows_api(work)

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
    work["PR Balance Source"] = "API PR/Status/PO + historical SO relationship AS-OF + SO pricing"

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





# === BEGIN NPR SHARED RULES: identical in API and PostgreSQL files ===
# The data adapters are deliberately separate; no cross-source replacement.
NPR_PARITY_VERSION = "20261019-npr-retry-and-period-proof-v11_2"
NPR_PARITY_CACHE_TTL = 600
NPR_STATUS_MAP = {0: "Request", 1: "Process", 2: "Complete"}
NPR_VALID_STATUSES = tuple(NPR_STATUS_MAP.values())
NPR_PUBLIC_COLUMNS = (
    "No. Transaksi", "Nama NPR", "Sales", "Status", "Tanggal Deadline",
    "Catatan", "User Input", "transaction_date",
    "Created At NPR", "Date Source NPR",
)
NPR_HISTORY_KEYS = (
    "history", "histories", "status_history", "status_histories",
    "product_request_histories", "product_requests_history",
    "new_product_request_histories", "request_histories", "transaction_histories",
)


def _npr_text(value) -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "null", "<na>", "nat"} else text


def _npr_pick(row: dict, names, default=None):
    lookup = {str(k).casefold(): v for k, v in row.items()}
    for name in names:
        value = lookup.get(str(name).casefold())
        if isinstance(value, (dict, list)) or _npr_text(value):
            return value
    return default


def _npr_id(value) -> str:
    text = _npr_text(value)
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def _npr_person_name(value) -> str:
    if isinstance(value, dict):
        return _npr_person_name(_npr_pick(value, ("name", "full_name", "display_name", "user")))
    return _npr_text(value)


def _normalize_npr_text_status(value):
    if isinstance(value, dict):
        value = _npr_pick(value, ("id", "code", "status", "name", "description"))
    text = _npr_text(value)
    if not text:
        return ""
    try:
        number = float(text)
        return NPR_STATUS_MAP.get(int(number), text) if number.is_integer() else text
    except (ValueError, OverflowError):
        pass
    return {
        "request": "Request", "requested": "Request", "process": "Process",
        "processed": "Process", "proses": "Process", "in process": "Process",
        "in progress": "Process", "complete": "Complete", "completed": "Complete",
        "selesai": "Complete",
    }.get(" ".join(text.casefold().split()), text)


def _npr_status(value):
    return _normalize_npr_text_status(value)


def _npr_event_status(event: dict) -> str:
    for field in ("status_code", "status", "status_id", "status_description", "status_name", "Status"):
        raw = _npr_pick(event, (field,))
        if isinstance(raw, dict):
            raw = _npr_pick(raw, ("id", "code", "status", "name"))
        text = _npr_text(raw)
        if not text:
            continue
        try:
            code = float(text)
            return NPR_STATUS_MAP.get(int(code), "") if code.is_integer() else ""
        except (ValueError, OverflowError):
            candidate = _normalize_npr_text_status(text)
            if candidate in NPR_VALID_STATUSES:
                return candidate
    return ""


def _npr_timestamp(value):
    """Preserve source wall-clock, as in the existing PG reference. No epoch guessing."""
    if not _npr_text(value) or isinstance(value, (int, float)):
        return pd.NaT
    try:
        timestamp = pd.Timestamp(value)
        return timestamp.tz_localize(None) if timestamp.tzinfo is not None else timestamp
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def _npr_date_from(row: dict, names):
    for name in names:
        timestamp = _npr_timestamp(_npr_pick(row, (name,)))
        if pd.notna(timestamp):
            return timestamp
    return pd.NaT


def _npr_date_series(series):
    # Parse scalars so mixed ISO formats/offsets do not discard otherwise valid rows.
    return pd.to_datetime(series.map(_npr_timestamp), errors="coerce")


def _npr_empty_frame():
    out = pd.DataFrame(columns=NPR_PUBLIC_COLUMNS)
    for col in ("transaction_date", "Tanggal Deadline", "Created At NPR"):
        out[col] = pd.Series(dtype="datetime64[ns]")
    return out


def _npr_pic(value):
    name = " ".join(_npr_text(value).upper().split())
    if name in {"", "UNASSIGNED"}:
        return "Unassigned"
    return "FAQIH" if name == "FAQIH RAMADHAN" else name


def _npr_assign_pic(df, col="Sales"):
    out = df.copy()
    if col not in out:
        out[col] = "Unassigned"
    out[col] = out[col].map(_npr_pic)
    return out


def _npr_safe_datetime(df, col):
    out = df.copy()
    if col in out:
        out[col] = _npr_date_series(out[col])
    return out


def _npr_unique(df, col):
    return int(df[col].nunique()) if df is not None and col in df else 0


def _npr_resolve_person(row: dict, id_names, name_names, lookup: dict) -> str:
    raw_id = _npr_pick(row, id_names)
    key = _npr_id(_npr_pick(raw_id, ("id", "user_id"))) if isinstance(raw_id, dict) else _npr_id(raw_id)
    if key in lookup and _npr_text(lookup[key]):
        return _npr_text(lookup[key])
    for field in name_names:
        person = _npr_pick(row, (field,))
        nested_id = _npr_id(_npr_pick(person, ("id", "user_id"))) if isinstance(person, dict) else ""
        if nested_id in lookup and _npr_text(lookup[nested_id]):
            return _npr_text(lookup[nested_id])
        name = _npr_person_name(person)
        if name:
            return _npr_text(lookup.get(_npr_id(name))) or name
        key = key or nested_id
    return key


def _npr_native_created_dates(df):
    """Canonical NPR document date for parity.

    v10 contract: input_date when available on both API and PostgreSQL; created_at
    only for legacy rows where input_date is missing. This same series drives filter,
    aging, SLA, heatmap and timeline on both adapters.
    """
    if "Created At NPR" in df:
        return _npr_date_series(df["Created At NPR"])
    return _npr_date_series(df.get("transaction_date", pd.Series(pd.NaT, index=df.index)))


def _npr_source_rows_complete_v11_1(df):
    """A source-reader assertion, not a date check and not proof of DB parity.

    Missing metadata is unverified. An explicit negative pagination diagnostic
    cannot be overridden by a stale positive DataFrame attribute. An explicit
    number/date comparison against a supplied complete PG period export is a
    separate, snapshot-relative proof; it does not alter pagination_complete.
    """
    def is_true(value):
        return str(value).strip().casefold() in {"true", "1"}
    evidence = df.attrs.get("npr_period_reference_v11_2", {})
    if evidence.get("matched") is True and evidence.get("basis") == "MATCHED_SUPPLIED_POSTGRESQL_PERIOD_EXPORT":
        return True  # Only the chosen period matches the supplied PG snapshot.
    fetch = df.attrs.get("npr_api_fetch_diagnostic", {})
    if "pagination_complete" in fetch and not is_true(fetch["pagination_complete"]):
        return False
    return is_true(df.attrs.get("npr_source_rows_complete", False))


def _npr_date_coverage(df):
    """Keep dates, source enumeration and final assessment as separate signals.

    'ready' is the legacy overall gate, retained for export compatibility.
    It must NOT be used as the reason that a document date is missing, or as
    a prerequisite to reading histories of already dated/selected documents.
    """
    canonical = _npr_native_created_dates(df)
    source = df.get("Date Source NPR", pd.Series("created_at", index=df.index))
    selected_dates_ready = bool(len(df) and canonical.notna().all())
    scope_dates_complete = bool(df.attrs.get("npr_scope_dates_complete", canonical.notna().all()))
    dates_ready = selected_dates_ready and scope_dates_complete
    source_rows_complete = _npr_source_rows_complete_v11_1(df)
    return {
        "total": len(df), "native_created_valid": int(canonical.notna().sum()),
        "preview_input_date": int(source.eq("input_date").sum()),
        "missing_created_at": int(canonical.isna().sum()),
        "canonical_dates_valid": int(canonical.notna().sum()),
        "missing_canonical_dates": int(canonical.isna().sum()),
        "selected_dates_ready": selected_dates_ready,
        "scope_dates_complete": scope_dates_complete,
        "dates_ready": dates_ready,
        "source_rows_complete": source_rows_complete,
        "ready": bool(dates_ready and source_rows_complete),
    }


def _npr_assessment_message_v11_1(df, history_ready, dates_ready, metric):
    """Explain each real blocker; do not label unverified pagination as a date error."""
    checks = _npr_date_coverage(df)
    blockers = []
    if not dates_ready or not checks["dates_ready"]:
        blockers.append("Masih ada tanggal NPR canonical (input_date/created_at) yang kosong dalam cakupan sumber.")
    if not checks["source_rows_complete"]:
        blockers.append("Kelengkapan daftar NPR dari sumber belum terverifikasi (pagination/cakupan header).")
    if not history_ready:
        blockers.append("History NPR terpilih belum selesai atau belum berhasil dibaca.")
    return " ".join(blockers + [metric + " final belum ditampilkan."])


def _npr_date_field_profile(raw):
    """Inspect date fields only. Never export arbitrary payloads, notes or tokens."""
    fields = ("created_at", "input_date", "transaction_date", "date", "request_date")
    result = {key: {"rows_with_key": 0, "nonblank": 0, "parsed_valid": 0, "types": {}} for key in fields}
    samples = []
    for row in raw.to_dict("records"):
        lower = {str(k).casefold(): v for k, v in row.items()}
        sample = {"transaction_number": _npr_text(_npr_pick(row, ("transaction_number", "No. Transaksi", "npr_number")))}
        for key in fields:
            value = lower.get(key)
            stats = result[key]
            if key in lower:
                stats["rows_with_key"] += 1
                kind = type(value).__name__
                stats["types"][kind] = stats["types"].get(kind, 0) + 1
            if _npr_text(value) or isinstance(value, (dict, list)):
                stats["nonblank"] += 1
            parsed = _npr_timestamp(value)
            if pd.notna(parsed):
                stats["parsed_valid"] += 1
            sample[key] = parsed.isoformat() if pd.notna(parsed) else None
        if len(samples) < 5:
            samples.append(sample)
    return {"fields": result, "parsed_samples": samples,
            "note": "Sampel hanya hasil parsing tanggal; bukan bukti input_date setara created_at PostgreSQL."}


def normalize_npr_headers(raw: pd.DataFrame, users=None, endpoint="", *, allow_input_date_preview=False) -> pd.DataFrame:
    """Preserve created_at for aging; optional input_date is a labelled document preview.

    API adapter enables preview for its legacy response. PostgreSQL stays strict.
    A preview date never enters Created At NPR or the aging/SLA calculation.

    One row per uppercase transaction number. Duplicate resolution is deterministic:
    display date, updated_at, ID, then public-field values (ascending, keep last).
    Invalid dates retain the old na-last convention and remain explicitly reported.
    """
    users = {_npr_id(k): _npr_text(v) for k, v in (users or {}).items()}
    raw = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    rows, contexts = [], []
    counts = dict(headers_input=len(raw), removed_deleted=0, removed_missing_number=0,
                  removed_invalid_status=0, duplicates_removed=0, invalid_created_date=0,
                  unresolved_pic_ids=0, conflicting_duplicate_documents=0)
    for row in raw.to_dict("records"):
        if _npr_text(_npr_pick(row, ("deleted_at",))):
            counts["removed_deleted"] += 1
            continue
        number = _npr_text(_npr_pick(row, ("transaction_number", "No. Transaksi", "no_npr", "npr_number", "number", "code"))).upper()
        if not number:
            counts["removed_missing_number"] += 1
            continue
        status = _npr_event_status(row)
        if status not in NPR_VALID_STATUSES:
            counts["removed_invalid_status"] += 1
            continue
        native_created = _npr_date_from(row, ("created_at",))
        input_date = _npr_date_from(row, ("input_date", "transaction_date"))
        # v10 parity contract: use the same business document date on BOTH sources.
        # Production API exposes input_date but not header created_at, while PostgreSQL
        # exposes both. Therefore input_date is authoritative when present; created_at
        # is only a fallback for legacy rows without input_date.
        created = input_date if pd.notna(input_date) else native_created
        source = "input_date" if pd.notna(input_date) else ("created_at" if pd.notna(native_created) else "MISSING_DATE")
        identifier = _npr_id(_npr_pick(row, ("id", "npr_id", "product_request_id", "header_id")))
        pic = _npr_resolve_person(row, ("pic_id", "responsible_id", "assignee_id"),
                                  ("pic_name", "pic", "Sales", "penanggung_jawab", "responsible_name", "assignee_name"), users)
        record = {
            "No. Transaksi": number,
            "Nama NPR": _npr_text(_npr_pick(row, ("name", "Nama NPR", "nama_npr", "npr_name", "title", "description"))),
            "Sales": _npr_pic(pic), "Status": status,
            "Tanggal Deadline": _npr_date_from(row, ("due_date", "Tanggal Deadline", "deadline", "deadline_date", "tanggal_deadline")),
            "Catatan": _npr_text(_npr_pick(row, ("notes", "note", "Catatan", "remarks", "remark"))),
            "User Input": _npr_resolve_person(row, ("created_by", "created_by_id", "user_id"),
                         ("created_by_name", "User Input", "user_input", "creator_name", "creator", "created_by", "user_name"), users),
            "transaction_date": created,
            "Created At NPR": created, "Date Source NPR": source,
        }
        context = {
            "number": number, "id": identifier, "endpoint": endpoint,
            "source_number": _npr_text(_npr_pick(row, ("transaction_number", "No. Transaksi", "no_npr", "npr_number", "number", "code"))),
            "embedded": {k: row[k] for k in row if str(k).casefold() in NPR_HISTORY_KEYS + ("history_meta", "history_pagination")},
            "date_source": source,
            "native_created_at": native_created, "input_date": input_date,
            "api_pic_id": _npr_id(_npr_pick(row, ("pic_id", "responsible_id", "assignee_id"))),
            "api_pic_name_raw": _npr_person_name(_npr_pick(row, ("pic_name", "pic", "Sales"))),
            "header_date_inprogress": _npr_date_from(row, ("date_inprogress",)),
            "header_date_complete": _npr_date_from(row, ("date_complete",)),
            "header_updated_at": _npr_date_from(row, ("updated_at", "modified_at")),
            "record_created_date": created,
            "declared_milestones": {k: row[k] for k in ("milestone_contract", "first_process_at", "first_complete_at", "history_count", "history_key_variants") if k in row},
            "pg_history_state": row.get("__pg_history_state", ""),
        }
        record.update(__ctx=len(contexts), __sort_updated=context["header_updated_at"],
                      __sort_id=identifier, __sort_tie=repr(tuple(_npr_text(record[c]) for c in NPR_PUBLIC_COLUMNS)))
        rows.append(record)
        contexts.append(context)
    if not rows:
        out = _npr_empty_frame()
        out.attrs.update(npr_api_context={}, npr_normalization_diagnostic=counts)
        return out
    work = pd.DataFrame(rows)
    for col in ("transaction_date", "Tanggal Deadline", "Created At NPR", "__sort_updated"):
        work[col] = _npr_date_series(work[col])
    duplicates = work.loc[work.duplicated("No. Transaksi", keep=False)]
    if not duplicates.empty:
        counts["conflicting_duplicate_documents"] = int(duplicates.groupby("No. Transaksi")["__sort_tie"].nunique().gt(1).sum())
    work = work.sort_values(["transaction_date", "__sort_updated", "__sort_id", "__sort_tie"],
                            kind="stable", na_position="last").drop_duplicates("No. Transaksi", keep="last")
    chosen = {r["No. Transaksi"]: contexts[int(r["__ctx"])] for r in work.to_dict("records")}
    out = work[list(NPR_PUBLIC_COLUMNS)].sort_values(["transaction_date", "No. Transaksi"], kind="stable").reset_index(drop=True)
    counts.update(duplicates_removed=len(rows)-len(out), invalid_created_date=int(out["Created At NPR"].isna().sum()),
                  invalid_document_date=int(out["transaction_date"].isna().sum()),
                  preview_input_date=0,
                  headers_valid=len(out), unresolved_pic_ids=int(out["Sales"].str.fullmatch(r"\d+", na=False).sum()))
    counts["created_date_sources"] = {
        "input_date": int(out["Date Source NPR"].eq("input_date").sum()),
        "created_at_fallback": int(out["Date Source NPR"].eq("created_at").sum()),
        "MISSING_DATE": counts["invalid_created_date"],
    }
    counts["document_date_sources"] = {str(k): int(v) for k, v in out["Date Source NPR"].value_counts().items()}
    out.attrs.update(npr_api_context=chosen, npr_api_endpoint=endpoint, npr_normalization_diagnostic=counts)
    return out


def apply_npr_date_filter(df, end_date, source_start=None):
    working = _npr_safe_datetime(df, "transaction_date")
    if working.empty:
        return working
    end = _npr_timestamp(end_date)
    if pd.isna(end):
        raise ValueError("Tanggal akhir NPR tidak valid.")
    # Exclusive next midnight includes fractions after 23:59:59.
    mask = working["transaction_date"].notna() & working["transaction_date"].lt(end.normalize() + pd.Timedelta(days=1))
    if source_start is not None and _npr_text(source_start):
        start = _npr_timestamp(source_start)
        if pd.isna(start) or start.normalize() > end.normalize():
            raise ValueError("Batas awal NPR tidak valid atau melewati tanggal akhir.")
        mask &= working["transaction_date"].ge(start.normalize())
    return working.loc[mask].copy()


def _apply_npr_filters_v3(df, search_number="", search_status="", search_pic="Semua PIC"):
    working = _npr_assign_pic(df)
    if working.empty:
        return working
    working["Status"] = working["Status"].map(_normalize_npr_text_status)
    pattern = _npr_text(search_number).casefold()
    if pattern:
        mask = pd.Series(False, index=working.index)
        for col in ("No. Transaksi", "Nama NPR", "Sales", "Catatan", "User Input"):
            if col in working:
                mask |= working[col].fillna("").astype(str).str.casefold().str.contains(pattern, regex=False)
        working = working.loc[mask].copy()
    status = _npr_text(search_status)
    if status and status.casefold() != "semua status":
        working = working.loc[working["Status"].eq(_normalize_npr_text_status(status))].copy()
    if _npr_text(search_pic) and str(search_pic).strip().casefold() != "semua pic":
        working = working.loc[working["Sales"].eq(_npr_pic(search_pic))].copy()
    return working.loc[working["Status"].isin(NPR_VALID_STATUSES)].copy()


def _npr_first_milestones(events):
    # Exact same rule as SQL MIN(COALESCE(updated_at, created_at)) FILTER(status).
    dates = {"Process": [], "Complete": []}
    for event in events:
        if _npr_text(_npr_pick(event, ("deleted_at",))):
            continue
        status = _npr_event_status(event)
        timestamp = _npr_date_from(event, ("updated_at", "created_at"))
        if status in dates and pd.notna(timestamp):
            dates[status].append(timestamp)
    return tuple(min(dates[k]) if dates[k] else pd.NaT for k in ("Process", "Complete"))


def _npr_normalize_history(history):
    cols = ["__npr_number", "__first_process_date", "__first_complete_date"]
    hist = history.reindex(columns=cols).copy() if isinstance(history, pd.DataFrame) else pd.DataFrame(columns=cols)
    attributes = dict(getattr(history, "attrs", {}))
    hist["__npr_number"] = hist["__npr_number"].map(_npr_text).str.upper()
    for col in cols[1:]:
        hist[col] = _npr_date_series(hist[col])
    hist = hist.loc[hist["__npr_number"].ne("")]
    hist = hist.groupby("__npr_number", as_index=False, sort=True)[cols[1:]].min()
    hist.attrs = attributes
    return hist


def _npr_build_aging(df, history, milestone):
    out = df.copy() if isinstance(df, pd.DataFrame) else _npr_empty_frame()
    for col in ("No. Transaksi", "transaction_date"):
        if col not in out:
            out[col] = pd.Series(index=out.index, dtype="object")
    suffix = "" if milestone == "Process" else " Complete"
    public_date, internal_date = "First " + milestone + " Date", "__first_" + milestone.lower() + "_date"
    aging_col, category_col = "Aging NPR" + suffix, "Aging Category NPR" + suffix
    out = out.drop(columns=[public_date, aging_col, category_col, "__npr_number", internal_date], errors="ignore")
    out["No. Transaksi"] = out["No. Transaksi"].map(_npr_text).str.upper()
    out["transaction_date"] = _npr_date_series(out["transaction_date"])
    hist = _npr_normalize_history(history)
    diagnostics = hist.attrs.get("npr_history_diagnostic")
    if diagnostics is not None:
        readable = {_npr_text(r.get("__npr_number")).upper() for r in diagnostics
                    if r.get("History State") in {"API_HISTORY", "NESTED_HISTORY",
                                                  "API_FIRST_MILESTONES", "POSTGRESQL_HISTORY"}}
        hist = hist.loc[hist["__npr_number"].isin(readable)].copy()
    mapping = hist.set_index("__npr_number")[internal_date]
    out[public_date] = _npr_date_series(out["No. Transaksi"].map(mapping))
    out[aging_col] = (out[public_date].dt.normalize() - _npr_native_created_dates(out).dt.normalize()).dt.days.astype("float64")
    out.loc[out[aging_col].lt(0), aging_col] = float("nan")
    out[category_col] = pd.cut(out[aging_col], [-1, 2, 5, 10, 30, float("inf")],
                               labels=["0-2 hari", "3-5 hari", "6-10 hari", "11-30 hari", ">30 hari"], right=True)
    return out


def build_npr_aging_first_process(df, history_df=None):
    history = history_df if history_df is not None else load_npr_first_process_history(df)
    return _npr_build_aging(df, history, "Process")


def build_npr_aging_first_complete(df, history_df=None):
    history = history_df if history_df is not None else load_npr_first_complete_history(df)
    return _npr_build_aging(df, history, "Complete")


def build_npr_sla_first_process(df):
    required = {"No. Transaksi", "transaction_date", "First Process Date", "Aging NPR"}
    if df is None or not required.issubset(df.columns):
        return pd.DataFrame(columns=["No. Transaksi", "Aging"])
    working = df.copy()
    working["No. Transaksi"] = working["No. Transaksi"].map(_npr_text).str.upper()
    working["Aging"] = pd.to_numeric(working["Aging NPR"], errors="coerce")
    valid = (working["No. Transaksi"].ne("") & _npr_native_created_dates(working).notna()
             & _npr_date_series(working["First Process Date"]).notna() & working["Aging"].notna() & working["Aging"].ge(0))
    return working.loc[valid].drop_duplicates("No. Transaksi", keep="first").copy()


def _npr_coverage(df, history):
    records = history.attrs.get("npr_history_diagnostic", [])
    good = {"API_HISTORY", "NESTED_HISTORY", "API_FIRST_MILESTONES", "POSTGRESQL_HISTORY"}
    states = {_npr_text(r.get("__npr_number")).upper(): r.get("History State", "HISTORY_NOT_LOADED") for r in records}
    keys = set(df["No. Transaksi"].map(_npr_text).str.upper()) if not df.empty else set()
    resolved = sum(states.get(k) in good for k in keys)
    return {"total": len(keys), "resolved": resolved, "unresolved": len(keys)-resolved,
            "ready": resolved == len(keys), "states": states}


def _npr_render_sla_gauge(df: pd.DataFrame, threshold: int = 5, title: str = "SLA Compliance"):
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


def render_npr_outstanding_heatmap(df: pd.DataFrame):
    """NPR Request/Process saat ini, per PIC NPR dan bulan dokumen dibuat.

    Sales berasal dari PIC dokumen (padanan pic_id -> users.name), sama dengan
    grafik NPR Outstanding per PIC. Fungsi ini tidak merekonstruksi status
    historis atau menghitung frekuensi tindakan PIC.
    """
    required = {"Sales", "Status", "transaction_date", "No. Transaksi"}
    if df is None or df.empty:
        st.info("Data NPR tidak tersedia untuk heatmap.")
        return
    if not required.issubset(df.columns):
        st.info("Data PIC, status, tanggal, atau nomor NPR belum tersedia.")
        return

    working = df.copy()
    working["Status"] = working["Status"].map(_normalize_npr_text_status)
    working = working[working["Status"].isin(["Request", "Process"])].copy()
    if working.empty:
        st.info("Tidak ada NPR berstatus Request atau Process.")
        return

    # Pakai normalisasi yang sama dengan grafik batang NPR.
    # Nama tertentu tidak dipindahkan ke Unassigned hanya karena judul grafik.
    working = _npr_assign_pic(working, "Sales")
    working["Sales"] = working["Sales"].replace({"UNASSIGNED": "Unassigned"})
    working["No. Transaksi"] = (
        working["No. Transaksi"].fillna("").astype(str).str.strip()
    )
    working = working[
        ~working["No. Transaksi"].str.upper().isin(["", "NAN", "NONE", "NULL", "<NA>"])
    ].copy()
    working = _npr_safe_datetime(working, "transaction_date")
    missing_date_count = working.loc[
        working["transaction_date"].isna(), "No. Transaksi"
    ].nunique()
    working = working[working["transaction_date"].notna()].copy()
    if working.empty:
        st.info("Tidak ada NPR outstanding dengan nomor dan tanggal yang valid.")
        return

    # Period M menyimpan tahun dan bulan: Jan 2025 berbeda dari Jan 2026.
    working["Bulan"] = working["transaction_date"].dt.to_period("M")
    matrix = (
        working.groupby(["Sales", "Bulan"], observed=True)["No. Transaksi"]
        .nunique()
        .unstack("Bulan", fill_value=0)
    )
    months = pd.period_range(
        working["Bulan"].min(), working["Bulan"].max(), freq="M"
    )
    matrix = matrix.reindex(columns=months, fill_value=0).fillna(0).astype(int)
    pic_order = matrix.sum(axis=1).sort_values(ascending=False, kind="stable").index
    matrix = matrix.loc[pic_order]

    month_names = [
        "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
        "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
    ]
    month_labels = [f"{month_names[p.month - 1]} {p.year}" for p in months]
    fig = go.Figure(go.Heatmap(
        z=matrix.to_numpy().tolist(),
        x=month_labels,
        y=matrix.index.tolist(),
        colorscale=[[0, "#EDF7ED"], [0.5, "#F2994A"], [1, "#A80B0B"]],
        zmin=0,
        zmax=max(1, int(matrix.to_numpy().max())),
        texttemplate="%{z:.0f}",
        textfont=dict(size=11),
        hovertemplate=(
            "PIC: %{y}<br>Bulan dibuat: %{x}"
            "<br>NPR outstanding: %{z:.0f}<extra></extra>"
        ),
        colorbar=dict(title="NPR", tickformat=".0f", thickness=12, len=0.7),
        xgap=1,
        ygap=1,
    ))
    fig.update_layout(
        xaxis=dict(
            title="Bulan NPR Dibuat", type="category", tickangle=-45,
            categoryorder="array", categoryarray=month_labels,
        ),
        yaxis=dict(
            title="PIC Procurement", type="category", autorange="reversed",
            categoryorder="array", categoryarray=matrix.index.tolist(),
            automargin=True,
        ),
        margin=dict(l=70, r=20, t=20, b=90),
        height=max(400, 150 + 32 * len(matrix)),
    )
    st.plotly_chart(
        fig, use_container_width=True, key="heatmap_npr_outstanding"
    )
    st.caption(
        "Angka menunjukkan jumlah NPR unik berstatus Request atau Process. "
        "Semakin merah, semakin banyak outstanding pada sel tersebut."
    )
    st.caption(
        ("Bulan mengikuti input_date untuk NPR pratinjau, bukan created_at yang sudah diverifikasi; "
         if _npr_date_coverage(df)["preview_input_date"] else "Bulan mengikuti tanggal NPR dibuat; ") +
        "status mengikuti data terbaru "
        "saat dimuat. PIC mengikuti penanggung jawab pada dokumen NPR."
    )
    if missing_date_count:
        st.caption(
            f"{missing_date_count:,} NPR tanpa tanggal pembuatan valid "
            "tidak ditampilkan dalam heatmap."
        )


def render_npr_aging_first_process(df: pd.DataFrame, *, provisional: bool = False):
    """Grafik distribusi waktu NPR Created -> First Process."""
    if (
        df is None
        or df.empty
        or "Aging Category NPR" not in df.columns
        or "No. Transaksi" not in df.columns
    ):
        st.info("Data Aging NPR ke First Process tidak tersedia.")
        return

    valid = df[
        df["Aging NPR"].notna()
        & df["Aging Category NPR"].notna()
    ].copy()

    if valid.empty:
        st.info("Tanggal First Process yang valid belum berhasil diperoleh untuk grafik ini.")
        return

    summary = (
        valid.groupby("Aging Category NPR", observed=False)["No. Transaksi"]
        .nunique()
        .reset_index(name="Jumlah NPR")
    )
    summary = summary[summary["Jumlah NPR"] > 0].copy()

    category_order = [
        "0-2 hari",
        "3-5 hari",
        "6-10 hari",
        "11-30 hari",
        ">30 hari",
    ]

    fig = px.bar(
        summary,
        x="Aging Category NPR",
        y="Jumlah NPR",
        color="Aging Category NPR",
        text="Jumlah NPR",
        title=("SEMENTARA — " if provisional else "") + "Distribusi Aging NPR — Created ke First Process",
        category_orders={"Aging Category NPR": category_order},
    )

    fig.update_traces(textposition="outside")
    fig.update_layout(
        showlegend=False,
        xaxis_title="Waktu dari NPR dibuat sampai First Process",
        yaxis_title="Jumlah NPR",
        margin=dict(l=40, r=40, t=60, b=90),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key="aging_npr_first_process",
    )


def render_npr_aging_first_complete(df: pd.DataFrame, *, provisional: bool = False):
    """Grafik distribusi waktu NPR Created -> First Complete."""
    if (
        df is None
        or df.empty
        or "Aging Category NPR Complete" not in df.columns
        or "No. Transaksi" not in df.columns
    ):
        st.info("Data Aging NPR ke First Complete tidak tersedia.")
        return

    valid = df[
        df["Aging NPR Complete"].notna()
        & df["Aging Category NPR Complete"].notna()
    ].copy()

    if valid.empty:
        st.info("Tanggal First Complete yang valid belum berhasil diperoleh untuk grafik ini.")
        return

    summary = (
        valid.groupby("Aging Category NPR Complete", observed=False)["No. Transaksi"]
        .nunique()
        .reset_index(name="Jumlah NPR")
    )
    summary = summary[summary["Jumlah NPR"] > 0].copy()

    category_order = [
        "0-2 hari",
        "3-5 hari",
        "6-10 hari",
        "11-30 hari",
        ">30 hari",
    ]

    fig = px.bar(
        summary,
        x="Aging Category NPR Complete",
        y="Jumlah NPR",
        color="Aging Category NPR Complete",
        text="Jumlah NPR",
        title=("SEMENTARA — " if provisional else "") + "Distribusi Aging NPR — Created ke First Complete",
        category_orders={
            "Aging Category NPR Complete": category_order
        },
    )

    fig.update_traces(textposition="outside")
    fig.update_layout(
        showlegend=False,
        xaxis_title="Waktu dari NPR dibuat sampai First Complete",
        yaxis_title="Jumlah NPR",
        margin=dict(l=40, r=40, t=60, b=90),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key="aging_npr_first_complete",
    )


def _npr_valid_aging_preview_rows(df: pd.DataFrame, milestone: str) -> pd.DataFrame:
    """Select valid existing metrics only; never supply dates or override source proof.

    The input is produced by _npr_build_aging(), which excludes unreadable history.
    Preview selection is separate from the unchanged final assessment gates.
    """
    working = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    suffix = "" if milestone == "Process" else " Complete"
    first_date = "First " + milestone + " Date"
    aging_col = "Aging NPR" + suffix
    category_col = "Aging Category NPR" + suffix
    required = {"No. Transaksi", first_date, aging_col, category_col}
    if working.empty or not required.issubset(working.columns):
        return working.iloc[0:0].copy()
    numbers = working["No. Transaksi"].map(_npr_text).str.upper()
    aging = pd.to_numeric(working[aging_col], errors="coerce")
    valid = (
        numbers.ne("")
        & _npr_native_created_dates(working).notna()
        & _npr_date_series(working[first_date]).notna()
        & aging.notna()
        & aging.ge(0)
        & working[category_col].notna()
    )
    selected = working.loc[valid].copy()
    selected["No. Transaksi"] = numbers.loc[valid]
    return selected.drop_duplicates("No. Transaksi", keep="first").copy()


def render_npr_dashboard(
    df_npr_f: pd.DataFrame,
    df_npr_aging: pd.DataFrame,
    df_npr_sla: pd.DataFrame,
    df_npr_aging_complete: pd.DataFrame,
    history_ready: bool = True,
    dates_ready: bool = True,
):
    """Shared panels with labelled previews; final assessment gates remain unchanged."""
    # Rendering may format dates for the timeline; keep caller data unchanged.
    df_npr_f = df_npr_f.copy()
    date_checks = _npr_date_coverage(df_npr_f)
    dates_ready = dates_ready and (date_checks["dates_ready"] or df_npr_f.empty)
    source_ready = date_checks["source_rows_complete"]
    selected_total = _npr_unique(df_npr_f, "No. Transaksi")
    col_kiri, col_tengah, col_kanan = st.columns([1, 1, 1], gap="small")
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
                    "Process": "#F2C94C",
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


        with st.container(border=True):
            st.subheader("🔥 Heatmap NPR Outstanding - Aktivitas PIC Procurement")
            render_npr_outstanding_heatmap(df_npr_f)




# =====================================================
# MID NPR
# =====================================================

    with col_tengah:
        with st.container(border=True):
            st.subheader("👥 NPR Outstanding per PIC — Request & Process")

            if df_npr_f.empty:
                st.info("Data NPR tidak tersedia.")
            else:
                # =========================================================
                # NPR OUTSTANDING PER PIC
                # =========================================================
                # Grafik ini khusus menampilkan NPR yang MASIH perlu diproses.
                # Complete sengaja dibuang dari visual ini.
                npr_active = df_npr_f[
                    df_npr_f["Status"].isin(["Request", "Process"])
                ].copy()

                if npr_active.empty:
                    st.info("Tidak ada NPR berstatus Request atau Process.")
                else:
                    # PIC NPR pada dataframe dashboard disimpan di kolom "Sales".
                    npr_active = _npr_assign_pic(npr_active, "Sales")
                    npr_active["Sales"] = npr_active["Sales"].replace(
                        {"UNASSIGNED": "Unassigned"}
                    )

                    # Ringkas jumlah NPR unik per PIC dan status aktif.
                    sales_summary = (
                        npr_active.groupby(["Sales", "Status"], dropna=False)
                        .agg(Jumlah_Doc=("No. Transaksi", "nunique"))
                        .reset_index()
                    )

                    # Urutkan PIC berdasarkan total outstanding terbanyak.
                    pic_order = (
                        sales_summary.groupby("Sales", as_index=False)["Jumlah_Doc"]
                        .sum()
                        .sort_values("Jumlah_Doc", ascending=False, kind="stable")["Sales"]
                        .tolist()
                    )

                    STATUS_COLORS = {
                        "Process": "#F2C94C",
                        "Request": "#56CCF2",
                    }

                    # Stacked bar: Request + Process per PIC.
                    fig = px.bar(
                        sales_summary,
                        x="Sales",
                        y="Jumlah_Doc",
                        color="Status",
                        color_discrete_map=STATUS_COLORS,
                        text="Jumlah_Doc",
                        title="NPR Outstanding per PIC — Request & Process",
                        category_orders={
                            "Sales": pic_order,
                            "Status": ["Request", "Process"],
                        },
                    )

                    # Total outstanding di atas masing-masing PIC.
                    totals = (
                        sales_summary.groupby("Sales", as_index=False)["Jumlah_Doc"]
                        .sum()
                    )
                    for _, row in totals.iterrows():
                        fig.add_annotation(
                            x=row["Sales"],
                            y=row["Jumlah_Doc"],
                            text=f"{int(row['Jumlah_Doc'])}",
                            showarrow=False,
                            font=dict(size=12, color="black"),
                            yshift=10,
                        )

                    fig.update_traces(
                        textposition="inside",
                        textfont=dict(size=10, color="white"),
                    )
                    fig.update_layout(
                        uniformtext_mode="hide",
                        yaxis_title="Jumlah NPR Outstanding",
                        xaxis_title="PIC",
                        margin=dict(l=40, r=40, t=60, b=100),
                    )

                    st.plotly_chart(fig, use_container_width=True)


        with st.container(border=True):
            st.subheader("⏳ Aging NPR — Created ke First Process")
            st.caption(
                "Aging dihitung dari selisih tanggal NPR pertama kali berstatus Process "
                "dikurangi tanggal NPR dibuat. NPR tanpa tanggal First Process yang valid "
                "pada riwayat terbaca belum dapat dinilai."
            )
            if history_ready and dates_ready and source_ready:
                render_npr_aging_first_process(df_npr_aging)
            else:
                st.info(_npr_assessment_message_v11_1(df_npr_f, history_ready, dates_ready, "Aging"))
                preview_process = _npr_valid_aging_preview_rows(df_npr_aging, "Process")
                preview_total = len(preview_process)
                if preview_total:
                    st.warning(
                        "SEMENTARA — Distribusi hanya dari NPR dengan tanggal dan riwayat "
                        "First Process valid yang sudah terbaca. Hasil dapat berubah setelah pemuatan "
                        "dan verifikasi selesai; belum mewakili seluruh periode."
                    )
                    st.caption(
                        f"Basis grafik sementara: {preview_total:,} dari {selected_total:,} NPR "
                        f"terbaca setelah filter. Belum dinilai: {max(0, selected_total-preview_total):,} NPR."
                    )
                    render_npr_aging_first_process(preview_process, provisional=True)
                else:
                    st.info("Pratinjau belum tersedia: belum ada tanggal First Process dan durasi yang valid.")





# =====================================================
# 📈 RIGHT - NPR Timeline
# =====================================================
    with col_kanan:
        with st.container(border=True):
            st.subheader("📏 SLA Compliance NPR")
            if history_ready and dates_ready and source_ready:
                npr_sla_threshold = 2
                _npr_render_sla_gauge(
                    df_npr_sla,
                    threshold=npr_sla_threshold,
                    title="SLA Compliance NPR",
                )
                npr_sla_total = len(df_npr_sla)
                npr_sla_met = int(df_npr_sla["Aging"].le(npr_sla_threshold).sum())
                npr_sla_not_assessed = max(
                    0, _npr_unique(df_npr_aging, "No. Transaksi") - npr_sla_total
                )
                st.caption(
                    "Durasi: tanggal NPR dibuat sampai pertama kali Process, "
                    f"dengan batas {npr_sla_threshold} hari kalender."
                )
                st.caption(
                    f"Dinilai: {npr_sla_total:,} NPR | "
                    f"Memenuhi SLA: {npr_sla_met:,} NPR | "
                    f"Belum dapat dinilai karena data aging tidak valid/lengkap: "
                    f"{npr_sla_not_assessed:,} NPR."
                )
            else:
                st.info(_npr_assessment_message_v11_1(df_npr_f, history_ready, dates_ready, "SLA"))
                # Reuse the existing SLA eligibility rule and denominator unchanged.
                preview_sla = build_npr_sla_first_process(df_npr_sla)
                preview_total = len(preview_sla)
                if preview_total:
                    preview_met = int(preview_sla["Aging"].le(2).sum())
                    st.warning(
                        "SEMENTARA — Persentase hanya berlaku untuk NPR dengan tanggal First Process "
                        "dan aging valid yang sudah terbaca. Ini belum merupakan SLA seluruh periode."
                    )
                    _npr_render_sla_gauge(
                        preview_sla, threshold=2, title="SLA Compliance NPR — SEMENTARA",
                    )
                    st.caption(
                        f"Penyebut SLA sementara: {preview_total:,} NPR | "
                        f"Memenuhi SLA ≤2 hari kalender: {preview_met:,} NPR | "
                        f"Dokumen terbaca setelah filter: {selected_total:,} NPR | "
                        f"Belum dinilai: {max(0, selected_total-preview_total):,} NPR."
                    )
                    st.caption("NPR yang belum dapat dinilai tidak dihitung sebagai memenuhi atau gagal SLA.")
                else:
                    st.info("SLA sementara belum tersedia: tidak ada NPR dengan First Process dan aging valid.")


        with st.container(border=True):
            st.subheader("⏳ Aging NPR — Created ke First Complete")
            st.caption(
                "Aging dihitung dari selisih tanggal NPR pertama kali berstatus Complete "
                "dikurangi tanggal NPR dibuat. NPR tanpa tanggal First Complete yang valid "
                "pada riwayat terbaca belum dapat dinilai."
            )
            if history_ready and dates_ready and source_ready:
                render_npr_aging_first_complete(df_npr_aging_complete)
            else:
                st.info(_npr_assessment_message_v11_1(df_npr_f, history_ready, dates_ready, "Aging"))
                preview_complete = _npr_valid_aging_preview_rows(df_npr_aging_complete, "Complete")
                preview_total = len(preview_complete)
                if preview_total:
                    st.warning(
                        "SEMENTARA — Distribusi hanya dari NPR dengan tanggal dan riwayat "
                        "First Complete valid yang sudah terbaca. Hasil dapat berubah setelah pemuatan "
                        "dan verifikasi selesai; belum mewakili seluruh periode."
                    )
                    st.caption(
                        f"Basis grafik sementara: {preview_total:,} dari {selected_total:,} NPR "
                        f"terbaca setelah filter. Belum dinilai: {max(0, selected_total-preview_total):,} NPR."
                    )
                    render_npr_aging_first_complete(preview_complete, provisional=True)
                else:
                    st.info("Pratinjau belum tersedia: belum ada tanggal First Complete dan durasi yang valid.")

        with st.container(border=True):
            st.subheader("📈 Timeline NPR per Tanggal Transaksi")
            if _npr_date_coverage(df_npr_f)["preview_input_date"]:
                st.caption("Tanggal NPR mengikuti kontrak parity v10: input_date, fallback created_at.")

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


def _npr_default_source_start():
    """Compatibility helper: derive the lower bound from the MAIN period only."""
    period = st.session_state.get("_npr_main_period_v11", {})
    start = _npr_timestamp(period.get("start"))
    return start.date() if pd.notna(start) else None


def _npr_export_frame(df, history, end_date, source_start, search_number, search_status, search_pic):
    """Identical audit schema, without tokens/server addresses or fabricated dates."""
    process = build_npr_aging_first_process(df, history)
    complete = build_npr_aging_first_complete(df, history).set_index("No. Transaksi")
    coverage = _npr_coverage(df, history)
    good = {"API_HISTORY", "NESTED_HISTORY", "API_FIRST_MILESTONES", "POSTGRESQL_HISTORY"}
    date_coverage = _npr_date_coverage(df)
    native_dates = dict(zip(df["No. Transaksi"], _npr_native_created_dates(df)))
    rows = []
    def timestamp(value):
        value = _npr_timestamp(value)
        return value.isoformat() if pd.notna(value) else ""
    def days(value):
        return str(int(value)) if pd.notna(value) else ""
    for row in process.sort_values("No. Transaksi", kind="stable").to_dict("records"):
        number = row["No. Transaksi"]
        other = complete.loc[number]
        read_ok = coverage["states"].get(number) in good
        aging = row["Aging NPR"]
        eligible = read_ok and pd.notna(aging) and aging >= 0
        rows.append({
            "transaction_number": number, "status_description": row["Status"], "pic": row["Sales"],
            "npr_name": _npr_text(row.get("Nama NPR")), "notes": _npr_text(row.get("Catatan")),
            "user_input": _npr_text(row.get("User Input")), "deadline": timestamp(row.get("Tanggal Deadline")),
            "date_policy": df.attrs.get("npr_date_policy", "CREATED_AT_ONLY"),
            "scope_creation_dates_complete": str(date_coverage["scope_dates_complete"]),
            "source_rows_complete": str(date_coverage["source_rows_complete"]),
            "created_at": timestamp(native_dates.get(number)),
            "document_date": timestamp(row["transaction_date"]),
            "document_date_source": row.get("Date Source NPR", "created_at"),
            "created_date_verified": str(pd.notna(native_dates.get(number))),
            "first_process_at": timestamp(row["First Process Date"]),
            "first_complete_at": timestamp(other["First Complete Date"]),
            "aging_process_days": days(aging), "aging_complete_days": days(other["Aging NPR Complete"]),
            "sla_eligible": str(bool(eligible)) if read_ok else "",
            "sla_met": str(bool(aging <= 2)) if eligible else "",
            "history_read": "READ_OK" if read_ok else "UNRESOLVED",
            "rule_version": NPR_PARITY_VERSION,
            "scope_start": str(source_start or ""), "scope_end": str(pd.Timestamp(end_date).date()),
            "filter_number": _npr_text(search_number).casefold(),
            "filter_status": _normalize_npr_text_status(search_status) if _npr_text(search_status).casefold() != "semua status" else "",
            "filter_pic": _npr_pic(search_pic) if _npr_text(search_pic).casefold() != "semua pic" else "",
            "documents_in_export": str(len(process)), "history_read_complete": str(coverage["ready"]),
            "assessment_ready": str(coverage["ready"] and date_coverage["ready"]),
        })
    cols = ["transaction_number", "status_description", "pic", "created_at", "first_process_at", "first_complete_at",
            "aging_process_days", "aging_complete_days", "sla_eligible", "sla_met", "history_read", "rule_version",
            "scope_start", "scope_end", "filter_number", "filter_status", "filter_pic", "documents_in_export", "history_read_complete", "document_date", "document_date_source",
            "created_date_verified", "assessment_ready", "npr_name", "notes", "user_input", "deadline",
            "date_policy", "scope_creation_dates_complete", "source_rows_complete"]
    return pd.DataFrame(rows, columns=cols)


def _npr_compare_exports(current, reference):
    required = list(current.columns)
    if not set(required).issubset(reference.columns):
        raise ValueError("Gunakan CSV NPR_Pembanding_v11_2 dari kedua script baru; format lama belum memisahkan tanggal pratinjau.")
    frames = []
    scope = ["rule_version", "scope_start", "scope_end", "filter_number", "filter_status", "filter_pic", "date_policy"]
    for label, frame in (("Aktif", current), ("Pembanding", reference)):
        frame = frame[required].fillna("").astype(str).copy()
        if frame.empty or frame["transaction_number"].eq("").any() or frame["transaction_number"].duplicated().any():
            raise ValueError(label + ": CSV kosong atau nomor NPR kosong/duplikat.")
        count = pd.to_numeric(frame["documents_in_export"], errors="coerce")
        if count.isna().any() or not count.eq(len(frame)).all():
            raise ValueError(label + ": CSV tidak lengkap; jumlah baris tidak cocok dengan kontrol ekspor.")
        if any(frame[c].nunique(dropna=False) != 1 for c in scope):
            raise ValueError(label + ": CSV mencampur beberapa aturan/filter/periode.")
        frames.append(frame)
    for col in scope:
        if frames[0][col].iloc[0] != frames[1][col].iloc[0]:
            raise ValueError("Samakan cakupan/filter sebelum membandingkan: " + col)
    merged = frames[0].merge(frames[1], on="transaction_number", how="outer", suffixes=("_aktif", "_pembanding"),
                             indicator=True, validate="one_to_one")
    results, reasons = [], []
    for row in merged.to_dict("records"):
        if row["_merge"] != "both":
            results.append("BERBEDA")
            reasons.append("HANYA_AKTIF" if row["_merge"] == "left_only" else "HANYA_PEMBANDING")
            continue
        history_ok = row["history_read_aktif"] == row["history_read_pembanding"] == "READ_OK"
        dates_ok = row["created_date_verified_aktif"] == row["created_date_verified_pembanding"] == "True"
        tested = ["status_description", "pic", "document_date", "npr_name", "notes", "user_input", "deadline"]
        if dates_ok:
            tested += ["created_at"]
        if history_ok:
            tested += ["first_process_at", "first_complete_at"]
        if history_ok and dates_ok:
            tested += ["aging_process_days", "aging_complete_days", "sla_eligible", "sla_met"]
        differences = [field for field in tested if row[field+"_aktif"] != row[field+"_pembanding"]]
        unresolved = ([] if dates_ok else ["CREATED_AT_BELUM_TERVERIFIKASI"])
        unresolved += [] if history_ok else ["HISTORY_BELUM_LENGKAP"]
        if not (row["scope_creation_dates_complete_aktif"] == row["scope_creation_dates_complete_pembanding"] == "True"):
            unresolved.append("CAKUPAN_TANGGAL_SUMBER_BELUM_LENGKAP")
        if not (row["source_rows_complete_aktif"] == row["source_rows_complete_pembanding"] == "True"):
            unresolved.append("CAKUPAN_DOKUMEN_SUMBER_BELUM_TERVERIFIKASI")
        results.append("BERBEDA" if differences else ("BELUM_DAPAT_DINILAI" if unresolved else "COCOK"))
        reasons.append(" | ".join(differences + unresolved) or "FIELD_YANG_DIBANDINGKAN_COCOK")
    merged["Hasil"], merged["Penyebab"] = results, reasons
    return merged[["transaction_number", "Hasil", "Penyebab"] + [c for c in merged if c not in {"transaction_number", "Hasil", "Penyebab", "_merge"}]]


def _npr_render_export(df, history, end_date, source_start, search_number, search_status, search_pic):
    import hashlib
    audit = _npr_export_frame(df, history, end_date, source_start, search_number, search_status, search_pic)
    content = audit.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
    with st.expander("📥 Data NPR dan pemeriksaan kesamaan", expanded=False):
        st.caption("Ekspor mengikuti filter aktif. CSV hanya untuk pemeriksaan, tidak pernah mengganti sumber grafik.")
        st.download_button("⬇️ Unduh NPR pembanding.csv", content, "NPR_Pembanding_v11_2_" + _npr_source_label() + ".csv",
                            "text/csv", key="npr_v7_download")
        st.caption("Sidik data NPR terfilter: " + hashlib.sha256(content).hexdigest())
        upload = st.file_uploader("Bandingkan dengan CSV NPR_Pembanding_v11_2 dari sumber lainnya", type=["csv"], key="npr_v7_compare_upload")
        if upload is not None:
            try:
                data = upload.getvalue()
                if len(data) > 25 * 1024 * 1024:
                    raise ValueError("CSV pembanding maksimal 25 MB.")
                other = pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
                comparison = _npr_compare_exports(audit, other)
                counts = comparison["Hasil"].value_counts().to_dict()
                st.write(counts)
                if comparison["Hasil"].eq("COCOK").all():
                    st.success("Semua nomor dan field yang diperiksa cocok pada kedua ekspor ini. Ini bukan bukti database sumber identik.")
                else:
                    st.warning("Masih ada field berbeda, tanggal dibuat belum terverifikasi, atau history belum lengkap.")
                st.dataframe(comparison, use_container_width=True, hide_index=True)
                st.download_button("⬇️ Unduh hasil perbandingan.csv", comparison.to_csv(index=False).encode("utf-8-sig"),
                                    "Perbandingan_NPR_v11_2.csv", "text/csv", key="npr_v7_compare_download")
            except (ValueError, KeyError, UnicodeError, pd.errors.ParserError) as exc:
                st.warning(str(exc))


def _npr_period_bounds_v11(start_date, end_date):
    """Require BOTH bounds from the main Select Date Range; never invent a period."""
    start = _npr_timestamp(start_date)
    end = _npr_timestamp(end_date)
    if pd.isna(start) or pd.isna(end):
        raise ValueError("Lengkapi tanggal awal dan akhir pada Select Date Range untuk NPR.")
    if start.normalize() > end.normalize():
        raise ValueError("Tanggal awal NPR tidak boleh melewati tanggal akhir pada Select Date Range.")
    return start.date(), end.date()


def run_npr_dashboard_v7(end_date, search_number, search_status, pic_column, *, start_date=None):
    """NPR v11: one period picker, supplied by main() on both adapters.

    The v10 date/milestone/SLA contract is unchanged. The lower bound now follows
    Select Date Range instead of a separate NPR control or a cumulative horizon.
    """
    try:
        source_start, end_date = _npr_period_bounds_v11(start_date, end_date)
    except ValueError as exc:
        st.info(str(exc))
        return
    # Retain only the selected period for legacy diagnostic helpers. Old NPR date
    # settings and session-state pickers cannot override the main period.
    st.session_state["_npr_main_period_v11"] = {
        "start": source_start.isoformat(), "end": end_date.isoformat()
    }
    if not _npr_source_controls(end_date, source_start):
        return
    with st.spinner("Membaca dokumen NPR..."):
        df = _npr_source_headers(source_start, end_date)
    if df.attrs.get("npr_source_state") != "OK":
        st.warning(df.attrs.get("npr_source_message", "Dokumen NPR belum berhasil dibaca; jumlah dokumen tidak dianggap nol."))
        _npr_source_diagnostics(df, None)
        return
    df = _npr_source_prepare_headers_v9(df)
    df = _npr_apply_date_policy_v9(df, preview=False)
    _npr_render_header_audit_v9(df, end_date, source_start)
    normal = df.attrs.get("npr_normalization_diagnostic", {})
    dated = apply_npr_date_filter(df, end_date, source_start)
    pic_options = ["Semua PIC"] + sorted(dated["Sales"].dropna().unique().tolist())
    # A PIC from another period must not keep the new period accidentally empty.
    if st.session_state.get("npr_v7_pic", "Semua PIC") not in pic_options:
        st.session_state["npr_v7_pic"] = "Semua PIC"
    with pic_column:
        selected_pic = st.selectbox("Pilih PIC Procurement 👤", pic_options,
                                   index=0, key="npr_v7_pic")
    filtered = _apply_npr_filters_v3(dated, search_number, search_status, selected_pic)
    date_values = _npr_date_series(df["transaction_date"])
    native = _npr_date_coverage(df)
    diag = {
        "normalized_documents": len(df), "missing_native_created_at": native["missing_created_at"],
        "preview_input_date": native["preview_input_date"], "date_policy": df.attrs["npr_date_policy"],
        "excluded_missing_document_date": int(date_values.isna().sum()),
        "excluded_before_source_start": int(date_values.lt(pd.Timestamp(source_start).normalize()).sum()) if source_start else 0,
        "excluded_after_cutoff": int(date_values.ge(pd.Timestamp(end_date).normalize()+pd.Timedelta(days=1)).sum()),
        "after_date_filter": len(dated), "after_filters": len(filtered),
        "report_end": str(pd.Timestamp(end_date).date()), "source_start": str(source_start),
        "period_source": "MAIN_SELECT_DATE_RANGE", "period_start": str(source_start),
        "period_end": str(end_date), "period_bounds": "START_INCLUSIVE_END_DAY_INCLUSIVE",
    }
    df.attrs.update(npr_filter_diagnostic=diag, npr_report_end=str(end_date))
    st.caption("Periode NPR: " + source_start.strftime("%d-%m-%Y") +
               " s.d. " + end_date.strftime("%d-%m-%Y") +
               f" | Dokumen terbaca: {len(df):,} → Sesuai periode: {len(dated):,} → Setelah filter: {len(filtered):,}")
    st.caption("Status mengikuti data terbaru saat dimuat; bukan snapshot status historis. "
               "Tanggal dokumen mengikuti kontrak v10: input_date, lalu created_at bila kosong. "
               "Aging/SLA tetap memakai milestone FIRST; batas SLA ≤2 hari kalender.")
    if not native["source_rows_complete"]:
        paging = df.attrs.get("npr_api_fetch_diagnostic", {}).get("pagination", "UNVERIFIED")
        st.warning(
            f"Daftar NPR dari sumber belum terverifikasi lengkap. Terbaca {len(df):,} dokumen; "
            f"status cakupan: {paging}. Grafik jumlah/status/PIC hanya menggambarkan dokumen yang terbaca, "
            "belum hasil final seluruh periode. Ini bukan berarti tanggal NPR kosong."
        )
    if native["missing_created_at"]:
        st.warning(f"{native['missing_created_at']:,} NPR belum memiliki tanggal canonical (input_date/created_at). "
                   "Dokumen tersebut tidak dapat dinilai pada filter tanggal maupun aging/SLA.")
    alerts = [("nomor kosong", normal.get("removed_missing_number", 0)),
              ("status tidak valid", normal.get("removed_invalid_status", 0)),
              ("baris duplikat", normal.get("duplicates_removed", 0)),
              ("PIC masih berupa ID", normal.get("unresolved_pic_ids", 0))]
    found = [f"{label}: {int(count):,}" for label, count in alerts if count]
    if found:
        st.warning("Periksa data sumber — " + " | ".join(found) + ".")
    if filtered.empty:
        if not df.empty and date_values.isna().all():
            st.error(f"{len(df):,} NPR terbaca tetapi tanggal canonical belum tersedia. Ini bukan nol dokumen. "
                     "Periksa field input_date/created_at pada sumber.")
        elif not dated.empty:
            st.info("Dokumen tersedia pada periode ini, tetapi tidak cocok dengan pencarian/status/PIC.")
        else:
            st.info("Tidak ada dokumen dengan tanggal valid dalam cakupan terpilih. Periksa ekspor sumber sebelum filter.")
        _npr_source_diagnostics(df, None)
        _npr_source_after_headers_v9(df)
        return
    date_coverage = _npr_date_coverage(filtered)
    # Histories of selected/dated documents can be read even when source
    # enumeration is unverified. Final aggregate assessment stays gated below.
    load_history = date_coverage["selected_dates_ready"]
    if not load_history:
        load_history = st.checkbox("Periksa history untuk diagnostik dokumen tanpa tanggal canonical",
                                   value=False, key="npr_v10_inspect_history_without_date")
    if load_history:
        _npr_source_history_controls(filtered)
        with st.spinner("Memeriksa history NPR..."):
            history = _npr_normalize_history(_npr_source_history(filtered))
    else:
        history = _npr_normalize_history(None)
        history.attrs["npr_history_diagnostic"] = [
            {"__npr_number": number, "History State": "PAUSED_MISSING_CANONICAL_DATE"}
            for number in filtered["No. Transaksi"]]
    coverage = _npr_coverage(filtered, history)
    st.caption(f"History diperiksa: {coverage['resolved']:,}/{coverage['total']:,} NPR. "
               "History berhasil dibaca tanpa milestone dibedakan dari history belum/gagal dibaca.")
    if load_history and not coverage["ready"]:
        st.warning(f"History {coverage['unresolved']:,} NPR belum selesai diperiksa. "
                   "Grafik dokumen tetap tampil. Aging/SLA yang sudah memiliki data valid ditampilkan "
                   "sebagai SEMENTARA dengan jumlah NPR yang dinilai; verifikasi hasil final tetap menunggu.")
    df.attrs["npr_validation_v11_1"] = {
        "selected_documents": len(filtered),
        "selected_canonical_dates_valid": date_coverage["canonical_dates_valid"],
        "selected_missing_canonical_dates": date_coverage["missing_canonical_dates"],
        "source_missing_canonical_dates": native["missing_canonical_dates"],
        "selected_dates_ready": date_coverage["selected_dates_ready"],
        "scope_dates_complete": date_coverage["scope_dates_complete"],
        "source_rows_complete": date_coverage["source_rows_complete"],
        "history_resolved": coverage["resolved"],
        "history_total": coverage["total"],
        "history_ready": coverage["ready"],
        "assessment_ready": bool(date_coverage["ready"] and coverage["ready"]),
    }
    process = build_npr_aging_first_process(filtered, history)
    complete = build_npr_aging_first_complete(filtered, history)
    sla = build_npr_sla_first_process(process)
    render_npr_dashboard(filtered, process, sla, complete,
                         history_ready=coverage["ready"], dates_ready=date_coverage["dates_ready"])
    _npr_render_export(filtered, history, end_date, source_start, search_number, search_status, selected_pic)
    _npr_source_diagnostics(df, history)
    _npr_source_after_headers_v9(df)
    if load_history:
        _npr_source_after_render(history)
def _npr_apply_date_policy_v9(df, preview=False):
    """v10 shared date policy: one canonical date on both data sources.

    input_date is preferred because it exists in production API and PostgreSQL.
    created_at is a fallback only when input_date is absent. The legacy preview
    argument is retained for UI compatibility but no longer changes calculations.
    """
    import copy
    out = df.copy()
    out.attrs = copy.deepcopy(df.attrs)
    canonical = _npr_native_created_dates(out)
    out["Created At NPR"] = canonical
    out["transaction_date"] = canonical
    if "Date Source NPR" not in out:
        out["Date Source NPR"] = "MISSING_DATE"
    out.loc[canonical.isna(), "Date Source NPR"] = "MISSING_DATE"
    out.attrs["npr_date_policy"] = "INPUT_DATE_PREFERRED_CREATED_AT_FALLBACK"
    # Date completeness describes dates only. Pagination has its own attribute.
    out.attrs["npr_scope_dates_complete"] = bool(canonical.notna().all())
    return out


def _npr_header_audit_frame_v9(df, end_date, source_start=None):
    """All normalized source headers, including undated rows excluded by date filtering."""
    def stamp(value):
        ts = _npr_timestamp(value)
        return ts.isoformat() if pd.notna(ts) else ""
    end = _npr_timestamp(end_date).normalize() + pd.Timedelta(days=1)
    start = _npr_timestamp(source_start).normalize() if source_start else None
    rows = []
    for r in df.sort_values("No. Transaksi", kind="stable").to_dict("records"):
        dt = _npr_timestamp(r.get("transaction_date"))
        membership = "UNKNOWN" if pd.isna(dt) else str(bool(dt < end and (start is None or dt >= start)))
        rows.append({
            "transaction_number": r["No. Transaksi"], "status_description": r["Status"],
            "pic": r["Sales"], "npr_name": _npr_text(r.get("Nama NPR")),
            "notes": _npr_text(r.get("Catatan")), "user_input": _npr_text(r.get("User Input")),
            "deadline": stamp(r.get("Tanggal Deadline")), "created_at": stamp(r.get("Created At NPR")),
            "document_date": stamp(dt), "document_date_source": r.get("Date Source NPR", "MISSING_DATE"),
            "in_report_period": membership, "documents_in_export": str(len(df)),
            "rule_version": NPR_PARITY_VERSION,
            "data_source": _npr_source_label(),
            "source_fetched_at_utc": df.attrs.get("npr_api_fetch_diagnostic", {}).get("fetched_at_utc", "") or df.attrs.get("npr_pg_snapshot", {}).get("fetched_at_utc", ""),
            "source_rows_complete": str(_npr_source_rows_complete_v11_1(df)),
            "date_policy": df.attrs.get("npr_date_policy", "CREATED_AT_ONLY"),
            "scope_start": str(source_start or ""), "scope_end": str(pd.Timestamp(end_date).date()),
        })
    cols = ["transaction_number", "status_description", "pic", "npr_name", "notes", "user_input",
            "deadline", "created_at", "document_date", "document_date_source", "in_report_period",
            "documents_in_export", "rule_version", "date_policy", "scope_start", "scope_end", "source_rows_complete", "data_source", "source_fetched_at_utc"]
    return pd.DataFrame(rows, columns=cols)


def _npr_compare_header_audits_v9(current, reference):
    required = list(current.columns)
    if not set(required).issubset(reference.columns):
        raise ValueError("Gunakan CSV NPR_Header_Sumber_v11_2 dari kedua dashboard, bukan CSV aging/versi lama.")
    frames = []
    scope = ["rule_version", "date_policy", "scope_start", "scope_end"]
    for label, raw in (("Aktif", current), ("Pembanding", reference)):
        f = raw[required].fillna("").astype(str).copy()
        if f.empty or f["transaction_number"].eq("").any() or f["transaction_number"].duplicated().any():
            raise ValueError(label + ": header kosong/nomor kosong/duplikat; tidak dapat disahkan cocok.")
        counts = pd.to_numeric(f["documents_in_export"], errors="coerce")
        if not counts.eq(len(f)).all():
            raise ValueError(label + ": kontrol jumlah baris gagal. Gunakan hasil ekspor lengkap.")
        if any(f[c].nunique(dropna=False) != 1 for c in scope):
            raise ValueError(label + ": ekspor mencampur beberapa aturan/periode.")
        frames.append(f)
    for col in scope:
        if frames[0][col].iloc[0] != frames[1][col].iloc[0]:
            raise ValueError("Samakan pengaturan sebelum membandingkan: " + col)
    result = frames[0].merge(frames[1], how="outer", on="transaction_number",
                            suffixes=("_aktif", "_pembanding"), indicator=True, validate="one_to_one")
    outcomes, reasons = [], []
    fields = ["status_description", "pic", "npr_name", "notes", "user_input", "deadline",
              "created_at", "document_date", "in_report_period"]
    for r in result.to_dict("records"):
        if r["_merge"] != "both":
            outcomes.append("BERBEDA")
            reasons.append("HANYA_AKTIF" if r["_merge"] == "left_only" else "HANYA_PEMBANDING")
            continue
        diff = [c for c in fields if r[c+"_aktif"] != r[c+"_pembanding"]]
        unknown = not r["created_at_aktif"] or not r["created_at_pembanding"]
        unresolved = ["TANGGAL_CANONICAL_BELUM_TERVERIFIKASI"] if unknown else []
        if not (r["source_rows_complete_aktif"] == r["source_rows_complete_pembanding"] == "True"):
            unresolved.append("CAKUPAN_DOKUMEN_SUMBER_BELUM_TERVERIFIKASI")
        outcomes.append("BERBEDA" if diff else ("BELUM_DAPAT_DINILAI" if unresolved else "COCOK"))
        reasons.append(" | ".join(diff + unresolved) or "HEADER_COCOK")
    result["Hasil"], result["Penyebab"] = outcomes, reasons
    return result[["transaction_number", "Hasil", "Penyebab"] +
                  [c for c in result if c not in {"transaction_number", "Hasil", "Penyebab", "_merge"}]]


def _npr_render_header_audit_v9(df, end_date, source_start=None):
    # Proof is recomputed from the current API snapshot on EVERY run, never copied
    # blindly from an earlier period. It changes attributes only, not data cells.
    df.attrs.pop("npr_period_reference_v11_2", None)
    audit = _npr_header_audit_frame_v9(df, end_date, source_start)
    with st.expander("🔎 Periksa dokumen sumber sebelum filter tanggal", expanded=False):
        if st.button("🔄 Muat ulang data NPR", key="npr_v11_reload"):
            _npr_source_clear()
            st.rerun()
        st.caption("Ekspor mencakup header yang diterima sumber; status in_report_period menunjukkan periode utama. "
                   "CSV hanya dipakai membandingkan, tidak pernah memasok nilai grafik atau history API.")
        st.download_button("⬇️ Unduh seluruh header NPR.csv", audit.to_csv(index=False).encode("utf-8-sig"),
                           "NPR_Header_Sumber_v11_2_" + _npr_source_label() + ".csv", "text/csv", key="npr_v9_header_csv")
        if _npr_source_label() == "API":
            st.caption("Untuk API tanpa metadata pagination: unggah ekspor header PostgreSQL v11.2 dengan Select Date Range yang sama. "
                       "Nomor dan tanggal seluruh NPR pada periode harus cocok; jumlah total yang sama saja tidak cukup.")
            other = st.file_uploader("Periksa cakupan terhadap header PostgreSQL v11.2", type=["csv"], key="npr_v11_2_period_reference")
            if other is not None:
                try:
                    content = other.getvalue()
                    if len(content) > 25 * 1024 * 1024:
                        raise ValueError("CSV pembanding maksimal 25 MB.")
                    reference = pd.read_csv(BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
                    proof, comparison = _npr_period_header_check_v11_2(audit, reference)
                    df.attrs["npr_period_reference_v11_2"] = proof
                    if proof["matched"]:
                        st.success("Nomor dan tanggal NPR pada periode cocok dengan snapshot PostgreSQL yang diunggah. "
                                   "Validasi cakupan periode lolos; history tetap wajib dibaca dari API.")
                        if not proof["all_header_fields_match"]:
                            st.warning("Cakupan cocok, tetapi ada field header berbeda. Hasil kedua dashboard belum boleh dinyatakan sama persis.")
                    else:
                        st.warning("Cakupan periode BELUM cocok. Periksa dokumen/tanggal yang berbeda; penanda lengkap tidak dipaksakan.")
                    st.write({"NPR API dalam periode": proof["api_period_documents"], "NPR PostgreSQL dalam periode": proof["reference_period_documents"],
                              "Semua field header cocok": proof["all_header_fields_match"]})
                    st.dataframe(comparison, use_container_width=True, hide_index=True)
                    st.download_button("⬇️ Unduh perbandingan header periode.csv", comparison.to_csv(index=False).encode("utf-8-sig"),
                                       "Perbandingan_Header_Periode_NPR_v11_2.csv", "text/csv", key="npr_v11_2_period_diff")
                except (ValueError, KeyError, UnicodeError, pd.errors.ParserError) as exc:
                    st.warning(str(exc))
        else:
            st.caption("Gunakan CSV header ini pada dashboard API untuk memeriksa cakupan periode yang sama. "
                       "Setelah history API selesai, gunakan NPR pembanding.csv untuk memeriksa milestone, aging, dan SLA.")

def _npr_period_header_check_v11_2(current, reference):
    """Compare *period* headers without importing any reference values into the API.

    Complete enumeration is supported only relative to the supplied, complete
    PostgreSQL snapshot. Matching counts alone is insufficient: numbers AND dates
    must match. Header field parity is a separate result.
    """
    import hashlib
    required = {"transaction_number", "document_date", "in_report_period", "documents_in_export",
                "rule_version", "date_policy", "scope_start", "scope_end", "source_rows_complete", "data_source"}
    frames = []
    for label, original in (("Aktif", current), ("Pembanding", reference)):
        if not required.issubset(original.columns):
            raise ValueError(label + ": gunakan ekspor header NPR v11.2, bukan CSV lama atau CSV aging.")
        f = original.fillna("").astype(str).copy()
        if f.empty or f["transaction_number"].str.strip().eq("").any() or f["transaction_number"].duplicated().any():
            raise ValueError(label + ": file kosong atau nomor NPR kosong/duplikat.")
        if not pd.to_numeric(f["documents_in_export"], errors="coerce").eq(len(f)).all():
            raise ValueError(label + ": jumlah baris tidak sesuai kontrol ekspor.")
        for col in ("rule_version", "date_policy", "scope_start", "scope_end", "data_source", "source_rows_complete"):
            if f[col].nunique(dropna=False) != 1:
                raise ValueError(label + ": metadata bercampur pada " + col)
        start, end = _npr_period_bounds_v11(f["scope_start"].iloc[0], f["scope_end"].iloc[0])
        dates = _npr_date_series(f["document_date"])
        expected = pd.Series("UNKNOWN", index=f.index)
        valid = dates.notna()
        inside = valid & dates.ge(pd.Timestamp(start)) & dates.lt(pd.Timestamp(end) + pd.Timedelta(days=1))
        expected.loc[valid] = inside.loc[valid].map({True: "True", False: "False"})
        if not f["in_report_period"].eq(expected).all():
            raise ValueError(label + ": penanda periode tidak sesuai tanggal dokumen.")
        frames.append((f, inside, bool(valid.all())))
    a, am, av = frames[0]
    b, bm, bv = frames[1]
    for col in ("rule_version", "date_policy", "scope_start", "scope_end"):
        if a[col].iloc[0] != b[col].iloc[0]:
            raise ValueError("Samakan Select Date Range dan versi file: " + col)
    if b["data_source"].iloc[0] != "POSTGRESQL":
        raise ValueError("Bukti cakupan harus berasal dari ekspor POSTGRESQL, bukan API lain.")
    if not b["source_rows_complete"].eq("True").all() or not bv:
        raise ValueError("Ekspor PostgreSQL belum lengkap atau memuat tanggal tidak valid.")
    aa, bb = a.loc[am].copy(), b.loc[bm].copy()
    # Compare ISO-normalized dates, never replace API values by CSV values.
    aa["document_date"] = _npr_date_series(aa["document_date"]).map(lambda t: t.isoformat())
    bb["document_date"] = _npr_date_series(bb["document_date"]).map(lambda t: t.isoformat())
    fields = ["document_date", "status_description", "pic", "npr_name", "notes", "user_input", "deadline"]
    if not set(fields).issubset(aa.columns) or not set(fields).issubset(bb.columns):
        raise ValueError("Kolom pembanding header tidak lengkap.")
    compare = aa[["transaction_number"] + fields].merge(bb[["transaction_number"] + fields],
                 on="transaction_number", how="outer", suffixes=("_api", "_postgre"), indicator=True, validate="one_to_one")
    results, reasons = [], []
    for r in compare.to_dict("records"):
        if r["_merge"] != "both":
            results.append("BERBEDA")
            reasons.append("HANYA_API" if r["_merge"] == "left_only" else "HANYA_POSTGRESQL")
        else:
            differences = [c for c in fields if r[c + "_api"] != r[c + "_postgre"]]
            results.append("BERBEDA" if differences else "COCOK")
            reasons.append(" | ".join(differences) or "HEADER_PERIODE_COCOK")
    compare["Hasil"], compare["Penyebab"] = results, reasons
    membership_ok = bool(av and len(aa) == len(bb) and compare["_merge"].eq("both").all()
                         and compare["document_date_api"].eq(compare["document_date_postgre"]).all())
    content = aa.sort_values("transaction_number")[["transaction_number", "document_date"]].to_csv(index=False, lineterminator="\n")
    proof = {"matched": membership_ok, "basis": "MATCHED_SUPPLIED_POSTGRESQL_PERIOD_EXPORT",
             "period_start": a["scope_start"].iloc[0], "period_end": a["scope_end"].iloc[0],
             "api_period_documents": len(aa), "reference_period_documents": len(bb),
             "all_header_fields_match": bool(membership_ok and compare["Hasil"].eq("COCOK").all()),
             "membership_sha256": hashlib.sha256(content.encode()).hexdigest(),
             "reference_snapshot_utc": b.get("source_fetched_at_utc", pd.Series([""])).iloc[0],
             "note": "Bukti dua snapshot pada periode ini; bukan sertifikasi database, token, atau history."}
    return proof, compare.drop(columns="_merge")


# === END NPR SHARED RULES ===



# =========================================================
# NPR API -> skema dan milestone yang sama dengan PostgreSQL
# =========================================================
# NPR memakai server BRP TEST yang diberikan pengguna, TERPISAH dari ERP PR/DO.
# Secrets opsional: [npr_api] base_url, token, endpoint, history_endpoint.
# Atau environment: SIBIMA_NPR_BASE_URL / SIBIMA_NPR_API_TOKEN.
# Token NPR tidak pernah mengambil API_TOKEN ERP secara otomatis.
# product-requests = NPR; price-update-requests = PUR (TIDAK digabung ke NPR).
# History route diuji terbatas sebelum dipakai; lihat Koneksi riwayat NPR.
# Field date_inprogress/date_complete header belum terbukti FIRST transition.
NPR_API_CACHE_TTL = NPR_PARITY_CACHE_TTL

















def _npr_setting(name: str, env_name: str, default=""):
    """Environment > secrets [npr_api] > default. Aman tanpa secrets.toml."""
    value = os.getenv(env_name)
    if value is not None and str(value).strip():
        return value
    try:
        section = st.secrets.get("npr_api", {})
        value = section.get(name)
        if value is not None and str(value).strip():
            return value
        value = st.secrets.get(env_name)
        if value is not None and str(value).strip():
            return value
    except Exception:
        pass
    return default


# NPR reader fix 2026-10-14. Tidak mengubah source/formula PR dan DO.
NPR_READER_VERSION = NPR_PARITY_VERSION
NPR_DEFAULT_FETCH_START = ""


class NPRAPIFetchError(RuntimeError):
    """Kesalahan aman tanpa URL ber-token. Tidak disimpan sebagai hasil cache."""
    def __init__(self, diagnostic):
        self.diagnostic = dict(diagnostic)
        super().__init__(str(self.diagnostic.get("state", "FETCH_ERROR")))


def _npr_clean_token(value, base_url: str) -> str:
    """Terima token atau URL product-requests yang disalin dari pesan pengguna."""
    from urllib.parse import urlsplit, parse_qs, unquote
    raw = str(value or "").strip().strip("\u200b\ufeff").strip()
    if not raw:
        return ""
    if raw.lower().startswith(("http://", "https://")):
        try:
            source, expected = urlsplit(raw), urlsplit(base_url)
            same_origin = (source.scheme, source.hostname, source.port or 443) == (
                expected.scheme, expected.hostname, expected.port or 443
            )
            endpoint_path = unquote(source.path).rstrip("/")
            allowed_path = expected.path.rstrip("/") + "/product-requests"
            if (not same_origin or source.username or source.password or source.fragment
                    or endpoint_path != allowed_path):
                raise ValueError("URL token harus endpoint product-requests pada server NPR yang dipilih.")
            values = parse_qs(source.query).get("token", [])
            if len(values) != 1:
                raise ValueError("URL NPR harus mempunyai tepat satu parameter token.")
            raw = values[0].strip()
        except ValueError:
            raise ValueError("URL token tidak valid atau tidak berasal dari endpoint NPR pada server yang dipilih.") from None
    if (not raw or any(ch.isspace() for ch in raw) or any(ch in raw for ch in ("?", "&", "\u200b", "\ufeff"))):
        raise ValueError("Token tidak valid: gunakan token saja atau URL product-requests lengkap, tanpa spasi di tengah.")
    return raw


def _npr_safe_message(value, token="") -> str:
    """Pesan API singkat; URL query, token, dan kredensial panjang disamarkan."""
    import re
    text = value if isinstance(value, str) else ""
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"https?://[^\s<>\"']+", "[URL disamarkan]", text)
    text = re.sub(r"(?i)(token|password|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "[REDACTED]", text)
    return " ".join(text.split())[:260]


def _npr_api_config() -> dict:
    """NPR connection from existing config; period comes only from Select Date Range.

    An ERP token may be reused only for the exact same HTTPS origin AND API path.
    A nonblank but invalid NPR token is an error, not permission to try another key.
    """
    from urllib.parse import urlsplit, unquote
    base = str(st.session_state.get("_npr_api_base_v6") or _npr_setting(
        "base_url", "SIBIMA_NPR_BASE_URL", "https://erp.sibima.id/api/"
    )).strip().rstrip("/") + "/"
    base = _npr_validate_base_v6(base)
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and ".." not in unquote(parsed.path).split("/"))
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Base URL NPR harus HTTPS tanpa token, query, username, atau password.")
    token_value = _npr_setting("token", "SIBIMA_NPR_API_TOKEN", "")
    fetch_start = _npr_default_source_start()
    # Preserve an existing session credential only on its original host/path.
    saved = st.session_state.get("_npr_api_credentials", {})
    if saved.get("base_url") == base and str(saved.get("token", "")).strip():
        token_value = saved["token"]
    if not str(token_value or "").strip():
        erp_base = str(BASE_URL.get("erp", "")).strip().rstrip("/") + "/"
        try:
            erp = urlsplit(erp_base)
            same_erp = (
                parsed.scheme == erp.scheme == "https"
                and parsed.hostname == erp.hostname
                and (parsed.port or 443) == (erp.port or 443)
                and parsed.path.rstrip("/") == erp.path.rstrip("/")
                and not erp.username and not erp.password and not erp.query and not erp.fragment
            )
        except ValueError:
            same_erp = False
        if same_erp:
            token_value = str(API_TOKEN or "").strip()
            if not token_value:
                try:
                    token_value = str(st.secrets.get("SIBIMA_API_TOKEN", "") or
                                      st.secrets.get("api", {}).get("token", "") or "").strip()
                except Exception:
                    token_value = ""
    token_error = ""
    try:
        token = _npr_clean_token(token_value, base)
    except ValueError as exc:
        # Invalid credentials remain visible as a configuration error.
        token, token_error = "", str(exc)
    end_param = str(_npr_setting("end_date_param", "SIBIMA_NPR_END_DATE_PARAM", "end_date")).strip()
    if end_param not in {"end_date", "date_end"}:
        raise ValueError("end_date_param NPR harus end_date atau date_end.")
    return {
        "base_url": base,
        "token": token,
        "token_config_error": token_error,
        "end_date_param": end_param,
        "fetch_start_date": str(fetch_start) if fetch_start else None,
        "history_endpoint": str(_npr_setting("history_endpoint", "SIBIMA_NPR_HISTORY_ENDPOINT", "")).strip(),
        "discover_history": str(_npr_setting("discover_history", "SIBIMA_NPR_DISCOVER_HISTORY", "true")).casefold() in {"1", "true", "yes"},
    }


def _npr_list_page(payload):
    """Baca list/header dan wrapper pagination tanpa me-flatten items produk."""
    meta = {}
    page_keys = ("current_page", "page", "last_page", "total_pages", "next_page_url", "has_more", "total")

    def visit(obj, depth=0):
        if depth > 8:
            return [], False
        if isinstance(obj, list):
            if any(not isinstance(row, dict) for row in obj):
                return [], False
            return obj, True
        if not isinstance(obj, dict):
            return [], False
        if str(obj.get("success", True)).strip().casefold() in {"false", "0"}:
            return [], False
        # Satu header dapat punya "items"; jangan menganggap items sebagai daftar NPR.
        if any(k in obj for k in ("transaction_number", "no_npr", "npr_number", "No. Transaksi")):
            return [obj], True
        for candidate in (obj, obj.get("meta"), obj.get("pagination"), obj.get("paginate")):
            if isinstance(candidate, dict):
                meta.update({key: candidate[key] for key in page_keys if key in candidate})
        for key in ("data", "results", "records", "product_requests", "items"):
            if isinstance(obj.get(key), (list, dict)):
                rows, valid = visit(obj[key], depth + 1)
                if valid:
                    return rows, True
        if obj and all(str(k).isdigit() and isinstance(v, dict) for k, v in obj.items()):
            return list(obj.values()), True
        return [], False

    rows, recognized = visit(payload)
    return rows, meta, recognized


def _npr_terminal_page(meta: dict, page: int, count: int):
    """None berarti list tanpa metadata pagination, bukan bukti seluruh DB lengkap."""
    for key in ("last_page", "total_pages"):
        if meta.get(key) is not None:
            try:
                return page >= int(meta[key])
            except (ValueError, TypeError):
                return None
    if "next_page_url" in meta:
        return not bool(meta["next_page_url"])
    if "has_more" in meta:
        text = str(meta["has_more"]).strip().casefold()
        if text in {"false", "0", "none", ""}:
            return True
        if text in {"true", "1"}:
            return False
    return None


def _create_npr_session():
    # Retry manual: mencegah logger urllib3 menuliskan URL lengkap ber-token.
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "Sibima-NPR-Dashboard/20261014"})
    session.mount("https://", HTTPAdapter(max_retries=0))
    return session


def _npr_request_json(session, base_url, endpoint, params, diagnostic, read_timeout=None):
    """Transport NPR: TLS aktif, redirect origin sama, 429 tanpa retry cepat."""
    from urllib.parse import urlsplit, urljoin, parse_qsl, unquote, urlunsplit
    base = urlsplit(base_url)
    url, request_params = base_url + endpoint, dict(params)
    token = str(params.get("token", ""))
    timeout = REQUEST_TIMEOUT if read_timeout is None else read_timeout
    for redirect_count in range(4):
        response = _npr_get_response_v5(session, url, request_params, base_url, diagnostic, timeout)
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                target = urlsplit(urljoin(url, response.headers.get("Location", "")))
                try:
                    allowed = ((target.scheme, target.hostname, target.port or 443) ==
                               (base.scheme, base.hostname, base.port or 443)
                               and not target.username and not target.password and not target.fragment
                               and unquote(target.path).startswith(base.path)
                               and ".." not in unquote(target.path).split("/")
                               and bool(response.headers.get("Location")))
                except ValueError:
                    allowed = False
                if not allowed or redirect_count >= 3:
                    diagnostic["state"] = "REDIRECT_BLOCKED"
                    raise NPRAPIFetchError(diagnostic)
                url = urlunsplit((target.scheme, target.netloc, target.path, "", ""))
                request_params = {**dict(parse_qsl(target.query)), **request_params}
                diagnostic["redirects"] = redirect_count + 1
                continue
            if response.status_code != 200:
                diagnostic["state"] = f"HTTP_{response.status_code}"
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        message = _npr_safe_message(payload.get("message", ""), token)
                        if message:
                            diagnostic["server_message"] = message
                except (ValueError, TypeError):
                    pass
                raise NPRAPIFetchError(diagnostic)
            try:
                payload = response.json()
            except ValueError:
                diagnostic["state"] = "INVALID_JSON"
                raise NPRAPIFetchError(diagnostic) from None
            if isinstance(payload, dict) and str(payload.get("success", True)).strip().casefold() in {"false", "0"}:
                diagnostic["state"] = "API_REJECTED"
                message = _npr_safe_message(payload.get("message", ""), token)
                if message:
                    diagnostic["server_message"] = message
                raise NPRAPIFetchError(diagnostic)
            return payload
        finally:
            response.close()
    diagnostic["state"] = "REDIRECT_BLOCKED"
    raise NPRAPIFetchError(diagnostic)



@st.cache_data(ttl=NPR_API_CACHE_TTL, show_spinner=False)
def _cached_npr_header_rows_v3(base_url: str, token: str, endpoint: str,
                               start_date: str, end_date: str, end_date_param: str,
                               reader_version: str = NPR_READER_VERSION) -> pd.DataFrame:
    """Fetch NPR headers with full pagination, including bare-list APIs.

    v10 rule: a response without pagination metadata is NOT assumed complete after
    page 1. The reader probes page 2+ until an empty page, explicit terminal
    metadata, or proof that the backend ignores the page parameter.
    """
    import hashlib
    import json
    from datetime import timezone
    diagnostic = {
        "reader_version": reader_version, "state": "NOT_FETCHED", "base_url": base_url,
        "endpoint": endpoint, "date_start": start_date, end_date_param: end_date,
        "date_end_parameter": end_date_param, "pages": 0, "headers_returned": 0,
        "pagination": "UNKNOWN", "pagination_complete": False, "request_attempts": 0,
    }
    session = _create_npr_session()
    all_rows, signatures = [], set()
    base_params = {k: v for k, v in {
        "date_start": start_date, end_date_param: end_date, "token": token
    }.items() if v is not None and v != ""}
    per_page = 200

    def finish(label, complete):
        diagnostic.update(
            fetched_at_utc=datetime.now(timezone.utc).isoformat(),
            state="OK", headers_returned=len(all_rows), pagination=label,
            pagination_complete=bool(complete),
        )
        diagnostic.pop("headers_received_before_failure", None)
        out = pd.DataFrame(all_rows)
        out.attrs["npr_api_fetch_diagnostic"] = diagnostic.copy()
        return out

    try:
        for page in range(1, 501):
            request_params = dict(base_params)
            request_params.update({"page": page, "per_page": per_page})
            diagnostic["pages"] = page
            payload = _npr_request_json(session, base_url, endpoint, request_params, diagnostic)
            rows, meta, recognized = _npr_list_page(payload)
            if not recognized:
                diagnostic["state"] = "UNSUPPORTED_PAYLOAD"
                raise NPRAPIFetchError(diagnostic)

            if not rows:
                terminal = _npr_terminal_page(meta, page, 0)
                if terminal is False:
                    diagnostic["state"] = "INCOMPLETE_PAGINATION"
                    raise NPRAPIFetchError(diagnostic)
                if meta.get("total") is not None:
                    try:
                        expected = int(meta["total"])
                    except (TypeError, ValueError):
                        diagnostic["state"] = "INVALID_PAGINATION_META"
                        raise NPRAPIFetchError(diagnostic) from None
                    if expected != len(all_rows):
                        diagnostic.update(state="TOTAL_MISMATCH", expected_total=expected)
                        raise NPRAPIFetchError(diagnostic)
                return finish("EMPTY_PAGE_TERMINAL" if not meta else "METADATA_EMPTY_TERMINAL", True)

            signature = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
            if signature in signatures:
                # Backend ignored ?page=. Keep the unique first response but flag scope as unproven.
                return finish("PAGE_PARAMETER_IGNORED_NO_METADATA", False)
            signatures.add(signature)
            all_rows.extend(rows)
            diagnostic["headers_received_before_failure"] = len(all_rows)

            terminal = _npr_terminal_page(meta, page, len(rows))
            if terminal is True:
                if meta.get("total") is not None:
                    try:
                        expected_total = int(meta["total"])
                    except (ValueError, TypeError):
                        expected_total = None
                    if expected_total is not None and expected_total != len(all_rows):
                        diagnostic.update(state="TOTAL_MISMATCH", expected_total=expected_total)
                        raise NPRAPIFetchError(diagnostic)
                return finish("METADATA_COMPLETE", True)
            if terminal is False:
                continue

            # No pagination metadata: continue probing page+1. If the next page is
            # empty we know the scope is complete; if it repeats we know paging is ignored.
            diagnostic["pagination"] = "PROBING_BARE_LIST_PAGES"

        diagnostic["state"] = "INCOMPLETE_PAGINATION"
        raise NPRAPIFetchError(diagnostic)
    except NPRAPIFetchError:
        raise
    except Exception as exc:
        diagnostic.update(state="FETCH_ERROR", error_type=type(exc).__name__)
        raise NPRAPIFetchError(diagnostic) from None
    finally:
        session.close()

def _fetch_npr_header_rows_api(base_url: str, token: str, endpoint: str,
                                start_date=None, end_date=None, end_date_param="end_date") -> pd.DataFrame:
    """Read optional date scope. No implicit 2025 lower bound or truncated-date fallback."""
    diagnostic = {"state": "NOT_FETCHED", "reader_version": NPR_READER_VERSION,
                  "base_url": base_url, "endpoint": endpoint, "headers_returned": 0}
    try:
        endpoint = _npr_safe_route(endpoint)
        token = _npr_clean_token(token, base_url)
        if not token:
            diagnostic["state"] = "TOKEN_MISSING"
            raise NPRAPIFetchError(diagnostic)
        if end_date_param not in {"end_date", "date_end"}:
            raise ValueError("Unsupported date parameter")
        start, end = _npr_timestamp(start_date), _npr_timestamp(end_date)
        if (_npr_text(start_date) and pd.isna(start)) or (_npr_text(end_date) and pd.isna(end)):
            diagnostic["state"] = "INVALID_DATE"
            raise NPRAPIFetchError(diagnostic)
        if pd.notna(start) and pd.notna(end) and start.date() > end.date():
            diagnostic["state"] = "INVALID_DATE_RANGE"
            raise NPRAPIFetchError(diagnostic)
        return _cached_npr_header_rows_v3(
            base_url, token, endpoint, start.date().isoformat() if pd.notna(start) else "",
            end.date().isoformat() if pd.notna(end) else "", end_date_param, reader_version=NPR_READER_VERSION)
    except NPRAPIFetchError as exc:
        diagnostic = exc.diagnostic
    except ValueError:
        diagnostic["state"] = "INVALID_CONFIG"
    out = pd.DataFrame()
    out.attrs["npr_api_fetch_diagnostic"] = diagnostic
    logger.warning("NPR API read failed: state=%s", diagnostic.get("state"))
    return out


def _clear_npr_caches():
    """Muat ulang header saja. Cache history sukses/cooldown tidak dihapus."""
    func = globals().get("_cached_npr_header_rows_v3")
    if hasattr(func, "clear"):
        func.clear()


def _render_npr_connection_controls(end_date):
    return _npr_source_controls(end_date, None)


def _npr_failure_message(diagnostic):
    state = str(diagnostic.get("state", "UNKNOWN"))
    messages = {
        "TOKEN_MISSING": "Token API NPR belum diisi.",
        "HTTP_401": "Server menolak autentikasi (HTTP 401). Periksa/ganti token NPR.",
        "HTTP_403": "Akses NPR ditolak (HTTP 403). Periksa izin token atau pembatasan akses server.",
        "HTTP_404": "Endpoint NPR tidak ditemukan (HTTP 404). Periksa base URL dan route product-requests.",
        "HTTP_422": "Parameter request ditolak (HTTP 422). Lihat tanggal dan pesan server pada diagnostik.",
        "HTTP_429": "Server membatasi request (HTTP 429). Tunggu Retry-After pada diagnostik; hasil riwayat sukses tidak dihapus.",
        "RATE_LIMIT_WAIT": "Request NPR belum dikirim karena masa tunggu server belum berakhir. Lihat retry_after_seconds pada diagnostik.",
        "CONNECTION_ERROR": "Koneksi dari komputer yang menjalankan Streamlit ke server NPR gagal. Periksa DNS, jaringan, VPN, atau proxy.",
        "SSL_ERROR": "Verifikasi sertifikat HTTPS server NPR gagal. Periksa sertifikat server/CA pada komputer; verifikasi TLS tidak dinonaktifkan.",
        "TIMEOUT": "Server NPR tidak merespons dalam batas waktu. Coba muat ulang atau uji tanggal akhir 26 Juni 2026.",
        "INVALID_JSON": "Server merespons, tetapi isinya bukan JSON valid. Respons mungkin halaman login atau kesalahan server.",
        "API_REJECTED": "API menyatakan request tidak berhasil. Periksa pesan server pada diagnostik.",
        "NORMALIZATION_ERROR": "Respons API telah diterima, tetapi normalisasi header NPR gagal. Periksa error_type pada diagnostik.",
        "UNSUPPORTED_PAYLOAD": "Format respons NPR belum dikenali; data tidak dianggap sebagai nol dokumen.",
        "INCOMPLETE_PAGINATION": "Pembacaan halaman NPR belum lengkap/berulang. Angka parsial tidak dipakai untuk grafik.",
        "TOTAL_MISMATCH": "Jumlah NPR yang diterima berbeda dari total yang diumumkan API. Angka parsial tidak dipakai untuk grafik.",
        "REDIRECT_BLOCKED": "Request dialihkan ke lokasi yang tidak diizinkan atau berulang. Token tidak diteruskan ke host lain.",
        "INVALID_DATE": "Parameter tanggal NPR tidak valid.",
        "INVALID_DATE_RANGE": "Batas awal NPR lebih besar dari tanggal akhir laporan.",
        "INVALID_CONFIG": "Konfigurasi route atau token NPR tidak valid.",
    }
    message = messages.get(state, f"Pembacaan API NPR belum berhasil ({state}). Periksa diagnostik.")
    return message + " Kegagalan pembacaan ini bukan berarti jumlah NPR = 0."



def _render_npr_diagnostics(df, end_date, filtered=None, history=None, expanded=False):
    import json
    with st.expander("ℹ️ Informasi Teknis Dashboard", expanded=expanded or bool(df.attrs.get("npr_diagnostics_expanded"))):
        fetch_diag = dict(df.attrs.get("npr_api_fetch_diagnostic", {}))
        normalization = dict(df.attrs.get("npr_normalization_diagnostic", {}))
        overview = {"fetch": fetch_diag, "normalization": normalization,
                    "headers_valid": len(df), "report_end": str(end_date),
                    "after_filters": len(filtered) if filtered is not None else df.attrs.get("npr_filter_diagnostic", {}).get("after_filters"),
                    "filters": df.attrs.get("npr_filter_diagnostic", {}),
                    "date_field_profile": df.attrs.get("npr_date_field_profile", {}),
                    "created_at_recovery": df.attrs.get("npr_created_recovery", {}),
                    "recovery_connection": df.attrs.get("npr_recovery_history_connection", {}),
                    "validation": df.attrs.get("npr_validation_v11_1", {}),
                    "period_reference": df.attrs.get("npr_period_reference_v11_2", {}),
                    "source_date_coverage": _npr_date_coverage(df)}
        if history is not None:
            overview["history_connection"] = history.attrs.get("npr_history_connection", {})
            overview["history_documents"] = history.attrs.get("npr_history_diagnostic", [])
            from collections import Counter
            records = overview["history_documents"]
            overview["history_summary"] = {
                "states": dict(Counter(r.get("History State", "UNKNOWN") for r in records)),
                "first_process_filled": int(history["__first_process_date"].notna().sum()),
                "first_complete_filled": int(history["__first_complete_date"].notna().sum()),
            }
        st.json({key: value for key, value in overview.items() if key != "history_documents"})
        st.caption("Status NPR mengikuti data terbaru. Tanggal awal dan akhir mengikuti Select Date Range; ini bukan snapshot status historis.")
        if not _npr_source_rows_complete_v11_1(df):
            st.warning("Cakupan header API belum terbukti lengkap. Reader sudah mencoba page berikutnya; lihat field pagination pada diagnostik.")
        st.caption("Snapshot header API: " + str(fetch_diag.get("fetched_at_utc", "-")) +
                   ". Snapshot dipertahankan selama pemuatan history; Muat ulang data NPR memulai pembacaan baru.")
        st.caption("Kontrak v10 memakai input_date pada kedua sumber bila tersedia, dan created_at hanya sebagai fallback. Dasar tanggal yang sama dipakai untuk filter, aging, SLA, heatmap, dan timeline.")
        st.caption("Aging memakai riwayat status pertama. date_inprogress/date_complete pada header tidak otomatis dianggap sebagai transisi pertama.")
        st.download_button(
            "⬇️ Unduh diagnostik NPR.json", data=json.dumps(overview, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            file_name="Diagnostik_NPR_API.json", mime="application/json", key="npr_diagnostic_download_v3",
        )
        if history is not None:
            details = pd.DataFrame(history.attrs.get("npr_history_diagnostic", []))
            if not details.empty:
                st.dataframe(details, use_container_width=True, hide_index=True)
















def _npr_endpoint_candidates():
    # Endpoint terkonfirmasi dari pengguna. Tidak menebak new-product-requests.
    configured = str(_npr_setting("endpoint", "SIBIMA_NPR_ENDPOINT", "product-requests")).strip("/")
    if configured == "price-update-requests":
        raise ValueError("price-update-requests adalah PUR, bukan NPR.")
    return [configured]


def _npr_safe_route(route: str) -> str:
    route = str(route).strip().strip("/")
    if not route or ":" in route or ".." in route or "?" in route or "#" in route:
        raise ValueError("Route NPR harus relatif terhadap base URL khusus NPR.")
    return route


def _load_npr_user_lookup_api() -> dict:
    """Lookup opsional di server NPR yang SAMA; pic dari API sudah berupa nama."""
    endpoint = str(_npr_setting("users_endpoint", "SIBIMA_NPR_USERS_ENDPOINT", "")).strip()
    if not endpoint:
        return {}
    config = _npr_api_config()
    try:
        users = _fetch_npr_header_rows_api(config["base_url"], config["token"], _npr_safe_route(endpoint))
    except Exception:
        return {}
    lookup = {}
    for row in users.to_dict("records"):
        if _npr_text(_npr_pick(row, ("deleted_at",))):
            continue
        key = _npr_id(_npr_pick(row, ("id", "user_id")))
        name = _npr_person_name(_npr_pick(row, ("name", "full_name", "display_name")))
        if key and name:
            lookup[key] = name
    return lookup




def _normalize_full_npr_api(raw: pd.DataFrame, endpoint: str, users: dict):
    return normalize_npr_headers(raw, users=users, endpoint=endpoint, allow_input_date_preview=False)



def load_full_npr_from_api(start_date=None, end_date=None):
    """Send the main period using existing configured date parameter names.

    Local canonical filtering remains authoritative. Sending parameters does NOT
    prove the server implements them; a PostgreSQL export can verify the period.
    Pin headers through the slow history cycle to avoid repeated HTTP requests.
    """
    config = _npr_api_config()
    endpoint = _npr_endpoint_candidates()[0]
    import time
    import copy
    pin_key = _npr_header_session_key_v11_2(config, start_date, end_date)
    pinned = st.session_state.get(pin_key)
    history_store = _npr_history_store_v5(_npr_history_settings())
    completed = history_store.get("snapshot_completed_at")
    if pinned is not None and (completed is None or time.time() - completed < NPR_PARITY_CACHE_TTL):
        return copy.deepcopy(pinned)
    raw = _fetch_npr_header_rows_api(config["base_url"], config["token"], endpoint,
                                   start_date=start_date,
                                   end_date=end_date,
                                   end_date_param=config["end_date_param"])
    diagnostic = dict(raw.attrs.get("npr_api_fetch_diagnostic", {}))
    diagnostic["date_filter_policy"] = "EXPLICIT_REQUEST_DATES_AND_LOCAL_CANONICAL_FILTER"
    diagnostic["server_date_contract"] = "PARAMETERS_SENT_SUPPORT_REQUIRES_REFERENCE_CHECK"
    diagnostic["header_snapshot_policy"] = "PINNED_DURING_HISTORY_CYCLE"
    out = _npr_empty_frame()
    if not raw.empty:
        try:
            needs_users = bool({str(c).casefold() for c in raw.columns}.intersection({"pic_id", "created_by", "created_by_id"}))
            users = _load_npr_user_lookup_api() if needs_users else {}
            out = normalize_npr_headers(raw, users=users, endpoint=endpoint, allow_input_date_preview=False)
        except Exception as exc:
            diagnostic.update(state="NORMALIZATION_ERROR", error_type=type(exc).__name__)
    out.attrs.update(npr_api_fetch_diagnostic=diagnostic, npr_api_base_url=config["base_url"],
                     npr_api_fetch_start=str(start_date) if start_date is not None else None,
                     npr_source_rows_complete=bool(diagnostic.get("pagination_complete", False)))
    out.attrs["npr_date_field_profile"] = _npr_date_field_profile(raw)
    if diagnostic.get("state") == "OK":
        # Bound per-session memory. No credentials or data are persisted to disk.
        old_keys = [k for k in st.session_state if str(k).startswith("_npr_pinned_headers_v11_2_")]
        if pin_key not in old_keys and len(old_keys) >= 8:
            st.session_state.pop(old_keys[0], None)
        st.session_state[pin_key] = copy.deepcopy(out)
    return out


# =========================================================
# NPR v4: koneksi riwayat terverifikasi, bukan tanggal perkiraan
# =========================================================
NPR_MILESTONE_CONTRACT = "npr_pg_first_milestones_v1"
NPR_HISTORY_GOOD_STATES = {"API_HISTORY", "NESTED_HISTORY", "API_FIRST_MILESTONES"}
NPR_HISTORY_BATCH_SIZE = 20


def _npr_history_settings() -> dict:
    """Opsi NPR saja. Kecepatan tidak termasuk key cache hasil riwayat."""
    config = _npr_api_config()
    default_route = "product-requests/history" if config["base_url"] == "https://erp.sibima.id/api/" else ""
    config.update({
        "history_endpoint": str(_npr_setting("history_endpoint", "SIBIMA_NPR_HISTORY_ENDPOINT", default_route)).strip(),
        "discover_history": str(_npr_setting("discover_history", "SIBIMA_NPR_DISCOVER_HISTORY", "true")).casefold() in {"1", "true", "yes"},
        "batch_size": NPR_HISTORY_BATCH_SIZE,
        "success_ttl": float(NPR_PARITY_CACHE_TTL),
    })
    old = st.session_state.get("_npr_history_options_v4", {})
    saved = st.session_state.get("_npr_history_options_v5", old)
    if saved.get("base_url") == config["base_url"]:
        config["history_endpoint"] = saved.get("history_endpoint", config["history_endpoint"])
        config["discover_history"] = bool(saved.get("discover_history", config["discover_history"]))
        config["batch_size"] = saved.get("batch_size", NPR_HISTORY_BATCH_SIZE)
    try:
        config["batch_size"] = max(1, min(100, int(config["batch_size"])))
    except (TypeError, ValueError):
        config["batch_size"] = NPR_HISTORY_BATCH_SIZE
    config["min_request_interval"] = _npr_transport_policy_v5(config["base_url"])["interval"]
    return config



def _npr_history_scope_key(config: dict) -> str:
    import hashlib
    # Token hanya masuk hash privat, tidak ditulis ke file diagnostik.
    value = (NPR_PARITY_VERSION, config["base_url"], config["token"], config["history_endpoint"], config["discover_history"])
    return hashlib.sha256(repr(value).encode()).hexdigest()[:20]


def _npr_clear_history_caches():
    """Hapus hasil secara eksplisit; TIDAK membatalkan cooldown dari server."""
    config = _npr_history_settings()
    _npr_shared_history_clear_v12(config)
    st.session_state.pop("_npr_history_store_v5_" + _npr_history_scope_key(config), None)
    for key in list(st.session_state):
        if str(key).startswith("_npr_history_limit_v4_"):
            st.session_state.pop(key, None)



def _render_npr_history_controls(df: pd.DataFrame):
    config = _npr_history_settings()
    with st.expander("🔗 Pengaturan koneksi riwayat NPR", expanded=False):
        st.caption(
            "Riwayat seluruh NPR terpilih dimuat otomatis dalam satu proses. Koneksi HTTP dipakai ulang "
            "dan hasil sukses disimpan per dokumen. Snapshot sesi dipertahankan selama pemuatan. "
            "Salinan riwayat di memori aplikasi dapat dipakai ulang sampai 10 menit sejak tiap NPR dibaca, "
            "termasuk setelah refresh browser selama proses aplikasi masih aktif. Restart aplikasi menghapus cache memori."
        )
        with st.form("npr_history_form_v5"):
            route = st.text_input("Endpoint riwayat", value=config["history_endpoint"],
                                  help="Route relatif terhadap API NPR, tanpa token.")
            discover = st.checkbox("Periksa kandidat route history otomatis", value=config["discover_history"])
            interval = st.number_input("Jeda minimal antar-request NPR (detik)", min_value=1.0, max_value=60.0,
                                       value=float(config["min_request_interval"]), step=0.5)
            submitted = st.form_submit_button("Terapkan dan lanjutkan pemuatan")
        if submitted:
            try:
                clean_route = _npr_safe_route(route) if route.strip() else ""
                if any(c in clean_route.replace("{id}", "").replace("{transaction_number}", "") for c in "{}"):
                    raise ValueError("Placeholder history hanya {id} atau {transaction_number}.")
            except ValueError as exc:
                st.warning(str(exc))
                return
            st.session_state["_npr_history_options_v5"] = {
                "base_url": config["base_url"], "history_endpoint": clean_route, "discover_history": discover,
                "min_request_interval": float(interval), "batch_size": int(config["batch_size"]),
            }
            config = _npr_history_settings()
            _npr_history_store_v5(config)["run"].update(
                request=True, auto=True, rate_limit_streak=0, pause_reason="",
            )
        if st.button("Cek ulang koneksi tanpa menghapus hasil sukses", key="recheck_npr_history_v5"):
            store = _npr_history_store_v5(config)
            store["selection"] = None
            store["run"].update(request=True, auto=True, rate_limit_streak=0, pause_reason="")
        if st.button("Perbarui riwayat dari API", key="clear_npr_history_success_v5"):
            _npr_clear_history_caches()
        st.caption("date_inprogress/date_complete header tidak otomatis dianggap FIRST. Tanggal kosong tetap tidak dinilai.")
    store = _npr_history_store_v5(config)
    wait = _npr_cooldown_status_v5(config["base_url"])["wait_seconds"]
    if wait:
        st.warning(
            f"Server meminta jeda sekitar {wait:,} detik. Pemuatan otomatis melanjutkan setelah jeda "
            "jika belum dijeda pengguna; hasil sukses tetap tersimpan."
        )
    buttons = st.columns(2)
    with buttons[0]:
        if st.button("Jeda pemuatan", key="npr_stop_batches_v5"):
            store["run"].update(request=False, auto=False, initial_attempted=True, pause_reason="USER_PAUSED")
    with buttons[1]:
        if st.button("Lanjutkan pemuatan", key="npr_all_batches_v5", disabled=bool(wait)):
            store["run"].update(request=True, auto=True, rate_limit_streak=0, pause_reason="")
    transport_status = _npr_cooldown_status_v5(config["base_url"])
    configured_interval = float(config["min_request_interval"])
    adaptive_interval = float(transport_status.get("adaptive_interval", configured_interval))
    effective_interval = max(configured_interval, adaptive_interval)
    st.caption(
        f"Pemuatan otomatis seluruh NPR terpilih | Jeda pengaturan: {configured_interval:g} detik | "
        f"Jeda efektif saat ini: {effective_interval:g} detik. "
        "Koneksi HTTP dipakai ulang. Jika server membatasi request, jeda efektif bertambah dan "
        "Retry-After tetap dipatuhi. Hasil sukses tidak diulang saat melanjutkan "
        "(maksimal 6 pembatasan beruntun tanpa kemajuan sebelum perlu dilanjutkan kembali)."
    )



def _npr_extract_history_page(payload, *, history_endpoint: bool):
    """Wrapper history/data/records; tidak masuk ke items milik produk.

    Metadata header tidak disamakan dengan metadata daftar history. Penanda
    __parent_number/__parent_id membuktikan history milik NPR yang diminta.
    """
    events, meta = [], {}
    recognized = False
    page_keys = ("current_page", "page", "last_page", "total_pages", "next_page_url", "has_more", "total")
    def visit(obj, inside=False, number="", identifier="", depth=0):
        nonlocal recognized
        if depth > 10:
            return
        if isinstance(obj, list):
            if inside:
                recognized = True
            for row in obj:
                visit(row, inside, number, identifier, depth + 1)
            return
        if not isinstance(obj, dict):
            return
        lower = {str(k).casefold(): v for k, v in obj.items()}
        explicit = [k for k in lower if k in NPR_HISTORY_KEYS]
        parent_no = _npr_text(_npr_pick(obj, ("transaction_number", "npr_number", "no_npr"))) or number
        # id pada EVENT adalah history id, bukan NPR id: dipakai hanya di wrapper.
        parent_id = _npr_id(_npr_pick(obj, ("product_request_id", "product_requests_id", "npr_id"))) or identifier
        if explicit:
            meta["explicit_history_wrapper"] = True
            # Pada endpoint history, pagination di sebelah data.history adalah
            # pagination history, bukan diabaikan sebagai metadata header.
            if inside or history_endpoint:
                for field in page_keys:
                    if field in lower:
                        meta[field] = lower[field]
                for key in ("meta", "pagination", "paginate"):
                    if isinstance(lower.get(key), dict):
                        for field in page_keys:
                            if field in lower[key]:
                                meta[field] = lower[key][field]
            for key in ("history_meta", "history_pagination"):
                if isinstance(lower.get(key), dict):
                    for field in page_keys:
                        if field in lower[key]:
                            meta[field] = lower[key][field]
            parent_id = _npr_id(_npr_pick(obj, ("product_request_id", "product_requests_id", "npr_id", "id"))) or identifier
            if parent_no:
                meta["document_number"] = parent_no
            if parent_id:
                meta["document_id"] = parent_id
            for key in explicit:
                if isinstance(lower[key], (dict, list)):
                    recognized = True
                    visit(lower[key], True, parent_no, parent_id, depth + 1)
            return
        if inside:
            for key in page_keys:
                if key in lower:
                    meta[key] = lower[key]
            for key in ("meta", "pagination", "paginate"):
                if isinstance(lower.get(key), dict):
                    for field in page_keys:
                        if field in lower[key]:
                            meta[field] = lower[key][field]
        for key in ("data", "results", "records", "events"):
            if isinstance(lower.get(key), (dict, list)):
                if parent_no:
                    meta["document_number"] = parent_no
                if parent_id:
                    meta["document_id"] = parent_id
                visit(lower[key], inside or history_endpoint, parent_no, parent_id, depth + 1)
                return
        if inside and any(k in lower for k in ("status", "status_code", "status_id", "status_description", "status_name")):
            if "items" in lower and not meta.get("explicit_history_wrapper"):
                meta["header_payload_detected"] = True
                return
            recognized = True
            wanted_keys = {
                "status", "status_code", "status_id", "status_description", "status_name",
                "updated_at", "created_at", "deleted_at", "transaction_number", "npr_number", "no_npr",
                "product_request_id", "product_requests_id", "npr_id", "request_id", "__parent_number", "__parent_id", "id",
            }
            event = {k: v for k, v in lower.items() if k in wanted_keys}
            if number:
                event["__parent_number"] = number
            if identifier:
                event["__parent_id"] = identifier
            events.append(event)
    if isinstance(payload, dict) and str(payload.get("success", True)).casefold() in {"false", "0"}:
        return [], {}, False
    visit(payload, history_endpoint)
    return events, meta, recognized


def _npr_history_for_document(events: list, number: str, header_id: str):
    wanted, kept, foreign = number.strip().upper(), [], 0
    for event in events:
        numbers = [_npr_text(_npr_pick(event, (key,))).upper() for key in ("transaction_number", "npr_number", "no_npr", "__parent_number")]
        ids = [_npr_id(_npr_pick(event, (key,))) for key in ("product_request_id", "product_requests_id", "npr_id", "request_id", "__parent_id")]
        if any(v and v != wanted for v in numbers) or (header_id and any(v and v != header_id for v in ids)):
            foreign += 1
        else:
            kept.append(event)
    return kept, foreign


def _npr_history_identity_ok(events, meta, number, header_id, path_bound=False):
    parent_no = _npr_text(meta.get("document_number")).upper()
    parent_id = _npr_id(meta.get("document_id"))
    if parent_no and parent_no != number.upper():
        return False
    if parent_id and header_id and parent_id != header_id:
        return False
    if path_bound or parent_no == number.upper() or (header_id and parent_id == header_id):
        return True
    if not events:
        return False
    for event in events:
        no = _npr_text(_npr_pick(event, ("transaction_number", "npr_number", "no_npr", "__parent_number"))).upper()
        key = _npr_id(_npr_pick(event, ("product_request_id", "product_requests_id", "npr_id", "request_id", "__parent_id")))
        if no != number.upper() and not (header_id and key == header_id):
            return False
    return True




def _npr_history_is_complete(meta: dict, page: int, rows: int, count: int):
    terminal = _npr_terminal_page(meta, page, rows)
    if terminal is None and meta.get("total") is not None:
        try:
            total = int(meta["total"])
            return count >= total
        except (TypeError, ValueError):
            return None
    return terminal


def _fetch_npr_history_route_api(route: str, number: str, header_id: str, history_endpoint=True,
                                  *, base_url: str, token: str, probe=False, http_session=None) -> dict:
    """Read-only. Probe satu halaman; full fetch mengikuti pagination dan cek identitas.

    Maksimum enam pemanggilan probe tingkat route pada auto-discovery. Transport
    dapat melakukan retry/redirect aman. Tidak mengambil tanggal dari header.
    """
    import hashlib
    import json
    from urllib.parse import quote
    template = route
    if "{id}" in route and not header_id:
        return {"state": "NO_ID", "events": []}
    try:
        route = _npr_safe_route(route)
        route = route.replace("{id}", quote(header_id, safe="")).replace("{transaction_number}", quote(number, safe=""))
    except ValueError:
        return {"state": "INVALID_ROUTE", "events": []}
    bound = "{id}" in template or "{transaction_number}" in template
    owns_session = http_session is None
    session = _create_npr_session() if owns_session else http_session
    all_events, seen, last_meta = [], set(), {}
    native_headers = []
    diagnostic = {"endpoint": template}
    try:
        for page in range(1, 101):
            params = {"token": token, "transaction_number": number}
            if page > 1:
                params["page"] = page
            payload = _npr_request_json(session, base_url, route, params, diagnostic, read_timeout=12 if probe else 25)
            events, meta, recognized = _npr_extract_history_page(payload, history_endpoint=history_endpoint)
            if not recognized or meta.get("header_payload_detected"):
                return {"state": "UNSUPPORTED_HISTORY_PAYLOAD", "events": [], "http_status": diagnostic.get("http_status")}
            kept, foreign = _npr_history_for_document(events, number, header_id)
            if foreign:
                return {"state": "WRONG_DOCUMENT", "events": []}
            if not _npr_history_identity_ok(kept, meta, number, header_id, bound):
                return {"state": "HISTORY_IDENTITY_UNPROVEN", "events": []}
            native_header = _npr_native_header_from_payload_v9(payload, number, header_id)
            if native_header:
                native_headers.append(native_header)
            valid = [e for e in kept if not _npr_text(e.get("deleted_at")) and _npr_event_status(e) in NPR_VALID_STATUSES]
            # Missing timestamps follow SQL MIN/COALESCE: ignored, never replaced with today.
            signature = hashlib.sha256(json.dumps(events, sort_keys=True, default=str).encode()).hexdigest()
            if events and signature in seen:
                return {"state": "INCOMPLETE_PAGINATION", "events": []}
            seen.add(signature)
            all_events.extend(kept)
            terminal = _npr_history_is_complete(meta, page, len(events), len(all_events))
            if probe:
                return {"state": "PROBE_OK" if valid else "PROBE_EMPTY", "events": [],
                        "sample_rows": len(kept), "http_status": diagnostic.get("http_status"),
                        "pagination": "EXPLICIT" if terminal is not None else "LIST_NO_METADATA",
                        "identity_verified": True}
            if not events and terminal is False:
                return {"state": "INCOMPLETE_PAGINATION", "events": []}
            if terminal is not False:
                if meta.get("total") is not None:
                    try:
                        if len(all_events) != int(meta["total"]):
                            return {"state": "TOTAL_MISMATCH", "events": []}
                    except (ValueError, TypeError):
                        return {"state": "INVALID_PAGINATION_META", "events": []}
                created_values = {_npr_timestamp(h["created_at"]) for h in native_headers}
                recovered = native_headers[0] if len(created_values) == 1 else {}
                return {"state": "OK", "events": all_events, "native_header": recovered,
                        "pagination": "METADATA_COMPLETE" if terminal is True else "LIST_NO_METADATA", "http_status": diagnostic.get("http_status")}
        return {"state": "INCOMPLETE_PAGINATION", "events": []}
    except NPRAPIFetchError as exc:
        return {"state": exc.diagnostic.get("state", "FETCH_ERROR"), "events": [],
                "http_status": exc.diagnostic.get("http_status"), "error_type": exc.diagnostic.get("error_type", ""),
                "retry_after_seconds": exc.diagnostic.get("retry_after_seconds"),
                "retry_after_source": exc.diagnostic.get("retry_after_source", "")}
    except Exception as exc:
        # Jangan ekspor str(exc), request URL, token, atau response headers.
        return {"state": "FETCH_ERROR", "events": [], "error_type": type(exc).__name__}
    finally:
        if owns_session:
            session.close()



def _npr_history_routes(endpoint: str, override: str):
    if override:
        return [(override, True)]
    return [(f"{endpoint}/history", True), (f"{endpoint}/{{id}}/history", True), (f"{endpoint}/{{id}}", False)]


def _discover_npr_history_route_api(endpoint: str, override: str, samples: tuple, *, base_url: str, token: str, http_session=None):
    """One FULL read validates the route and seeds the first document's history.

    Never promote a single page of paginated history to a complete history.
    Prefetched content stays private and is removed from exported diagnostics.
    """
    attempts = []
    stop_states = {"HTTP_401", "HTTP_403", "CONNECTION_ERROR", "SSL_ERROR", "TIMEOUT", "HTTP_429", "RATE_LIMIT_WAIT", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504"}
    for route, is_history in _npr_history_routes(endpoint, override):
        for number, header_id in samples[:2]:
            result = _fetch_npr_history_route_api(route, number, header_id, is_history,
                                                 base_url=base_url, token=token, probe=False, http_session=http_session)
            attempts.append({"Route": route, "No. NPR": number, "State": result["state"],
                             "HTTP": result.get("http_status", 200 if result["state"] == "OK" else None),
                             "Sample Rows": len(result.get("events", [])),
                             "Retry After Seconds": result.get("retry_after_seconds")})
            if result["state"] == "OK":
                return {"route": route, "is_history": is_history, "state": "ROUTE_VERIFIED", "attempts": attempts,
                        "_prefetched": {"number": number.upper(), "header_id": header_id, "result": result}}
            if result["state"] in stop_states:
                return {"route": "", "state": result["state"], "attempts": attempts,
                        "retry_after_seconds": result.get("retry_after_seconds")}
            if result["state"] in {"HTTP_404", "HTTP_405"} and "{id}" not in route:
                break
    return {"route": "", "state": "HISTORY_ROUTE_UNVERIFIED", "attempts": attempts}



def _npr_declared_milestones(context: dict):
    """Kontrak dari query referensi, BUKAN date_inprogress/date_complete biasa."""
    data = context.get("declared_milestones", {})
    if data.get("milestone_contract") != NPR_MILESTONE_CONTRACT:
        return None
    required = {"first_process_at", "first_complete_at", "history_count"}
    if not required.issubset(data):
        return {"state": "MILESTONE_FIELDS_MISSING"}
    for key in ("first_process_at", "first_complete_at"):
        if _npr_text(data[key]) and pd.isna(_npr_timestamp(data[key])):
            return {"state": "MILESTONE_DATE_INVALID"}
    try:
        count = int(data["history_count"])
        if count < 0 or float(data["history_count"]) != count:
            raise ValueError
        if int(data.get("history_key_variants", 1)) > 1:
            return {"state": "AMBIGUOUS_HISTORY_KEY"}
    except (ValueError, TypeError, OverflowError):
        return {"state": "MILESTONE_COUNT_INVALID"}
    p, c = _npr_timestamp(data["first_process_at"]), _npr_timestamp(data["first_complete_at"])
    if count == 0 and (pd.notna(p) or pd.notna(c)):
        return {"state": "MILESTONE_COUNT_CONFLICT"}
    return {"state": "API_FIRST_MILESTONES", "process": p, "complete": c, "count": count}


def load_npr_history_milestones_api(df: pd.DataFrame, *, allow_remote: bool = True, batch_limit=None, load_all: bool = False) -> pd.DataFrame:
    """FIRST/nested -> cache -> one continuous read of pending selected histories.

    load_all reads every pending document in this run; an explicit batch_limit
    keeps the legacy bounded-call contract. Success is saved after each document.
    One creator-owned HTTP session serves discovery and all history pages.

    429 menghentikan batch dan dilanjutkan hanya setelah Retry-After. Hasil
    pengujian koneksi lengkap dipakai kembali; tidak ada request ganda ke NPR uji.
    Maksimal 6 pembatasan beruntun tanpa kemajuan sebelum jeda manual.
    """
    import copy
    import time
    from datetime import timezone
    columns = ["__npr_number", "__first_process_date", "__first_complete_date"]
    if df is None or df.empty or "No. Transaksi" not in df:
        return pd.DataFrame(columns=columns)
    config = _npr_history_settings()
    continuous = bool(load_all and batch_limit is None)
    if batch_limit is not None:
        config["batch_size"] = max(1, min(config["batch_size"], int(batch_limit)))
    store = _npr_history_store_v5(config)
    shared_generation = _npr_shared_history_generation_v12(config)
    started_at = time.monotonic()
    run = store["run"]
    fetch_enabled = allow_remote and bool(run["request"] or run["auto"] or not run["initial_attempted"])
    if allow_remote:
        run.update(initial_attempted=True, request=False)
    else:
        # Pause does not count as a failed/empty history fetch. Preserve successes.
        run.update(request=False, auto=False)
    contexts = df.attrs.get("npr_api_context", {})
    endpoint = df.attrs.get("npr_api_endpoint", _npr_endpoint_candidates()[0])
    docs = df.drop_duplicates("No. Transaksi").copy()
    docs["__rank"] = docs["Status"].map({"Process": 0, "Complete": 1, "Request": 2}).fillna(3)
    docs = docs.sort_values(["__rank", "transaction_date"], ascending=[True, False], kind="stable")
    resolved, pending, cached_count, shared_cached_count = {}, [], 0, 0
    now = time.time()
    for row in docs.to_dict("records"):
        # Do not copy the whole NPR attrs/history context for every Series row.
        number = _npr_text(row["No. Transaksi"])
        key = number.upper()
        context = contexts.get(key, {})
        fingerprint = _npr_header_fingerprint_v5(context, row.get("Status"), row.get("transaction_date"))
        record = {"__npr_number": key, "__first_process_date": pd.NaT, "__first_complete_date": pd.NaT,
                  "History State": "HISTORY_NOT_LOADED", "History Rows": 0,
                  "Created Date Source": context.get("date_source", "UNKNOWN"),
                  "Header date_inprogress (bukan acuan FIRST)": context.get("header_date_inprogress", pd.NaT),
                  "Header date_complete (bukan acuan FIRST)": context.get("header_date_complete", pd.NaT),
                  "History Pagination": "", "Milestone Source": "UNRESOLVED", "From Session Cache": False}
        declared = _npr_declared_milestones(context)
        if declared and declared["state"] == "API_FIRST_MILESTONES":
            record.update({"__first_process_date": declared["process"], "__first_complete_date": declared["complete"],
                           "History State": declared["state"], "History Rows": declared["count"], "Milestone Source": NPR_MILESTONE_CONTRACT})
            resolved[key] = record
            continue
        embedded = context.get("embedded", {})
        if embedded:
            events, meta, recognized = _npr_extract_history_page(embedded, history_endpoint=False)
            events, foreign = _npr_history_for_document(events, number, context.get("id", ""))
            complete = _npr_history_is_complete(meta, 1, len(events), len(events))
            valid_dates = all(pd.notna(_npr_date_from(e, ("updated_at", "created_at"))) for e in events
                              if not _npr_text(e.get("deleted_at")) and _npr_event_status(e) in NPR_VALID_STATUSES)
            total_ok = True
            if meta.get("total") is not None:
                try:
                    total_ok = int(meta["total"]) == len(events)
                except (TypeError, ValueError):
                    total_ok = False
            if recognized and not foreign and total_ok and complete is not False:
                p, c = _npr_first_milestones(events)
                record.update({"__first_process_date": p, "__first_complete_date": c, "History State": "NESTED_HISTORY",
                               "History Rows": len(events), "Milestone Source": "EMBEDDED_HISTORY",
                               "History Pagination": "METADATA_COMPLETE" if complete is True else "LIST_NO_METADATA"})
                resolved[key] = record
                continue
        cached = store["success"].get(key)
        if cached and cached["fingerprint"] == fingerprint:
            record = copy.deepcopy(cached["record"])
            record["From Session Cache"] = True
            resolved[key] = record
            cached_count += 1
            continue
        store["success"].pop(key, None)
        shared = _npr_shared_history_get_v12(config, key, fingerprint)
        if shared is not None:
            # Preserve the original fetched_at: a cache read never renews freshness.
            store["success"][key] = shared
            record = copy.deepcopy(shared["record"])
            record["From Session Cache"] = False
            record["From Shared Cache"] = True
            resolved[key] = record
            store["errors"].pop(key, None)
            shared_cached_count += 1
            continue
        error = store["errors"].get(key, {})
        if error.get("fingerprint") == fingerprint:
            record.update(error["fields"])
        else:
            store["errors"].pop(key, None)
        pending.append((context.get("source_number") or number, context.get("id", ""), fingerprint, record))
    http_session = None
    try:
        if pending and fetch_enabled:
            http_session = _create_npr_session()
        prefetched = None
        saved_selection = store.get("selection")
        selection = {"route": "", "state": "NOT_NEEDED", "attempts": []}
        if pending:
            selection = {"route": "", "state": "HISTORY_NOT_LOADED", "attempts": []}
            if saved_selection:
                selection = copy.deepcopy(saved_selection["data"])
            elif fetch_enabled and (config["history_endpoint"] or config["discover_history"]):
                samples = tuple((n, identifier) for n, identifier, _, _ in pending[:2])
                selection = _discover_npr_history_route_api(endpoint, config["history_endpoint"], samples,
                                                            base_url=config["base_url"], token=config["token"], http_session=http_session)
                prefetched = selection.pop("_prefetched", None)
                if selection.get("state") == "ROUTE_VERIFIED":
                    store["selection"] = {"data": copy.deepcopy(selection), "fetched_at": time.time()}
            elif not config["history_endpoint"] and not config["discover_history"]:
                selection["state"] = "HISTORY_NOT_CONFIGURED"
        route = selection.get("route", "")
        seed_count = 0
        if prefetched and prefetched.get("result", {}).get("state") == "OK":
            still_pending = []
            for number, identifier, fingerprint, record in pending:
                key = number.upper()
                if key != prefetched["number"] or identifier != prefetched["header_id"]:
                    still_pending.append((number, identifier, fingerprint, record))
                    continue
                seed = prefetched["result"]
                p, c = _npr_first_milestones(seed["events"])
                for label in ("HTTP Status", "Retry After Seconds", "Retry After Source"):
                    record.pop(label, None)
                record.update({"__first_process_date": p, "__first_complete_date": c,
                               "History State": "API_HISTORY", "History Rows": len(seed["events"]),
                               "Milestone Source": route, "History Pagination": seed.get("pagination", ""),
                               "Reused Route Validation": True,
                               "History Fetched At UTC": datetime.now(timezone.utc).isoformat()})
                store["success"][key] = {"record": copy.deepcopy(record), "fingerprint": fingerprint, "fetched_at": time.time()}
                _npr_shared_history_put_v12(config, key, store["success"][key], shared_generation)
                store["errors"].pop(key, None)
                resolved[key] = record
                seed_count += 1
            pending = still_pending
        if not allow_remote:
            for _, _, _, record in pending:
                record["History State"] = "HISTORY_PAUSED_HEADER_CHECK"
        attempted, fresh_success, batch_state = 0, seed_count, ("IDLE" if allow_remote else "HEADER_CHECK_PAUSED")
        wait = _npr_cooldown_status_v5(config["base_url"])["wait_seconds"]
        eligible = []
        if fetch_enabled and pending:
            if wait:
                # Preserve a real 429/503 from discovery so the retry bound counts it.
                batch_state = (selection.get("state") if saved_selection is None and
                               selection.get("state") in {"HTTP_429", "HTTP_503"} else "RATE_LIMIT_WAIT")
            elif not route:
                batch_state = selection.get("state", "HISTORY_ROUTE_UNVERIFIED")
                if batch_state not in {"HTTP_429", "RATE_LIMIT_WAIT", "HTTP_503"}:
                    run["auto"] = False
            else:
                eligible = pending if continuous else pending[:max(0, config["batch_size"] - seed_count)]
                batch_state = "BATCH_COMPLETE"
        if seed_count and batch_state == "IDLE" and pending and fetch_enabled:
            batch_state = "BATCH_COMPLETE"
        total_documents = len(docs)
        completed_before_requests = sum(
            record.get("History State") in NPR_HISTORY_GOOD_STATES for record in resolved.values()
        )
        progress = st.progress(
            completed_before_requests / total_documents,
            text=f"Riwayat NPR: {completed_before_requests:,}/{total_documents:,} selesai diperiksa; melanjutkan dokumen yang belum terbaca.",
        )
        if eligible:
            for number, identifier, fingerprint, record in eligible:
                key = number.upper()
                result = _fetch_npr_history_route_api(route, number, identifier, selection.get("is_history", True),
                                                       base_url=config["base_url"], token=config["token"], http_session=http_session)
                attempted += 1
                if result["state"] == "OK":
                    for label in ("HTTP Status", "Retry After Seconds", "Retry After Source"):
                        record.pop(label, None)
                    p, c = _npr_first_milestones(result["events"])
                    record.update({"__first_process_date": p, "__first_complete_date": c,
                                   "History State": "API_HISTORY", "History Rows": len(result["events"]),
                                   "Recovered Header Created At": result.get("native_header", {}).get("created_at", pd.NaT),
                                   "Milestone Source": route, "History Pagination": result.get("pagination", ""),
                                   "History Fetched At UTC": datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat()})
                    store["success"][key] = {"record": copy.deepcopy(record), "fingerprint": fingerprint, "fetched_at": time.time()}
                    _npr_shared_history_put_v12(config, key, store["success"][key], shared_generation)
                    store["errors"].pop(key, None)
                    fresh_success += 1
                else:
                    error_fields = {"History State": result["state"], "HTTP Status": result.get("http_status"),
                                    "Retry After Seconds": result.get("retry_after_seconds"),
                                    "Retry After Source": result.get("retry_after_source", "")}
                    record.update(error_fields)
                    # Error adalah diagnostik antrean, TIDAK cache respons; percobaan
                    # berikutnya tetap memanggil endpoint setelah cooldown berakhir.
                    store["errors"][key] = {"fingerprint": fingerprint, "fields": error_fields}
                    batch_state = result["state"]
                    if result["state"] not in {"HTTP_429", "RATE_LIMIT_WAIT", "HTTP_503"}:
                        run["auto"] = False
                completed_now = completed_before_requests + fresh_success - seed_count
                progress.progress(
                    completed_now / total_documents,
                    text=f"Riwayat NPR: {completed_now:,}/{total_documents:,} selesai diperiksa | tersisa: {total_documents-completed_now:,}.",
                )
                if result["state"] != "OK":
                    break
        for number, identifier, fingerprint, record in pending:
            resolved[number.upper()] = record
        result = pd.DataFrame(list(resolved.values()))
        for col in columns[1:]:
            result[col] = pd.to_datetime(result[col], errors="coerce")
        output = result[columns].copy()
        good_count = int(result["History State"].isin(NPR_HISTORY_GOOD_STATES).sum())
        remaining = len(result) - good_count
        if not remaining:
            batch_state = "COMPLETE"
            run["auto"] = False
            if store.get("snapshot_completed_at") is None:
                store["snapshot_completed_at"] = time.time()
        _npr_resume_plan_v11_2(store, config, batch_state, fresh_success)
        progress.progress(
            good_count / total_documents,
            text=(f"Selesai: {good_count:,}/{total_documents:,} riwayat NPR diperiksa."
                  if not remaining else f"Riwayat NPR: {good_count:,}/{total_documents:,} selesai diperiksa; {remaining:,} belum selesai."),
        )
        selection.update({"loading_mode": "ALL_PENDING" if continuous else "BATCH_COMPAT",
                          "progress_resolved": good_count, "progress_total": total_documents,
                          "batch_state": batch_state, "batch_attempted": attempted, "new_success": fresh_success,
                          "from_session_cache": cached_count, "from_shared_cache": shared_cached_count,
                          "shared_cache_ttl_seconds": NPR_PARITY_CACHE_TTL,
                          "http_sessions_created": int(http_session is not None),
                          "load_elapsed_seconds": round(time.monotonic() - started_at, 3),
                          "route_validation_reused_documents": seed_count,
                          "consecutive_rate_limits": int(run.get("rate_limit_streak", 0)),
                          "pause_reason": run.get("pause_reason", ""), "resolved_documents": good_count,
                          "unresolved_documents": remaining, "batch_size": config["batch_size"],
                          "auto_continue": bool(run["auto"]), "cooldown": _npr_cooldown_status_v5(config["base_url"])})
        output.attrs["npr_history_diagnostic"] = result.to_dict("records")
        output.attrs["npr_history_connection"] = selection
        return output


    finally:
        if http_session is not None:
            http_session.close()


def load_npr_first_process_history(df: pd.DataFrame) -> pd.DataFrame:
    return load_npr_history_milestones_api(df)[["__npr_number", "__first_process_date"]]


def load_npr_first_complete_history(df: pd.DataFrame) -> pd.DataFrame:
    return load_npr_history_milestones_api(df)[["__npr_number", "__first_complete_date"]]


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



def _npr_audit_rows_v4(df: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Ekspor satu baris per NPR; tidak mengandung token atau payload produk."""
    aging = build_npr_aging_first_process(df, history[["__npr_number", "__first_process_date"]])
    complete = build_npr_aging_first_complete(df, history[["__npr_number", "__first_complete_date"]])
    if aging.empty:
        return pd.DataFrame()
    out = aging[["No. Transaksi", "Status", "Sales", "transaction_date", "First Process Date", "Aging NPR"]].copy()
    out = out.merge(complete[["No. Transaksi", "First Complete Date", "Aging NPR Complete"]], on="No. Transaksi", how="left", validate="one_to_one")
    out = out.rename(columns={"No. Transaksi": "transaction_number", "Status": "status_description", "Sales": "pic",
                              "transaction_date": "created_at_used", "First Process Date": "first_process_at",
                              "Aging NPR": "aging_process_days", "First Complete Date": "first_complete_at",
                              "Aging NPR Complete": "aging_complete_days"})
    details = {r["__npr_number"]: r for r in history.attrs.get("npr_history_diagnostic", [])}
    mapping = {"created_source": "Created Date Source", "history_state": "History State", "history_rows": "History Rows",
               "milestone_source": "Milestone Source", "history_pagination": "History Pagination",
               "header_process_unverified": "Header date_inprogress (bukan acuan FIRST)",
               "header_complete_unverified": "Header date_complete (bukan acuan FIRST)"}
    for new, old in mapping.items():
        values = []
        for n in out["transaction_number"]:
            key = str(n).strip().upper()
            fallback = {
                "Created Date Source": df.attrs.get("npr_api_context", {}).get(key, {}).get("date_source", "UNKNOWN"),
                "History State": "HISTORY_NOT_LOADED", "History Rows": 0,
                "Milestone Source": "UNRESOLVED",
            }
            values.append(details.get(key, {}).get(old, fallback.get(old, "")))
        out[new] = values
    values = pd.to_numeric(out["aging_process_days"], errors="coerce")
    out["sla_eligible"] = values.notna() & values.ge(0)
    out["sla_met"] = pd.Series(pd.NA, index=out.index, dtype="boolean")
    out.loc[out["sla_eligible"], "sla_met"] = values.loc[out["sla_eligible"]].le(2)
    return out


def _npr_reference_audit_v4(raw: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    """Terima HANYA ekspor dari SQL referensi yang disertakan, bukan field tebakan."""
    required = {"transaction_number", "created_at", "status_code", "pic", "first_process_at", "first_complete_at", "history_count", "milestone_contract", "reference_source_start", "reference_report_end"}
    if not required.issubset(raw.columns):
        missing = ", ".join(sorted(required - set(raw.columns)))
        raise ValueError("CSV PostgreSQL tidak sesuai query referensi. Kolom belum ada: " + missing)
    if not raw["milestone_contract"].eq(NPR_MILESTONE_CONTRACT).all():
        raise ValueError("CSV harus berasal dari Query_NPR_Referensi_PostgreSQL.sql (kontrak milestone tidak sesuai).")
    if not raw.empty:
        source_dates = raw["reference_source_start"].map(_npr_timestamp)
        end_dates = raw["reference_report_end"].map(_npr_timestamp)
        if (source_dates.isna().any() or end_dates.isna().any()
                or source_dates.nunique() != 1 or end_dates.nunique() != 1
                or source_dates.iloc[0].normalize() > pd.Timestamp(start_date).normalize()
                or end_dates.iloc[0].normalize() < pd.Timestamp(end_date).normalize()):
            raise ValueError("Periode CSV PostgreSQL tidak mencakup horizon API. Sesuaikan tanggal query referensi lalu ekspor ulang.")
    normalized = raw["transaction_number"].fillna("").astype(str).str.strip().str.upper()
    if normalized.duplicated().any():
        raise ValueError("Ekspor PostgreSQL mempunyai nomor NPR ganda setelah normalisasi. Periksa duplikasi sebelum membandingkan.")
    frame = _normalize_full_npr_api(raw, "POSTGRESQL_CSV", {})
    frame = assign_unassigned(frame, "Sales")
    frame["Sales"] = frame["Sales"].replace({"UNASSIGNED": "Unassigned"})
    invalid = len(raw) - len(frame)
    if invalid or frame["transaction_date"].isna().any():
        raise ValueError("Ekspor PostgreSQL mengandung nomor/status/tanggal yang tidak valid. Periksa sumber sebelum membandingkan.")
    # Batasi periode pembanding sama dengan horizon sumber API, bukan tanggal awal picker.
    frame = apply_cumulative_filter(frame, end_date)
    frame = frame.loc[frame["transaction_date"].ge(pd.Timestamp(start_date).normalize())].copy()
    rows = []
    for number in frame["No. Transaksi"]:
        ctx = frame.attrs["npr_api_context"][number.upper()]
        result = _npr_declared_milestones(ctx)
        if not result or result["state"] != "API_FIRST_MILESTONES":
            raise ValueError("Milestone ekspor PostgreSQL tidak valid/ambigu pada salah satu NPR. Periksa hasil query referensi.")
        rows.append({"__npr_number": number.upper(), "__first_process_date": result["process"], "__first_complete_date": result["complete"],
                     "History State": "API_FIRST_MILESTONES", "History Rows": result["count"], "Created Date Source": "created_at",
                     "Milestone Source": "POSTGRESQL_CSV", "History Pagination": "SQL_AGGREGATE"})
    history = pd.DataFrame(rows, columns=["__npr_number", "__first_process_date", "__first_complete_date", "History State", "History Rows", "Created Date Source", "Milestone Source", "History Pagination"])
    for col in ("__first_process_date", "__first_complete_date"):
        history[col] = pd.to_datetime(history[col], errors="coerce")
    history.attrs["npr_history_diagnostic"] = rows
    return _npr_audit_rows_v4(frame, history)


def _npr_compare_audits_v4(api: pd.DataFrame, pg: pd.DataFrame) -> pd.DataFrame:
    """Outer join dokumen; nilai belum terbaca berbeda dari nol/no milestone yang sah."""
    fields = ["transaction_number", "status_description", "pic", "created_at_used", "first_process_at", "first_complete_at",
              "aging_process_days", "aging_complete_days", "sla_eligible", "sla_met", "history_state", "created_source"]
    frames = []
    for name, source in (("API", api), ("PG", pg)):
        frame = source.reindex(columns=fields).copy()
        frame["key"] = frame["transaction_number"].fillna("").astype(str).str.strip().str.upper()
        if frame["key"].duplicated().any():
            raise ValueError(name + " memiliki nomor NPR ganda. Hasil tidak boleh dibandingkan dengan menghapus baris diam-diam.")
        frames.append(frame.add_prefix(name + "_"))
    result = frames[0].merge(frames[1], how="outer", left_on="API_key", right_on="PG_key", indicator=True, validate="one_to_one")
    result["No. NPR"] = result["API_key"].combine_first(result["PG_key"])
    state, causes = [], []
    for _, row in result.iterrows():
        if row["_merge"] != "both":
            state.append("BERBEDA")
            causes.append("HANYA_API" if row["_merge"] == "left_only" else "HANYA_POSTGRESQL")
            continue
        differences = []
        for field in ("status_description", "pic"):
            if _npr_text(row["API_" + field]).casefold() != _npr_text(row["PG_" + field]).casefold():
                differences.append(field.upper())
        date_verified = row["API_created_source"] == "created_at"
        history_verified = row["API_history_state"] in NPR_HISTORY_GOOD_STATES
        for field in ("created_at_used", "first_process_at", "first_complete_at"):
            if field != "created_at_used" and not history_verified:
                continue
            a, b = _npr_timestamp(row["API_" + field]), _npr_timestamp(row["PG_" + field])
            # Banding tanggal kalender karena rumus acuan memakai normalize().
            a = a.normalize() if pd.notna(a) else pd.NaT
            b = b.normalize() if pd.notna(b) else pd.NaT
            if (pd.isna(a) != pd.isna(b)) or (pd.notna(a) and pd.notna(b) and a != b):
                differences.append(field.upper())
        if history_verified and date_verified:
            for field in ("aging_process_days", "aging_complete_days", "sla_eligible", "sla_met"):
                a, b = row["API_" + field], row["PG_" + field]
                if (pd.isna(a) != pd.isna(b)) or (pd.notna(a) and pd.notna(b) and a != b):
                    differences.append(field.upper())
        unresolved = []
        if not date_verified:
            unresolved.append("SUMBER_CREATED_BELUM_TERVERIFIKASI")
        if not history_verified:
            unresolved.append("RIWAYAT_BELUM_TERVERIFIKASI")
        state.append("BERBEDA" if differences else ("BELUM_TERVERIFIKASI" if unresolved else "COCOK"))
        causes.append(" | ".join(differences + unresolved) or "COCOK_PADA_FIELD_DAN_TANGGAL_KALENDER")
    result["Hasil"] = state
    result["Penyebab"] = causes
    first = ["No. NPR", "Hasil", "Penyebab"]
    return result[first + [c for c in result if c not in first and c != "_merge"]]


def _render_npr_audit_v4(df, history, end_date):
    import json
    audit = _npr_audit_rows_v4(df, history)
    with st.expander("🧪 Cocokkan NPR API dengan PostgreSQL", expanded=False):
        st.caption("Perbandingan hanya berjalan setelah CSV PostgreSQL diunggah. File ini tidak membuka koneksi database. Gunakan database/lingkungan dan waktu pengambilan yang sama; server uji belum tentu berisi data produksi yang sama.")
        st.download_button("⬇️ Unduh data pembanding NPR API.csv", data=audit.to_csv(index=False).encode("utf-8-sig"),
                           file_name="NPR_API_Pembanding.csv", mime="text/csv", key="npr_api_audit_v4")
        upload = st.file_uploader("CSV dari Query_NPR_Referensi_PostgreSQL.sql", type=["csv"], key="npr_pg_audit_upload_v4")
        if upload is not None:
            try:
                if upload.size > 25 * 1024 * 1024:
                    raise ValueError("Ukuran CSV pembanding maksimal 25 MB.")
                raw = pd.read_csv(upload, dtype=str, keep_default_na=False, encoding="utf-8-sig")
                config = _npr_api_config()
                reference = _npr_reference_audit_v4(raw, config["fetch_start_date"], end_date)
                if reference.empty:
                    st.warning("CSV PostgreSQL tidak mempunyai NPR dalam horizon sumber yang sama. Belum ada pembanding yang layak.")
                    return
                compare = _npr_compare_audits_v4(audit, reference)
                counts = compare["Hasil"].value_counts().to_dict()
                st.write({"Cocok": counts.get("COCOK", 0), "Berbeda": counts.get("BERBEDA", 0), "Belum terverifikasi": counts.get("BELUM_TERVERIFIKASI", 0)})
                st.caption("Perbandingan memakai seluruh NPR yang lolos cutoff sebelum pencarian/PIC. Created/First Process/First Complete dibandingkan pada tanggal kalender, sesuai rumus aging; ini bukan pemeriksaan kesamaan seluruh tabel DB.")
                st.dataframe(compare, use_container_width=True, hide_index=True)
                st.download_button("⬇️ Unduh perbedaan NPR API–PostgreSQL.csv", data=compare.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="Perbandingan_NPR_API_PostgreSQL.csv", mime="text/csv", key="npr_compare_export_v4")
            except (ValueError, KeyError, pd.errors.ParserError, UnicodeError) as exc:
                st.warning(_npr_safe_message(str(exc)))


def _render_npr_history_summary_v4(df, history, sla):
    diag = pd.DataFrame(history.attrs.get("npr_history_diagnostic", []))
    if diag.empty:
        return
    good = diag["History State"].isin(NPR_HISTORY_GOOD_STATES)
    count, total = int(good.sum()), len(diag)
    valid_p = int(pd.to_datetime(history["__first_process_date"], errors="coerce").notna().sum())
    valid_c = int(pd.to_datetime(history["__first_complete_date"], errors="coerce").notna().sum())
    empty = int((good & pd.to_numeric(diag["History Rows"], errors="coerce").eq(0)).sum())
    st.caption(f"Riwayat berhasil diperiksa: {count:,}/{total:,} NPR | Respons history kosong: {empty:,} | First Process terisi: {valid_p:,} | First Complete terisi: {valid_c:,} | SLA dinilai: {len(sla):,}.")
    connection = history.attrs.get("npr_history_connection", {})
    if count < total:
        st.warning(f"HASIL AGING/SLA MASIH PARSIAL: {total - count:,} NPR belum berhasil diperiksa. Persentase SLA di bawah hanya berlaku untuk {len(sla):,} NPR dengan tanggal valid yang sudah terbaca, BUKAN SLA keseluruhan periode. Jumlah/status/PIC tidak terpotong oleh antrean history.")
        wait = connection.get("cooldown", {}).get("wait_seconds", 0)
        if wait:
            st.info(f"Pemuatan dijeda karena pembatasan server. Tunggu sekitar {wait:,} detik, lalu klik Lanjutkan pemuatan jika proses telah dijeda. Hasil sukses tetap tersimpan dalam sesi.")
        elif connection.get("batch_state") not in {"IDLE", "BATCH_COMPLETE", "COMPLETE"}:
            st.info(f"Status pemuatan: {connection.get('batch_state')}. Periksa Informasi Teknis Dashboard; percobaan berikutnya tidak mengulang hasil sukses.")
        else:
            st.caption("Klik Lanjutkan pemuatan untuk meneruskan seluruh riwayat yang belum terbaca selama aplikasi terbuka.")
    else:
        st.caption("Semua dokumen terpilih telah diperiksa melalui sumber yang dapat dibaca. History kosong tetap kosong. Tanpa metadata total/pagination atau CSV PostgreSQL, kelengkapan seluruh DB dan kesamaan sumber belum terbukti.")
    alternate = ~diag["Created Date Source"].eq("created_at")
    if alternate.any():
        st.caption(f"Sumber tanggal dibuat {int(alternate.sum()):,} NPR masih memakai alias input_date/transaction_date. Kesetaraannya dengan created_at PostgreSQL belum terverifikasi.")



def _run_npr_dashboard_v3(end_date, search_number, search_status, pic_column, *, start_date=None):
    return run_npr_dashboard_v7(end_date, search_number, search_status, pic_column, start_date=start_date)



def _npr_native_header_from_payload_v9(payload, number, header_id=""):
    """Extract HEADER created_at only; never traverse history/events/product items.

    On a history route, an explicit history wrapper or a named header container
    establishes header context. A bare status event is not a document header.
    Identity conflicts or different header creation timestamps are rejected.
    """
    wanted, identifier = _npr_text(number).upper(), _npr_id(header_id)
    found = []
    named_headers = {"product_request", "productrequest", "header", "document"}
    wrappers = {"data", "result", "results", "record", "records", "product_requests"} | named_headers
    conflict = False

    def visit(obj, named=False, depth=0):
        nonlocal conflict
        if depth > 8:
            return
        if isinstance(obj, list):
            for value in obj:
                visit(value, named, depth + 1)
            return
        if not isinstance(obj, dict):
            return
        lower = {str(k).casefold(): v for k, v in obj.items()}
        explicit_history = any(k in lower and isinstance(lower[k], (dict, list)) for k in NPR_HISTORY_KEYS)
        if explicit_history or named:
            no = _npr_text(_npr_pick(obj, ("transaction_number", "npr_number", "no_npr"))).upper()
            key = _npr_id(_npr_pick(obj, ("product_request_id", "product_requests_id", "npr_id", "id")))
            matches = bool((no and no == wanted) or (identifier and key == identifier))
            mismatches = bool((no and no != wanted) or (identifier and key and key != identifier))
            if matches and mismatches:
                conflict = True
            if matches and not mismatches:
                created = _npr_date_from(obj, ("created_at",))
                if pd.notna(created):
                    found.append({"transaction_number": wanted, "id": identifier or key,
                                  "created_at": created, "source": "API_HEADER.created_at"})
        # A field called created_at inside history remains an event date, not creation.
        for k, value in lower.items():
            if k in wrappers and isinstance(value, (dict, list)):
                visit(value, k in named_headers, depth + 1)

    visit(payload)
    unique = {row["created_at"] for row in found}
    return found[0] if found and len(unique) == 1 and not conflict else {}


def _npr_apply_recovered_created_dates_v9(df, store):
    """Fill missing native dates only from identity-checked, matching-source cache."""
    import copy
    out = df.copy()
    out.attrs = copy.deepcopy(df.attrs)
    contexts = out.attrs.get("npr_api_context", {})
    filled = 0
    created_headers = store.setdefault("created_headers", {})
    # Records avoid repeated deep copies of DataFrame.attrs for each iterrows Series.
    for idx, row in zip(out.index, out.to_dict("records")):
        if pd.notna(_npr_timestamp(row.get("Created At NPR"))):
            continue
        number = _npr_text(row.get("No. Transaksi")).upper()
        context = contexts.get(number, {})
        cached = store.get("success", {}).get(number)
        current_fingerprint = _npr_header_fingerprint_v5(context, row.get("Status"), row.get("transaction_date"))
        accepted = {cached.get("fingerprint"), cached.get("header_recovery_input_fingerprint")} if cached else set()
        cached_matches = bool(cached and current_fingerprint in accepted)
        recovered = _npr_timestamp(cached.get("record", {}).get("Recovered Header Created At")) if cached_matches else pd.NaT
        saved = created_headers.get(number, {})
        saved_matches = current_fingerprint in {saved.get("input_fingerprint"), saved.get("enriched_fingerprint")}
        if pd.isna(recovered) and saved_matches:
            recovered = _npr_timestamp(saved.get("created_at"))
        if pd.isna(recovered):
            if saved and not saved_matches:
                created_headers.pop(number, None)
            continue
        if cached_matches:
            cached.setdefault("header_recovery_input_fingerprint", current_fingerprint)
        out.at[idx, "Created At NPR"] = recovered
        out.at[idx, "transaction_date"] = recovered
        out.at[idx, "Date Source NPR"] = "created_at"
        context.update(native_created_at=recovered, date_source="created_at",
                       native_created_at_source="API_HISTORY_HEADER.created_at", record_created_date=recovered)
        contexts[number] = context
        # Recovery is the same document read, not a new server update: keep its history.
        enriched_fingerprint = _npr_header_fingerprint_v5(context, row.get("Status"), recovered)
        if cached_matches:
            cached["fingerprint"] = enriched_fingerprint
            cached["record"]["Created Date Source"] = "created_at"
        # Preserve native header creation through a history-TTL refresh. This cache
        # is identity/fingerprint bound and does NOT count as successful history.
        created_headers[number] = {"input_fingerprint": current_fingerprint,
                                   "enriched_fingerprint": enriched_fingerprint, "created_at": recovered}
        filled += 1
    out.attrs["npr_api_context"] = contexts
    out.attrs["npr_created_recovery"] = {"filled_this_pass": filled,
                                       "missing_after_recovery": int(out["Created At NPR"].isna().sum()),
                                       "source_documents_with_recovered_created_at": sum(
                                           c.get("native_created_at_source") == "API_HISTORY_HEADER.created_at"
                                           for c in contexts.values()),
                                       "source": "API_HEADER.created_at_ONLY"}
    return out


def _npr_source_prepare_headers_v9(df):
    """v10: no created_at recovery gate.

    Production API exposes input_date as the shared canonical NPR date. History is
    reserved for FIRST Process/Complete milestones and is never used to fabricate
    the document date.
    """
    return df

def _npr_source_after_headers_v9(df):
    return None


def _npr_source_label():
    return "API"


def _npr_source_clear():
    _clear_npr_caches()
    _npr_clear_history_caches()
    for key in list(st.session_state):
        if str(key).startswith(("_npr_v9_recovery_auto_", "_npr_pinned_headers_v11_2_")):
            st.session_state.pop(key, None)


def _npr_source_controls(end_date, source_start):
    """Headless NPR connection check. No per-page source/settings form."""
    try:
        config = _npr_api_config()
    except ValueError as exc:
        st.error(str(exc))
        return False
    if config.get("token_config_error"):
        st.error(config["token_config_error"])
        return False
    if not config["token"]:
        st.error(
            "Token NPR belum tersedia. Isi SIBIMA_NPR_API_TOKEN pada environment "
            "atau token pada [npr_api] di .streamlit/secrets.toml, kemudian restart. "
            "Token ERP existing hanya dipakai otomatis untuk host/path ERP yang sama."
        )
        return False
    return True


def _npr_source_headers(source_start, end_date):
    out = load_full_npr_from_api(start_date=source_start, end_date=end_date)
    diagnostic = out.attrs.get("npr_api_fetch_diagnostic", {})
    out.attrs["npr_source_state"] = diagnostic.get("state", "NOT_FETCHED")
    out.attrs["npr_report_end"] = str(end_date)
    if diagnostic.get("state") != "OK":
        message = _npr_failure_message(diagnostic)
        if diagnostic.get("state") in {"HTTP_400", "HTTP_422", "API_REJECTED"}:
            message += (" Periode NPR sekarang dikirim ke API. Periksa date_start dan nama parameter akhir yang "
                        "didukung server; filter tanggal lokal tetap dijalankan.")
        out.attrs["npr_source_message"] = message
    return out


def _npr_prepare_history_cycle(df):
    import time
    import hashlib
    config = _npr_history_settings()
    store = _npr_history_store_v5(config)
    completed = store.get("snapshot_completed_at")
    if completed is not None and time.time() - completed >= NPR_PARITY_CACHE_TTL:
        store.update(success={}, errors={}, selection=None, snapshot_completed_at=None)
        store["run"].update(initial_attempted=False, request=True, auto=True)
    keys = tuple(sorted(df["No. Transaksi"].tolist())) if not df.empty else ()
    scope = hashlib.sha256(repr(keys).encode()).hexdigest()
    if store.get("last_document_scope") != scope:
        store.update(last_document_scope=scope, snapshot_completed_at=None)
        store["run"].update(initial_attempted=False, request=True, auto=True)
    return store


def _npr_source_history_controls(df):
    if df.empty:
        return
    _npr_prepare_history_cycle(df)
    _render_npr_history_controls(df)


def _npr_source_history(df):
    if df.empty:
        out = pd.DataFrame(columns=["__npr_number", "__first_process_date", "__first_complete_date"])
        out.attrs["npr_history_diagnostic"] = []
        return out
    _npr_prepare_history_cycle(df)
    # CSV comparison is optional and NEVER gates reading source history.
    return load_npr_history_milestones_api(df, allow_remote=True, load_all=True)


def _npr_source_after_render(history):
    _npr_continue_auto_v5(history)


def _npr_source_diagnostics(df, history):
    _render_npr_diagnostics(df, df.attrs.get("npr_report_end", ""), history=history)


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

    if selected_doc_type == "NPR":
        # Do not silently use the default month while the user is still picking
        # the second date. Other document types keep their existing behavior.
        if not isinstance(selected_date_range, (tuple, list)) or len(selected_date_range) != 2:
            st.info("Lengkapi tanggal awal dan akhir pada Select Date Range untuk NPR.")
            return
        _run_npr_dashboard_v3(end_date, search_number, search_status, col_head5, start_date=start_date)
        return

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
            # Kompatibilitas: jalur aktif NPR sudah ditangani _run_npr_dashboard_v3.
            # date_start berasal dari batas sumber, bukan tanggal awal picker.
            df_npr = load_full_npr_from_api(end_date=end_date)
            df_npr = assign_unassigned(df_npr, "Sales")
            df_npr["Sales"] = df_npr["Sales"].replace({"UNASSIGNED": "Unassigned"})

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
    if selected_doc_type == "NPR" and "Sales" in df_npr.columns:
        pic_list = sorted(df_npr["Sales"].dropna().unique().tolist())
    elif "PIC Procurement" in df_pr_final.columns:
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
    if selected_doc_type == "NPR":
        # Saat pemilihan rentang belum lengkap, tetap gunakan default tanggal yang valid.
        df_npr_f = apply_cumulative_filter(df_npr_f, end_date)
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
                st.subheader("📥 Download Data Distribusi Aging PR")

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
                st.subheader("📥 Download Data Distribusi Aging PR Balance")

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




# NPR v5 helpers (transport gate / resumable state)
@st.cache_resource(show_spinner=False)
def _npr_transport_registry_v5():
    """Gate per origin, bersama semua sesi NPR pada satu proses Streamlit."""
    import threading
    return {"lock": threading.RLock(), "origins": {}}



def _npr_transport_gate_v5(base_url):
    from urllib.parse import urlsplit
    import threading
    parsed = urlsplit(base_url)
    origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
    registry = _npr_transport_registry_v5()
    with registry["lock"]:
        if origin not in registry["origins"]:
            registry["origins"][origin] = {
                "lock": threading.RLock(), "next_at": 0.0, "cooldown_until": 0.0,
                "adaptive_interval": 0.0, "rate_limit_count": 0,
            }
        return registry["origins"][origin]



def _npr_transport_policy_v5(base_url):
    """Nilai awal konservatif, BUKAN pernyataan kuota resmi server."""
    import math
    values = {
        "interval": _npr_setting("min_request_interval", "SIBIMA_NPR_MIN_REQUEST_INTERVAL", "2"),
        "fallback_cooldown": _npr_setting("rate_limit_cooldown", "SIBIMA_NPR_RATE_LIMIT_COOLDOWN", "60"),
    }
    saved = st.session_state.get("_npr_history_options_v5", {})
    if saved.get("base_url") == base_url:
        values["interval"] = saved.get("min_request_interval", values["interval"])
    def bounded(value, default, lower, upper):
        try:
            n = float(value)
            return min(upper, max(lower, n)) if math.isfinite(n) else default
        except (ValueError, TypeError):
            return default
    return {"interval": bounded(values["interval"], 2.0, 1.0, 60.0),
            "fallback_cooldown": bounded(values["fallback_cooldown"], 60.0, 1.0, 3600.0)}



def _npr_retry_after_seconds_v5(headers, fallback=60.0, now=None):
    """Retry-After berupa detik atau HTTP-date; nilai sah tidak dipotong lebih pendek."""
    import math
    import time
    from email.utils import parsedate_to_datetime
    raw = str(headers.get("Retry-After", "")).strip()
    if raw.isdigit():
        return max(0, int(raw)), "RETRY_AFTER_SECONDS"
    if raw:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                from datetime import timezone
                target = target.replace(tzinfo=timezone.utc)
            reference = time.time() if now is None else float(now)
            # Date server dipakai bila tersedia, mengurangi dampak beda jam komputer.
            if headers.get("Date"):
                try:
                    server_date = parsedate_to_datetime(str(headers["Date"]))
                    if server_date.tzinfo is not None:
                        reference = server_date.timestamp()
                except (ValueError, TypeError, OverflowError):
                    pass
            return max(0, math.ceil(target.timestamp() - reference)), "RETRY_AFTER_HTTP_DATE"
        except (ValueError, TypeError, OverflowError):
            pass
    return max(1, math.ceil(fallback)), "CLIENT_FALLBACK"



def _npr_cooldown_status_v5(base_url):
    import math
    import time
    gate = _npr_transport_gate_v5(base_url)
    with gate["lock"]:
        seconds = max(0, math.ceil(gate["cooldown_until"] - time.monotonic()))
        return {"wait_seconds": seconds, "adaptive_interval": gate["adaptive_interval"],
                "rate_limit_count": gate["rate_limit_count"]}



def _npr_get_response_v5(session, url, params, base_url, diagnostic, read_timeout):
    """Satu request aktif per origin. HTTP 429 langsung menjeda SEMUA request NPR."""
    import math
    import time
    from datetime import timezone
    gate = _npr_transport_gate_v5(base_url)
    policy = _npr_transport_policy_v5(base_url)
    # Lock tetap dipegang sampai status dibaca: sesi lain tidak menyalip respons 429.
    with gate["lock"]:
        remaining = gate["cooldown_until"] - time.monotonic()
        if remaining > 0:
            diagnostic.update(state="RATE_LIMIT_WAIT", retry_after_seconds=math.ceil(remaining),
                              request_sent=False)
            raise NPRAPIFetchError(diagnostic)
        delay = max(0.0, gate["next_at"] - time.monotonic())
        if delay:
            time.sleep(delay)
        diagnostic["request_attempts"] = diagnostic.get("request_attempts", 0) + 1
        diagnostic["request_sent"] = True
        response = None
        try:
            response = session.get(url, params=params, timeout=(10, max(1, read_timeout)), allow_redirects=False)
        except requests.exceptions.SSLError:
            diagnostic.update(state="SSL_ERROR", error_type="SSLError")
            raise NPRAPIFetchError(diagnostic) from None
        except requests.exceptions.Timeout:
            diagnostic.update(state="TIMEOUT", error_type="Timeout")
            raise NPRAPIFetchError(diagnostic) from None
        except requests.exceptions.ConnectionError:
            diagnostic.update(state="CONNECTION_ERROR", error_type="ConnectionError")
            raise NPRAPIFetchError(diagnostic) from None
        except requests.exceptions.RequestException as exc:
            diagnostic.update(state="FETCH_ERROR", error_type=type(exc).__name__)
            raise NPRAPIFetchError(diagnostic) from None
        finally:
            # Jeda setelah respons, bukan hanya jarak start antar thread.
            gate["next_at"] = time.monotonic() + max(policy["interval"], gate["adaptive_interval"])
        diagnostic["http_status"] = response.status_code
        if response.status_code == 429 or (response.status_code == 503 and response.headers.get("Retry-After")):
            if response.status_code == 429:
                gate["rate_limit_count"] += 1
                gate["adaptive_interval"] = min(60.0, max(policy["interval"], gate["adaptive_interval"]) * 1.5)
            fallback = min(3600.0, policy["fallback_cooldown"] * (2 ** min(5, max(0, gate["rate_limit_count"] - 1))))
            wait, wait_source = _npr_retry_after_seconds_v5(response.headers, fallback)
            gate["cooldown_until"] = time.monotonic() + wait
            gate["next_at"] = max(gate["next_at"], gate["cooldown_until"])
            diagnostic.update(state=f"HTTP_{response.status_code}", retry_after_seconds=wait,
                              retry_after_source=wait_source,
                              retry_at_utc=datetime.fromtimestamp(time.time() + wait, tz=timezone.utc).isoformat())
            response.close()
            raise NPRAPIFetchError(diagnostic)
        return response



NPR_SHARED_HISTORY_MAX_ENTRIES_V12 = 10000


@st.cache_resource(show_spinner=False)
def _npr_shared_history_cache_v12():
    from collections import OrderedDict
    import threading
    return {"lock": threading.RLock(), "entries": OrderedDict(), "generations": {}}


def _npr_shared_history_scope_v12(config):
    import hashlib
    value = (_npr_history_scope_key(config), NPR_READER_VERSION,
             _npr_endpoint_candidates()[0])
    return hashlib.sha256(repr(value).encode()).hexdigest()


def _npr_shared_history_key_v12(config, number, fingerprint):
    number = str(number or "").strip().upper()
    fingerprint = str(fingerprint or "").strip()
    if number in {"", "NAN", "NONE", "NULL", "<NA>", "NAT"} or not fingerprint:
        return None
    return (_npr_shared_history_scope_v12(config), number, fingerprint)


def _npr_shared_history_route_ok_v12(config, record):
    # Validate the resolved route/template against the current route policy.
    # Route provenance is retained in the cached record, never guessed on read.
    route = str(record.get("Milestone Source", "")).strip().strip("/")
    if not route or any(part in route for part in (":", "..", "?", "#")):
        return False
    override = str(config.get("history_endpoint", "")).strip().strip("/")
    if override:
        allowed = {override}
    elif config.get("discover_history"):
        endpoint = str(_npr_endpoint_candidates()[0]).strip().strip("/")
        allowed = {endpoint + "/history", endpoint + "/{id}/history", endpoint + "/{id}"}
    else:
        allowed = set()
    return route in allowed


def _npr_shared_history_fresh_v12(entry, now):
    import math
    try:
        fetched_at = float(entry["fetched_at"])
        ttl = float(NPR_PARITY_CACHE_TTL)
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    # A read does not renew freshness. Reject future timestamps/clock rollback.
    return (math.isfinite(fetched_at) and math.isfinite(ttl) and ttl > 0
            and 0 <= now - fetched_at < ttl)


def _npr_shared_history_generation_v12(config):
    scope = _npr_shared_history_scope_v12(config)
    cache = _npr_shared_history_cache_v12()
    with cache["lock"]:
        return cache["generations"].get(scope, 0)


def _npr_shared_history_get_v12(config, number, fingerprint):
    import copy
    import time
    key = _npr_shared_history_key_v12(config, number, fingerprint)
    if key is None:
        return None
    cache = _npr_shared_history_cache_v12()
    with cache["lock"]:
        entry = cache["entries"].get(key)
        if entry is None:
            return None
        record = entry.get("record", {})
        if (not _npr_shared_history_fresh_v12(entry, time.time())
                or record.get("History State") != "API_HISTORY"
                or not _npr_shared_history_route_ok_v12(config, record)):
            cache["entries"].pop(key, None)
            return None
        cache["entries"].move_to_end(key)
        return copy.deepcopy(entry)


def _npr_shared_history_put_v12(config, number, entry, generation):
    import copy
    import math
    import time
    if not isinstance(entry, dict) or not isinstance(entry.get("record"), dict):
        return False
    key = _npr_shared_history_key_v12(config, number, entry.get("fingerprint"))
    if key is None:
        return False
    record = entry["record"]
    if (record.get("History State") != "API_HISTORY"
            or str(record.get("__npr_number", "")).strip().upper() != key[1]
            or not _npr_shared_history_route_ok_v12(config, record)
            or record.get("History Pagination") not in {"METADATA_COMPLETE", "LIST_NO_METADATA"}
            or not _npr_shared_history_fresh_v12(entry, time.time())):
        return False
    try:
        rows = float(record["History Rows"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(rows) or rows < 0 or not rows.is_integer():
        return False
    # Only known, credential-free diagnostic/milestone fields may be retained.
    # Do not cache arbitrary request objects, tokens, headers, or raw payloads.
    fields = {
        "__npr_number", "__first_process_date", "__first_complete_date",
        "History State", "History Rows", "Created Date Source",
        "Header date_inprogress (bukan acuan FIRST)",
        "Header date_complete (bukan acuan FIRST)", "History Pagination",
        "Milestone Source", "From Session Cache", "Reused Route Validation",
        "History Fetched At UTC", "Recovered Header Created At",
    }
    safe_record = copy.deepcopy({k: v for k, v in record.items() if k in fields})
    safe_record["__npr_number"] = key[1]
    safe_record["History Rows"] = int(rows)
    stored = {"record": safe_record, "fingerprint": key[2],
              "fetched_at": float(entry["fetched_at"])}
    cache = _npr_shared_history_cache_v12()
    with cache["lock"]:
        if generation != cache["generations"].get(key[0], 0):
            return False
        previous = cache["entries"].get(key)
        if previous is not None and previous["fetched_at"] > stored["fetched_at"]:
            return False  # A delayed older write cannot replace fresher data.
        cache["entries"][key] = stored
        cache["entries"].move_to_end(key)
        while len(cache["entries"]) > NPR_SHARED_HISTORY_MAX_ENTRIES_V12:
            cache["entries"].popitem(last=False)
        return True


def _npr_shared_history_clear_v12(config):
    scope = _npr_shared_history_scope_v12(config)
    cache = _npr_shared_history_cache_v12()
    with cache["lock"]:
        cache["generations"][scope] = cache["generations"].get(scope, 0) + 1
        for key in list(cache["entries"]):
            if key[0] == scope:
                cache["entries"].pop(key, None)
    # The independent transport registry/cooldown is deliberately untouched.


def _npr_history_store_v5(config):
    scope = _npr_history_scope_key(config)
    key = "_npr_history_store_v5_" + scope
    if key not in st.session_state:
        st.session_state[key] = {"success": {}, "errors": {}, "selection": None,
            "snapshot_completed_at": None, "last_document_scope": None,
            "run": {"initial_attempted": False, "request": False, "auto": True}}
    return st.session_state[key]



def _npr_header_fingerprint_v5(context, status, created_date):
    import hashlib
    # Context perubahan header membatalkan snapshot tersimpan. TTL tetap berlaku
    # untuk perubahan history yang tidak ikut memperbarui header.
    parts = [context.get("id"), status, str(created_date), context.get("date_source"),
             str(context.get("header_updated_at")), str(context.get("header_date_inprogress")),
             str(context.get("header_date_complete"))]
    return hashlib.sha256(repr(parts).encode()).hexdigest()



def _npr_continue_auto_v5(history):
    """Resume a server cooldown; legacy bounded calls may also request a foreground continuation."""
    import time
    connection = history.attrs.get("npr_history_connection", {})
    config = _npr_history_settings()
    store = _npr_history_store_v5(config)
    state = connection.get("batch_state")
    progress = state == "BATCH_COMPLETE" and connection.get("new_success", 0) > 0
    waiting = state in {"HTTP_429", "RATE_LIMIT_WAIT", "HTTP_503"}
    enabled = bool(store["run"].get("auto") and connection.get("auto_continue")
                   and connection.get("unresolved_documents", 0) > 0 and (progress or waiting))
    store["resume_pending_v11_2"] = enabled
    if not enabled:
        return
    scope = _npr_history_scope_key(config)
    fragment = getattr(st, "fragment", None)
    if callable(fragment):
        fragment(run_every=1.0)(_npr_resume_tick_v11_2)(scope)
        return
    wait = _npr_auto_wait_seconds_v11_2(config, store)
    if wait:
        st.info(f"Menunggu jeda {wait:,} detik; pemuatan akan lanjut di halaman ini. Hasil sukses tetap disimpan.")
        time.sleep(min(5.0, wait))
        st.rerun()
    _npr_resume_tick_v11_2(scope)



# =========================================================
# NPR v6: periksa header sebelum mengambil ribuan history.
# CSV PostgreSQL hanya pembanding, tidak pernah menjadi sumber grafik API.
# =========================================================

def _npr_validate_base_v6(value: str) -> str:
    from urllib.parse import urlsplit, unquote
    base = str(value or '').strip().rstrip('/') + '/'
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and '..' not in unquote(parsed.path).split('/')
                 and not any(ch.isspace() for ch in base))
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Base URL NPR harus HTTPS tanpa token, query, username, atau password.')
    return base


def _npr_apply_connection_v6(config, base, raw_token, fetch_start, approved_host=False):
    """Token lama tidak boleh ikut ketika berpindah host/base URL."""
    new_base = _npr_validate_base_v6(base)
    changed = new_base != config['base_url']
    if changed and not approved_host:
        raise ValueError('Konfirmasi bahwa base URL baru berasal dari developer dan token memang untuk server tersebut.')
    if changed and not str(raw_token).strip():
        raise ValueError('Masukkan token khusus server baru. Token server lama tidak dipindahkan otomatis.')
    token = _npr_clean_token(raw_token, new_base) if str(raw_token).strip() else config['token']
    if not token:
        raise ValueError('Token NPR belum diisi.')
    # Semua validasi dilakukan sebelum menyimpan perubahan konfigurasi.
    st.session_state['_npr_api_base_v6'] = new_base
    st.session_state['_npr_api_credentials'] = {'base_url': new_base, 'token': token}
    st.session_state.pop('_npr_api_period', None)  # Horizon is managed by shared controls.
    if changed:
        st.session_state['_npr_history_options_v5'] = {
            'base_url': new_base, 'history_endpoint': '', 'discover_history': False,
            'batch_size': NPR_HISTORY_BATCH_SIZE,
        }
    _clear_npr_caches()
    return changed


def _npr_csv_bytes_v6(frame):
    """CSV unduhan aman saat dibuka Excel; tidak mengubah dataframe kalkulasi."""
    out = frame.copy()
    for col in out.select_dtypes(include=['object', 'string']).columns:
        out[col] = out[col].map(lambda value: "'" + value if isinstance(value, str)
                               and value.lstrip().startswith(('=', '+', '-', '@')) else value)
    return out.to_csv(index=False).encode('utf-8-sig')


def _npr_pg_reference_from_audit_v6(raw, reference_end):
    """Terima hasil audit Python milik pengguna; jangan pakai kolom status_api.

    Memeriksa kontrol total agar ekspor sebagian tidak tampak sebagai acuan penuh.
    Tidak memotong referensi pada date_start API: selisih sebelum 2026 tetap terlihat.
    """
    required = {
        'transaction_number', 'membership', 'status_pg', 'pic_pg', 'created_pg',
        'first_process_pg', 'first_complete_pg', 'history_rows_pg',
        'pg_header_duplicates', 'history_key_variants_pg', 'total_pg_dalam_query',
        'total_api_snapshot', 'jumlah_pg_only', 'jumlah_api_only',
        'database_pg', 'waktu_ekspor_pg',
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError('Unggah CSV Hasil_Audit_NPR_PostgreSQL, bukan script/CSV API. Kolom kurang: '
                         + ', '.join(sorted(missing)))
    if raw.empty:
        raise ValueError('CSV audit tidak memiliki baris hasil.')
    work = raw.copy()
    membership = work['membership'].fillna('').astype(str).str.strip()
    if not membership.isin(['BOTH', 'PG_ONLY', 'API_ONLY']).all():
        raise ValueError('Kolom membership CSV audit tidak valid.')
    numbers = work['transaction_number'].fillna('').astype(str).str.strip()
    if numbers.eq('').any() or numbers.str.upper().duplicated().any():
        raise ValueError('Nomor NPR kosong/ganda dalam CSV. Periksa ekspor, jangan hapus duplikat diam-diam.')
    actual = {
        'total_pg_dalam_query': int(membership.ne('API_ONLY').sum()),
        'total_api_snapshot': int(membership.ne('PG_ONLY').sum()),
        'jumlah_pg_only': int(membership.eq('PG_ONLY').sum()),
        'jumlah_api_only': int(membership.eq('API_ONLY').sum()),
    }
    for col, count in actual.items():
        values = pd.to_numeric(work[col], errors='coerce')
        if values.isna().any() or values.nunique() != 1 or values.iloc[0] != count:
            raise ValueError('CSV audit tidak lengkap atau kontrol total tidak sesuai: ' + col)
    pg = work.loc[membership.ne('API_ONLY')].copy()
    if pg.empty:
        raise ValueError('Hasil audit tidak mempunyai dokumen PostgreSQL.')
    duplicates = pd.to_numeric(pg['pg_header_duplicates'], errors='coerce')
    variants = pd.to_numeric(pg['history_key_variants_pg'], errors='coerce')
    if duplicates.isna().any() or duplicates.ne(1).any() or variants.isna().any() or variants.gt(1).any():
        raise ValueError('Acuan memiliki duplikat/variasi nomor ambigu; hasil belum dapat dipakai untuk pemeriksaan otomatis.')
    out = pd.DataFrame({
        'No. Transaksi': pg['transaction_number'].fillna('').astype(str).str.strip(),
        'Status': pg['status_pg'].map(_normalize_npr_text_status),
        'Sales': pg['pic_pg'].fillna(''),
        'transaction_date': pd.to_datetime(pg['created_pg'].map(_npr_timestamp)),
        '__pg_id': pg.get('pg_id', pd.Series('', index=pg.index)).map(_npr_id),
        '__pg_pic_id': pg.get('pic_id_pg', pd.Series('', index=pg.index)).map(_npr_id),
        '__first_process_date': pd.to_datetime(pg['first_process_pg'].map(_npr_timestamp)),
        '__first_complete_date': pd.to_datetime(pg['first_complete_pg'].map(_npr_timestamp)),
        '__history_rows': pd.to_numeric(pg['history_rows_pg'], errors='coerce'),
    }).reset_index(drop=True)
    if (not out['Status'].isin(NPR_VALID_STATUSES).all() or out['transaction_date'].isna().any()
            or out['__history_rows'].isna().any() or out['__history_rows'].lt(0).any()):
        raise ValueError('Status, tanggal dibuat, atau jumlah history pada referensi tidak valid.')
    for old, new in [('first_process_pg', '__first_process_date'), ('first_complete_pg', '__first_complete_date')]:
        invalid_date = pg[old].map(_npr_text).ne('').to_numpy() & out[new].isna().to_numpy()
        if invalid_date.any():
            raise ValueError('Tanggal milestone pada CSV referensi tidak dapat dibaca: ' + old)
    out = assign_unassigned(out, 'Sales')
    out['Sales'] = out['Sales'].replace({'UNASSIGNED': 'Unassigned'})
    cutoff = _npr_timestamp(reference_end)
    if pd.isna(cutoff):
        raise ValueError('Tanggal akhir referensi tidak valid.')
    if out['transaction_date'].gt(cutoff.normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59)).any():
        raise ValueError('CSV memuat tanggal dibuat setelah tanggal akhir referensi yang Anda pilih.')
    for col in ['database_pg', 'waktu_ekspor_pg']:
        if pg[col].fillna('').astype(str).nunique() != 1:
            raise ValueError('CSV menggabungkan beberapa database/waktu ekspor: ' + col)
    out.attrs['pg_reference_meta'] = {
        'kind': 'CSV_HASIL_AUDIT_POSTGRESQL',
        'database': _npr_text(pg['database_pg'].iloc[0]),
        'extracted_at': _npr_text(pg['waktu_ekspor_pg'].iloc[0]),
        'report_end_confirmed_by_user': cutoff.date().isoformat(),
        'documents': len(out),
        'earliest_created_date': out['transaction_date'].min().date().isoformat(),
        'scope': 'Kumulatif tanpa batas awal; status pada saat ekstraksi, bukan snapshot historis.',
    }
    return out


def _npr_compare_headers_v6(api, reference, fetch_start):
    """Outer join pemeriksaan saja; tidak menulis balik nilai PG ke API."""
    source_frames = []
    for prefix, source in [('API', api), ('PG', reference)]:
        frame = source.reindex(columns=['No. Transaksi', 'Status', 'Sales', 'transaction_date']).copy()
        frame['key'] = frame['No. Transaksi'].fillna('').astype(str).str.strip().str.upper()
        if frame['key'].eq('').any() or frame['key'].duplicated().any():
            raise ValueError(prefix + ': nomor kosong/ganda setelah normalisasi, pemeriksaan dihentikan.')
        frame['transaction_date'] = pd.to_datetime(frame['transaction_date'].map(_npr_timestamp))
        source_frames.append(frame.add_prefix(prefix + '_'))
    out = source_frames[0].merge(source_frames[1], how='outer', left_on='API_key', right_on='PG_key',
                                 indicator=True, validate='one_to_one')
    out['No. NPR'] = out['API_key'].combine_first(out['PG_key'])
    out['membership'] = out['_merge'].map({'left_only': 'API_ONLY', 'right_only': 'PG_ONLY', 'both': 'BOTH'}).astype(str)
    both = out['membership'].eq('BOTH')
    for field, target in [('Status', 'status_berbeda'), ('Sales', 'pic_berbeda')]:
        a = out['API_' + field].fillna('').astype(str).str.strip().str.casefold()
        b = out['PG_' + field].fillna('').astype(str).str.strip().str.casefold()
        out[target] = both & a.ne(b)
    a, b = out['API_transaction_date'], out['PG_transaction_date']
    out['tanggal_dibuat_berbeda'] = both & (a.isna() ^ b.isna() | (a.notna() & b.notna() & a.dt.normalize().ne(b.dt.normalize())))
    out['tanggal_api_tidak_valid'] = out['membership'].ne('PG_ONLY') & a.isna()
    out['pg_sebelum_batas_api'] = out['membership'].eq('PG_ONLY') & b.lt(pd.Timestamp(fetch_start).normalize())
    reasons = []
    for row in out.to_dict('records'):
        if row['membership'] == 'PG_ONLY':
            reasons.append('PG_ONLY_SEBELUM_BATAS_API' if row['pg_sebelum_batas_api'] else 'PG_ONLY_DALAM_RENTANG_API')
        elif row['membership'] == 'API_ONLY':
            reasons.append('API_ONLY_PADA_HASIL_YANG_DIBANDINGKAN')
        else:
            labels = [label for column, label in [('status_berbeda', 'STATUS_BERBEDA'), ('pic_berbeda', 'PIC_BERBEDA'),
                       ('tanggal_dibuat_berbeda', 'TANGGAL_DIBUAT_BERBEDA')] if row[column]]
            reasons.append('; '.join(labels) or 'FIELD_DASAR_COCOK')
    out['pemeriksaan_dokumen'] = reasons
    difference = ~out['pemeriksaan_dokumen'].eq('FIELD_DASAR_COCOK')
    summary = {
        'api_documents': int(out['membership'].ne('PG_ONLY').sum()),
        'pg_reference_documents': int(out['membership'].ne('API_ONLY').sum()),
        'both': int(both.sum()), 'pg_only': int(out['membership'].eq('PG_ONLY').sum()),
        'api_only': int(out['membership'].eq('API_ONLY').sum()),
        'pg_only_before_api_start': int(out['pg_sebelum_batas_api'].sum()),
        'status_different': int(out['status_berbeda'].sum()), 'pic_different': int(out['pic_berbeda'].sum()),
        'created_date_different': int(out['tanggal_dibuat_berbeda'].sum()),
        'invalid_api_created_date': int(out['tanggal_api_tidak_valid'].sum()),
        'both_with_differences': int((both & difference).sum()),
        'headers_match_in_compared_snapshots': not bool(difference.any()) and not bool(out['tanggal_api_tidak_valid'].any()),
    }
    context = api.attrs.get('npr_api_context', {})
    out['API_created_source'] = out['API_No. Transaksi'].map(lambda n: context.get(_npr_text(n).upper(), {}).get('date_source', ''))
    out['API_pic_id'] = out['API_No. Transaksi'].map(lambda n: context.get(_npr_text(n).upper(), {}).get('api_pic_id', ''))
    first = ['No. NPR', 'membership', 'pemeriksaan_dokumen', 'API_Status', 'PG_Status', 'API_Sales', 'PG_Sales',
             'API_transaction_date', 'PG_transaction_date', 'API_created_source', 'API_pic_id']
    out = out[first + [c for c in out if c not in first and c != '_merge']]
    return out, summary


def _render_npr_header_gate_v6(api, end_date):
    import hashlib
    config = _npr_api_config()
    gate = {'allow_remote': False, 'state': 'REFERENSI_BELUM_DIUNGGAH', 'summary': {}, 'reference': None}
    with st.expander('🧪 Periksa dokumen utama sebelum memuat history', expanded=True):
        st.caption('CSV hanya pembanding. Seluruh grafik tetap memakai dokumen dari API; tidak ada data PG yang disisipkan.')
        upload = st.file_uploader('Unggah Hasil_Audit_NPR_PostgreSQL*.csv yang sudah Anda unduh', type=['csv'], key='npr_header_reference_v6')
        reference_end = st.date_input('Tanggal akhir laporan pada CSV PostgreSQL', value=date(2026, 10, 14),
                                      key='npr_reference_end_v6', help='Ini tanggal cutoff query pada CSV, bukan waktu mengunduh file.')
        if upload is not None:
            try:
                content = upload.getvalue()
                if len(content) > 25 * 1024 * 1024:
                    raise ValueError('Ukuran CSV referensi maksimal 25 MB.')
                raw = pd.read_csv(BytesIO(content), dtype=str, keep_default_na=False, encoding='utf-8-sig')
                reference = _npr_pg_reference_from_audit_v6(raw, reference_end)
                gate['reference_sha256'] = hashlib.sha256(content).hexdigest()
                gate['reference_meta'] = reference.attrs['pg_reference_meta']
                meta = gate['reference_meta']
                st.caption(f"Referensi: {meta['documents']:,} NPR | Database: {meta['database']} | Ekstraksi: {meta['extracted_at']}")
                if pd.Timestamp(end_date).date() != pd.Timestamp(reference_end).date():
                    gate['state'] = 'PERIODE_REFERENSI_BERBEDA'
                    st.warning('Tanggal akhir dashboard berbeda dari cutoff CSV. Samakan tanggal atau gunakan CSV PG baru; hasil tidak dipaksakan cocok.')
                else:
                    compare, summary = _npr_compare_headers_v6(api, reference, config['fetch_start_date'])
                    gate.update(reference=reference, compare=compare, summary=summary,
                                state='HEADER_COCOK_PADA_SNAPSHOT' if summary['headers_match_in_compared_snapshots'] else 'HEADER_BERBEDA')
                    st.write({
                        'NPR API sekarang': summary['api_documents'], 'NPR referensi PG': summary['pg_reference_documents'],
                        'Hanya PG': summary['pg_only'], 'Hanya API': summary['api_only'],
                        'Status berbeda': summary['status_different'], 'PIC berbeda': summary['pic_different'],
                        'Tanggal berbeda': summary['created_date_different'],
                    })
                    if summary['headers_match_in_compared_snapshots']:
                        st.success('Nomor, status, PIC, dan tanggal kalender dibuat cocok pada dua hasil ini. Ini belum membuktikan seluruh history atau asal database identik.')
                        gate['allow_remote'] = True
                    else:
                        st.warning('DATA DOKUMEN UTAMA BELUM COCOK. Request history terpisah dijeda agar tidak mengambil ribuan riwayat dari sumber yang belum setara.')
                    if summary['pg_only_before_api_start']:
                        earliest = meta['earliest_created_date']
                        st.info(f"{summary['pg_only_before_api_start']:,} NPR referensi dibuat sebelum batas awal API. Tanggal tertua pada CSV: {earliest}.")
                        if st.button('Gunakan batas awal dari CSV PostgreSQL', key='npr_extend_start_v6'):
                            st.session_state['_npr_api_period'] = {'base_url': config['base_url'], 'start_date': earliest}
                            st.session_state['_npr_connection_form_revision_v6'] = st.session_state.get('_npr_connection_form_revision_v6', 0) + 1
                            _clear_npr_caches()
                            st.rerun()
                    differences = compare.loc[~compare['pemeriksaan_dokumen'].eq('FIELD_DASAR_COCOK')]
                    st.dataframe(differences, use_container_width=True, hide_index=True, height=260)
                    st.download_button('⬇️ Unduh pemeriksaan dokumen utama.csv', _npr_csv_bytes_v6(compare),
                                       'Pemeriksaan_Header_NPR_v6.csv', 'text/csv', key='npr_headers_export_v6')
            except (ValueError, KeyError, pd.errors.ParserError, UnicodeError) as exc:
                gate['state'] = 'REFERENSI_TIDAK_VALID'
                st.warning(_npr_safe_message(str(exc)))
        else:
            st.info('Unggah CSV hasil audit yang sudah ada. Tanpa referensi, history terpisah tidak dimuat otomatis; grafik dokumen utama tetap tersedia.')
        normal = api.attrs.get('npr_normalization_diagnostic', {})
        invalid_keys = ('removed_missing_number', 'removed_invalid_status', 'duplicates_removed', 'invalid_created_date')
        if gate['allow_remote'] and any(normal.get(k, 0) for k in invalid_keys):
            gate.update(allow_remote=False, state='NORMALISASI_PERLU_DIPERIKSA')
            st.warning('Baris yang ditolak/duplikat/tanggal invalid pada normalisasi perlu diperiksa sebelum history dimuat otomatis.')
        if not gate['allow_remote']:
            # Persetujuan diikat pada server, token, periode, referensi, dan isi header API.
            # Berpindah sumber tidak membawa persetujuan lama.
            scope = repr((config['base_url'], config['token'], config['fetch_start_date'], str(end_date),
                          gate.get('reference_sha256'), gate['state'],
                          api.reindex(columns=['No. Transaksi', 'Status', 'Sales', 'transaction_date']).astype(str).to_csv(index=False)))
            checkbox_key = 'npr_allow_unverified_v6_' + hashlib.sha256(scope.encode()).hexdigest()[:16]
            gate['allow_remote'] = st.checkbox('Tetap muat history untuk pemeriksaan, meskipun dokumen utama belum terverifikasi cocok',
                                              value=False, key=checkbox_key)
            if gate['allow_remote']:
                st.caption('Pemuatan hanya untuk pemeriksaan. Izin ini tidak berarti sumber sudah sama dan tidak mengubah status hasil perbandingan.')
    return gate


def _npr_pg_reference_audit_rows_v6(reference):
    """Bentuk pembanding history lokal. Tidak dipanggil sebagai source dashboard."""
    history = reference[['No. Transaksi', '__first_process_date', '__first_complete_date']].copy()
    history = history.rename(columns={'No. Transaksi': '__npr_number'})
    history['__npr_number'] = history['__npr_number'].str.upper()
    history.attrs['npr_history_diagnostic'] = [
        {'__npr_number': row['No. Transaksi'].upper(), 'History State': 'API_FIRST_MILESTONES',
         'History Rows': row['__history_rows'], 'Created Date Source': 'created_at',
         'Milestone Source': 'CSV_POSTGRESQL_REFERENCE_ONLY', 'History Pagination': 'SQL_AGGREGATE'}
        for row in reference.to_dict('records')
    ]
    return _npr_audit_rows_v4(reference[['No. Transaksi', 'Status', 'Sales', 'transaction_date']].copy(), history)


def _render_npr_full_comparison_v6(api, history, gate):
    audit = _npr_audit_rows_v4(api, history)
    if audit.empty:
        return
    with st.expander('📥 Data pembanding NPR dan pemeriksaan milestone'):
        st.download_button('⬇️ Unduh data NPR API pembanding.csv', _npr_csv_bytes_v6(audit),
                           'NPR_API_Pembanding.csv', 'text/csv', key='npr_api_snapshot_v6')
        reference = gate.get('reference')
        if reference is not None:
            compare = _npr_compare_audits_v4(audit, _npr_pg_reference_audit_rows_v6(reference))
            st.caption('Riwayat belum dibaca tidak dianggap nol. SUMBER_CREATED_BELUM_TERVERIFIKASI berarti API masih memakai alias tanggal, meski tanggal kalender bisa cocok.')
            st.dataframe(compare, use_container_width=True, hide_index=True, height=300)
            st.download_button('⬇️ Unduh perbandingan lengkap NPR.csv', _npr_csv_bytes_v6(compare),
                               'Perbandingan_Lengkap_NPR_v6.csv', 'text/csv', key='npr_full_comparison_v6')



def _npr_resume_plan_v11_2(store, config, batch_state, fresh_success=0):
    """Bounded automatic retry; RATE_LIMIT_WAIT sends no new request and costs no retry."""
    import time
    run = store["run"]
    if fresh_success:
        run["rate_limit_streak"] = 0
    if batch_state in {"HTTP_429", "HTTP_503"}:
        run["rate_limit_streak"] = int(run.get("rate_limit_streak", 0)) + 1
    if batch_state in {"HTTP_429", "RATE_LIMIT_WAIT", "HTTP_503"}:
        wait = _npr_cooldown_status_v5(config["base_url"])["wait_seconds"]
        # An upstream 503 without Retry-After does not authorize an endless retry.
        retryable = batch_state != "HTTP_503" or wait > 0
        if int(run.get("rate_limit_streak", 0)) >= 6 or not retryable:
            run.update(auto=False, request=False, pause_reason="AUTO_RETRY_LIMIT_OR_NONRETRYABLE_503")
        elif run.get("auto"):
            # Extra delay can lengthen, but NEVER shorten, the server's cooldown.
            run["resume_not_before"] = max(float(run.get("resume_not_before", 0)), time.monotonic() + wait + 1.0)
    elif batch_state in {"BATCH_COMPLETE", "COMPLETE", "IDLE"}:
        if batch_state == "COMPLETE":
            run.update(auto=False, resume_not_before=0.0, rate_limit_streak=0)
    else:
        run.update(auto=False, request=False, pause_reason=batch_state)


def _npr_auto_wait_seconds_v11_2(config, store):
    import time
    import math
    gate = _npr_transport_gate_v5(config["base_url"])
    with gate["lock"]:
        target = max(gate["cooldown_until"], gate["next_at"],
                     float(store["run"].get("resume_not_before", 0)))
    return max(0, math.ceil(target - time.monotonic()))


def _npr_resume_tick_v11_2(scope):
    """Only schedules a foreground app rerun. No network request happens here."""
    config = _npr_history_settings()
    if _npr_history_scope_key(config) != scope:
        return
    store = _npr_history_store_v5(config)
    run = store["run"]
    if not run.get("auto") or not store.get("resume_pending_v11_2", False):
        return
    wait = _npr_auto_wait_seconds_v11_2(config, store)
    if wait:
        st.info(f"Menunggu {wait:,} detik sebelum lanjut otomatis. Hasil sukses tetap disimpan; tidak ada request saat jeda.")
        return
    store["resume_pending_v11_2"] = False
    run.update(request=True, resume_not_before=0.0)
    st.rerun()


def _npr_header_session_key_v11_2(config, start_date, end_date):
    import hashlib
    value = (NPR_PARITY_VERSION, config["base_url"], config["token"], _npr_endpoint_candidates()[0],
             config["end_date_param"], str(start_date), str(end_date))
    return "_npr_pinned_headers_v11_2_" + hashlib.sha256(repr(value).encode()).hexdigest()


if __name__ == "__main__":
    main()
