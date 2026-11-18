FROM solo36-tracedesk:1.0
USER root
RUN rm -rf /app && mkdir -p /app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SPHEREVIEW_DATA_DIR=/data SPHEREVIEW_HOST=0.0.0.0 PORT=18937 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 CMD python3 -B -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18937/sphere/health')"
EXPOSE 18937
CMD ["python3", "-B", "app.py"]
