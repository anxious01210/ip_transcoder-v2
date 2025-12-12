# transcoder/management/commands/transcoder_enforcer.py
import time
import subprocess
from pathlib import Path
from typing import Dict, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from transcoder.models import Channel
from transcoder.ffmpeg_runner import FFmpegJobConfig
from transcoder.retention import prune_channel_recordings

PURPOSE_RECORD = "record"
PURPOSE_PLAYBACK = "playback"
JobKey = Tuple[str, int]  # (purpose, channel_id)

LAST_PRUNE_AT = 0.0
PRUNE_EVERY_SECONDS = 60  # run retention once per minute


class Command(BaseCommand):
    help = "Ensures ffmpeg jobs are running according to Channel schedule and settings."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting transcoder enforcer..."))

        running: Dict[JobKey, subprocess.Popen] = {}

        logs_dir = Path(settings.MEDIA_ROOT) / "ffmpeg_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        try:
            while True:
                now = timezone.localtime()

                channels = list(Channel.objects.all())
                channel_by_id = {c.id: c for c in channels}

                # -----------------------------
                # Retention (auto-delete) throttle
                # -----------------------------
                global LAST_PRUNE_AT
                t = time.time()
                if t - LAST_PRUNE_AT >= PRUNE_EVERY_SECONDS:
                    for chan in channels:
                        if chan.auto_delete_enabled:
                            stats = prune_channel_recordings(chan, dry_run=False)
                            if stats["deleted"] > 0:
                                self.stdout.write(
                                    f"[retention] {chan.name}: deleted={stats['deleted']} "
                                    f"protected_skips={stats['skipped_protected']} scanned={stats['scanned']}"
                                )
                    LAST_PRUNE_AT = t

                # -----------------------------
                # Compute desired jobs
                # -----------------------------
                desired_jobs: Dict[JobKey, str] = {}

                for chan in channels:
                    # RECORD follows schedule strictly
                    record_should_run = chan.record_enabled and chan.is_record_active_now(now)

                    # PLAYBACK follows schedule OR tail-window (if playback_tail_enabled)
                    playback_should_run = chan.is_playback_active_now(now)

                    if record_should_run:
                        desired_jobs[(PURPOSE_RECORD, chan.id)] = f"{chan.name} [record]"

                    if playback_should_run:
                        desired_jobs[(PURPOSE_PLAYBACK, chan.id)] = f"{chan.name} [playback]"

                # -----------------------------
                # Stop jobs that are no longer desired
                # -----------------------------
                for key in list(running.keys()):
                    if key not in desired_jobs:
                        proc = running.pop(key)
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass

                # -----------------------------
                # Start any missing desired jobs
                # -----------------------------
                for key, label in desired_jobs.items():
                    if key in running:
                        # if process died, clean it up so it can be restarted
                        if running[key].poll() is not None:
                            try:
                                running[key].wait(timeout=0.2)
                            except Exception:
                                pass
                            running.pop(key, None)
                        else:
                            continue

                    purpose, channel_id = key
                    chan = channel_by_id.get(channel_id)
                    if not chan:
                        continue

                    try:
                        job = FFmpegJobConfig(channel=chan, purpose=purpose)
                        cmd = job.build_command()

                        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in chan.name)
                        log_path = logs_dir / f"{safe_name}_{purpose}.log"
                        log_f = open(log_path, "a", buffering=1)

                        proc = subprocess.Popen(
                            cmd,
                            stdout=log_f,
                            stderr=log_f,
                            text=True,
                        )
                        running[key] = proc
                        self.stdout.write(self.style.SUCCESS(f"Started {label} (pid={proc.pid})"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Failed to start {label}: {e}"))

                time.sleep(5)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Stopping enforcer..."))
        finally:
            for proc in running.values():
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
