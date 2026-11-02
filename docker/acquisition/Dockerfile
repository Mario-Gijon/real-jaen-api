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
WORKDIR /opt/app
ENV PYTHONPATH=/opt/app:/opt/scraper PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
USER 10001:10001
# Administrative profile commands by default; Compose runs the service with `python -B -m acquisition.service`.
ENTRYPOINT ["python", "-B", "-m", "acquisition.admin"]
CMD ["--help"]

# Test-only instruments (contract tests, local fixture site) never belong to the runtime target above.
FROM runtime AS test
COPY --from=checks / /opt/checks/
# The backend client, only to test the contract end to end; the runtime target never has it.
COPY --from=backend persistencia/adquisicion.py /opt/checks/persistencia/
COPY --from=backend productores-adquisicion.json /opt/checks/
ENV PYTHONPATH=/opt/app:/opt/scraper:/opt/checks
