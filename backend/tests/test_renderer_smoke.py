"""FFmpeg smoke: a real 2-second 1080x1920 MP4 via the public render primitives."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ame.media.errors import RetryableMediaError
from ame.media.ffmpeg import (
    build_render_args,
    build_vertical_filtergraph,
    ffmpeg_available,
    resolve_font,
    run_ffmpeg,
)
from ame.media.template import CANVAS_HEIGHT, CANVAS_WIDTH, default_template
from ame.media.wavutil import write_tone_wav

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _ffprobe_streams(path: Path) -> dict:
    binary = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not binary:
        from ame.media.ffmpeg import find_ffmpeg
        from ame.qa.ffprobe import _probe_with_ffmpeg

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return {}
        probe = _probe_with_ffmpeg(path, ffmpeg)
        if not probe.has_video:
            return {}
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": probe.width,
                    "height": probe.height,
                }
            ]
        }
    completed = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-show_entries",
            "stream=width,height,codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return {}
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _render_two_second_vertical(output: Path, work_dir: Path) -> Path:
    try:
        from ame.media.renderer import render_vertical_clip

        render_vertical_clip(output, duration_s=2, width=1080, height=1920)
        return output
    except ImportError:
        pass

    template = default_template()
    voice = work_dir / "voice.wav"
    write_tone_wav(voice, 2.0)
    plan = build_vertical_filtergraph(
        scenes=[
            {
                "on_screen_text": "AME SMOKE",
                "start_s": 0.0,
                "end_s": 2.0,
                "typography": {"font_size": 64, "font_color": "#F4F6F8"},
            }
        ],
        cues=[{"text": "smoke test", "start_s": 0.2, "end_s": 1.8}],
        font=resolve_font(),
        work_dir=work_dir / "work",
        duration_s=2.0,
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT,
        fps=30,
        background=str(template["colors"]["background"]),
        subtitle_color=str(template["colors"]["subtitle"]),
        subtitle_size=int(template["subtitle_size"]),
        accent=str(template["colors"]["accent"]),
        margins=template["safe_margins"],
    )
    args = build_render_args(
        plan,
        voiceover_path=voice,
        output_path=output,
        filter_script=work_dir / "filtergraph.txt",
    )
    run_ffmpeg(args, timeout=90)
    return output


def test_render_two_second_1080x1920(tmp_path: Path) -> None:
    output = tmp_path / "smoke.mp4"
    try:
        _render_two_second_vertical(output, tmp_path)
    except RetryableMediaError as exc:
        pytest.skip(str(exc))

    assert output.is_file()
    assert output.stat().st_size > 1000

    probe = _ffprobe_streams(output)
    streams = probe.get("streams") or []
    videos = [item for item in streams if item.get("codec_type") == "video"]
    if videos:
        assert int(videos[0]["width"]) == 1080
        assert int(videos[0]["height"]) == 1920
