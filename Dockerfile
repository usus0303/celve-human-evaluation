FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CELVE_STATE_DIR=/data/state
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home celve \
    && mkdir -p /data/state /data/backups \
    && chown -R celve:celve /data
COPY *.py ./
COPY static ./static
USER celve
EXPOSE 8000
CMD ["python", "railway_start.py"]
