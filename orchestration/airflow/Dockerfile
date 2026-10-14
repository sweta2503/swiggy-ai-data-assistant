# Apache Airflow with this project's own Python packages in a separate virtualenv, so the
# DAG's tasks (`make bronze`, `make silver`, ...) run exactly as they do on your laptop.
FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/swiggy-venv \
    && chown -R airflow /opt/swiggy-venv

USER airflow
COPY requirements.txt /tmp/swiggy-requirements.txt
RUN /opt/swiggy-venv/bin/pip install --no-cache-dir -r /tmp/swiggy-requirements.txt
