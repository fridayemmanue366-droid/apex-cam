# Apex Cam cloud server (accounts, credits, payments, Decart proxy).
FROM python:3.11-slim

# opencv-python-headless still wants libglib at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The DB lives on the mounted volume (/data), never in the image.
ENV APEXCAM_DB=/data/apexcam.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
