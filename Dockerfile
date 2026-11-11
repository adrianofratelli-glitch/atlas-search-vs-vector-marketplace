# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Same-origin in production: nginx proxies the backend routes below, so the
# frontend calls relative paths instead of a separate VITE_API_URL host.
# "/" (not ""): api.js does `VITE_API_URL || "http://localhost:8200"`, so an
# empty value fell back to localhost:8200 and the browser missed the proxy.
ENV VITE_API_URL=/
RUN npm run build

FROM python:3.12-slim AS runtime
RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx curl \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 10001 app

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
# pov-shared (grove_client) vem de um contexto nomeado; sem ele /agent e
# /reviews-rag respondem "unavailable":
#   docker build --build-context shared=../_shared -t search-vs-vector .
COPY --from=shared . /opt/pov-shared
RUN pip install --no-cache-dir "/opt/pov-shared[llm]"

COPY backend/ ./backend/
COPY --from=frontend-build /build/dist ./frontend/dist
COPY nginx.conf /etc/nginx/nginx.conf
COPY docker/start.sh /start.sh
RUN chmod +x /start.sh && chown -R app:app /app
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["/start.sh"]
