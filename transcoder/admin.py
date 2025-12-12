from django.contrib import admin

from .models import Channel


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "enabled",
        "input_type",
        "input_url",
        "output_type",
        "output_target",
        "schedule_summary",
        "auto_delete_enabled",
        "auto_delete_after_days",
        "created_at",
    )
    list_filter = (
        "enabled",
        "input_type",
        "output_type",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    search_fields = ("name", "input_url", "output_target")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Basic", {
            "fields": ("name", "enabled"),
        }),
        ("Input", {
            "fields": ("input_type", "input_url"),
        }),
        ("Output", {
            "fields": ("output_type", "output_target"),
        }),
        ("Processing", {
            "fields": ("hardware_preference", "video_mode", "audio_mode", "video_codec", "audio_codec", "video_bitrate"),
        }),
        ("Schedule", {
            "description": (
                "Define when this channel is active. "
                "If start and end are both 00:00, the channel will run the entire day "
                "for the selected weekdays, within the optional date range."
            ),
            "fields": (
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
                ("start_time", "end_time"),
                ("date_from", "date_to"),
            ),
        }),
        ("Recording & Auto-delete", {
            "fields": (
                "record_enabled",
                "recording_path_template",
                "recording_segment_minutes",
                "auto_delete_enabled",
                "auto_delete_after_segments",
                "auto_delete_after_days",
            ),
        }),
        ("Time-shift / Playback", {
            "description": (
                "Configure delay and UDP output for time-shift playback. "
                "If left blank, the channel will only record to disk."
            ),
            "fields": (
                "timeshift_delay_minutes",
                "timeshift_output_udp_url",
            ),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def schedule_summary(self, obj: Channel) -> str:
        days = []
        if obj.monday:
            days.append("Mon")
        if obj.tuesday:
            days.append("Tue")
        if obj.wednesday:
            days.append("Wed")
        if obj.thursday:
            days.append("Thu")
        if obj.friday:
            days.append("Fri")
        if obj.saturday:
            days.append("Sat")
        if obj.sunday:
            days.append("Sun")
        days_text = ",".join(days) if days else "-"
        if obj.start_time and obj.end_time:
            time_text = f"{obj.start_time.strftime('%H:%M')}–{obj.end_time.strftime('%H:%M')}"
        else:
            time_text = "00:00–00:00 (full day)"
        date_from = obj.date_from.isoformat() if obj.date_from else "any"
        date_to = obj.date_to.isoformat() if obj.date_to else "any"
        return f"{days_text} {time_text} [{date_from} → {date_to}]"

    schedule_summary.short_description = "Schedule"

