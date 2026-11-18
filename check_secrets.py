import streamlit as st
from pathlib import Path
import os

st.title("Diagnostic Streamlit Secrets")

st.write("Working directory:", os.getcwd())
st.write("Expected secrets path:",
         str(Path.cwd() / ".streamlit" / "secrets.toml"))

try:
    keys = list(st.secrets.keys())
    st.write("Daftar konfigurasi:", keys)

    api_key = str(st.secrets.get("SIBIMA_API_KEY", "")).strip()
    st.write("API Key:", "TERBACA" if api_key else "TIDAK TERBACA")

except Exception as e:
    st.error(f"{type(e).__name__}: {e}")



import requests

st.subheader("Test ERP API Connection")

if st.button("Test Purchase Requests API"):
    api_key = str(st.secrets["SIBIMA_API_KEY"]).strip()

    try:
        response = requests.get(
            "https://erp.sibima.id/api/purchase-requests",
            headers={
                "x-api-key": api_key,
                "Accept": "application/json"
            },
            params={
                "date_start": "2026-01-01",
                "date_end": "2026-07-07"
            },
            timeout=30
        )

        st.write("HTTP Status:", response.status_code)

        if response.status_code == 200:
            payload = response.json()
            st.success("Koneksi API berhasil!")
            st.write("Success:", payload.get("success"))
            data = payload.get("data", [])
            st.write(
                "Jumlah data dalam respons:",
                len(data) if isinstance(data, list) else "Periksa pagination"
            )
        else:
            st.error("API menolak request atau mengalami gangguan.")

    except requests.RequestException as e:
        st.error(f"Koneksi gagal: {type(e).__name__}")