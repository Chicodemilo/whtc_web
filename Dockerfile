FROM python:3.12-slim

WORKDIR /app

# No pip dependencies needed — stdlib only
COPY server.py .
COPY static/ static/

# Data and music dirs will be mounted as volumes
RUN mkdir -p data music

EXPOSE 8080

CMD ["python3", "server.py"]
