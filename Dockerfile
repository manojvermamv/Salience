FROM python@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419

ARG SALIENCE_WHEEL_FILE

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY ${SALIENCE_WHEEL_FILE} /opt/salience-artifacts/salience.whl
RUN ln -s /opt/salience-artifacts/salience.whl /opt/salience-artifacts/salience-0.1.0-py3-none-any.whl \
    && python -m pip install --no-cache-dir --no-compile /opt/salience-artifacts/salience-0.1.0-py3-none-any.whl \
    && rm /opt/salience-artifacts/salience-0.1.0-py3-none-any.whl
COPY alembic.ini /usr/local/lib/python3.13/site-packages/alembic.ini
COPY migrations /usr/local/lib/python3.13/site-packages/migrations

CMD ["/usr/local/bin/python", "-I", "-B", "-S", "/run/salience-control/launch-v4-publication-worker.py", "--mode", "current", "--worker-kind", "deployable"]
