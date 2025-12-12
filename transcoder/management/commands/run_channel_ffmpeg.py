import subprocess
from django.core.management.base import BaseCommand, CommandError

from transcoder.ffmpeg_runner import FFmpegJobConfig
from transcoder.models import Channel


class Command(BaseCommand):
    help = "Run a single ffmpeg job for a channel (live_forward, record, or playback)."

    def add_arguments(self, parser):
        parser.add_argument("channel_id", type=int)
        parser.add_argument(
            "--purpose",
            choices=["live_forward", "record", "playback"],
            default="live_forward",
            help="Type of job to run: live_forward, record, or playback.",
        )

    def handle(self, *args, **options):
        channel_id = options["channel_id"]
        purpose = options["purpose"]

        try:
            chan = Channel.objects.get(pk=channel_id)
        except Channel.DoesNotExist:
            raise CommandError(f"Channel with id={channel_id} does not exist.")

        job = FFmpegJobConfig(channel=chan, purpose=purpose)
        cmd_list = job.build_command()

        self.stdout.write(f"Running FFmpeg:\n{' '.join(cmd_list)}")

        import subprocess
        proc = subprocess.Popen(cmd_list)
        proc.wait()

        self.stdout.write("FFmpeg stopped.")