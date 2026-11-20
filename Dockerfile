FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed HF_HOME=/app/.cache/hf

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
# dataset sizes and results.json feed the dashboard's Fine-tuning tab (training itself runs on Colab)
COPY finetune finetune

# Bake the knowledge base and the embedding / reranker models into the image so cold starts are fast
# and the container never needs to download anything at runtime. Hosts that run with RAG_BACKEND=lite
# (512 MB plans) never load the models, so the download is skipped when that value is passed at build time.
ARG RAG_BACKEND=auto
RUN cd backend && python -m scripts.generate_docs
RUN if [ "$RAG_BACKEND" != "lite" ]; then cd backend && python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; TextEmbedding('BAAI/bge-small-en-v1.5'); TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')" || echo "model pre-download skipped"; fi

RUN useradd -m app && mkdir -p /app/backend/data && chown -R app /app
USER app

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')" || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
