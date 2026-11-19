FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home app

# OAuth state (claude.ai's client registration, encrypted upstream tokens) lives
# here; mount it, or every redeploy forces a new login in claude.ai.
ENV FASTMCP_HOME=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
RUN mkdir /data && chown app /data
VOLUME /data
USER app

EXPOSE 8000
# python, not curl: the slim image ships neither curl nor wget.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["chefkoch-mcp"]
