# =============================================================================
# Dockerfile — AURA-Grid PoC (application Streamlit)
# =============================================================================
# Rôle : construire l'image du conteneur Streamlit déployée sur Google Cloud Run.
#
# - Base python:3.10-slim (Python 3.10+ requis, aucune dépendance apt).
# - Installe les dépendances Python (streamlit, openai) via requirements.txt.
# - Embarque app.py, .streamlit/ (thème + headless) et Ressources/ (3 PDF servis
#   par le serveur HTTP interne sur 127.0.0.1:8765).
# - Écoute sur 0.0.0.0:$PORT (Cloud Run injecte PORT, défaut 8080).
# - Exécute le conteneur avec un utilisateur non-root.
#
# Build local : docker build -t aura-grid .
# Run local   : docker run -p 8080:8080 -e PORT=8080 aura-grid
# =============================================================================

FROM python:3.10-slim

# --- Variables d'environnement Python / pip --------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- Dépendances Python (couche mise en cache) -----------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Code applicatif et ressources runtime ---------------------------------
COPY app.py ./
COPY .streamlit/ ./.streamlit/
COPY Ressources/ ./Ressources/

# --- Utilisateur non-root --------------------------------------------------
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# --- Port documentaire (Cloud Run injecte $PORT) ---------------------------
EXPOSE 8080

# --- Démarrage Streamlit ---------------------------------------------------
# Forme shell volontaire pour permettre l'expansion de ${PORT}.
CMD streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false
