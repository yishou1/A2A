FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS web-build
WORKDIR /web
COPY algorithmrepo/web/package.json algorithmrepo/web/package-lock.json ./
RUN npm ci
COPY algorithmrepo/web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS algolib-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake ninja-build git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY commander/CMakeLists.txt /app/commander/CMakeLists.txt
COPY commander/include/ /app/commander/include/
COPY commander/src/ /app/commander/src/
COPY commander/demo/demo_main.cpp /app/commander/demo/demo_main.cpp
RUN cmake -S commander -B commander/build -G Ninja \
      -DALGOLIB_BUILD_TESTS=OFF -DALGOLIB_WITH_ONNXRUNTIME=OFF \
    && cmake --build commander/build -j2 \
    && test -x commander/build/algolib \
    && test -x commander/build/algolib_server

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/root/.cache/huggingface/sentence_transformers
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.docker.txt constraints.docker.txt /app/
COPY commander/requirements.txt /app/commander/requirements.txt
COPY commander/services/requirements.txt /app/commander/services/requirements.txt
RUN python -m pip install --upgrade pip==26.2.1 setuptools==84.0.0 wheel==0.48.0 \
    && python -m pip install torch==2.14.0+cpu torchvision==0.29.0+cpu \
       --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r requirements.docker.txt -c constraints.docker.txt
COPY algorithmrepo/services/synapserag/requirements.docker.txt /app/algorithmrepo/services/synapserag/requirements.docker.txt
RUN python -m pip install -r algorithmrepo/services/synapserag/requirements.docker.txt \
    && python -m pip check
COPY . /app
COPY --from=web-build /web/dist /app/algorithmrepo/web/dist
COPY --from=algolib-build /app/commander/build/algolib /app/commander/build/algolib
COPY --from=algolib-build /app/commander/build/algolib_server /app/commander/build/algolib_server
COPY --from=algolib-build /app/commander/build/CMakeCache.txt /app/commander/build/CMakeCache.txt
RUN python -m pip install --no-deps -e ./commander -e './amos-platform[dev]' \
    && python -m pip check
CMD ["bash", "scripts/start.sh", "--llm-profile", "local-qwen", "--require-llm"]
