"""
image_gen.py — Image generation wrapper for hum content creation.

Wraps scripts/lib/image-gen/generate.py so it can be called
as a Python module without subprocess.

Provider is resolved from: explicit arg > IMAGE_MODEL env > openclaw.json > "gemini".
Visual style from VOICE.md is auto-injected when no style arg is given.

Usage:
    from create.image_gen import generate_image
    path = generate_image("split screen finance illustration", platform="twitter")
"""

import base64
import json
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path


# Path to the image-gen lib (bundled inside hum)
_IMAGE_GEN_DIR = Path(__file__).resolve().parent.parent / "lib" / "image-gen"
_IMAGE_GEN_SCRIPT = _IMAGE_GEN_DIR / "generate.py"


def _load_generate_script():
    """Load generate.py as a module."""
    if not _IMAGE_GEN_SCRIPT.exists():
        raise FileNotFoundError(
            f"image-gen lib not found at {_IMAGE_GEN_SCRIPT}. "
            "Check that scripts/lib/image-gen/ exists."
        )
    loader = SourceFileLoader("img_gen_script", str(_IMAGE_GEN_SCRIPT))
    return loader.load_module()


def _resolve_provider(provider: str | None) -> str:
    """Resolve provider from arg, config, or default."""
    if provider:
        return provider
    # Lazy import to avoid circular deps at module load
    from scripts.config import load_config
    return load_config().get("image_model", "gemini")


def _resolve_style(style: str | None) -> str | None:
    """Load visual style from VOICE.md if no explicit style given."""
    if style is not None:
        return style
    from scripts.config import load_config, load_visual_style
    cfg = load_config()
    return load_visual_style(cfg["data_dir"])


# ── Public API ───────────────────────────────────────────────────────────────


PLATFORM_SIZES = {
    "x":            (1200, 675),
    "twitter":      (1200, 675),
    "linkedin":     (1200, 627),
    "instagram":    (1080, 1080),
    "facebook":     (1200, 630),
    "1:1":          (1024, 1024),
    "16:9":         (1920, 1080),
    "9:16":         (1080, 1920),
}


def generate_image(
    prompt: str,
    *,
    provider: str | None = None,
    platform: str | None = None,
    size: tuple[int, int] | None = None,
    model: str | None = None,
    style: str | None = None,
    no_enhance: bool = False,
    output_path: str | None = None,
) -> str:
    """
    Generate an image and return the path to the saved file.

    Args:
        prompt: Image description
        provider: "gemini", "openai", "grok", "minimax" (default from config)
        platform: Target platform (auto-selects size) — use "twitter" for X posts
        size: W×H tuple, overrides platform
        model: Provider-specific model override
        style: Style directive (defaults to VOICE.md Visual Style)
        no_enhance: Skip LLM prompt enhancement
        output_path: Save to this path instead of temp file

    Returns:
        Path to the generated image file.

    Raises:
        RuntimeError: If generation fails
    """
    script = _load_generate_script()

    resolved_provider = _resolve_provider(provider)
    resolved_style = _resolve_style(style)

    # Map "x" to "twitter" for the generate.py CLI
    platform_arg = platform
    if platform_arg == "x":
        platform_arg = "twitter"

    # Resolve size
    if size is None and platform:
        size = PLATFORM_SIZES.get(platform)

    size_str = f"{size[0]}x{size[1]}" if size else None

    # Build args
    args = [
        "--provider", resolved_provider,
    ]
    if no_enhance:
        args += ["--no-enhance", "--prompt", prompt]
    else:
        args += ["--prompt", prompt]
    if platform_arg:
        args.extend(["--platform", platform_arg])
    if size_str:
        args.extend(["--size", size_str])
    if model:
        args.extend(["--model", model])
    if resolved_style:
        args.extend(["--style", resolved_style])

    if output_path is None:
        ext = "png"
        output_path = f"/tmp/hum-image-{os.getpid()}.{ext}"

    args.extend(["--output", output_path])

    # Patch sys.argv for the script's argparse
    old_argv = sys.argv
    sys.argv = ["generate.py"] + args

    try:
        script.main()
    except SystemExit as exc:
        if exc.code != 0:
            raise RuntimeError(f"Image generation failed with exit code {exc.code}")
    finally:
        sys.argv = old_argv

    if not Path(output_path).exists():
        raise RuntimeError(f"Image was not saved to {output_path}")

    return output_path


def generate_image_json(
    prompt: str,
    *,
    provider: str | None = None,
    platform: str | None = None,
    size: tuple[int, int] | None = None,
    model: str | None = None,
    style: str | None = None,
    no_enhance: bool = False,
) -> dict:
    """
    Generate an image and return full metadata as a JSON dict.

    Returns:
        {
            "success": True,
            "provider": str,
            "model": str,
            "size": (w, h),
            "prompt": original prompt,
            "enhanced_prompt": str | None,
            "revised_prompt": str | None,
            "image_b64": base64-encoded PNG,
            "image_path": temp file path,
        }
    """
    script = _load_generate_script()

    resolved_provider = _resolve_provider(provider)
    resolved_style = _resolve_style(style)

    if size is None and platform:
        size = PLATFORM_SIZES.get(platform)

    size_str = f"{size[0]}x{size[1]}" if size else None

    args = ["--provider", resolved_provider, "--json"]
    if no_enhance:
        args += ["--no-enhance", "--prompt", prompt]
    else:
        args += ["--prompt", prompt]
    if platform:
        args.extend(["--platform", platform])
    if size_str:
        args.extend(["--size", size_str])
    if model:
        args.extend(["--model", model])
    if resolved_style:
        args.extend(["--style", resolved_style])

    old_argv = sys.argv
    sys.argv = ["generate.py"] + args

    try:
        # Capture stdout
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            script.main()
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        data = json.loads(output)
        # Also write the temp file
        if data.get("success") and data.get("image_b64"):
            img_bytes = base64.b64decode(data["image_b64"])
            ext = "png"
            img_path = f"/tmp/hum-image-{os.getpid()}.{ext}"
            Path(img_path).write_bytes(img_bytes)
            data["image_path"] = img_path
        return data

    except SystemExit as exc:
        sys.stdout = old_stdout
        raise RuntimeError(f"Image generation failed with exit code {exc.code}")
    finally:
        sys.argv = old_argv
