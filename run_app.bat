@echo off
cd /d "%~dp0"
.venv\Scripts\streamlit run app.py --server.address localhost
