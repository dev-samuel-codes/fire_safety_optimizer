FROM node:20-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY scripts ./scripts
COPY public ./public
COPY src ./src
RUN npm run build

# DWG→DXF 변환기(dwg2dxf/dwgread) — apt 패키지가 없어 소스에서 정적 링크로 빌드.
# 최종 이미지엔 바이너리 2개만 COPY하고 이 빌드 스테이지 자체는 버려짐(이미지 용량 영향 없음).
FROM python:3.11-slim AS libredwg-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential autoconf automake libtool pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/LibreDWG/libredwg.git /tmp/libredwg
WORKDIR /tmp/libredwg
RUN ./autogen.sh && \
    ./configure --disable-bindings --disable-docs --disable-shared --enable-static && \
    make -j$(nproc)

FROM python:3.11-slim
WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/fireopt ./fireopt
COPY backend/fireval ./fireval

COPY --from=frontend /app/dist /app/static
COPY --from=libredwg-builder /tmp/libredwg/programs/dwg2dxf /usr/local/bin/dwg2dxf
COPY --from=libredwg-builder /tmp/libredwg/programs/dwgread /usr/local/bin/dwgread

ENV HOST=0.0.0.0
ENV PORT=7860
ENV STATIC_DIR=/app/static
EXPOSE 7860

CMD ["python", "-m", "fireval.api.server"]
