FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/alertas.db PORT=8080
COPY alertas ./alertas
COPY run.py .
VOLUME /data
EXPOSE 8080
CMD ["python", "run.py"]
