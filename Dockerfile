# One image runs all four apps:  docker build -t retail-multi-agent .   then   docker run -p 8501-8504:8501-8504 retail-multi-agent
FROM python:3.12-slim

# tzdata: the apps print local times, so the container needs a time zone database
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# install the packages first: this layer is reused when only the code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# a normal user, not root; it owns the data folder (the database lives there)
RUN useradd --create-home app && mkdir -p /app/data /app/outbox && chown -R app:app /app
USER app

EXPOSE 8501 8502 8503 8504
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')" || exit 1

# first start: no database yet -> invent demo data and build it; later starts reuse the database in /app/data
CMD ["python", "start_all.py", "--demo-if-empty", "--host", "0.0.0.0"]
