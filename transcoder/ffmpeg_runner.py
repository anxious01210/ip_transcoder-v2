import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from django.conf import settings
from django.utils import timezone

from .models import Channel, VideoMode, AudioMode
from datetime import datetime, timedelta
import tempfile

PLAYBACK_PLAYLIST_WINDOW_SECONDS = 3 * 3600  # configurable


@dataclass
class FFmpegJobConfig:
    channel: Channel
    purpose: str  # "record" | "playback"

    # -------------------------------------------------
    # RECORDING
    # -------------------------------------------------
    def _resolve_input_url_for_record(self) -> str:
        chan = self.channel
        raw_input_url = chan.input_url

        if chan.input_type == "file":
            p = Path(raw_input_url)
            if not p.is_absolute():
                p = Path(settings.MEDIA_ROOT) / p
            return str(p)

        if chan.input_type == "udp_multicast":
            if "fifo_size=" not in raw_input_url:
                sep = "&" if "?" in raw_input_url else "?"
                return f"{raw_input_url}{sep}fifo_size=1000000&overrun_nonfatal=1"
            return raw_input_url

        return raw_input_url

    def _build_record_output(self, args: List[str]) -> None:
        chan = self.channel
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")

        base_dir = Path(
            chan.recording_path_template.format(
                channel=chan.name,
                date=date_str,
                time=now.strftime("%H%M%S"),
            )
        )
        if not base_dir.is_absolute():
            base_dir = Path(settings.MEDIA_ROOT) / base_dir

        base_dir.mkdir(parents=True, exist_ok=True)

        args += [
            "-f", "segment",
            "-segment_time", str(chan.recording_segment_minutes * 60),
            "-reset_timestamps", "1",
            "-strftime", "1",
            str(base_dir / f"{chan.name}_%Y%m%d-%H%M%S.ts"),
        ]

    # -------------------------------------------------
    # PLAYBACK HELPERS
    # -------------------------------------------------
    def _parse_ts(self, path: Path) -> datetime | None:
        prefix = f"{self.channel.name}_"
        if not path.stem.startswith(prefix):
            return None
        try:
            return datetime.strptime(path.stem[len(prefix):], "%Y%m%d-%H%M%S")
        except ValueError:
            return None

    def _find_start_segment(self, delay_seconds: int) -> Path | None:
        target = (timezone.localtime() - timedelta(seconds=delay_seconds)).replace(tzinfo=None)

        root_tpl = self.channel.recording_path_template.format(
            channel=self.channel.name, date="*", time="*"
        )
        root = Path(root_tpl)
        if not root.is_absolute():
            root = Path(settings.MEDIA_ROOT) / root

        candidates: list[tuple[datetime, Path]] = []

        for p in root.glob(f"{self.channel.name}_*.ts"):
            ts = self._parse_ts(p)
            if ts:
                candidates.append((ts, p))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])

        chosen = candidates[0][1]
        for ts, p in candidates:
            if ts <= target:
                chosen = p
            else:
                break
        return chosen

    def _collect_segments(self, start_file: Path) -> list[Path]:
        chan = self.channel
        seg_len = max(1, chan.recording_segment_minutes * 60)
        max_files = int(PLAYBACK_PLAYLIST_WINDOW_SECONDS // seg_len) + 2

        root_tpl = chan.recording_path_template.format(
            channel=chan.name, date="*", time="*"
        )
        root = Path(root_tpl)
        if not root.is_absolute():
            root = Path(settings.MEDIA_ROOT) / root

        segs: list[tuple[datetime, Path]] = []
        for p in root.glob(f"{chan.name}_*.ts"):
            ts = self._parse_ts(p)
            if ts:
                segs.append((ts, p))

        segs.sort(key=lambda x: x[0])

        started = False
        out: list[Path] = []
        for ts, p in segs:
            if not started:
                if p == start_file:
                    started = True
                else:
                    continue
            if p.exists():
                out.append(p)
            if len(out) >= max_files:
                break

        return out

    def _write_playlist(self, segs: list[Path]) -> Path:
        tmp_dir = Path(settings.MEDIA_ROOT) / "tmp_playlists"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=tmp_dir, suffix=".txt"
        ) as f:
            for p in segs:
                f.write(f"file '{p}'\n")
            return Path(f.name)

    # -------------------------------------------------
    # COMMAND BUILDER
    # -------------------------------------------------
    def build_command(self) -> List[str]:
        chan = self.channel
        args: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]

        if self.purpose == "record":
            args += ["-i", self._resolve_input_url_for_record()]

            args += ["-c:v", "copy" if chan.video_mode == VideoMode.COPY else (chan.video_codec or "libx264")]

            if chan.audio_mode == AudioMode.COPY:
                args += ["-c:a", "copy"]
            elif chan.audio_mode == AudioMode.DISABLE:
                args += ["-an"]
            else:
                args += ["-c:a", chan.audio_codec or "aac"]

            self._build_record_output(args)
            return args

        if self.purpose == "playback":
            delay = int(chan.delay_seconds or 0)
            if not chan.output_url:
                raise ValueError("output_url is required for playback")

            start = self._find_start_segment(delay)
            if not start:
                raise FileNotFoundError("No recorded segments available yet")

            segs = self._collect_segments(start)
            if not segs:
                raise FileNotFoundError("Not enough segments for playback yet")

            playlist = self._write_playlist(segs)

            args += [
                "-re",
                "-f", "concat",
                "-safe", "0",
                "-i", str(playlist),
                "-c:v", "copy",
                "-c:a", "copy",
                "-f", "mpegts",
                chan.output_url,
            ]
            return args

        raise ValueError(f"Unsupported purpose: {self.purpose}")


def build_ffmpeg_cmd_for_channel(channel_id: int, purpose: str = "record") -> str:
    chan = Channel.objects.get(pk=channel_id)
    job = FFmpegJobConfig(channel=chan, purpose=purpose)
    return " ".join(shlex.quote(p) for p in job.build_command())
