# transcoder/management/commands/show_ffmpeg_cmd.py
from django.core.management.base import BaseCommand, CommandError

from transcoder.ffmpeg_runner import build_ffmpeg_cmd_for_channel
from transcoder.models import Channel


class Command(BaseCommand):
    help = "Show the ffmpeg command that would be used for a given Channel."

    def add_arguments(self, parser):
        parser.add_argument("channel_id", type=int, help="ID of the Channel")

    def handle(self, *args, **options):
        channel_id = options["channel_id"]

        # Validate that the channel exists
        try:
            chan = Channel.objects.get(pk=channel_id)
        except Channel.DoesNotExist:
            raise CommandError(f"Channel with id={channel_id} does not exist.")

        cmd = build_ffmpeg_cmd_for_channel(channel_id, purpose="live_forward")
        self.stdout.write(self.style.SUCCESS(f"Channel: {chan.name}"))
        self.stdout.write("FFmpeg command:")
        self.stdout.write(cmd)
