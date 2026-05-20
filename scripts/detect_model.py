#!/usr/bin/env python3
"""Detect the best embedding model for this machine's hardware.

Standalone utility — run once at setup time. Prints the recommended model
name and writes it to .env if needed.

Strategy:
  • No GPU detected           → modernbert-embed-base  (768-dim, 512 ctx)
    Fast on CPU, smallest model, good for lightweight machines.
  • GPU with < 8 GB VRAM     → modernbert-embed-large (896-dim, 512 ctx)
    Leverages GPU for the larger model, better quality than base.
  • GPU with ≥ 8 GB VRAM     → nomic-embed-text       (768-dim, ~8k ctx)
    Enough VRAM for the long-context model with comfortable batching.

Override at any time with CODEINDEX_EMBED_MODEL.

Usage:
    python scripts/detect_model.py              # print recommended model
    python scripts/detect_model.py --write-env   # write/update .env
    python scripts/detect_model.py --json        # machine-readable output
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# VRAM threshold for nomic-embed-text (needs enough GPU memory for
# comfortable batching with long 8k-token context).
GPU_VRAM_THRESHOLD_GB = 8.0

FALLBACK_MODEL = "modernbert-embed-base"

MODEL_INFO = {
    "nomic-embed-text": {
        "dim": 768,
        "max_text_len": 32000,
        "context_tokens": "~8k",
        "best_for": "Large GPU (≥8 GB)",
        "backend": "ollama",
    },
    "modernbert-embed-large": {
        "dim": 1024,
        "max_text_len": 32768,
        "context_tokens": "8192",
        "best_for": "Small GPU (<8 GB) or CPU with enough RAM",
        "backend": "sentence_transformers",
    },
    "modernbert-embed-base": {
        "dim": 768,
        "max_text_len": 32768,
        "context_tokens": "8192",
        "best_for": "CPU-only",
        "backend": "sentence_transformers",
    },
}


def detect_gpu_vram() -> float:
    """Return max VRAM in GB across all CUDA GPUs, or 0.0 if no GPU."""
    # 1) Try torch
    try:
        import torch  # type: ignore
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            max_vram = max(
                torch.cuda.get_device_properties(i).total_memory
                for i in range(torch.cuda.device_count())
            )
            return max_vram / (1024 ** 3)
    except ImportError:
        pass

    # 2) Try nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            vrams = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    vrams.append(float(line) / 1024)  # MiB → GB
            if vrams:
                return max(vrams)
        except Exception:
            pass

    # 3) Device files — GPU exists but can't determine VRAM
    if any(Path(f"/dev/nvidia{i}").exists() for i in range(4)):
        return 8.0  # assume reasonable default

    return 0.0


def recommend_model(vram: float = None) -> tuple[str, str]:
    """Return (model_name, reason) based on detected hardware."""
    if vram is None:
        vram = detect_gpu_vram()

    if vram == 0.0:
        return "modernbert-embed-base", "No GPU detected — CPU-only machine"
    elif vram < GPU_VRAM_THRESHOLD_GB:
        return "modernbert-embed-large", \
            f"GPU detected ({vram:.1f} GB VRAM < {GPU_VRAM_THRESHOLD_GB:.0f} GB threshold)"
    else:
        return "nomic-embed-text", \
            f"GPU detected ({vram:.1f} GB VRAM ≥ {GPU_VRAM_THRESHOLD_GB:.0f} GB threshold)"


def write_env(model: str, env_path: Path) -> None:
    """Update or create .env with CODEINDEX_EMBED_MODEL."""
    lines = []
    written = False

    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("CODEINDEX_EMBED_MODEL="):
                lines.append(f"CODEINDEX_EMBED_MODEL={model}")
                written = True
            else:
                lines.append(line)

    if not written:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"CODEINDEX_EMBED_MODEL={model}")

    env_path.write_text("\n".join(lines) + "\n")
    print(f"Written CODEINDEX_EMBED_MODEL={model} to {env_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Detect best embedding model for this machine's hardware.")
    parser.add_argument("--write-env", action="store_true",
                        help="Write/update .env file with detected model")
    parser.add_argument("--env-path", type=Path, default=None,
                        help="Path to .env file (default: <script_dir>/../.env)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--override", type=str, default=None,
                        help="Override detected model with this name")
    args = parser.parse_args()

    vram = detect_gpu_vram()
    model, reason = recommend_model(vram)

    if args.override:
        model = args.override
        reason = f"Override from --override flag (detected: {reason})"

    info = MODEL_INFO.get(model, {})
    result = {
        "model": model,
        "reason": reason,
        "vram_gb": round(vram, 1),
        "dim": info.get("dim", "?"),
        "max_text_len": info.get("max_text_len", "?"),
        "context_tokens": info.get("context_tokens", "?"),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Recommended model: {model}")
        print(f"  Reason: {reason}")
        print(f"  Dimension: {result['dim']}")
        print(f"  Context: {result['context_tokens']} tokens")
        print(f"  VRAM: {result['vram_gb']} GB")
        print()
        print("Override with: CODEINDEX_EMBED_MODEL=<model>")
        print("Available models:", ", ".join(MODEL_INFO.keys()))

    if args.write_env:
        env_path = args.env_path
        if env_path is None:
            env_path = Path(__file__).resolve().parent.parent / ".env"
        write_env(model, env_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())