from __future__ import annotations

import datetime
from django.db import models
from django.utils import timezone
from django.core.validators import MaxValueValidator


class InputType(models.TextChoices):
    MULTICAST_UDP = "udp_multicast", "UDP Multicast (MPEG-TS)"
    RTSP = "rtsp", "RTSP"
    RTMP = "rtmp", "RTMP"
    FILE = "file", "File"
    INTERNAL_GENERATOR = "internal_gen", "Internal Generator (Test)"


class OutputType(models.TextChoices):
    UDP_TS = "udp_ts", "UDP TS Unicast"
    HLS = "hls", "HLS (m3u8)"
    RTMP = "rtmp", "RTMP"
    FILE_TS = "file_ts", "File (TS)"
    FILE_MP4 = "file_mp4", "File (MP4)"


class VideoMode(models.TextChoices):
    COPY = "copy", "Copy (no re-encode)"
    ENCODE = "encode", "Encode (re-encode)"


class AudioMode(models.TextChoices):
    COPY = "copy", "Copy (no re-encode)"
    ENCODE = "encode", "Encode (re-encode)"
    DISABLE = "disable", "Disable (no audio)"


class JobPurpose(models.TextChoices):
    RECORD = "record", "Record"
    PLAYBACK = "playback", "Playback (time-shift)"


class Channel(models.Model):
    """
    v3 baseline:
    - Single mode: Record + Time-shift (delayed playback) with ONE output target.
    - Recording writes .ts segments to disk.
    - Playback restreams from recorded segments according to delay (TimeShiftProfile).
    """

    name = models.CharField(max_length=100, unique=True)

    is_test_channel = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Marks a channel as an internal test channel (created from admin tools).",
    )

    enabled = models.BooleanField(
        default=True,
        help_text="If off, enforcer will not run this channel.",
    )

    # Input
    input_type = models.CharField(
        max_length=20,
        choices=InputType.choices,
        default=InputType.MULTICAST_UDP,  # ✅ default prevents migration prompt
    )

    input_url = models.CharField(
        max_length=512,
        default="udp://@239.0.0.1:5000",  # ✅ safe placeholder for existing rows
        help_text=(
            "For multicast: e.g. udp://@239.10.10.10:5001 "
            "(fifo_size & overrun options may be added automatically). "
            "For Internal Generator, this can be internal://generator."
        ),
    )

    multicast_interface = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional: network interface/IP for multicast receiving (advanced usage).",
    )

    # Output (ONE target only)
    output_type = models.CharField(
        max_length=20,
        choices=OutputType.choices,
        default=OutputType.UDP_TS,  # ✅ default prevents migration prompt
    )

    output_target = models.CharField(
        max_length=512,
        default="udp://127.0.0.1:5002",  # ✅ default prevents migration prompt
        help_text=(
            "Destination for the channel output.\n\n"
            "✅ Unicast (send to one receiver):\n"
            "  udp://10.120.0.111:5000?pkt_size=1316\n\n"
            "✅ Multicast (send to many receivers):\n"
            "  udp://239.0.0.1:5000?pkt_size=1316&ttl=16\n\n"
            "Notes:\n"
            "• You can enter a multicast group (239.x/232.x) even if you think of it as “unicast mode” — "
            "it’s still the same UDP-TS output; only the destination IP changes.\n"
            "• Useful options: pkt_size=1316, ttl=… (multicast), localaddr=… (force interface, helpful on Windows).\n"
        )
        ,
    )

    # Tail behavior (your earlier requirement)
    playback_tail_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled: when schedule ends, recording stops immediately but playback continues "
            "until (schedule_end + delay). Useful to flush the delayed buffer."
        ),
    )

    # Recording
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
        help_text="If enabled, old recording segments will be deleted automatically.",
    )

    auto_delete_after_segments = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Keep last N segments. Leave blank to ignore.",
    )

    auto_delete_after_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Delete segments older than N days. Leave blank to ignore.",
    )

    # Transcoding / copy settings
    video_mode = models.CharField(max_length=20, choices=VideoMode.choices, default=VideoMode.COPY)
    video_codec = models.CharField(max_length=50, blank=True, default="", help_text="When encoding video, e.g. libx264")

    audio_mode = models.CharField(max_length=20, choices=AudioMode.choices, default=AudioMode.COPY)
    audio_codec = models.CharField(max_length=50, blank=True, default="", help_text="When encoding audio, e.g. aac")

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
        help_text="Local start time. If blank, treated as 00:00.",
    )

    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Local end time. If blank, treated as 00:00. If start == end -> full day.",
    )

    date_from = models.DateField(null=True, blank=True, help_text="First active date (inclusive).")
    date_to = models.DateField(null=True, blank=True, help_text="Last active date (inclusive).")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def is_active_now(self, now: datetime.datetime | None = None) -> bool:
        """
        Schedule semantics:
        - If start_time == end_time (and not null)  -> active FULL day for selected weekdays.
        - If start_time < end_time                  -> active between start and end.
        - If start_time > end_time                  -> overnight window (e.g. 20:00 -> 06:00).
        - If start_time or end_time is null         -> treated as 00:00.
        """
        if not self.enabled:
            return False

        if now is None:
            now = timezone.localtime()

        local_date = now.date()
        local_time = now.time()

        # Date range check
        if self.date_from and local_date < self.date_from:
            return False
        if self.date_to and local_date > self.date_to:
            return False

        # Weekday check
        weekday = local_date.weekday()  # Mon=0..Sun=6
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

        start_time = self.start_time or datetime.time(0, 0)
        end_time = self.end_time or datetime.time(0, 0)

        if start_time == end_time:
            return True

        if start_time < end_time:
            return start_time <= local_time < end_time

        # Overnight
        return (local_time >= start_time) or (local_time < end_time)


# class TimeShiftProfile(models.Model):
#     """
#     Delay configuration for a channel.
#     Output destination is channel.output_target (single output design).
#     """
#     channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="timeshift_profile")
#     enabled = models.BooleanField(default=False)
#
#     delay_minutes = models.PositiveIntegerField(
#         default=60,
#         validators=[MaxValueValidator(24 * 60)],  # 0..1440
#         help_text="Delay amount in minutes (0..1440).",
#     )
#
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)
#
#     class Meta:
#         verbose_name = "Time-shift profile"
#         verbose_name_plural = "Time-shift profiles"
#
#     def __str__(self) -> str:
#         return f"TimeShift({self.channel.name}, {self.delay_minutes} min)"
# models.py

class TimeShiftProfile(models.Model):
    channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="timeshift_profile")
    enabled = models.BooleanField(default=False)

    delay_seconds = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(24 * 60 * 60)],  # 0..86400
        help_text=(
            "Delay in seconds (0..86400). "
            "0 = LIVE mode (no recording, direct restream from input to output). "
            ">0 = Time-shift mode (records to disk then plays back with this delay)."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time-shift profile"
        verbose_name_plural = "Time-shift profiles"

    def __str__(self) -> str:
        return f"TimeShift({self.channel.name}, {self.delay_seconds}s)"
