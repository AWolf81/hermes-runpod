# Hermes RunPod template
# Build variants:
#   docker build .                              → :vllm (hermes + vllm, self-contained)
#   docker build --build-arg INCLUDE_VLLM=0 .  → :base (hermes only, external model)
#
# Runtime override (vllm image only):
#   ENABLE_VLLM=0  → skip starting vllm, point hermes at an external MODEL_BASE_URL
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1

WORKDIR /app

RUN apt-get update --yes && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        curl \
        ca-certificates \
        tini \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install hermes-agent from the latest release tag
ARG HERMES_VERSION=v2026.4.8
RUN git clone --depth=1 --branch "${HERMES_VERSION}" \
        https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent && \
    python -m venv /opt/hermes-agent/venv && \
    /opt/hermes-agent/venv/bin/pip install --no-cache-dir -e "/opt/hermes-agent[all]"
ENV PATH="/opt/hermes-agent/venv/bin:$PATH"

# Base deps (always installed)
COPY requirements-base.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements-base.txt

# vllm (only installed when INCLUDE_VLLM=1)
ARG INCLUDE_VLLM=1
COPY requirements-vllm.txt /app/
RUN if [ "$INCLUDE_VLLM" = "1" ]; then \
        pip install --no-cache-dir -r /app/requirements-vllm.txt ; \
    else \
        echo "Skipping vllm install (INCLUDE_VLLM=0)"; \
    fi

ENV ENABLE_VLLM=${INCLUDE_VLLM} \
    MODEL_BASE_URL="" \
    MODEL_REPO=Qwen/Qwen3-14B-AWQ \
    MODEL_PATH=/opt/models/Qwen3-14B-AWQ \
    SERVED_MODEL_NAME=qwen3-14b \
    VLLM_HOST=127.0.0.1 \
    VLLM_PORT=8000 \
    VLLM_TOOL_CALL_PARSER=qwen3_xml \
    VLLM_DTYPE=auto \
    VLLM_TOOL_PARSER_PLUGIN= \
    HERMES_BOOTSTRAP_DEFAULTS=1 \
    HERMES_BOOTSTRAP_SOUL=1 \
    HERMES_SOUL_TEMPLATE_PATH=/app/defaults/soul/SOUL.safe.md \
    HERMES_SOUL_FORCE=0 \
    HERMES_BOOTSTRAP_SKILLS=1 \
    HERMES_SKILLS_TEMPLATE_DIR=/app/defaults/skills \
    HERMES_SKILLS_FORCE=0 \
    HERMES_HOME=/workspace/.hermes \
    HERMES_API_HOST=0.0.0.0 \
    HERMES_API_PORT=8642

# Optional model preloading during image build (vllm variant only).
# Set PRELOAD_MODEL=0 at build time to skip.
ARG PRELOAD_MODEL=1
ARG PRELOAD_MODEL_REPO=Qwen/Qwen3-14B-AWQ
ARG PRELOAD_MODEL_DIR=/opt/models/Qwen3-14B-AWQ
RUN if [ "$INCLUDE_VLLM" = "1" ] && [ "$PRELOAD_MODEL" = "1" ]; then \
        mkdir -p "${PRELOAD_MODEL_DIR}" && \
        HF_HOME=/tmp/hf-cache python -c "\
from huggingface_hub import snapshot_download; \
print('Preloading model ${PRELOAD_MODEL_REPO} into ${PRELOAD_MODEL_DIR}'); \
snapshot_download('${PRELOAD_MODEL_REPO}', local_dir='${PRELOAD_MODEL_DIR}'); \
print('Model preload complete')"; \
        rm -rf /tmp/hf-cache; \
    else \
        echo "Skipping model preload"; \
    fi

COPY main.py /app/main.py
COPY vllm_lfm2_tool_parser.py /app/vllm_lfm2_tool_parser.py
COPY defaults/soul/SOUL.safe.md /app/defaults/soul/SOUL.safe.md
COPY defaults/skills /app/defaults/skills

# 8888: JupyterLab (RunPod base services)
# 8000: local model server (vLLM OpenAI-compatible endpoint)
# 8642: Hermes API server (OpenAI-compatible endpoint)
EXPOSE 8888 8000 8642

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/main.py"]
