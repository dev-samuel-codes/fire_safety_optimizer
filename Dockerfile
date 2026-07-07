FROM node:20-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY scripts ./scripts
COPY public ./public
COPY src ./src
RUN npm run build

FROM python:3.11-slim
WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/fireopt ./fireopt
COPY backend/fireval ./fireval

COPY --from=frontend /app/dist /app/static

ENV HOST=0.0.0.0
ENV PORT=7860
ENV STATIC_DIR=/app/static
EXPOSE 7860

CMD ["python", "-m", "fireval.api.server"]
