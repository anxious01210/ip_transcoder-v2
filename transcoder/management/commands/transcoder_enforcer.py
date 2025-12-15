import subprocess
import time
from typing import Dict, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from transcoder.ffmpeg_runner import FFmpegJobConfig
from transcoder.models import Channel, JobPurpose

JobKey = Tuple[str, int]  # (purpose, channel_id)


class Command(BaseCommand):
    help = (
        "Enforcer: starts/stops ffmpeg jobs based on each channel's schedule. "
        "Mode: Record + Time-shift playback (single output)."
    )

    POLL_INTERVAL = 5  # seconds

    def _auto_delete_for_channel(self, chan: Channel, now) -> None:
        """
        Delete old TS segments based on:
          - auto_delete_after_segments (keep last N)
          - auto_delete_after_days (delete older than N days)

        Also protects a window required for playback:
            delay_minutes + PLAYBACK_PLAYLIST_WINDOW_SECONDS (+ one segment)
        """
        from pathlib import Path
        from datetime import datetime, timedelta
        import re

        window_seconds = int(getattr(settings, "PLAYBACK_PLAYLIST_WINDOW_SECONDS", 3 * 3600))

        delay_seconds = 0
        profile = getattr(chan, "timeshift_profile", None) or getattr(chan, "timeshiftprofile", None)
        if profile and getattr(profile, "enabled", False):
            delay_seconds = int(getattr(profile, "delay_seconds", 0) or 0)

        protect_seconds = delay_seconds + window_seconds + (chan.recording_segment_minutes * 60)
        protect_after = (now - timedelta(seconds=protect_seconds)).replace(tzinfo=None)

        # Derive recordings root
        try:
            root_str = chan.recording_path_template.format(channel=chan.name, date="", time="")
        except Exception:
            root_str = chan.recording_path_template

        root = Path(root_str)
        if not root.is_absolute():
            root = Path(settings.MEDIA_ROOT) / root

        if not root.exists():
            root = Path(settings.MEDIA_ROOT) / "recordings" / chan.name

        ts_files = list(root.glob("**/*.ts"))
        if not ts_files:
            return

        rx = re.compile(rf"^{re.escape(chan.name)}_(\d{{8}})-(\d{{6}})\.ts$")
        items = []
        for p in ts_files:
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

        to_delete = set()

        # Age-based deletion
        if chan.auto_delete_after_days:
            cutoff = (now - timedelta(days=int(chan.auto_delete_after_days))).replace(tzinfo=None)
            for ts, p in items:
                if ts < cutoff and ts < protect_after:
                    to_delete.add(p)

        # Count-based deletion (keep last N)
        if chan.auto_delete_after_segments:
            keep_n = int(chan.auto_delete_after_segments)
            if keep_n > 0 and len(items) > keep_n:
                older = items[:-keep_n]
                for ts, p in older:
                    if ts < protect_after:
                        to_delete.add(p)

        for p in sorted(to_delete):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def handle(self, *args, **options):
        running: Dict[JobKey, subprocess.Popen] = {}

        self.stdout.write(self.style.SUCCESS("Enforcer started. Press Ctrl+C to stop."))

        try:
            while True:
                now = timezone.localtime()

                channels = list(Channel.objects.filter(enabled=True))
                desired_jobs: Dict[JobKey, str] = {}

                for chan in channels:
                    if not chan.is_active_now(now):
                        continue

                    profile = getattr(chan, "timeshift_profile", None) or getattr(chan, "timeshiftprofile", None)
                    enabled_prof = bool(profile and getattr(profile, "enabled", False))
                    delay_seconds = int(getattr(profile, "delay_seconds", 0) or 0)

                    has_udp_out = (chan.output_type == "udp_ts") and (chan.output_target or "").strip()

                    # LIVE mode (delay 0 or profile missing/disabled): playback only (no recording)
                    if has_udp_out and (not enabled_prof or delay_seconds <= 0):
                        key_play: JobKey = (JobPurpose.PLAYBACK, chan.id)
                        desired_jobs[key_play] = f"{chan.name} [LIVE playback]"

                    # TIME-SHIFT mode (delay > 0): record + delayed playback
                    if enabled_prof and delay_seconds > 0:
                        key_rec: JobKey = (JobPurpose.RECORD, chan.id)
                        desired_jobs[key_rec] = f"{chan.name} [record]"

                        if has_udp_out:
                            key_play: JobKey = (JobPurpose.PLAYBACK, chan.id)
                            desired_jobs[key_play] = f"{chan.name} [timeshift playback]"

                # Start missing desired jobs
                for key, descr in desired_jobs.items():
                    proc = running.get(key)

                    # If ffmpeg is already running, we may still need a restart
                    # (e.g. admin changed output_target, so the command must change).
                    if proc is not None and proc.poll() is None:
                        purpose, channel_id = key

                        # Only playback depends on output_target; record does not.
                        if purpose == JobPurpose.PLAYBACK:
                            # Always compare against the correct channel for this key
                            this_chan = next(c for c in channels if c.id == channel_id)

                            desired_cmd = None
                            try:
                                desired_cmd = FFmpegJobConfig(channel=this_chan, purpose=purpose).build_command()

                            except Exception:
                                # If we can't build a command right now, keep running and retry next poll.
                                desired_cmd = None

                            # Store the last command per job so we can detect changes safely
                            if not hasattr(self, "_last_cmd"):
                                self._last_cmd = {}
                            last_cmd = self._last_cmd.get(key)

                            if desired_cmd and last_cmd and desired_cmd != last_cmd:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"Restarting ffmpeg for {descr} (command changed, likely output_target updated)..."
                                    )
                                )
                                proc.terminate()
                                del running[key]
                                # fall through to start logic below (will rebuild & start)
                            else:
                                # Record current desired cmd (first time) then keep running
                                if desired_cmd and not last_cmd:
                                    self._last_cmd[key] = desired_cmd
                                continue
                        else:
                            continue

                    purpose, channel_id = key
                    chan = next(c for c in channels if c.id == channel_id)

                    job = FFmpegJobConfig(channel=chan, purpose=purpose)
                    try:
                        cmd = job.build_command()
                    except FileNotFoundError as e:
                        # Common at startup: playback may be requested before enough TS segments exist.
                        # Do not crash the enforcer; just retry on the next poll.
                        self.stdout.write(self.style.WARNING(f"Skipping {descr} (not ready yet): {e}"))
                        continue
                    except Exception as e:
                        # Any other build failure should not kill the long-running service.
                        self.stdout.write(self.style.WARNING(f"Skipping {descr} (build error): {e}"))
                        continue

                    self.stdout.write(self.style.SUCCESS(f"Starting ffmpeg for {descr}: {' '.join(cmd)}"))
                    try:
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"Failed to start ffmpeg for {descr}: {e}"))
                        continue
                    running[key] = proc

                    if not hasattr(self, "_last_cmd"):
                        self._last_cmd = {}
                    self._last_cmd[key] = cmd

                # Stop jobs no longer desired
                for key, proc in list(running.items()):
                    if key not in desired_jobs:
                        if proc.poll() is None:
                            purpose, channel_id = key
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Stopping ffmpeg for channel_id={channel_id}, purpose={purpose} (no longer desired)..."
                                )
                            )
                            proc.terminate()
                        del running[key]

                # Auto-delete (optional)
                for chan in channels:
                    if not chan.auto_delete_enabled:
                        continue
                    try:
                        self._auto_delete_for_channel(chan, now)
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"Auto-delete warning for {chan.name}: {e}"))

                time.sleep(self.POLL_INTERVAL)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Enforcer stopping (Ctrl+C)..."))
            for key, proc in running.items():
                if proc.poll() is None:
                    purpose, channel_id = key
                    self.stdout.write(
                        self.style.WARNING(f"Terminating ffmpeg for channel_id={channel_id}, purpose={purpose}...")
                    )
                    proc.terminate()
            self.stdout.write(self.style.SUCCESS("Enforcer stopped."))
