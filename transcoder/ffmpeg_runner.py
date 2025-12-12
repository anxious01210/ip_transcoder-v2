# transcoder/ffmpeg_runner.py
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .models import Channel, VideoMode, AudioMode, TimeShiftProfile
from datetime import datetime, timedelta


@dataclass
class FFmpegJobConfig:
    channel: Channel
    purpose: str  # "live_forward" | "record" | "playback"

    def _resolve_input_url_for_live(self) -> str:
        """
        Resolve the input URL for live_forward/record purposes.
        - FILE inputs: relative paths are resolved under MEDIA_ROOT.
        - UDP/RTSP/RTMP: returned as-is (with multicast tuning if needed).
        """
        chan = self.channel
        raw_input_url = chan.input_url

        if chan.input_type == "file":
            in_path = Path(raw_input_url)
            if not in_path.is_absolute():
                in_path = Path(settings.MEDIA_ROOT) / in_path
            return str(in_path)

        if chan.input_type == "udp_multicast":
            input_url = raw_input_url
            if "fifo_size=" not in input_url:
                sep = "&" if "?" in input_url else "?"
                input_url = f"{input_url}{sep}fifo_size=1000000&overrun_nonfatal=1"
            return input_url

        # RTSP/RTMP or others: use as-is
        return raw_input_url

    def _build_record_output(self, args: List[str]) -> None:
        """
        Append arguments to record input into TS segments under MEDIA_ROOT,
        with timestamped filenames so we can map back from a datetime later.
        """
        chan = self.channel
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")

        base_dir_str = chan.recording_path_template.format(
            channel=chan.name,
            date=date_str,
            time=now.strftime("%H%M%S"),
        )
        base_dir = Path(base_dir_str)
        if not base_dir.is_absolute():
            base_dir = Path(settings.MEDIA_ROOT) / base_dir

        base_dir.mkdir(parents=True, exist_ok=True)

        segment_seconds = chan.recording_segment_minutes * 60

        # Filename pattern includes timestamp: e.g. "ChannelName_20251210-120000.ts"
        segment_pattern = str(base_dir / f"{chan.name}_%Y%m%d-%H%M%S.ts")

        args += [
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1",
            "-strftime", "1",
            segment_pattern,
        ]

    def _find_playback_segment(self, delay_minutes: int) -> Path:
        """
        Given a delay (in minutes), find the recorded segment file that
        corresponds to "now - delay_minutes".

        Recording files are expected to be named:
          <channel>_YYYYMMDD-HHMMSS.ts
        in:
          MEDIA_ROOT / recording_path_template.format(channel=..., date=YYYYMMDD, time=...)
        """
        chan = self.channel

        # 'now_aware' is timezone-aware; convert to naive for filename comparisons.
        now_aware = timezone.localtime()
        target_dt_aware = now_aware - timedelta(minutes=delay_minutes)
        target_dt = target_dt_aware.replace(tzinfo=None)

        date_str = target_dt.strftime("%Y%m%d")

        base_dir_str = chan.recording_path_template.format(
            channel=chan.name,
            date=date_str,
            time=target_dt.strftime("%H%M%S"),
        )
        base_dir = Path(base_dir_str)
        if not base_dir.is_absolute():
            base_dir = Path(settings.MEDIA_ROOT) / base_dir

        if not base_dir.exists():
            raise FileNotFoundError(
                f"No recording directory found for {chan.name!r} at {base_dir}"
            )

        prefix = f"{chan.name}_"
        candidates: List[Tuple[datetime, Path]] = []

        for path in base_dir.glob(f"{chan.name}_*.ts"):
            stem = path.stem  # e.g. "Channel_20251210-120000"
            if not stem.startswith(prefix):
                continue
            ts_str = stem[len(prefix):]  # "20251210-120000"
            try:
                ts_dt = datetime.strptime(ts_str, "%Y%m%d-%H%M%S")  # naive
            except ValueError:
                continue
            candidates.append((ts_dt, path))

        if not candidates:
            raise FileNotFoundError(
                f"No timestamped TS segments found in {base_dir} for channel {chan.name!r}"
            )

        candidates.sort(key=lambda x: x[0])

        # Pick the segment whose start time is <= target_dt, or the earliest available
        chosen = None
        for ts_dt, path in candidates:
            if ts_dt <= target_dt:
                chosen = (ts_dt, path)
            else:
                break

        if chosen is None:
            # All segments are later than target_dt -> pick the earliest one
            chosen = candidates[0]

        return chosen[1]



        return chosen[1]

    def build_command(self) -> List[str]:
        """
        Builds an ffmpeg command for this channel & purpose.

        Cross-platform rules:
        - For live_forward/record:
            - FILE inputs: relative paths are resolved under MEDIA_ROOT.
            - HLS outputs: relative output_target is treated as a folder under MEDIA_ROOT.
            - Recording paths: relative recording_path_template is treated under MEDIA_ROOT.
            - Network URLs (UDP/RTSP/RTMP) are used as-is.
        - For playback:
            - Input is a recorded TS segment chosen based on TimeShiftProfile.delay_minutes.
            - Output is MPEG-TS to TimeShiftProfile.output_udp_url.
        """
        chan = self.channel
        args: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]

        # ------------------------
        # LIVE_FORWARD / RECORD
        # ------------------------
        if self.purpose in ("live_forward", "record"):
            input_url = self._resolve_input_url_for_live()
            args += ["-i", input_url]

            # Video
            if chan.video_mode == VideoMode.COPY:
                args += ["-c:v", "copy"]
            else:
                args += ["-c:v", chan.video_codec or "libx264"]

            # Audio
            if chan.audio_mode == AudioMode.COPY:
                args += ["-c:a", "copy"]
            elif chan.audio_mode == AudioMode.DISABLE:
                args += ["-an"]
            else:
                args += ["-c:a", chan.audio_codec or "aac"]

            if self.purpose == "live_forward":
                raw_output_target = chan.output_target

                if chan.output_type == "hls":
                    out_dir = Path(raw_output_target)
                    if not out_dir.is_absolute():
                        out_dir = Path(settings.MEDIA_ROOT) / out_dir
                    out_dir.mkdir(parents=True, exist_ok=True)
                    playlist_path = out_dir / "index.m3u8"

                    args += [
                        "-f", "hls",
                        "-hls_time", "4",
                        "-hls_list_size", "10",
                        "-hls_flags", "delete_segments",
                        str(playlist_path),
                    ]

                elif chan.output_type == "rtmp":
                    args += ["-f", "flv", raw_output_target]

                elif chan.output_type == "udp_ts":
                    args += ["-f", "mpegts", raw_output_target]

                else:
                    # fallback: TS file under MEDIA_ROOT
                    out_path = Path(raw_output_target)
                    if not out_path.is_absolute():
                        out_path = Path(settings.MEDIA_ROOT) / out_path
                    args += ["-f", "mpegts", str(out_path)]

            elif self.purpose == "record":
                self._build_record_output(args)

            return args

        # ------------------------
        # PLAYBACK (time-shifted)
        # ------------------------
        if self.purpose == "playback":
            # We ignore channel.input_url and instead read from recorded segments.

            # Prefer channel-level config (v2)
            delay_minutes = getattr(chan, "timeshift_delay_minutes", None)
            output_udp_url = (getattr(chan, "timeshift_output_udp_url", "") or "").strip()

            # Backwards-compatibility: fall back to TimeShiftProfile if needed
            if not delay_minutes or not output_udp_url:
                profile: Optional[TimeShiftProfile] = getattr(chan, "timeshift_profile", None)
                if profile and getattr(profile, "enabled", False):
                    delay_minutes = profile.delay_minutes
                    output_udp_url = (profile.output_udp_url or "").strip()

            if not delay_minutes or not output_udp_url:
                raise ValueError(
                    f"No time-shift configuration found for channel {chan.name!r}"
                )

            # Pick the TS segment that corresponds to "now - delay_minutes"
            playback_file = self._find_playback_segment(delay_minutes)

            # Important:
            # - We KEEP -re so the file is pushed at real-time pace.
            # - We REMOVE -stream_loop so ffmpeg exits at the end of this segment.
            #   The transcoder_enforcer will then start a new playback job, which
            #   will select the next appropriate segment based on (now - delay).
            args += [
                "-re",
                # "-stream_loop", "-1",  # loop the chosen segment infinitely for now
                "-i", str(playback_file),
                "-c:v", "copy",
                "-c:a", "copy",
                "-f", "mpegts",
            ]
            args.append(output_udp_url)
            return args


        # ------------------------
        # Unknown purpose
        # ------------------------
        raise ValueError(f"Unsupported purpose: {self.purpose!r}")


def build_ffmpeg_cmd_for_channel(channel_id: int, purpose: str = "live_forward") -> str:
    """
    Helper: loads the Channel and returns a shell-safe ffmpeg command string.
    """
    chan = Channel.objects.get(pk=channel_id)
    job = FFmpegJobConfig(channel=chan, purpose=purpose)
    cmd_list = job.build_command()
    return " ".join(shlex.quote(part) for part in cmd_list)
