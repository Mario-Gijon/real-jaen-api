# Requires an existing, audited runtime image. Build never installs or downloads.
ARG PROFILE_RUNTIME=local/rj-browser-runtime:required
FROM ${PROFILE_RUNTIME} AS runtime
USER root
RUN rm -rf /opt/probe && mkdir -p /profiles && chown 10001:10001 /profiles
COPY --from=implementation / /opt/app/acquisition/
WORKDIR /opt/app
ENV PYTHONPATH=/opt/app PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
USER 10001:10001
ENTRYPOINT ["python", "-B", "-m", "acquisition.admin"]
CMD ["--help"]

# Test-only instruments never belong to the runtime target above.
FROM runtime AS test
COPY --from=checks / /opt/checks/
ENV PYTHONPATH=/opt/app:/opt/checks
