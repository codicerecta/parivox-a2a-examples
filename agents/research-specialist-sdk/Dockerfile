# syntax=docker/dockerfile:1
FROM python:3.13-slim

# Unbuffered stdout so container logs appear in CloudWatch as they happen rather
# than when the buffer flushes.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user. Nothing here needs write access to the filesystem.
RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz', timeout=2).status==200 else 1)"

CMD ["python", "-m", "app.main"]
