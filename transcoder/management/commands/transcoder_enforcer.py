import subprocess
import time
from typing import Dict, Tuple

from django.core.management.base import BaseCommand
from django.utils import timezone

from transcoder.ffmpeg_runner import FFmpegJobConfig
from transcoder.models import Channel, JobPurpose

JobKey = Tuple[str, int]  # (purpose, channel_id)


class Command(BaseCommand):
    help = (
        "Enforcer v3: starts/stops ffmpeg jobs based on each channel's schedule, "
        "always recording and optionally running a time-shift playback if configured."
    )

    # How often to re-evaluate schedules and restart/stop ffmpeg (in seconds)
    POLL_INTERVAL = 10

    def handle(self, *args, **options):
        running: Dict[JobKey, subprocess.Popen] = {}

        self.stdout.write(
            self.style.SUCCESS(
                "Starting transcoder enforcer (v2, channel-centric scheduling)..."
            )
        )

        try:
            while True:
                now = timezone.localtime()

                # Collect active channels and decide which jobs we want
                channels = list(Channel.objects.filter(enabled=True))
                desired_jobs: Dict[JobKey, str] = {}  # key -> human description


                for chan in channels:
                    if not chan.is_active_now(now):
                        continue

                    # Always run a RECORD job for active channels
                    key_rec: JobKey = (JobPurpose.RECORD, chan.id)
                    desired_jobs[key_rec] = f"{chan.name} [record]"

                    # Optionally run a PLAYBACK job if time-shift is configured
                    delay = getattr(chan, "timeshift_delay_minutes", None)
                    udp = (getattr(chan, "timeshift_output_udp_url", "") or "").strip()
                    if delay is not None and udp:
                        key_play: JobKey = (JobPurpose.PLAYBACK, chan.id)
                        desired_jobs[key_play] = f"{chan.name} [record+timeshift: playback]"


                # Start any missing desired jobs
                for key, descr in desired_jobs.items():
                    proc = running.get(key)
                    if proc is not None and proc.poll() is None:
                        # Still running and desired
                        continue

                    purpose, channel_id = key
                    chan = next(c for c in channels if c.id == channel_id)
                    job = FFmpegJobConfig(channel=chan, purpose=purpose)
                    cmd = job.build_command()

                    self.stdout.write(
                        self.style.SUCCESS(f"Starting ffmpeg for {descr}: {' '.join(cmd)}")
                    )
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    running[key] = proc

                # Stop jobs that are no longer desired
                for key, proc in list(running.items()):
                    if key not in desired_jobs:
                        if proc.poll() is None:
                            purpose, channel_id = key
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Stopping ffmpeg for channel_id={channel_id}, purpose={purpose} "
                                    "(no longer desired)..."
                                )
                            )
                            proc.terminate()
                        del running[key]

                time.sleep(self.POLL_INTERVAL)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Enforcer stopping (Ctrl+C)..."))
            for key, proc in running.items():
                if proc.poll() is None:
                    purpose, channel_id = key
                    self.stdout.write(
                        self.style.WARNING(
                            f"Terminating ffmpeg for channel_id={channel_id}, purpose={purpose}..."
                        )
                    )
                    proc.terminate()
            self.stdout.write(self.style.SUCCESS("Enforcer stopped."))

