from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

from .models import Channel


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    ordering = ("name",)
    list_per_page = 50

    list_display = (
        "name",
        "enabled",
        "input_type",
        "input_url",
        "output_type",
        "output_url",
        "delay_seconds",
        "playback_tail_enabled",
        "recording_segment_minutes",
        "auto_delete_enabled",
        "auto_delete_after_segments",
        "auto_delete_after_days",
        "schedule_summary",
        "created_at",
    )

    list_filter = (
        "enabled",
        "input_type",
        "output_type",
        "auto_delete_enabled",
        "playback_tail_enabled",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )

    search_fields = ("name", "input_url", "output_url")

    readonly_fields = (
        "created_at",
        "updated_at",
        "record_log",
        "playback_log",
    )

    fieldsets = (
        ("Basic", {
            "fields": (("name", "enabled"),),
        }),
        ("Input", {
            "fields": (
                ("input_type",),
                ("input_url",),
                ("multicast_interface",),
            ),
        }),
        ("Output", {
            "description": "Single output URL. delay_seconds controls how far back we play from recordings.",
            "fields": (
                ("output_type", "output_url"),
                ("delay_seconds", "playback_tail_enabled"),
            ),
        }),
        ("Processing", {
            "fields": (
                ("hardware_preference",),
                ("video_mode", "audio_mode"),
                ("video_codec", "audio_codec"),
                ("video_bitrate",),
                ("target_width", "target_height"),
            ),
        }),
        ("Schedule", {
            "description": (
                "If start_time == end_time (including both empty), the channel runs full-day "
                "for the selected weekdays, within the optional date range."
            ),
            "fields": (
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
                ("start_time", "end_time"),
                ("date_from", "date_to"),
            ),
        }),
        ("Recording & Auto-delete", {
            "description": "Segments are .ts files. Deletion can be by segment count, by age (days), or both.",
            "fields": (
                ("record_enabled",),
                ("recording_path_template",),
                ("recording_segment_minutes",),
                ("auto_delete_enabled",),
                ("auto_delete_after_segments", "auto_delete_after_days"),
            ),
        }),
        ("Logs", {
            "description": "FFmpeg logs written by the enforcer under media/ffmpeg_logs/.",
            "fields": (
                ("record_log", "playback_log"),
            ),
        }),
        ("Timestamps", {
            "fields": (("created_at", "updated_at"),),
        }),
    )

    # -----------------------------
    # Schedule summary
    # -----------------------------
    @admin.display(description="Schedule")
    def schedule_summary(self, obj: Channel) -> str:
        days = []
        if obj.monday: days.append("Mon")
        if obj.tuesday: days.append("Tue")
        if obj.wednesday: days.append("Wed")
        if obj.thursday: days.append("Thu")
        if obj.friday: days.append("Fri")
        if obj.saturday: days.append("Sat")
        if obj.sunday: days.append("Sun")
        days_text = ",".join(days) if days else "-"

        start = obj.start_time.strftime("%H:%M") if obj.start_time else "00:00"
        end = obj.end_time.strftime("%H:%M") if obj.end_time else "00:00"
        time_text = f"{start}–{end}"

        date_from = obj.date_from.isoformat() if obj.date_from else "any"
        date_to = obj.date_to.isoformat() if obj.date_to else "any"
        return f"{days_text} {time_text} [{date_from} → {date_to}]"

    # -----------------------------
    # Log links (media-served files)
    # -----------------------------
    def _safe_name(self, name: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)

    def _log_paths(self, obj: Channel, purpose: str) -> tuple[Path, Path]:
        safe_name = self._safe_name(obj.name)
        rel = Path("ffmpeg_logs") / f"{safe_name}_{purpose}.log"
        abs_path = Path(settings.MEDIA_ROOT) / rel
        return rel, abs_path

    def _log_link_html(self, rel: Path, abs_path: Path) -> str:
        if not abs_path.exists():
            return "Missing"
        try:
            size_kb = abs_path.stat().st_size // 1024
        except Exception:
            size_kb = "?"
        media_url = settings.MEDIA_URL if settings.MEDIA_URL.endswith("/") else settings.MEDIA_URL + "/"
        url = f"{media_url}{rel.as_posix()}"
        return format_html('<a href="{}" target="_blank">Open</a> ({} KB)', url, size_kb)

    @admin.display(description="Record log")
    def record_log(self, obj: Channel):
        rel, abs_path = self._log_paths(obj, "record")
        return self._log_link_html(rel, abs_path)

    @admin.display(description="Playback log")
    def playback_log(self, obj: Channel):
        rel, abs_path = self._log_paths(obj, "playback")
        return self._log_link_html(rel, abs_path)
