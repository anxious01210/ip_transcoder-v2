# transcoder/admin_views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from .models import Channel


@staff_member_required
def transcoder_overview(request):
    """
    High-level overview of all channels in the system.

    This v3 view is channel-centric:
    - each Channel has an input, an output, and a built-in weekly schedule.
    - every channel can optionally publish a delayed UDP stream based on its own recordings.
    """
    channels = Channel.objects.all()

    context = {
        "title": "IP Transcoder Overview (v3)",
        "channels": channels,
    }
    return render(request, "admin/transcoder/overview.html", context)

