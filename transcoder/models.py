from django.db import models
from django.utils import timezone
from django.core.validators import MaxValueValidator
import datetime
from datetime import timedelta


class InputType(models.TextChoices):
    MULTICAST_UDP = "udp_multicast", "UDP Multicast (MPEG-TS)"
    RTSP = "rtsp", "RTSP"
    RTMP = "rtmp", "RTMP"
    FILE = "file", "File"


class OutputType(models.TextChoices):
    UDP_TS = "udp_ts", "UDP TS Unicast"
    HLS = "hls", "HLS (m3u8)"
    RTMP = "rtmp", "RTMP"
    FILE_TS = "file_ts", "File (TS)"
    FILE_MP4 = "file_mp4", "File (MP4)"


class VideoMode(models.TextChoices):
    COPY = "copy", "Copy (no transcode)"
    TRANSCODE = "transcode", "Transcode"


class AudioMode(models.TextChoices):
    COPY = "copy", "Copy (no transcode)"
    TRANSCODE = "transcode", "Transcode"
    DISABLE = "disable", "Disable audio"


class HardwarePreference(models.TextChoices):
    AUTO = "auto", "Auto (NVIDIA → Intel → CPU)"
    NVIDIA = "nvidia", "Force NVIDIA (if available)"
    INTEL = "intel", "Force Intel QSV (if available)"
    CPU = "cpu", "Force CPU"


class Channel(models.Model):
    """
    v3 baseline:
    - Single mode: Record + Time-shift (delayed playback) with ONE output URL.
    - Recording writes .ts segments to disk.
    - Output restreams from recorded segments according to delay_seconds (0..24h).
    """
    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)

    # Input
    input_type = models.CharField(max_length=20, choices=InputType.choices)
    input_url = models.CharField(
        max_length=512,
        help_text=(
            "For multicast: e.g. udp://@224.2.2.2:2001 "
            "(we'll add fifo_size & overrun options automatically)."
        ),
    )
    multicast_interface = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional: e.g. eth0. Leave blank to use system default.",
    )

    # Output (ONE target only)
    output_type = models.CharField(
        max_length=20,
        choices=OutputType.choices,
        default=OutputType.UDP_TS,
    )
    output_url = models.CharField(
        max_length=512,
        help_text="For UDP/RTMP: URL (udp://ip:port, rtmp://...). For HLS: directory path.",
    )

    # Time-shift delay (0..86400 seconds = 24 hours)
    delay_seconds = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(24 * 3600)],
        help_text="0 = no delay. Max 86400 seconds (24 hours).",
    )

    playback_tail_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled: when schedule ends, recording stops immediately but playback continues "
            "until (schedule_end + delay_seconds). Useful to flush the delayed buffer."
        ),
    )

    # Recording settings
    record_enabled = models.BooleanField(
        default=True,
        help_text="If on, the channel records TS segments to disk (required for time-shift).",
    )
    recording_path_template = models.CharField(
        max_length=512,
        default="recordings/{channel}/{date}/",
        help_text=(
            "Recording path. Relative paths are under MEDIA_ROOT. "
            "Use {channel}, {date}, {time} placeholders."
        ),
    )
    recording_segment_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Length of each recording segment in minutes.",
    )

    # Auto-delete (segments and/or days)
    auto_delete_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, old recording segments will be deleted automatically "
            "based on the thresholds below."
        ),
    )
    auto_delete_after_segments = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Maximum number of most recent segments to keep in each recording folder. "
            "Older segments beyond this count may be deleted. Leave blank to ignore."
        ),
    )
    auto_delete_after_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Also delete segments older than this many days. "
            "Leave blank to ignore age-based cleanup."
        ),
    )

    # Codec / processing – copy by default (CPU-only is your target, but we keep the fields)
    video_mode = models.CharField(max_length=16, choices=VideoMode.choices, default=VideoMode.COPY)
    audio_mode = models.CharField(max_length=16, choices=AudioMode.choices, default=AudioMode.COPY)

    video_codec = models.CharField(
        max_length=16,
        default="h264",
        help_text="Used only when transcoding (e.g. h264, hevc).",
    )
    audio_codec = models.CharField(
        max_length=16,
        default="aac",
        help_text="Used only when transcoding audio.",
    )

    hardware_preference = models.CharField(
        max_length=16,
        choices=HardwarePreference.choices,
        default=HardwarePreference.CPU,  # v3: CPU-only baseline
        help_text="v3 baseline is CPU-only. Keep this field for future flexibility.",
    )

    target_width = models.PositiveIntegerField(null=True, blank=True, help_text="E.g. 1920. If null, keep source.")
    target_height = models.PositiveIntegerField(null=True, blank=True, help_text="E.g. 1080. If null, keep source.")
    video_bitrate = models.CharField(max_length=16, blank=True, help_text="E.g. 4000k. If blank, FFmpeg decides.")

    # Weekly schedule directly on Channel
    monday = models.BooleanField(default=True)
    tuesday = models.BooleanField(default=True)
    wednesday = models.BooleanField(default=True)
    thursday = models.BooleanField(default=True)
    friday = models.BooleanField(default=True)
    saturday = models.BooleanField(default=True)
    sunday = models.BooleanField(default=True)

    start_time = models.TimeField(
        null=True,
        blank=True,
        help_text=(
            "Local start time (HH:MM). "
            "If start_time == end_time, the channel runs the full day on selected weekdays."
        ),
    )
    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text=(
            "Local end time (HH:MM). "
            "If earlier than start_time, the channel runs overnight."
        ),
    )

    date_from = models.DateField(null=True, blank=True, help_text="Optional: only apply from this date (inclusive).")
    date_to = models.DateField(null=True, blank=True, help_text="Optional: only apply up to this date (inclusive).")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def is_active_now(self, now: datetime.datetime | None = None) -> bool:
        """
        Check if this channel should be active at the given 'now' (local time)
        based on its built-in weekly schedule.

        Semantics:
        - If start_time == end_time (and not null) -> active full day for selected weekdays
        - If start_time < end_time                 -> active between start and end same day
        - If start_time > end_time                 -> overnight window (e.g. 20:00 -> 06:00)
        - If start_time or end_time is null        -> treated as 00:00
        """
        if not self.enabled:
            return False

        if now is None:
            now = timezone.localtime()

        local_date = now.date()
        local_time = now.time()
        weekday = now.weekday()  # Mon=0..Sun=6

        start_time = self.start_time or datetime.time(0, 0)
        end_time = self.end_time or datetime.time(0, 0)

        if self.date_from and local_date < self.date_from:
            return False
        if self.date_to and local_date > self.date_to:
            return False

        weekday_flags = [
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        ]
        if not weekday_flags[weekday]:
            return False

        if start_time == end_time:
            return True

        if start_time < end_time:
            return start_time <= local_time < end_time
        else:
            return (local_time >= start_time) or (local_time < end_time)

    def is_record_active_now(self, now=None) -> bool:
        """
        Recording follows schedule strictly.
        """
        return self.is_active_now(now=now)

    def is_playback_active_now(self, now=None) -> bool:
        """
        Playback is active if:
        - schedule is active now, OR
        - playback_tail_enabled and we're within (last_schedule_end + delay_seconds)
        """
        if not self.enabled:
            return False

        if now is None:
            now = timezone.localtime()

        # If schedule is currently active -> playback should run
        if self.is_active_now(now=now):
            return True

        # If no tail -> stop playback when schedule stops
        if not self.playback_tail_enabled:
            return False

        # If delay is 0 -> tail doesn't matter
        if not self.delay_seconds:
            return False

        last_end = self._most_recent_schedule_end_dt(now)
        if not last_end:
            return False

        return now <= (last_end + timedelta(seconds=int(self.delay_seconds)))

    def _most_recent_schedule_end_dt(self, now):
        """
        Find the most recent schedule end datetime that is <= now.
        Works for same-day and overnight windows.

        We search backward up to 8 days to find the latest end moment.
        """
        # If start==end -> full-day schedule, no "end moment" tail concept
        start_time = self.start_time or datetime.time(0, 0)
        end_time = self.end_time or datetime.time(0, 0)
        if start_time == end_time:
            return None

        # Helper: weekday enabled?
        weekday_flags = [
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        ]

        best_end = None
        today = now.date()

        # We iterate start-days backward; for overnight schedules,
        # end is on the next day morning.
        for delta_days in range(0, 8):
            start_day = today - datetime.timedelta(days=delta_days)

            # date_from/date_to apply to the "start_day" of the scheduled window
            if self.date_from and start_day < self.date_from:
                continue
            if self.date_to and start_day > self.date_to:
                continue

            if not weekday_flags[start_day.weekday()]:
                continue

            # Compute end datetime for the window starting on start_day
            if start_time < end_time:
                # Same-day window: start_day start -> start_day end
                end_dt = datetime.datetime.combine(start_day, end_time)
            else:
                # Overnight window: start_day start -> (start_day + 1) end
                end_dt = datetime.datetime.combine(start_day + datetime.timedelta(days=1), end_time)

            # Make it timezone-aware in the same zone as `now`
            # If your project runs with USE_TZ=False, this still behaves fine because now is localtime().
            if timezone.is_naive(end_dt) and not timezone.is_naive(now):
                end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())

            if end_dt <= now and (best_end is None or end_dt > best_end):
                best_end = end_dt

        return best_end
