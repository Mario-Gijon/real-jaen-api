# Isolated packaging candidate, never referenced by production Compose.
FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright PYTHONPATH=/opt/scraper \
    SOFASCORE_CONFIG_DIR=/acquisition/config SOFASCORE_ENV_FILE=/acquisition/config/settings.env \
    SOFASCORE_CLIENT__BROWSER_PROFILE=/acquisition/profile SOFASCORE_LOG__TO_FILE=false \
    SOFASCORE_CLIENT__MAX_CONCURRENT=1
COPY --from=scraper / /opt/scraper/
COPY --from=backend /requirements-sql.txt /opt/requirements-sql.txt
WORKDIR /opt/scraper
RUN test "$(cat SOURCE_REVISION)" = '611e436a1528b0f487aa56a045f023e8ae8ae098' \
    && sha256sum -c SOURCE_FILES.sha256 \
    && test ! -e frontend/package-lock.json \
    && pip install --no-cache-dir -r requirements.txt -c constraints.txt -r /opt/requirements-sql.txt \
    && pip check \
    && python -m patchright install --with-deps --no-shell chromium \
    && pip freeze > /opt/runtime-python.txt \
    && dpkg-query -W > /opt/runtime-system.txt \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 analyst \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin analyst \
    && mkdir -p /acquisition/profile /acquisition/config /acquisition/data \
    && chown -R 10001:10001 /acquisition
COPY offline_probe.py /opt/probe/offline_probe.py
ENV SOFASCORE_STORAGE__DATA_DIR=/acquisition/data SOFASCORE_CLIENT__THROTTLE_DIR=/acquisition/throttle
USER 10001:10001
# No server, scraper download or coordinator is started by this image.
ENTRYPOINT ["python", "-B", "/opt/probe/offline_probe.py"]
CMD ["--phase", "dependencies"]
