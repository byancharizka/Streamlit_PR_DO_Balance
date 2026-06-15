#Import Library, lampirkan di file requirements.txt
import requests
import numpy as np
import pandas as pd
import pytz
import os
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, date
from PIL import Image
import csv
import re
import xlsxwriter
from io import BytesIO

# --- CONFIGURASI PAGE (WAJIB PALING ATAS) ---
st.set_page_config(layout="wide", page_title="SIBIMA Performance Dashboard")

# --- CSS CUSTOM UNTUK FULL WIDTH ---
st.markdown("""
    <style>
    .block-container {
        padding-top: 3rem;
        padding-bottom: 1rem;
        padding-left: 5rem;
        padding-right: 5rem;
        max-width: 100%;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.8rem;
    }
    </style>
    """, unsafe_allow_html=True)

st.markdown("""
    <style>
    /* Styling untuk Card */
    .metric-card {
        background-color: #b3b2ae;  /* Abu-abu muda standar Streamlit */
        border: 1px solid #dcdcdc;  /* Border abu-abu sedikit lebih gelap */
        border-radius: 10px;
        padding: 20px;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.05);
        text-align: center;
        margin: 10px 0;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 1. KONFIGURASI DATA & API ---
timezone = pytz.timezone('Asia/Jakarta')
now = datetime.now(timezone)
today = now.strftime("%Y-%m-%d")

#TOKEN = "44b71f38c25ddd02cd31b409f85e9f3aca4f337f02f2fa90237afc2a0736"
BASE_URL = "https://eas.sibima.id/api/dashboard/"


# --- 1. PERBAIKAN FUNGSI PENGAMBILAN DATA ---
@st.cache_data(ttl=600)
def get_api_data(endpoint, start_date_override=None):
    url = f"{BASE_URL}{endpoint}"
    actual_start = start_date_override if start_date_override else "2026-01-01"
    
    params = {"date_start": actual_start, "date_end": today}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            json_response = response.json()
            # Akses ke 'data' -> 'data' sesuai struktur JSON Anda
            if 'data' in json_response and 'data' in json_response['data']:
                return pd.DataFrame(json_response['data']['data'])
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Error fetching {endpoint}: {e}")
        return pd.DataFrame()

# --- 2. HAPUS FUNGSI EXPAND (TIDAK DIPERLUKAN LAGI) ---
# Karena get_api_data sudah langsung mengambil list yang tepat, 
# Anda tidak perlu lagi melakukan .explode() atau .apply(pd.Series)

# Eksekusi langsung
#df_so = get_api_data("so-balance")
df_pr = get_api_data("pr-balance")
df_po = get_api_data("po-balance")
df_do = get_api_data("do-balance")

# Mengubah kolom 'Tgl ...' menjadi 'transaction_date'
#df_so = df_so.rename(columns={'tanggal': 'transaction_date'})
df_pr = df_pr.rename(columns={'Tgl. PR': 'transaction_date'})
#df_po = df_po.rename(columns={'Tgl. PO': 'transaction_date'})
df_do = df_do.rename(columns={'Tgl. DO': 'transaction_date'})


# --- 3. HANDLING INPUT TANGGAL ---
st.sidebar.header("📅 Filter period")

start_default = date(2026, 2, 1) # Diubah ke Feb agar sesuai case
end_default = date.today()

selected_date_range = st.sidebar.date_input(
    "Select Date Range:",
    value=(start_default, end_default),
    max_value=date.today()
)

# --- 4. FUNGSI FILTER ---
def apply_realization_filter(df, date_range):
    if df.empty: 
        return df
    
    df = df.copy()
    
    # Pastikan tipe data numerik agar sum() akurat
    cols_to_fix = ['Qty Permintaan (PR)', 'Qty Sudah PO', 'Qty Closed','Qty Outstanding']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Filter Tanggal (Hanya jalan jika rentang lengkap: Start & End)
    if 'transaction_date' in df.columns and isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        df['transaction_date'] = pd.to_datetime(df['transaction_date']).dt.tz_localize(None)
        
        start_dt = pd.to_datetime(date_range[0]).replace(hour=0, minute=0, second=0)
        end_dt = pd.to_datetime(date_range[1]).replace(hour=23, minute=59, second=59)
        
        df = df[(df['transaction_date'] >= start_dt) & (df['transaction_date'] <= end_dt)]

    return df


def apply_cumulative_filter(df, end_date):
    """
    Mengambil SEMUA data dari awal hingga batas end_date.
    Data masa lalu (Januari) akan ikut, data masa depan (Maret) akan dibuang.
    """
    if df.empty or 'transaction_date' not in df.columns:
        return df
    
    df = df.copy()
    # Konversi ke datetime dan hilangkan timezone
    df['transaction_date'] = pd.to_datetime(df['transaction_date']).dt.tz_localize(None)
    
    # Ambil batas akhir hari (23:59:59)
    upper_limit = pd.to_datetime(end_date).replace(hour=23, minute=59, second=59)
    
    # Filter hanya berdasarkan batas atas
    return df[df['transaction_date'] <= upper_limit]


if isinstance(selected_date_range, (tuple, list)) and len(selected_date_range) == 2:
    # Misal user pilih 1 Feb - 28 Feb di Sidebar
    # start_date = 2026-02-01 (Kita abaikan ini)
    # end_date = 2026-02-28 (Ini yang kita pakai)
    report_end_date = selected_date_range[1]
    
    # Semua dokumen diproses secara akumulatif
    df_pr_f = apply_cumulative_filter(df_pr, report_end_date)
    df_po_f = apply_cumulative_filter(df_po, report_end_date)
    df_do_f = apply_cumulative_filter(df_do, report_end_date)

# --- 5. EKSEKUSI ---
#df_so_real = apply_realization_filter(df_so, selected_date_range)
#df_pr_real = apply_realization_filter(df_pr, selected_date_range)
#df_po_real = apply_realization_filter(df_po, selected_date_range)
#df_do_real = apply_realization_filter(df_do, selected_date_range)

# Cek data PR apakah data berhasil dimuat
#if not df_pr_real.empty:
    #st.write("Data PR Loaded:", df_pr_real)

# Cek data DO apakah data berhasil dimuat
#if not df_do_real.empty:
    #st.write("Data DO Loaded:", df_do_real)


# Konversi kolom Nominal ke float
df_pr_f['Nominal'] = pd.to_numeric(df_pr_f['Nominal'], errors='coerce').fillna(0.0).astype(float)
df_do_f['Nominal'] = pd.to_numeric(df_do_f['Nominal'], errors='coerce').fillna(0.0).astype(float)
# Mengisi nilai NaN atau kosong dengan nama yang Anda pilih
df_pr_f['PIC Procurement'] = df_pr_f['PIC Procurement'].fillna('Unassigned')


# --- AGREGASI FINAL UNTUK DASHBOARD PR ---
total_pr_unpr = df_pr_f['Nominal'].sum()


def metric_card(label, value):
    # Menggunakan HTML untuk membungkus metric
    st.markdown(f"""
    <div class="metric-card">
        <div style="color: #666; font-size: 0.9rem;">{label}</div>
        <div style="font-size: 1.5rem; font-weight: bold; color: #333;">{value}</div>
    </div>
    """, unsafe_allow_html=True)


# --- STATUS PR ---
# Menghitung angka-angka kunci untuk ringkasan di atas
total_pr_count = df_pr_f['No. PR'].nunique()
total_pr_rows = len(df_pr_f)
avg_nominal_pr = df_pr_f['Nominal'].mean()
# --- LOGIKA PIC TERBANYAK (DOKUMEN UNIK) ---

# 1. Pastikan PIC kosong sudah jadi 'Unassigned'
df_pr_f['PIC Procurement'] = df_pr_f['PIC Procurement'].fillna('Unassigned')
df_pr_f.loc[df_pr_f['PIC Procurement'] == "", 'PIC Procurement'] = 'Unassigned'

# 2. Filter hanya untuk PIC yang bertugas
df_assigned = df_pr_f[df_pr_f['PIC Procurement'] != 'Unassigned']

# 3. Hitung jumlah DOKUMEN PR UNIK per PIC (menggunakan nunique)
pic_counts = df_assigned.groupby('PIC Procurement')['No. PR'].nunique().sort_values(ascending=False)

# 4. Ambil daftar PIC
list_pic_urut = pic_counts.index.tolist()

# 5. Tentukan top_pic
if len(list_pic_urut) >= 1:
    top_pic = list_pic_urut[0]  # Juara 1 (Faqih)
else:
    top_pic = "Tidak ada"

# Debugging (Opsional): Tampilkan di bawah st.write untuk memastikan angkanya benar
# st.write("Data debug untuk PIC:", pic_counts)

st.subheader("📊Detail Outstanding PR")
c1, c2 = st.columns(2)
with c1: metric_card("PR Balance", f"Rp {total_pr_unpr:,.0f}")
with c2: metric_card("Rata-rata Nominal", f"Rp {avg_nominal_pr:,.0f}")


c1, c2, c3 = st.columns(3)
with c1: metric_card("Total Dokumen PR", f"{total_pr_count:,}")
with c2: metric_card("Total Item PR", f"{total_pr_rows:,}")
with c3: metric_card("PIC Terbanyak", top_pic)


#st.markdown("---") # Garis pembatas


# --- AGREGASI STATUS PR ---
#if not df_pr_f.empty and 'Status' in df_pr_f.columns:
# Mengelompokkan berdasarkan Status
pr_summary = df_pr_f.groupby('Status').agg(
Total_PR=('No. PR', 'nunique'),     # Menghitung jumlah unik nomor PR
Total_Amount=('Nominal', 'sum')     # Menjumlahkan nominal
).reset_index()


# Tentukan warna untuk setiap status agar konsisten di seluruh dashboard
status_colors = {
    "Complete": "#00CC96",   # Hijau
    "In Progress": "#f2e6ac", # Kuning
    "Approved": "#f6a27e",    # Oranye
    # Tambahkan status lain jika ada
}

# --- VISUALISASI PIE CHART ---
if not pr_summary.empty:
    st.subheader("🍩Proporsi Nominal PR per Status")
    
    fig_pie = px.pie(
        pr_summary, 
        values='Total_Amount', 
        names='Status', 
        color='Status',
        color_discrete_map=status_colors, # Menggunakan mapping warna yang sama
        hole=0.4, # Membuat tampilan menjadi Donut Chart (opsional)
        title="Persentase Distribusi Nominal PR"
    )
    
    # Mengatur tampilan agar lebih bersih
    fig_pie.update_traces(
        textinfo='percent+value', # Menampilkan persentase dan nilai
        texttemplate='%{percent:.1%} <br>(Rp %{value:,.0f})'
    )
    
    st.plotly_chart(fig_pie, use_container_width=True)


st.subheader("🔍Analisis Status PR")
    
    # Menampilkan tabel ringkasan
    #st.table(pr_summary)
    
    # Opsional: Menampilkan dengan format Rupiah yang lebih cantik di tabel
pr_summary_display = pr_summary.copy()
pr_summary_display['Total_Amount'] = pr_summary_display['Total_Amount'].apply(lambda x: f"Rp {x:,.0f}")
st.write(pr_summary_display)

#else:
    #st.info("Data PR tidak tersedia atau kolom 'Status' tidak ditemukan.")


# --- VISUALISASI BAR CHART DENGAN WARNA BERBEDA ---


fig = px.bar(
    pr_summary, 
    x='Status', 
    y='Total_Amount', 
    color='Status',
    color_discrete_map=status_colors, # Konsistensi warna
    title="Distribusi Nominal PR per Status"
)

# 1. Update traces untuk angka di atas bar (format ribuan/jutaan penuh)
fig.update_traces(
    texttemplate='Rp %{y:,.0f}', # Menggunakan format ribuan (,) dengan 0 desimal
    textposition='outside'
)

# 2. Update layout untuk menghilangkan legenda warna dan mengatur sumbu Y
fig.update_layout(
    showlegend=False,              # Menghilangkan legenda warna di samping
    yaxis=dict(
        tickformat=',.0f',         # Format sumbu Y agar muncul angka penuh
        title="Total Nominal (Rp)"
    )
)

st.plotly_chart(fig, use_container_width=True)



# --- ANALISIS PIC PROCUREMENT TERBANYAK PER STATUS ---
# --- PEMBERSIHAN DATA (TAMBAHKAN SEBELUM PROSES FILTER) ---

# Jika ternyata isinya bukan NaN tapi string kosong (""), gunakan ini:
df_pr_f.loc[df_pr_f['PIC Procurement'] == "", 'PIC Procurement'] = 'Unassigned'
if not df_pr_f.empty and 'PIC Procurement' in df_pr_f.columns:
    
    # 1. Grouping berdasarkan PIC dan Status, lalu hitung jumlah baris (atau unique No. PR)
    pic_summary = df_pr_f.groupby(['PIC Procurement', 'Status']).agg(
        Jumlah_PR=('No. PR', 'nunique')
    ).reset_index()

    # 2. Urutkan berdasarkan jumlah terbanyak
    pic_summary = pic_summary.sort_values(by='Jumlah_PR', ascending=False)

    st.subheader("👤Analisis PIC Procurement per Status")
    
    # 3. Tampilkan dalam bentuk Bar Chart
    fig_pic = px.bar(
    pic_summary, 
    x='PIC Procurement', 
    y='Jumlah_PR', 
    color='Status',
    color_discrete_map=status_colors, # Warna akan mengikuti mapping yang sama
    title="Jumlah PR per PIC Procurement",
    )

    fig_pic.update_traces(
        texttemplate='%{y}',           
        textposition='inside',         
        textfont_size=10,
        textangle=0                    # <--- TAMBAHKAN INI: Memaksa sudut teks 0 derajat (tegak lurus)
    )

    
    fig_pic.update_layout(
        xaxis_title="PIC Procurement",
        yaxis_title="Jumlah PR",
        legend_title="Status PR",
        # --- TAMBAHKAN KONFIGURASI DI BAWAH INI ---
        uniformtext_mode='hide',       # Menyembunyikan teks jika batang terlalu sempit
        uniformtext_minsize=8          # Ukuran font minimum agar tetap terbaca
    )
    
    st.plotly_chart(fig_pic, use_container_width=True)
    
    # 4. Tampilkan tabel detailnya
    st.write("Tabel Detail PIC:")
    st.table(pic_summary)

else:
    st.info("Data PIC Procurement tidak tersedia atau kolom tidak ditemukan.")


# Tambahkan ini setelah eksekusi data
#df_pr_real['month'] = df_pr_real['transaction_date'].dt.to_period('M').astype(str)
#trend_data = df_pr_real.groupby('month')['Nominal'].sum().reset_index()

#st.subheader("Tren Nominal PR Bulanan")
#fig_trend = px.line(trend_data, x='month', y='Nominal', markers=True, title="Tren PR per Bulan")
#st.plotly_chart(fig_trend, use_container_width=True)



# --- FITUR DOWNLOAD EXCEL PER PIC ---
st.subheader("📥 Download Data PR per PIC")

if not df_pr_f.empty and 'PIC Procurement' in df_pr_f.columns:
    # Ambil list unik PIC yang ada di data
    list_pic = df_pr_f['PIC Procurement'].unique().tolist()
    
    # Dropdown untuk memilih PIC
    selected_pic = st.selectbox("Pilih PIC Procurement:", list_pic)
    
    if selected_pic:
        # Filter data berdasarkan PIC yang dipilih
        df_filtered = df_pr_f[df_pr_f['PIC Procurement'] == selected_pic]
        
        # Konversi ke Excel di memori (menggunakan BytesIO)
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_filtered.to_excel(writer, index=False, sheet_name='Data_PR')
            
        # Tombol download
        st.download_button(
            label=f"Download Data {selected_pic}.xlsx",
            data=output.getvalue(),
            file_name=f"Data_PR_{selected_pic}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("Data tidak tersedia untuk fitur download.")


search_pr = st.sidebar.text_input("Cari No. PR:")
if search_pr:
    result = df_pr_f[df_pr_f['No. PR'].str.contains(search_pr, case=False, na=False)]
    st.write("Hasil Pencarian:", result)

st.markdown("---") # Garis pembatas



# --- AGREGASI FINAL UNTUK DASHBOARD PR ---
total_do_unpr = df_do_f['Nominal'].sum()


def metric_card(label, value):
    # Menggunakan HTML untuk membungkus metric
    st.markdown(f"""
    <div class="metric-card">
        <div style="color: #666; font-size: 0.9rem;">{label}</div>
        <div style="font-size: 1.5rem; font-weight: bold; color: #333;">{value}</div>
    </div>
    """, unsafe_allow_html=True)


# --- STATUS PR ---
# Menghitung angka-angka kunci untuk ringkasan di atas
total_do_count = df_do_f['No. DO'].nunique()
total_do_rows = len(df_do_f)
avg_nominal_do = df_do_f['Nominal'].mean()


st.subheader("📊Detail Outstanding DO")
c1, c2 = st.columns(2)
with c1: metric_card("DO Balance", f"Rp {total_do_unpr:,.0f}")
with c2: metric_card("Rata-rata Nominal", f"Rp {avg_nominal_do:,.0f}")


c1, c2 = st.columns(2)
with c1: metric_card("Total Dokumen DO", f"{total_do_count:,}")
with c2: metric_card("Total Item DO", f"{total_do_rows:,}")
