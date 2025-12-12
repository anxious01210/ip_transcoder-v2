from django.db import models
from django.utils import timezone
import datetime


class InputType(models.TextChoices):
    MULTICAST_UDP = "udp_multicast", "UDP Multicast (MPEG-TS)"
    RTSP = "rtsp", "RTSP"
    RTMP = "rtmp", "RTMP"
    FILE = "file", "File"


class OutputType(models.TextChoices):
    HLS = "hls", "HLS (m3u8)"
    RTMP = "rtmp", "RTMP"
    UDP_TS = "udp_ts", "UDP TS Unicast"
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


class JobPurpose(models.TextChoices):
    LIVE_FORWARD = "live_forward", "Live forward"
    RECORD = "record", "Record"
    # Later we can add: PLAYBACK = "playback", "Playback (from recording)"
    PLAYBACK = "playback", "Playback (delayed)"



class Channel(models.Model):
    """
    One logical source (multicast, RTSP, RTMP, file) and how we handle it.
    Copy/remux by default; transcoding only when explicitly enabled.
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

    # Output
    output_type = models.CharField(max_length=20, choices=OutputType.choices)
    output_target = models.CharField(
        max_length=512,
        help_text="For HLS: directory path; for RTMP/UDP: URL (udp://ip:port, rtmp://...).",
    )

    # Recording settings
    record_enabled = models.BooleanField(
        default=True,
        help_text="If on, we are allowed to record this channel to disk.",
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


    # Codec / processing – copy by default
    video_mode = models.CharField(
        max_length=16, choices=VideoMode.choices, default=VideoMode.COPY
    )
    audio_mode = models.CharField(
        max_length=16, choices=AudioMode.choices, default=AudioMode.COPY
    )

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
        default=HardwarePreference.AUTO,
        help_text="AUTO = NVIDIA → Intel → CPU; or force one.",
    )

    # Transcoding constraints (only if video_mode=TRANSCODE)
    target_width = models.PositiveIntegerField(
        null=True, blank=True, help_text="E.g. 1920. If null, keep source width."
    )
    target_height = models.PositiveIntegerField(
        null=True, blank=True, help_text="E.g. 1080. If null, keep source height."
    )
    video_bitrate = models.CharField(
        max_length=16,
        blank=True,
        help_text="E.g. 4000k. If blank, FFmpeg decides.",
    )


    # Simple weekly schedule attached directly to the channel
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
            "If Start time and End time are both set to 00:00, the channel runs for the entire day."
        ),
    )
    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text=(
            "Local end time (HH:MM). "
            "If earlier than Start time, the channel runs overnight. "
            "If Start time and End time are both set to 00:00, the channel runs for the entire day."
        ),
    )

    date_from = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: only apply from this date (inclusive).",
    )
    date_to = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: only apply up to this date (inclusive).",
    )

    # Recording retention / auto-delete settings
    auto_delete_enabled = models.BooleanField(
        default=False,
        help_text="If enabled, old recordings will be automatically deleted based on 'auto_delete_after_days'.",
    )
    auto_delete_after_days = models.PositiveIntegerField(
        default=7,
        help_text="Delete recordings older than this many days.",
    )

    # Optional time-shift configuration stored directly on the channel
    timeshift_delay_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Delay in minutes for time-shift playback. Used when mode is 'Record + Time-shift' or 'Playback from recordings'.",
    )
    timeshift_output_udp_url = models.CharField(
        max_length=512,
        blank=True,
        help_text="UDP TS URL for delayed output, e.g. udp://239.0.0.10:2001?ttl=1&pkt_size=1316",
    )
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

        Semantics (same as RecurringSchedule):
        - If start_time == end_time (and not null)  -> active the FULL day for selected weekdays.
        - If start_time < end_time                  -> active between start and end on the same day.
        - If start_time > end_time                  -> overnight window (e.g. 20:00 -> 06:00 next day).
        - If start_time or end_time is null         -> treated as 00:00.
        """
        if not self.enabled:
            return False

        if now is None:
            now = timezone.localtime()

        local_date = now.date()
        local_time = now.time()
        weekday = now.weekday()  # Monday=0, Sunday=6

        # Use 00:00 as default if times are not set
        start_time = self.start_time or datetime.time(0, 0)
        end_time = self.end_time or datetime.time(0, 0)

        # Date range check
        if self.date_from and local_date < self.date_from:
            return False
        if self.date_to and local_date > self.date_to:
            return False

        # Weekday check via booleans (note: weekday() is Mon=0..Sun=6)
        weekday_flags = [
            self.monday,    # 0
            self.tuesday,   # 1
            self.wednesday, # 2
            self.thursday,  # 3
            self.friday,    # 4
            self.saturday,  # 5
            self.sunday,    # 6
        ]
        if not weekday_flags[weekday]:
            return False

        # Time window semantics
        if start_time == end_time:
            # Full-day: active 24 hours for the selected weekdays
            return True

        if start_time < end_time:
            # Normal daytime window: e.g. 08:00 -> 20:00
            return start_time <= local_time < end_time
        else:
            # Overnight window: e.g. 20:00 -> 06:00
            return (local_time >= start_time) or (local_time < end_time)

class Schedule(models.Model):
    """
    One scheduled job for a channel:
    - LIVE_FORWARD: take input and restream (e.g. to HLS/RTMP/UDP)
    - RECORD: take input and save to disk
    For now: simple one-off schedule with start/end datetimes.
    """
    name = models.CharField(max_length=100)
    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    purpose = models.CharField(
        max_length=16,
        choices=JobPurpose.choices,
        default=JobPurpose.LIVE_FORWARD,
    )

    enabled = models.BooleanField(default=True)

    start_at = models.DateTimeField(
        help_text="When this job should start (server/local time)."
    )
    end_at = models.DateTimeField(
        help_text="When this job should stop (server/local time)."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.channel.name})"


class RecurringSchedule(models.Model):
    """
    Weekly recurring schedule.
    Example: record Channel A every Sat–Fri from 00:00 to 23:59, between date_from and date_to.
    """
    name = models.CharField(max_length=100)
    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name="recurring_schedules",
    )
    purpose = models.CharField(
        max_length=16,
        choices=JobPurpose.choices,
        default=JobPurpose.RECORD,
    )

    enabled = models.BooleanField(default=True)

    # Weekday checkboxes – logical semantics are the same,
    # but we'll display them Sat→Fri in admin.
    monday = models.BooleanField(default=True)
    tuesday = models.BooleanField(default=True)
    wednesday = models.BooleanField(default=True)
    thursday = models.BooleanField(default=True)
    friday = models.BooleanField(default=True)
    saturday = models.BooleanField(default=True)
    sunday = models.BooleanField(default=True)

    start_time = models.TimeField(
        help_text=(
            "Local start time (HH:MM). "
            "If Start time and End time are both set to 00:00, the schedule runs for the entire day."
        )
    )

    end_time = models.TimeField(
        help_text=(
            "Local end time (HH:MM). "
            "If later than Start time, the schedule runs within the same day. "
            "If earlier than Start time, the schedule runs overnight. "
            "If Start time and End time are both set to 00:00, the schedule runs for the entire day."
        )
    )

    date_from = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: only apply from this date (inclusive).",
    )
    date_to = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: only apply up to this date (inclusive).",
    )

    # auto add & read-only (via admin): creation timestamp
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.channel.name})"

    def weekdays_text(self) -> str:
        """
        Human-readable summary of which days are enabled, in Sat→Fri order.
        """
        parts = []
        if self.saturday:
            parts.append("Sat")
        if self.sunday:
            parts.append("Sun")
        if self.monday:
            parts.append("Mon")
        if self.tuesday:
            parts.append("Tue")
        if self.wednesday:
            parts.append("Wed")
        if self.thursday:
            parts.append("Thu")
        if self.friday:
            parts.append("Fri")
        return ", ".join(parts) if parts else "—"

    weekdays_text.short_description = "Days"

    def is_active_now(self, now: datetime.datetime) -> bool:
        """
        Check if this recurring schedule should be active at the given 'now' (local time).

        Semantics:
        - If start_time == end_time  -> active the FULL day for selected weekdays.
        - If start_time < end_time   -> active between start and end on the same day.
        - If start_time > end_time   -> overnight window (e.g. 20:00 -> 06:00 next day).
        """
        if not self.enabled:
            return False

        local_date = now.date()
        local_time = now.time()
        weekday = now.weekday()  # Monday=0, Sunday=6

        # Date range check
        if self.date_from and local_date < self.date_from:
            return False
        if self.date_to and local_date > self.date_to:
            return False

        # Weekday check via booleans (note: weekday() is Mon=0..Sun=6)
        weekday_flags = [
            self.monday,  # 0
            self.tuesday,  # 1
            self.wednesday,  # 2
            self.thursday,  # 3
            self.friday,  # 4
            self.saturday,  # 5
            self.sunday,  # 6
        ]
        if not weekday_flags[weekday]:
            return False

        # Time window semantics
        if self.start_time == self.end_time:
            # Full-day: active 24 hours for the selected weekdays
            return True

        if self.start_time < self.end_time:
            # Normal daytime window: e.g. 08:00 -> 20:00
            return self.start_time <= local_time < self.end_time
        else:
            # Overnight window: e.g. 20:00 -> 06:00
            return (local_time >= self.start_time) or (local_time < self.end_time)


class TimeShiftProfile(models.Model):
    """
    Configuration for a delayed (time-shifted) output for a channel.
    This defines:
      - how much delay (in minutes)
      - where to restream (udp_ts URL)
    Playback logic will read this config.
    """
    channel = models.OneToOneField(
        Channel,
        on_delete=models.CASCADE,
        related_name="timeshift_profile",
    )
    enabled = models.BooleanField(default=False)

    delay_minutes = models.PositiveIntegerField(
        default=180,
        help_text="Delay between live input and delayed output, in minutes (e.g. 180 = 3 hours).",
    )

    output_udp_url = models.CharField(
        max_length=512,
        help_text="UDP TS URL for delayed output, e.g. udp://239.0.0.10:2001?ttl=1&pkt_size=1316",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time-shift profile"
        verbose_name_plural = "Time-shift profiles"

    def __str__(self) -> str:
        return f"TimeShift({self.channel.name}, {self.delay_minutes} min)"
