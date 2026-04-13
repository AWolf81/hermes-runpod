# Hermes RunPod Pod Template

This repository builds a custom container for RunPod Pods that runs
`hermes gateway` with an OpenAI-compatible API server on `:8642`.

Two image variants, one Dockerfile:

| Tag | Contents | Use case |
|-----|----------|----------|
| `:vllm` / `:latest` | hermes + vLLM | Self-contained — model runs inside the pod |
| `:base` | hermes only | Bring your own model (separate pod, llama.cpp, etc.) |

Default model (`:vllm`): `Qwen/Qwen3-14B-AWQ`

> Why this default: Qwen3-14B-AWQ is the best reasoning + tool-calling model that fits comfortably in 16GB VRAM (~8-9GB loaded). It uses vLLM's built-in `qwen3_xml` tool parser (no custom parser plugin required).
> This Docker setup loads `gpt-oss-20b` directly from Hugging Face/vLLM weights (safetensors), so GGUF is not required here.

## What this repo contains

- `Dockerfile`: single file for both variants, controlled by `INCLUDE_VLLM`
- `main.py`: startup supervisor — starts vLLM (if enabled) then hermes gateway
- `requirements-base.txt`: deps always installed (huggingface_hub, PyYAML, …)
- `requirements-vllm.txt`: vllm (only installed when `INCLUDE_VLLM=1`)
- `defaults/soul/SOUL.safe.md`: safe default persona written to `~/.hermes/SOUL.md` at startup
- `defaults/skills/core/*`: four bootstrapped core skills for basic agentic coding workflows

## Local workflow (recommended before Docker Hub)

### 1) Pre-checks (must pass)

```bash
docker --version
nvidia-smi
docker context ls
```

Pass criteria:

- `docker` command works
- `nvidia-smi` shows your GPU
- You can use a Docker context that supports NVIDIA runtime

If current context is `desktop-linux *`, switch to host daemon first:

```bash
docker context use default
sudo systemctl restart docker
```

Verify host daemon runtime support:

```bash
sudo docker info | rg -i "runtime|nvidia"
```

Container GPU smoke test:

```bash
sudo docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

### 2) Build local images

**vllm variant** (hermes + vLLM, self-contained):

```bash
docker build -t hermes-runpod:vllm --build-arg PRELOAD_MODEL=0 .
```

Include model preload (larger image, faster cold start on RunPod):

```bash
docker build -t hermes-runpod:vllm .
```

> **Initial build timing note:** the first `:vllm` build (with default `PRELOAD_MODEL=1`) downloads large model artifacts and can take a long time. During `exporting layers`, Docker may run for several minutes (sometimes much longer) while assembling large layers. The model is downloaded directly to its target path (no HuggingFace blob cache is retained in the image), so disk usage matches the model size only — not double.

**base variant** (hermes only, no vLLM):

```bash
docker build --build-arg INCLUDE_VLLM=0 -t hermes-runpod:base .
```

### 3) Run container locally

**vllm variant:**

```bash
docker run --rm --name hermes-local \
  --gpus all \
  -e HERMES_API_KEY=local-dev-key \
  -e VLLM_HOST=0.0.0.0 \
  -e VLLM_ENABLE_AUTO_TOOL_CHOICE=1 \
  -e VLLM_TOOL_CALL_PARSER=qwen3_xml \
  -e VLLM_DTYPE=auto \
  -e VLLM_GPU_MEMORY_UTILIZATION=0.93 \
  -e VLLM_MAX_MODEL_LEN=16384 \
  -p 8642:8642 \
  -p 8001:8000 \
  -p 8888:8888 \
  -v "/your-example-project:/workspace/project" \
  -w /workspace/project \
  hermes-runpod:vllm
```

> **Note:** Default mapping uses host port 8001 (`-p 8001:8000`) because port 8000 is often already in use.
> Check with: `sudo ss -tlnp | grep 8000`.

> **Working directory note:** for repo tasks in OpenCode (for example “Summarize this repo”), mount the repo with `-v /your-example-project:/workspace/project` and set `-w /workspace/project`; otherwise Hermes may report missing or empty files. For quick local tests, `-v "${PWD}:/workspace/project"` is also common.

**base variant** (point at an external OpenAI-compatible endpoint):

```bash
docker run --rm --name hermes-local \
  -e HERMES_API_KEY=local-dev-key \
  -e ENABLE_VLLM=0 \
  -e MODEL_BASE_URL=http://my-vllm-host:8000/v1 \
  -e SERVED_MODEL_NAME=my-model \
  -p 8642:8642 \
  hermes-runpod:base
```

If you need host daemon permissions, prefix with `sudo docker ...`.


### 3b) Workspace / project access

Hermes starts with `WORKSPACE_PATH` as its working directory (default: `/workspace/project`).

**Option A — Git clone on startup** (works on RunPod, no network volume needed):

```bash
docker run --rm --env-file .env \
  -e GIT_REPO_URL=https://github.com/you/your-repo \
  -e GIT_REPO_REF=main \
  -p 8642:8642 \
  hermes-runpod:base
```

Hermes clones the repo into `WORKSPACE_PATH` on boot. Ephemeral — clone repeats each pod start.

**Option B — RunPod Network Volume** (persistent, survives pod restarts):

1. Create a Network Volume in RunPod Console → Storage → Network Volumes
2. Attach it at pod creation — mounts to `/runpod-volume`
3. SSH into the pod and clone once: `git clone https://github.com/you/your-repo /runpod-volume/project`
4. Set env var: `WORKSPACE_PATH=/runpod-volume/project`

**Option C — Local volume mount** (local Docker, `-v` flag required — cannot go in `.env`):

```bash
docker run --rm --env-file .env \
  -v /your/local/project:/workspace/project \
  -p 8642:8642 \
  hermes-runpod:base
```

### 3c) Safe Defaults (SOUL + Skills)

This template bootstraps a safe default `SOUL.md` plus a minimal core skill set on startup.

Defaults:

- `HERMES_BOOTSTRAP_DEFAULTS=1` (master switch)
- `HERMES_BOOTSTRAP_SOUL=1`
- `HERMES_SOUL_TEMPLATE_PATH=/app/defaults/soul/SOUL.safe.md`
- `HERMES_SOUL_FORCE=0`
- `HERMES_BOOTSTRAP_SKILLS=1`
- `HERMES_SKILLS_TEMPLATE_DIR=/app/defaults/skills`
- `HERMES_SKILLS_FORCE=0`

Bootstrapped core skills (minimum set):

- `core/repo-summary`
- `core/implement-change`
- `core/debug-fix`
- `core/release-commit`

> If you modify files under `defaults/skills/`, rebuild the image before testing (`docker build ...`), otherwise the running container will still use the previous baked-in skill set.

Override behavior:

- Set `HERMES_BOOTSTRAP_DEFAULTS=0` to disable all default soul/skill bootstrapping.
- Mount your own `~/.hermes/SOUL.md` and keep `HERMES_SOUL_FORCE=0`.
- Set `HERMES_BOOTSTRAP_SOUL=0` to disable only soul bootstrap.
- Set `HERMES_BOOTSTRAP_SKILLS=0` to disable only skills bootstrap.
- Set `HERMES_SOUL_TEMPLATE_PATH=/path/to/custom.md` to use a custom soul template.
- Set `HERMES_SKILLS_TEMPLATE_DIR=/path/to/skills` to use a custom skills directory.
- Set `HERMES_SOUL_FORCE=1` or `HERMES_SKILLS_FORCE=1` to overwrite existing defaults.

### 4) Test local runtime

In another terminal:

```bash
docker logs -f hermes-local
```

Expected logs (vllm variant):

- `vLLM is ready`
- Hermes API server listening on `:8642`

API checks from host:

```bash
curl -s http://127.0.0.1:8642/health
curl -s http://127.0.0.1:8001/v1/models   # vllm variant only
```

Chat completion test through Hermes API:

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}]}'
```

### 5) Stop local test

```bash
docker stop hermes-local
```

## Local troubleshooting (GPU runtime)

If tool calls appear in plain text, your parser likely does not match the model's tool format.

- For `Qwen/Qwen3-14B-AWQ` (template default), use `VLLM_TOOL_CALL_PARSER=qwen3_xml`.
- For Qwen2.5 or Hermes-style models, use `VLLM_TOOL_CALL_PARSER=hermes`.
- For LFM2.5 models, use `VLLM_TOOL_CALL_PARSER=lfm2` and `VLLM_TOOL_PARSER_PLUGIN=/app/vllm_lfm2_tool_parser.py`.
- For gpt-oss models, use `VLLM_TOOL_CALL_PARSER=hermes`.

If repo summaries look generic or fabricated (for example high-level text without concrete file references), treat it as an access/tooling failure:

- Ensure your project is mounted: `-v /your-example-project:/workspace/project`
- Ensure working directory is set: `-w /workspace/project`
- Rebuild and restart after changing `defaults/soul` or `defaults/skills`
- If you persist `~/.hermes`, use `-e HERMES_SOUL_FORCE=1 -e HERMES_SKILLS_FORCE=1` once to refresh defaults
- Verify defaults are present in the running container:
  - `docker exec hermes-local ls -la /workspace/.hermes/skills/core`
  - `docker exec hermes-local sed -n '1,120p' /workspace/.hermes/SOUL.md`
- For tiny models, reduce randomness in the client (`temperature` near `0`) to reduce invented skill/tool names.


If `docker run --gpus all ...` fails with
`failed to discover GPU vendor from CDI: no known GPU vendor found`:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
sudo systemctl restart nvidia-cdi-refresh.service
sudo nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
nvidia-ctk --debug cdi list
```

Notes:

- `nvidia-ctk cdi generate` may print many warnings on desktop systems.
- If it ends with `Generated CDI spec with version ...`, generation succeeded.

If you see CDI conflicts referencing `nvidia.yam.yaml`:

```bash
sudo rm -f /var/run/cdi/nvidia.yam /var/run/cdi/nvidia.yam.yaml
sudo nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
nvidia-ctk --debug cdi list
```

Fallback test using explicit NVIDIA runtime:

```bash
docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

## Push to Docker Hub

Replace `YOUR_DOCKER_USERNAME`. Run after local testing passes.

```bash
docker login

# vllm variant (also tag as :latest)
docker build -t YOUR_DOCKER_USERNAME/hermes-runpod:vllm \
             -t YOUR_DOCKER_USERNAME/hermes-runpod:latest .

# base variant
docker build --build-arg INCLUDE_VLLM=0 \
             -t YOUR_DOCKER_USERNAME/hermes-runpod:base .

docker push YOUR_DOCKER_USERNAME/hermes-runpod:vllm
docker push YOUR_DOCKER_USERNAME/hermes-runpod:latest
docker push YOUR_DOCKER_USERNAME/hermes-runpod:base
```

To pin a versioned release (recommended for RunPod templates):

```bash
VERSION=v2026.4.8   # match HERMES_VERSION in Dockerfile

docker tag YOUR_DOCKER_USERNAME/hermes-runpod:vllm YOUR_DOCKER_USERNAME/hermes-runpod:${VERSION}-vllm
docker tag YOUR_DOCKER_USERNAME/hermes-runpod:base YOUR_DOCKER_USERNAME/hermes-runpod:${VERSION}-base

docker push YOUR_DOCKER_USERNAME/hermes-runpod:${VERSION}-vllm
docker push YOUR_DOCKER_USERNAME/hermes-runpod:${VERSION}-base
```

## Create RunPod Pod template

In RunPod Console → Pods → Templates → New Template:

**vllm variant:**

- Container image: `YOUR_DOCKER_USERNAME/hermes-runpod:vllm`
- Exposed HTTP ports: `8642,8888` (optional: `8000`)
- Exposed TCP ports: `22`
- Recommended env vars:
  - `HERMES_API_KEY=<strong-random-value>`
  - `MODEL_REPO=Qwen/Qwen3-14B-AWQ`
  - `SERVED_MODEL_NAME=qwen3-14b`
  - `VLLM_MAX_MODEL_LEN=16384`
  - `VLLM_GPU_MEMORY_UTILIZATION=0.93`
  - `VLLM_ENABLE_AUTO_TOOL_CHOICE=1`
  - `VLLM_TOOL_CALL_PARSER=qwen3_xml` (built-in parser for Qwen3 models)
  - `VLLM_DTYPE=auto`
  - For LFM2.5 models, set `VLLM_TOOL_CALL_PARSER=lfm2` and `VLLM_TOOL_PARSER_PLUGIN=/app/vllm_lfm2_tool_parser.py`
  - For Hermes/Qwen-style models, set `VLLM_TOOL_CALL_PARSER=hermes`
  - Optional: `VLLM_CHAT_TEMPLATE=tool_use` (if your model tokenizer provides a tool-use template)
  - Optional: `HERMES_BOOTSTRAP_DEFAULTS=1`, `HERMES_BOOTSTRAP_SOUL=1`, `HERMES_BOOTSTRAP_SKILLS=1`

**base variant:**

- Container image: `YOUR_DOCKER_USERNAME/hermes-runpod:base`
- Exposed HTTP ports: `8642,8888`
- Exposed TCP ports: `22`
- Required env vars:
  - `HERMES_API_KEY=<strong-random-value>`
  - `ENABLE_VLLM=0`
  - `MODEL_BASE_URL=http://<your-model-endpoint>/v1`
  - `SERVED_MODEL_NAME=<model-name>`

**Override vllm image to use external model at runtime:**

Set `ENABLE_VLLM=0` and `MODEL_BASE_URL=...` on any pod using the `:vllm` image —
no need to pull a different image.

## Verify on RunPod Pod

From Pod shell:

```bash
curl -s http://127.0.0.1:8642/health
```

Chat test on Pod:

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}]}'
```

## Connect OpenCode to Hermes on RunPod

Use Pod public URL for port `8642` in OpenCode config:

`~/.config/opencode/opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "hermes-runpod": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Hermes on RunPod",
      "options": {
        "baseURL": "https://YOUR-POD-8642-URL/v1",
        "apiKey": "{env:HERMES_API_KEY}"
      },
      "models": {
        "hermes-agent": {
          "id": "hermes-agent",
          "name": "Hermes Agent (RunPod)",
          "limit": { "context": 32768, "output": 4096 }
        }
      }
    }
  }
}
```

Local env:

```bash
export HERMES_API_KEY="same-value-as-runpod-template"
opencode models
```

Expected model entry: `hermes-runpod/hermes-agent`

## Publish on RunPod Hub later

Pod templates and Hub listings are different workflows.

Hub expects serverless repo structure, including:

- `.runpod/hub.json`
- `.runpod/tests.json`
- `handler.py`, `Dockerfile`, `README.md`
- GitHub release tags (Hub indexes releases)

To allow deploy as Serverless or Pod from Hub listing:

```json
{
  "type": "serverless",
  "config": {
    "endpointType": "LB"
  }
}
```

## Sources

- RunPod custom Pod template guide: https://docs.runpod.io/pods/templates/create-custom-template
- Hermes Docker guide: https://hermes-agent.nousresearch.com/docs/user-guide/docker
- Hermes API server: https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/
- Hermes provider custom endpoint config: https://hermes-agent.nousresearch.com/docs/integrations/providers/
- RunPod OpenCode setup pattern: https://docs.runpod.io/public-endpoints/ai-coding-tools
- RunPod Hub publishing guide: https://docs.runpod.io/hub/publishing-guide

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
