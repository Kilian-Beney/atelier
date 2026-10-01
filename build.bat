@echo off
rem Compile l'application en dist\Atelier\Atelier.exe (sur Mac : ./build.sh)
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
if not exist icone.ico .venv\Scripts\python creer_icone.py
rem Les DLL CUDA ne sont pas embarquees : l'application les telecharge a la demande.
rem Pour les embarquer (exe de 2,6 Go) : set ATELIER_CUDA=1 avant de lancer ce script.
.venv\Scripts\pyinstaller --noconfirm --clean Atelier.spec
echo.
echo Termine : dist\Atelier\Atelier.exe
