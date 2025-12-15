# transcoder/ffmpeg_runner.py
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .models import AudioMode, Channel, TimeShiftProfile, VideoMode


@dataclass
class FFmpegJobConfig:
    channel: Channel
    purpose: str  # "record" or "playback"

    def build_command(self) -> List[str]:
        """
        Build an ffmpeg command for this channel & purpose.

        - record:
            Reads from channel.input_* and writes TS segments under MEDIA_ROOT.
        - playback:
            Reads from recorded TS segments (concat playlist window) and outputs delayed UDP TS to channel.output_target.
            The playlist is finite; enforcer restarts playback after it exits, keeping it continuous.
        """
        chan = self.channel
        args: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]

        # ------------------------
        # RECORD
        # ------------------------
        if self.purpose == "record":
            args += self._build_live_input_args()

            is_internal_gen = (chan.input_type == "internal_gen")

            # Video codec
            if (not is_internal_gen) and chan.video_mode == VideoMode.COPY:
                args += ["-c:v", "copy"]
            else:
                args += ["-c:v", chan.video_codec or "libx264"]
                if is_internal_gen:
                    args += [
                        "-preset", "veryfast",
                        "-tune", "zerolatency",
                        "-pix_fmt", "yuv420p",
                        "-g", "50",
                        "-x264-params", "repeat-headers=1",
                    ]

            # Audio codec
            if (not is_internal_gen) and chan.audio_mode == AudioMode.COPY:
                args += ["-c:a", "copy"]
            elif chan.audio_mode == AudioMode.DISABLE:
                args += ["-an"]
            else:
                args += ["-c:a", chan.audio_codec or "aac"]
                if is_internal_gen:
                    args += ["-b:a", "128k", "-ac", "2"]

            self._build_record_output(args)
            return args

        # ------------------------
        # PLAYBACK (LIVE or TIME-SHIFT)
        # ------------------------
        if self.purpose == "playback":
            if chan.output_type != "udp_ts" or not (chan.output_target or "").strip():
                raise ValueError("Channel output must be UDP TS and output_target must be set for playback.")

            output_udp_url = (chan.output_target or "").strip()

            # # VLC-safe UDP TS output (works better when joining mid-stream)
            # if output_udp_url.startswith("udp://") and "pkt_size=" not in output_udp_url:
            #     sep = "&" if "?" in output_udp_url else "?"
            #     output_udp_url = f"{output_udp_url}{sep}pkt_size=1316"
            # VLC-safe UDP TS output (works better when joining mid-stream)
            if output_udp_url.startswith("udp://") and "pkt_size=" not in output_udp_url:
                sep = "&" if "?" in output_udp_url else "?"
                output_udp_url = f"{output_udp_url}{sep}pkt_size=1316"

            # If it's multicast, add a reasonable default TTL if not set
            # (Not strictly required if your network already forwards it, but safe)
            mcast_match = re.search(r"udp://(\d{1,3}\.){3}\d{1,3}", output_udp_url)
            if mcast_match and "ttl=" not in output_udp_url:
                ip = mcast_match.group(0).replace("udp://", "")
                first_octet = int(ip.split(".")[0])
                if 224 <= first_octet <= 239:
                    sep = "&" if "?" in output_udp_url else "?"
                    output_udp_url = f"{output_udp_url}{sep}ttl=16"

            profile: Optional[TimeShiftProfile] = (
                    getattr(chan, "timeshift_profile", None)
                    or getattr(chan, "timeshiftprofile", None)
            )

            enabled = bool(profile and getattr(profile, "enabled", False))
            delay_seconds = int(getattr(profile, "delay_seconds", 0) or 0)

            # LIVE MODE (0 sec) => NO recording required, direct restream
            if (not enabled) or delay_seconds <= 0:
                args += self._build_live_input_args()

                is_internal_gen = (chan.input_type == "internal_gen")

                # Video
                if (not is_internal_gen) and chan.video_mode == VideoMode.COPY:
                    args += ["-c:v", "copy"]
                else:
                    args += ["-c:v", chan.video_codec or "libx264"]
                    if is_internal_gen:
                        args += [
                            "-preset", "veryfast",
                            "-tune", "zerolatency",
                            "-pix_fmt", "yuv420p",
                            "-g", "50",
                            "-x264-params", "repeat-headers=1",
                        ]

                # Audio
                if (not is_internal_gen) and chan.audio_mode == AudioMode.COPY:
                    args += ["-c:a", "copy"]
                elif chan.audio_mode == AudioMode.DISABLE:
                    args += ["-an"]
                else:
                    args += ["-c:a", chan.audio_codec or "aac"]
                    if is_internal_gen:
                        args += ["-b:a", "128k", "-ac", "2"]

                args += [
                    "-f", "mpegts",
                    "-mpegts_flags", "+resend_headers",
                    "-muxdelay", "0",
                    "-muxpreload", "0",
                    output_udp_url,
                ]
                return args

            # TIME-SHIFT MODE (>0 sec) => requires enabled profile + recorded segments
            if not enabled:
                raise ValueError("Time-shift profile is not enabled for delayed playback.")
            if delay_seconds > 24 * 60 * 60:
                raise ValueError("delay_seconds must be <= 86400 (24h).")

            playlist_path = self._build_playback_concat_playlist(delay_seconds)

            args += [
                "-re",
                "-f", "concat",
                "-safe", "0",
                "-i", str(playlist_path),

                "-c:v", "copy",
                "-c:a", "copy",

                "-f", "mpegts",
                "-mpegts_flags", "+resend_headers",
                "-muxdelay", "0",
                "-muxpreload", "0",

                output_udp_url,
            ]
            return args

        raise ValueError(f"Unknown purpose: {self.purpose!r}")

    # ------------------------
    # Inputs
    # ------------------------
    def _build_live_input_args(self) -> List[str]:
        """
        Build ffmpeg input arguments for recording.

        - FILE: relative paths resolved under MEDIA_ROOT
        - UDP multicast: adds fifo_size/overrun tuning if missing
        - RTSP/RTMP: used as-is
        - INTERNAL_GENERATOR: lavfi test bars + sine tone (works everywhere)
        """
        chan = self.channel
        raw_input_url = (chan.input_url or "").strip()

        if chan.input_type == "internal_gen":
            return [
                "-re",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                "-shortest",
                "-map", "0:v:0",
                "-map", "1:a:0",
            ]

        if chan.input_type == "file":
            in_path = Path(raw_input_url)
            if not in_path.is_absolute():
                in_path = Path(settings.MEDIA_ROOT) / in_path
            return ["-i", str(in_path)]

        if chan.input_type == "udp_multicast":
            input_url = raw_input_url
            if "fifo_size=" not in input_url:
                sep = "&" if "?" in input_url else "?"
                input_url = f"{input_url}{sep}fifo_size=1000000&overrun_nonfatal=1"
            return ["-i", input_url]

        return ["-i", raw_input_url]

    # ------------------------
    # Record output
    # ------------------------
    def _build_record_output(self, args: List[str]) -> None:
        """
        Write TS segments under MEDIA_ROOT with timestamped filenames.
        Example: ChannelName_YYYYMMDD-HHMMSS.ts
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
        segment_pattern = str(base_dir / f"{chan.name}_%Y%m%d-%H%M%S.ts")

        args += [
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1",
            "-strftime", "1",
            segment_pattern,
        ]

    # ------------------------
    # Playback playlist builder
    # ------------------------
    def _iter_recording_segments(self) -> List[Tuple[datetime, Path]]:
        """
        Return a sorted list of (timestamp, path) for TS segments for this channel.
        Timestamp is parsed from filenames like: <channel>_YYYYMMDD-HHMMSS.ts
        Falls back to mtime if parsing fails.
        """
        chan = self.channel

        tmpl = chan.recording_path_template
        try:
            root_str = tmpl.format(channel=chan.name, date="", time="")
        except Exception:
            root_str = tmpl

        root = Path(root_str)
        if not root.is_absolute():
            root = Path(settings.MEDIA_ROOT) / root

        if not root.exists():
            root = Path(settings.MEDIA_ROOT) / "recordings" / chan.name

        candidates = list(root.glob("**/*.ts"))
        items: List[Tuple[datetime, Path]] = []

        rx = re.compile(rf"^{re.escape(chan.name)}_(\d{{8}})-(\d{{6}})\.ts$")

        for p in candidates:
            ts = None
            m = rx.match(p.name)
            if m:
                try:
                    ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
                except Exception:
                    ts = None
            if ts is None:
                ts = datetime.fromtimestamp(p.stat().st_mtime)
            items.append((ts, p))

        items.sort(key=lambda x: x[0])
        return items

    def _build_playback_concat_playlist(self, delay_seconds: int) -> Path:
        """
        Build a finite concat playlist window that ENDS at the delayed timestamp.

        Important:
        - We must NOT include segments newer than the delayed target time.
          If we do, playback will drift toward "live" as soon as any new segment exists
          (including the currently-being-written segment), making a 1-minute delay look
          like only a few seconds.
        - Therefore, the playlist is chosen from [target-window, target].

        Enforcer will restart playback after it exits, which keeps playback continuous.
        """
        window_seconds = int(getattr(settings, "PLAYBACK_PLAYLIST_WINDOW_SECONDS", 3 * 3600))

        # localtime -> make naive for filename timestamps
        now = timezone.localtime().replace(tzinfo=None)
        target = now - timedelta(seconds=delay_seconds)

        # Choose a historical window that ENDS at the target (delayed) time.
        start_ts = target - timedelta(seconds=window_seconds)
        end_ts = target

        segments = self._iter_recording_segments()
        # Only pick segments that are not newer than the delayed target.
        chosen = [p for (ts, p) in segments if (start_ts <= ts <= end_ts)]

        if not chosen:
            raise FileNotFoundError(
                f"No TS segments found for playback yet for channel {self.channel.name!r} "
                f"(delay={delay_seconds}s, window={window_seconds}s). "
                f"Recording may still be warming up; enforcer will retry."
            )

        out_dir = Path(settings.MEDIA_ROOT) / "playlists" / f"channel_{self.channel.id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        playlist_path = out_dir / "concat.txt"

        lines = [f"file {shlex.quote(str(p))}" for p in chosen]
        playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return playlist_path
