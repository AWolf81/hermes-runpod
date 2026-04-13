import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import yaml

PROCESSES: list[tuple[str, subprocess.Popen]] = []
REQUIRED_PROCESS_NAMES: set[str] = set()
SHUTTING_DOWN = False


def log(message: str) -> None:
    print(f"[startup] {message}", flush=True)


def parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    values = parse_env_file(path)
    values.update(updates)

    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_config(path: Path, base_url: str, model_name: str, api_key: str = "local-vllm") -> None:
    config: dict = {}
    if path.exists():
        current = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(current, dict):
            config = current

    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}

    model_config.update(
        {
            "provider": "custom",
            "default": model_name,
            "base_url": base_url,
            "api_key": api_key,
        }
    )

    config["model"] = model_config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _inject_token(url: str, token: str) -> str:
    """Return url with token injected as https://<token>@host/path."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    # Strip any existing credentials so we don't double-embed
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{token}@{rest}"


def setup_workspace(
    workspace_path: str, git_repo_url: str, git_repo_ref: str, git_token: str
) -> None:
    target = Path(workspace_path)

    if target.exists() and any(target.iterdir()):
        log(f"workspace already populated: {target}")
        return

    if git_repo_url:
        clone_url = _inject_token(git_repo_url, git_token) if git_token else git_repo_url
        log(f"cloning {git_repo_url} into {target}")  # log original URL, never the token
        target.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1"]
        if git_repo_ref:
            cmd.extend(["--branch", git_repo_ref])
        cmd.extend([clone_url, str(target)])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Scrub token from error output before logging
            err = result.stderr.strip()
            if git_token:
                err = err.replace(git_token, "***")
            log(f"git clone failed: {err}")
        else:
            log(f"cloned repo into {target}")
    else:
        target.mkdir(parents=True, exist_ok=True)
        log(f"workspace path set to {target} (no git repo configured)")


def ensure_soul_file(destination: Path, template: Path, force: bool) -> None:
    if destination.exists() and not force:
        log(f"keeping existing SOUL.md: {destination}")
        return

    if not template.exists():
        log(f"SOUL template not found: {template}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    log(f"wrote SOUL.md from template: {template}")


def ensure_skills_dir(destination_root: Path, template_root: Path, force: bool) -> None:
    if not template_root.exists():
        log(f"skills template directory not found: {template_root}")
        return

    destination_root.mkdir(parents=True, exist_ok=True)

    skill_sources = sorted(path.parent for path in template_root.rglob("SKILL.md"))
    if not skill_sources:
        log(f"no default skills found in: {template_root}")
        return

    for skill_src in skill_sources:
        relative = skill_src.relative_to(template_root)
        skill_dst = destination_root / relative

        if skill_dst.exists():
            if force:
                shutil.rmtree(skill_dst)
            else:
                log(f"keeping existing skill: {skill_dst}")
                continue

        shutil.copytree(skill_src, skill_dst)
        log(f"installed default skill: {relative.as_posix()}")


def start_process(
    name: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    required: bool = True,
    cwd: str | None = None,
) -> subprocess.Popen:
    log(f"starting {name}: {' '.join(cmd)}")
    process = subprocess.Popen(cmd, env=env, cwd=cwd)
    PROCESSES.append((name, process))
    if required:
        REQUIRED_PROCESS_NAMES.add(name)
    return process


def wait_for_http(url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=5):
                return
        except URLError:
            time.sleep(2)
    raise TimeoutError(f"timeout waiting for {url}")


def terminate_processes() -> None:
    global SHUTTING_DOWN
    if SHUTTING_DOWN:
        return
    SHUTTING_DOWN = True

    for name, process in reversed(PROCESSES):
        if process.poll() is None:
            log(f"stopping {name} (pid={process.pid})")
            process.terminate()

    grace_deadline = time.time() + 15
    for _, process in reversed(PROCESSES):
        while process.poll() is None and time.time() < grace_deadline:
            time.sleep(0.2)

    for name, process in reversed(PROCESSES):
        if process.poll() is None:
            log(f"force killing {name} (pid={process.pid})")
            process.kill()


def signal_handler(signum: int, _frame) -> None:
    log(f"received signal {signum}")
    terminate_processes()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    hermes_home = Path(os.environ.get("HERMES_HOME", "/workspace/.hermes"))
    hermes_profile_home = hermes_home.parent

    enable_vllm = os.environ.get("ENABLE_VLLM", "1").strip() == "1"
    model_path = os.environ.get("MODEL_PATH", "/opt/models/gpt-oss-20b")
    model_repo = os.environ.get("MODEL_REPO", "openai/gpt-oss-20b")
    served_model_name = os.environ.get("SERVED_MODEL_NAME", "gpt-oss-20b")

    vllm_host = os.environ.get("VLLM_HOST", "127.0.0.1")
    vllm_port = int(os.environ.get("VLLM_PORT", "8000"))
    hermes_api_host = os.environ.get("HERMES_API_HOST", "0.0.0.0")
    hermes_api_port = int(os.environ.get("HERMES_API_PORT", "8642"))
    hermes_api_key = os.environ.get("HERMES_API_KEY", "change-me-runpod")
    startup_timeout = int(os.environ.get("MODEL_STARTUP_TIMEOUT", "900"))

    workspace_path = os.environ.get("WORKSPACE_PATH", "/workspace/project").strip()
    git_repo_url = os.environ.get("GIT_REPO_URL", "").strip()
    git_repo_ref = os.environ.get("GIT_REPO_REF", "").strip()
    git_token = os.environ.get("GIT_TOKEN", "").strip()

    hermes_profile_home.mkdir(parents=True, exist_ok=True)
    hermes_home.mkdir(parents=True, exist_ok=True)

    setup_workspace(workspace_path, git_repo_url, git_repo_ref, git_token)

    gateway_allow_all = os.environ.get("GATEWAY_ALLOW_ALL_USERS", "true").strip()

    hermes_env_updates = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": hermes_api_host,
        "API_SERVER_PORT": str(hermes_api_port),
        "API_SERVER_KEY": hermes_api_key,
        "API_SERVER_MODEL_NAME": "hermes-agent",
        "GATEWAY_ALLOW_ALL_USERS": gateway_allow_all,
    }
    write_env_file(hermes_home / ".env", hermes_env_updates)

    model_provider = os.environ.get("MODEL_PROVIDER", "local").strip().lower()

    # Remote providers don't need a local model server
    if model_provider in ("openrouter", "opencode"):
        enable_vllm = False

    if enable_vllm:
        model_base_url = f"http://127.0.0.1:{vllm_port}/v1"
        model_api_key = "local-vllm"
    elif model_provider == "openrouter":
        openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not openrouter_api_key:
            raise ValueError("MODEL_PROVIDER=openrouter requires OPENROUTER_API_KEY to be set")
        openrouter_model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o").strip()
        model_base_url = "https://openrouter.ai/api/v1"
        served_model_name = openrouter_model
        model_api_key = openrouter_api_key
        log(f"using OpenRouter with model: {openrouter_model}")
    elif model_provider == "opencode":
        opencode_api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
        if not opencode_api_key:
            raise ValueError("MODEL_PROVIDER=opencode requires OPENCODE_API_KEY to be set")
        opencode_model = os.environ.get("OPENCODE_MODEL", "claude-sonnet-4").strip()
        model_base_url = "https://opencode.ai/zen/v1"
        served_model_name = opencode_model
        model_api_key = opencode_api_key
        log(f"using OpenCode Zen with model: {opencode_model}")
    else:
        model_base_url = os.environ.get("MODEL_BASE_URL", "").strip()
        model_api_key = "local-vllm"
        if not model_base_url:
            raise ValueError(
                "ENABLE_VLLM=0 requires MODEL_BASE_URL to be set "
                "(e.g. http://my-vllm-pod:8000/v1)"
            )
        log(f"vLLM disabled, pointing hermes at external model: {model_base_url}")

    write_config(hermes_home / "config.yaml", model_base_url, served_model_name, model_api_key)

    bootstrap_defaults_enabled = (
        os.environ.get("HERMES_BOOTSTRAP_DEFAULTS", "1").strip() == "1"
    )

    soul_bootstrap_enabled = os.environ.get("HERMES_BOOTSTRAP_SOUL", "1").strip() == "1"
    soul_force = os.environ.get("HERMES_SOUL_FORCE", "0").strip() == "1"
    soul_template_path = Path(
        os.environ.get("HERMES_SOUL_TEMPLATE_PATH", "/app/defaults/soul/SOUL.safe.md")
    )

    skills_bootstrap_enabled = (
        os.environ.get("HERMES_BOOTSTRAP_SKILLS", "1").strip() == "1"
    )
    skills_force = os.environ.get("HERMES_SKILLS_FORCE", "0").strip() == "1"
    skills_template_dir = Path(
        os.environ.get("HERMES_SKILLS_TEMPLATE_DIR", "/app/defaults/skills")
    )

    if not bootstrap_defaults_enabled:
        log("default bootstrap disabled (HERMES_BOOTSTRAP_DEFAULTS=0)")
    else:
        if soul_bootstrap_enabled:
            ensure_soul_file(
                destination=hermes_home / "SOUL.md",
                template=soul_template_path,
                force=soul_force,
            )
        else:
            log("SOUL bootstrap disabled (HERMES_BOOTSTRAP_SOUL=0)")

        if skills_bootstrap_enabled:
            ensure_skills_dir(
                destination_root=hermes_home / "skills",
                template_root=skills_template_dir,
                force=skills_force,
            )
        else:
            log("skills bootstrap disabled (HERMES_BOOTSTRAP_SKILLS=0)")

    if Path("/start.sh").exists():
        start_process("runpod base services", ["bash", "/start.sh"], required=False)

    if enable_vllm:
        model_target = model_path if Path(model_path).exists() else model_repo

        vllm_enable_auto_tool_choice = (
            os.environ.get("VLLM_ENABLE_AUTO_TOOL_CHOICE", "1").strip() == "1"
        )
        parser_default_hint = f"{model_target} {model_repo}".lower()
        if "qwen3" in parser_default_hint:
            default_tool_call_parser = "qwen3_xml"
            default_dtype = "auto"
        elif "gpt-oss" in parser_default_hint or "openai/" in parser_default_hint:
            default_tool_call_parser = "hermes"
            default_dtype = "bfloat16"  # gpt-oss uses mxfp4 quant, requires bfloat16
        elif "lfm2.5" in parser_default_hint or "liquidai/" in parser_default_hint:
            default_tool_call_parser = "lfm2"
            default_dtype = "float16"
        else:
            default_tool_call_parser = "hermes"
            default_dtype = "auto"
        default_tool_parser_plugin = (
            "/app/vllm_lfm2_tool_parser.py"
            if default_tool_call_parser == "lfm2"
            else ""
        )

        vllm_tool_call_parser = os.environ.get(
            "VLLM_TOOL_CALL_PARSER", default_tool_call_parser
        ).strip()
        vllm_tool_parser_plugin = os.environ.get(
            "VLLM_TOOL_PARSER_PLUGIN", default_tool_parser_plugin
        ).strip()
        vllm_chat_template = os.environ.get("VLLM_CHAT_TEMPLATE", "").strip()

        vllm_command = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--host",
            vllm_host,
            "--port",
            str(vllm_port),
            "--model",
            model_target,
            "--served-model-name",
            served_model_name,
            "--trust-remote-code",
            "--gpu-memory-utilization",
            os.environ.get("VLLM_GPU_MEMORY_UTILIZATION", "0.93"),
            "--max-model-len",
            os.environ.get("VLLM_MAX_MODEL_LEN", "16384"),
            "--max-num-seqs",
            os.environ.get("VLLM_MAX_NUM_SEQS", "64"),
        ]

        vllm_dtype = os.environ.get("VLLM_DTYPE", default_dtype).strip()
        if vllm_dtype:
            vllm_command.extend(["--dtype", vllm_dtype])

        # Qwen3 models default to thinking mode which causes reasoning loops in
        # agentic tool-call workflows. Disable it via chat template kwargs so
        # the tokenizer applies enable_thinking=False when rendering the prompt.
        # Can be re-enabled per-request via extra_body: {"chat_template_kwargs": {"enable_thinking": true}}
        if "qwen3" in parser_default_hint:
            chat_template_kwargs = os.environ.get(
                "VLLM_CHAT_TEMPLATE_KWARGS",
                '{"enable_thinking": false}',
            ).strip()
            if chat_template_kwargs:
                vllm_command.extend(
                    ["--default-chat-template-kwargs", chat_template_kwargs]
                )

        if vllm_enable_auto_tool_choice:
            if not vllm_tool_call_parser:
                raise ValueError(
                    "VLLM_ENABLE_AUTO_TOOL_CHOICE=1 requires VLLM_TOOL_CALL_PARSER"
                )

            if vllm_tool_parser_plugin:
                plugin_path = Path(vllm_tool_parser_plugin)
                if not plugin_path.exists():
                    raise ValueError(
                        "VLLM_TOOL_PARSER_PLUGIN does not exist: "
                        f"{vllm_tool_parser_plugin}"
                    )
                vllm_command.extend(["--tool-parser-plugin", vllm_tool_parser_plugin])
            elif vllm_tool_call_parser == "lfm2":
                raise ValueError(
                    "VLLM_TOOL_CALL_PARSER=lfm2 requires VLLM_TOOL_PARSER_PLUGIN "
                    "to point to the custom parser file"
                )

            vllm_command.append("--enable-auto-tool-choice")
            vllm_command.extend(["--tool-call-parser", vllm_tool_call_parser])

            if vllm_chat_template:
                vllm_command.extend(["--chat-template", vllm_chat_template])

            log(
                "vLLM auto tool choice enabled with parser: "
                f"{vllm_tool_call_parser}"
            )
            if vllm_tool_parser_plugin:
                log(f"vLLM custom parser plugin: {vllm_tool_parser_plugin}")

        vllm_env = os.environ.copy()
        if Path(model_target).exists():
            # Model is local — prevent vLLM from attempting HuggingFace Hub
            # lookups with the local path as a repo ID (noisy retrying errors).
            vllm_env["HF_HUB_OFFLINE"] = "1"
        # Reduce fragmentation so reserved-but-unallocated VRAM is usable
        # during sampler warmup on cards with tight memory budgets.
        vllm_env.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

        start_process("local model server (vLLM)", vllm_command, env=vllm_env)
        wait_for_http(f"http://127.0.0.1:{vllm_port}/v1/models", timeout_seconds=startup_timeout)
        log("vLLM is ready")

    hermes_env = os.environ.copy()
    hermes_env["HOME"] = str(hermes_profile_home)
    hermes_env["HERMES_HOME"] = str(hermes_home)

    log(f"hermes working directory: {workspace_path}")
    start_process("hermes gateway", ["hermes", "gateway"], env=hermes_env, cwd=workspace_path)

    while True:
        for name, process in PROCESSES:
            exit_code = process.poll()
            if exit_code is not None:
                if name not in REQUIRED_PROCESS_NAMES:
                    continue
                if SHUTTING_DOWN:
                    return
                log(f"{name} exited with code {exit_code}")
                terminate_processes()
                sys.exit(exit_code)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log(f"fatal startup error: {error}")
        terminate_processes()
        raise
