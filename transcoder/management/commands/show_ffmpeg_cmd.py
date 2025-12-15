from django.core.management.base import BaseCommand, CommandError

from transcoder.ffmpeg_runner import FFmpegJobConfig
from transcoder.models import Channel


class Command(BaseCommand):
    help = "Show the ffmpeg command that would be used for a given Channel (v3)."

    def add_arguments(self, parser):
        parser.add_argument("channel_id", type=int, help="ID of the Channel")
        parser.add_argument(
            "--purpose",
            default="record",
            choices=["record", "playback"],
            help="Purpose: record or playback (default: record)",
        )

    def handle(self, *args, **options):
        channel_id = options["channel_id"]
        purpose = options["purpose"]

        try:
            chan = Channel.objects.get(pk=channel_id)
        except Channel.DoesNotExist:
            raise CommandError(f"Channel with id={channel_id} does not exist.")

        cmd = FFmpegJobConfig(channel=chan, purpose=purpose).build_command()

        self.stdout.write(self.style.SUCCESS(f"Channel: {chan.name}"))
        self.stdout.write(f"Purpose: {purpose}")
        self.stdout.write("FFmpeg command:")
        self.stdout.write(" ".join(cmd))
