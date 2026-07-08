FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pandas openpyxl

WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
COPY import_excel.py /work/import_excel.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

