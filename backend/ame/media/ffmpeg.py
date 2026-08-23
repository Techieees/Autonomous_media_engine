from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ame.media.errors import RetryableMediaError
from ame.media.template import hex_to_lavfi, wrap_lines

FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "ARIALBD.TTF",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "ARIAL.TTF",
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
)


def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def require_ffmpeg() -> str:
    binary = find_ffmpeg()
    if not binary:
        raise RetryableMediaError(
            "ffmpeg is not installed or not on PATH. Install ffmpeg and retry (retryable)."
        )
    return binary


def resolve_font() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()
    raise RetryableMediaError(
        "no usable font found (DejaVuSans-Bold or Arial). Install fonts-dejavu-core or Arial "
        "and retry (retryable)."
    )


def sanitize_user_text(value: str) -> str:
    cleaned: list[str] = []
    for char in value or "":
        code = ord(char)
        if char in {"\n", "\t"} or (code >= 32 and char != "\x7f"):
            cleaned.append(char)
    return "".join(cleaned).replace("\r", "")


def escape_drawtext_value(value: str) -> str:
    """Escape text for an inline drawtext=text= argument (colons, quotes, percent)."""
    return (
        sanitize_user_text(value)
        .replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


def escape_filter_path(path: str | Path) -> str:
    text = Path(path).resolve().as_posix()
    return text.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _drawtext(
    *,
    font: Path,
    text_path: Path,
    fontsize: int,
    fontcolor: str,
    x: str,
    y: str,
    start_s: float,
    end_s: float,
    line_spacing: int = 16,
    box: bool = True,
) -> str:
    parts = [
        f"fontfile='{escape_filter_path(font)}'",
        f"textfile='{escape_filter_path(text_path)}'",
        "expansion=none",
        f"fontsize={int(fontsize)}",
        f"fontcolor={hex_to_lavfi(fontcolor)}",
        f"x={x}",
        f"y={y}",
        f"line_spacing={int(line_spacing)}",
        f"enable='between(t,{start_s:.3f},{end_s:.3f})'",
    ]
    if box:
        parts.append("box=1")
        parts.append("boxcolor=black@0.45")
        parts.append("boxborderw=28")
    return "drawtext=" + ":".join(parts)


@dataclass
class RenderPlan:
    filtergraph: str
    duration_s: float
    width: int
    height: int
    fps: int
    background: str
    work_files: dict[str, str] = field(default_factory=dict)


def build_vertical_filtergraph(
    *,
    scenes: list[dict],
    cues: list[dict],
    font: Path,
    work_dir: Path,
    duration_s: float,
    width: int,
    height: int,
    fps: int,
    background: str,
    subtitle_color: str,
    subtitle_size: int,
    accent: str,
    margins: dict[str, int],
) -> RenderPlan:
    work_dir.mkdir(parents=True, exist_ok=True)
    filters: list[str] = [f"fps={int(fps)}", "format=yuv420p", "setsar=1"]
    top = int(margins.get("top", 180))
    files: dict[str, str] = {}
    filters.append(
        "drawbox="
        + ":".join(
            [
                "x=0",
                "y=0",
                f"w={int(width)}",
                "h=10",
                f"color={hex_to_lavfi(accent)}",
                "t=fill",
            ]
        )
    )

    for index, scene in enumerate(scenes):
        text = wrap_lines(sanitize_user_text(str(scene.get("on_screen_text") or "")), width=18)
        text_path = work_dir / f"scene_{index:02d}.txt"
        text_path.write_text(text, encoding="utf-8")
        files[f"scene_{index:02d}"] = str(text_path)
        typography = scene.get("typography") if isinstance(scene.get("typography"), dict) else {}
        filters.append(
            _drawtext(
                font=font,
                text_path=text_path,
                fontsize=int(typography.get("font_size") or 64),
                fontcolor=str(typography.get("font_color") or "#F4F6F8"),
                x="(w-text_w)/2",
                y=str(top + 80),
                start_s=float(scene.get("start_s") or 0),
                end_s=float(scene.get("end_s") or duration_s),
                line_spacing=int(typography.get("line_spacing") or 16),
                box=bool(typography.get("box", True)),
            )
        )

    bottom = int(margins.get("bottom", 280))
    for index, cue in enumerate(cues):
        text = wrap_lines(sanitize_user_text(str(cue.get("text") or "")), width=28, max_lines=3)
        text_path = work_dir / f"cue_{index:03d}.txt"
        text_path.write_text(text, encoding="utf-8")
        files[f"cue_{index:03d}"] = str(text_path)
        filters.append(
            _drawtext(
                font=font,
                text_path=text_path,
                fontsize=int(subtitle_size),
                fontcolor=subtitle_color,
                x="(w-text_w)/2",
                y=f"h-text_h-{bottom}",
                start_s=float(cue.get("start_s") or 0),
                end_s=float(cue.get("end_s") or duration_s),
                line_spacing=10,
                box=True,
            )
        )

    video_chain = "[0:v]" + ",".join(filters) + "[v]"
    audio_chain = (
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{duration_s:.3f},apad=whole_dur={duration_s:.3f},"
        "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )
    graph = f"{video_chain};{audio_chain}"
    return RenderPlan(
        filtergraph=graph,
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        background=background,
        work_files=files,
    )


def _color_source(plan: RenderPlan) -> str:
    return (
        f"color=c={hex_to_lavfi(plan.background)}:s={plan.width}x{plan.height}"
        f":d={plan.duration_s:.3f}:r={plan.fps}"
    )


def _encode_args(plan: RenderPlan, output_path: Path) -> list[str]:
    return [
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-r",
        str(plan.fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        f"{plan.duration_s:.3f}",
        str(output_path),
    ]


def build_render_args(
    plan: RenderPlan,
    *,
    voiceover_path: Path,
    output_path: Path,
    filter_script: Path,
    use_script: bool = True,
) -> list[str]:
    filter_script.parent.mkdir(parents=True, exist_ok=True)
    filter_script.write_text(plan.filtergraph, encoding="utf-8")
    graph_args = (
        ["-filter_complex_script", str(filter_script)]
        if use_script
        else ["-filter_complex", plan.filtergraph]
    )
    return [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        _color_source(plan),
        "-i",
        str(voiceover_path),
        *graph_args,
        *_encode_args(plan, output_path),
    ]


def compose_vertical_mp4(
    plan: RenderPlan,
    *,
    voiceover_path: Path,
    output_path: Path,
    filter_script: Path,
    timeout: int,
) -> list[str]:
    args = build_render_args(
        plan,
        voiceover_path=voiceover_path,
        output_path=output_path,
        filter_script=filter_script,
        use_script=True,
    )
    try:
        run_ffmpeg(args, timeout=timeout)
        return args
    except RetryableMediaError as exc:
        detail = str(exc).lower()
        if "filter_complex_script" not in detail and "unrecognized option" not in detail:
            raise
        fallback = build_render_args(
            plan,
            voiceover_path=voiceover_path,
            output_path=output_path,
            filter_script=filter_script,
            use_script=False,
        )
        run_ffmpeg(fallback, timeout=timeout)
        return fallback


def run_ffmpeg(args: list[str], *, timeout: int) -> None:
    binary = require_ffmpeg()
    try:
        completed = subprocess.run(
            [binary, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RetryableMediaError(f"ffmpeg timed out after {timeout}s (retryable)") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown ffmpeg error")[-2000:]
        raise RetryableMediaError(f"ffmpeg failed (retryable): {detail}")


def extract_thumbnail(*, video_path: Path, output_path: Path, at_s: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, at_s):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output_path),
        ],
        timeout=30,
    )
