# Requires an existing, audited runtime image (docker/scraper). Build never installs or downloads.
# Named contexts: implementation=./acquisition (its .dockerignore applies), backend=. (the root allowlist applies:
# only api.py, queries.py, importar.py and persistencia/bundle.py are taken), checks=./tests/acquisition for the test target.
ARG PROFILE_RUNTIME=local/rj-browser-runtime:required
FROM ${PROFILE_RUNTIME} AS runtime
USER root
RUN rm -rf /opt/probe && mkdir -p /profiles /acquisition-jobs && chown 10001:10001 /profiles /acquisition-jobs
COPY --from=implementation / /opt/app/acquisition/
COPY --from=backend api.py queries.py importar.py /opt/app/
COPY --from=backend persistencia/bundle.py /opt/app/persistencia/bundle.py
COPY --from=backend requirements-acquisition.txt /opt/requirements-acquisition.txt
WORKDIR /opt/app
ENV PYTHONPATH=/opt/app:/opt/scraper PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m pip check \
    && python -c "from importlib.metadata import version; required = dict(line.strip().split('==', 1) for line in open('/opt/requirements-acquisition.txt') if line.strip() and not line.lstrip().startswith('#')); assert all(version(package) == expected for package, expected in required.items())"
USER 10001:10001
# The service uses one worker because it owns the exclusive browser profile and jobs journal.
ENTRYPOINT ["python", "-B", "-m", "uvicorn"]
CMD ["acquisition.app:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1", "--no-access-log"]

# Test-only instruments (contract tests, local fixture site) never belong to the runtime target above.
FROM runtime AS test
COPY --from=checks / /opt/checks/
# The backend client, only to test the contract end to end; the runtime target never has it.
COPY --from=backend persistencia/adquisicion.py /opt/checks/persistencia/
COPY --from=backend productores-adquisicion.json /opt/checks/
ENV PYTHONPATH=/opt/app:/opt/scraper:/opt/checks
