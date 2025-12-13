from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone

from .models import Channel, InputType, OutputType, TimeShiftProfile


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    change_list_template = "admin/transcoder/channel/change_list.html"

    list_display = (
        "name",
        "enabled",
        "input_type",
        "input_url",
        "output_type",
        "output_target",
        "schedule_summary",
        "auto_delete_enabled",
        "auto_delete_after_segments",
        "auto_delete_after_days",
        "created_at",
    )
    list_filter = ("enabled", "input_type", "output_type", "auto_delete_enabled")
    search_fields = ("name", "input_url", "output_target")

    fieldsets = (
        ("Basics", {"fields": ("name", "enabled", "is_test_channel")}),
        ("Input", {"fields": ("input_type", "input_url", "multicast_interface")}),
        ("Output", {"fields": ("output_type", "output_target")}),
        ("Schedule", {
            "fields": (
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
                ("start_time", "end_time"),
                ("date_from", "date_to"),
            )
        }),
        ("Recording & Auto-delete", {
            "fields": (
                "recording_path_template",
                "recording_segment_minutes",
                "auto_delete_enabled",
                "auto_delete_after_segments",
                "auto_delete_after_days",
            )
        }),
        ("Transcoding", {"fields": (("video_mode", "audio_mode"), "video_codec", "audio_codec")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    readonly_fields = ("created_at", "updated_at")

    # ------------------------
    # Admin tools (no JS)
    # ------------------------
    def get_urls(self):
        """
        IMPORTANT: our custom URLs must come BEFORE the default ModelAdmin URLs,
        otherwise 'tools/...' can be interpreted as <object_id>.
        """
        urls = super().get_urls()
        my_urls = [
            path(
                "tools/create-test/",
                self.admin_site.admin_view(self.create_test_channel_view),
                name="transcoder_channel_create_test",
            ),
            path(
                "tools/delete-test/",
                self.admin_site.admin_view(self.delete_test_channel_view),
                name="transcoder_channel_delete_test",
            ),
            path(
                "tools/start-test/",
                self.admin_site.admin_view(self.start_test_channel_view),
                name="transcoder_channel_start_test",
            ),
            path(
                "tools/stop-test/",
                self.admin_site.admin_view(self.stop_test_channel_view),
                name="transcoder_channel_stop_test",
            ),
        ]
        return my_urls + urls

    def _changelist_redirect(self) -> HttpResponseRedirect:
        return HttpResponseRedirect(reverse("admin:transcoder_channel_changelist"))

    def _ensure_superuser(self, request: HttpRequest) -> bool:
        if not request.user.is_superuser:
            self.message_user(request, "Superuser permission required.", level=messages.ERROR)
            return False
        return True

    def _test_output_url(self) -> str:
        return getattr(settings, "TEST_CHANNEL_OUTPUT_URL", "udp://127.0.0.1:5002")

    def _get_test_channel(self):
        return Channel.objects.filter(is_test_channel=True).order_by("-id").first()

    def _build_test_defaults(self):
        now = timezone.localtime()
        today = now.date()
        return {
            "name": "__TEST__ Internal Generator",
            "enabled": False,
            "is_test_channel": True,
            "input_type": InputType.INTERNAL_GENERATOR,
            "input_url": "internal://generator",
            "output_type": OutputType.UDP_TS,
            "output_target": self._test_output_url(),
            "recording_segment_minutes": 1,
            "auto_delete_enabled": True,
            "auto_delete_after_segments": 5,
            "auto_delete_after_days": None,
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": True,
            "start_time": None,
            "end_time": None,
            "date_from": today,
            "date_to": today + timedelta(days=2),
        }

    def create_test_channel_view(self, request: HttpRequest):
        if request.method != "POST":
            return self._changelist_redirect()
        if not self._ensure_superuser(request):
            return self._changelist_redirect()

        existing = self._get_test_channel()
        if existing:
            self.message_user(
                request,
                f"Test channel already exists (id={existing.id}).",
                level=messages.WARNING,
            )
            return self._changelist_redirect()

        chan = Channel.objects.create(**self._build_test_defaults())
        TimeShiftProfile.objects.create(channel=chan, enabled=True, delay_minutes=1)

        self.message_user(
            request,
            f"Created test channel (id={chan.id}) output={chan.output_target}. "
            f"Now click 'Start test channel' and open VLC: {chan.output_target}",
            level=messages.SUCCESS,
        )
        return self._changelist_redirect()

    def delete_test_channel_view(self, request: HttpRequest):
        if request.method != "POST":
            return self._changelist_redirect()
        if not self._ensure_superuser(request):
            return self._changelist_redirect()

        chan = self._get_test_channel()
        if not chan:
            self.message_user(request, "No test channel found.", level=messages.WARNING)
            return self._changelist_redirect()

        cid = chan.id
        chan.delete()
        self.message_user(request, f"Deleted test channel (id={cid}).", level=messages.SUCCESS)
        return self._changelist_redirect()

    def start_test_channel_view(self, request: HttpRequest):
        if request.method != "POST":
            return self._changelist_redirect()
        if not self._ensure_superuser(request):
            return self._changelist_redirect()

        chan = self._get_test_channel()
        if not chan:
            self.message_user(request, "No test channel found. Create it first.", level=messages.ERROR)
            return self._changelist_redirect()

        chan.enabled = True
        chan.save(update_fields=["enabled"])

        self.message_user(
            request,
            f"Started test channel (id={chan.id}). If enforcer is running, record+playback should start.",
            level=messages.SUCCESS,
        )
        return self._changelist_redirect()

    def stop_test_channel_view(self, request: HttpRequest):
        if request.method != "POST":
            return self._changelist_redirect()
        if not self._ensure_superuser(request):
            return self._changelist_redirect()

        chan = self._get_test_channel()
        if not chan:
            self.message_user(request, "No test channel found.", level=messages.WARNING)
            return self._changelist_redirect()

        chan.enabled = False
        chan.save(update_fields=["enabled"])

        self.message_user(
            request,
            f"Stopped test channel (id={chan.id}). Enforcer should terminate ffmpeg shortly.",
            level=messages.SUCCESS,
        )
        return self._changelist_redirect()

    # ------------------------
    # Display helper
    # ------------------------
    @admin.display(description="Schedule")
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
            time_text = "Full day"

        date_from = obj.date_from.isoformat() if obj.date_from else "any"
        date_to = obj.date_to.isoformat() if obj.date_to else "any"
        return f"{days_text} {time_text} [{date_from} → {date_to}]"
