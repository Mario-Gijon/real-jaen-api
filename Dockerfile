FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-sql.txt ./
RUN pip install --no-cache-dir -r requirements-sql.txt \
    && groupadd --gid 10001 analyst \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin analyst \
    && mkdir /state /imports && chown 10001:10001 /state /imports
COPY api.py importar.py ambito.json alembic.ini ./
COPY persistencia ./persistencia
COPY scripts ./scripts
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3).close()"
ENTRYPOINT ["python", "-B"]
CMD ["api.py", "--almacen", "sql", "--base", "/state/real-jaen.sqlite", "--ambito", "/app/ambito.json", "--host", "0.0.0.0", "--port", "8001"]
