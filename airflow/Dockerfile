
FROM apache/airflow:3.4.0

USER root

COPY requirements.txt /tmp/ke24-requirements.txt

RUN python -m venv /opt/ke24-venv \
    && /opt/ke24-venv/bin/pip install --no-cache-dir \
       -r /tmp/ke24-requirements.txt

USER airflow
