@echo off
setlocal
title AGC Assurances S.A. - RCA Auto Generator

echo =====================================================================
echo  AGC ASSURANCES S.A. - SYSTEME OFFICIEL D'EMISSION RCA AUTOMOBILE
echo =====================================================================
echo.

cd /d "%~dp0"

REM ---- 1. .env file ----
if exist ".env" goto check_python
echo [INFO] Fichier .env absent. Creation a partir du modele...
copy ".env.example" ".env" >nul

:check_python
REM ---- 2. Python check ----
python --version >nul 2>&1
if not errorlevel 1 goto check_tesseract
echo [ERREUR] Python n'a pas ete detecte sur ce PC.
echo Veuillez installer Python depuis https://www.python.org/
echo Rappel : cochez "Add Python to PATH" pendant l'installation.
echo.
pause
exit /b 1

:check_tesseract
REM ---- 3. Tesseract check ----
echo [VERIF] Recherche du moteur OCR Tesseract...
set "TESS_FOUND=0"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESS_FOUND=1"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS_FOUND=1"
if "%TESS_FOUND%"=="1" goto tesseract_ok
echo.
echo  [AVERTISSEMENT] Tesseract OCR n'est pas encore installe.
echo  Si vous l'avez deja telecharge, terminez son installation
echo  puis relancez ce fichier. Sans Tesseract, l'OCR repose
echo  uniquement sur le moteur neuronal local (RapidOCR) ;
echo  si lui aussi est absent, la lecture des photos est
echo  indisponible. Les deux moteurs sont 100 % locaux.
echo.
echo  Telechargement : https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe
echo.
pause
goto setup_venv

:tesseract_ok
echo  [OK] Tesseract OCR detecte.
echo.

:setup_venv
REM ---- 4. Virtual environment ----
python --version
if exist ".venv" goto activate_venv
echo [1/3] Creation de l'environnement virtuel...
python -m venv .venv
if errorlevel 1 goto venv_failed
goto activate_venv

:venv_failed
echo [ERREUR] Echec de creation de l'environnement virtuel.
pause
exit /b 1

:activate_venv
echo [2/3] Activation de l'environnement virtuel...
call ".venv\Scripts\activate.bat"

echo [3/3] Installation des librairies requises - patientez...
python -m pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
if errorlevel 1 goto pip_failed

echo [3/3] Moteur OCR neuronal optionnel...
pip install --quiet -r requirements-ocr.txt
if errorlevel 1 goto ocr_optional
echo  [OK] Moteur OCR neuronal installe.
goto launch

:ocr_optional
echo.
echo  [INFO] Moteur neuronal non disponible sur ce Python - poursuite
echo  en mode Tesseract-OCR. L'application reste pleinement utilisable.
echo.
goto launch

:pip_failed
echo [ERREUR] Echec de l'installation des librairies de base.
echo  Verifiez votre connexion Internet puis relancez start.bat.
echo  Pour voir le detail :  pip install -r requirements.txt
pause
exit /b 1

:launch
REM ---- 5. Port depuis .env (defaut 8000) ----
set "PORT=8000"
if exist ".env" for /f "tokens=1,* delims==" %%A in ('findstr /b "PORT=" .env') do set "PORT=%%B"
set "PORT=%PORT: =%"
if "%PORT%"=="" set "PORT=8000"
echo.
echo =====================================================================
echo  SERVEUR AGC ASSURANCES DEMARRE AVEC SUCCES SUR :
echo  ==^> http://localhost:%PORT%
echo =====================================================================
echo.
echo  Moteurs d'extraction (100 %% locaux, hors-ligne) :
echo   - RapidOCR neuronal (si installe sur ce Python)
echo   - Tesseract OCR (francais + anglais)
echo.
echo  Laissez cette fenetre ouverte. Fermez-la pour arreter le serveur.
echo =====================================================================
echo.

start "" http://localhost:%PORT%
python -m uvicorn main:app --host 0.0.0.0 --port %PORT%
echo.
echo Le serveur s'est arrete.
pause
