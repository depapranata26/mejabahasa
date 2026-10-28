# MejaBahasa on Docker hosts (Hugging Face Spaces, Koyeb, ...).
# Stdlib-only server: no pip dependencies to install.
FROM python:3.12-slim
WORKDIR /app
COPY . /app
# Hosts route to $PORT (HF Spaces defaults to 7860). Fall back to 7860
# when the host does not set PORT.
EXPOSE 7860
CMD ["sh", "-c", "python3 backend/server.py ${PORT:-7860}"]
