FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV OBSIDIAN_ROOT=/data/obsidian
ENV EMBED_PROVIDER=ollama

CMD ["python", "server.py"]