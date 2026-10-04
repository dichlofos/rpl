from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from tracks.permissions import require_manage

from .forms import JourneyForm, JourneyTracksForm
from .models import TravelReport
from .services import set_report_tracks


def accessible_reports(user):
    reports = TravelReport.objects.select_related("owner", "track_group")
    return reports if user.is_superuser else reports.filter(owner=user)


@login_required
@require_GET
def journey_list(request):
    reports = accessible_reports(request.user).annotate(track_count=Count("track_links"))
    return render(request, "reports/journey_list.html", {"reports": reports})


@login_required
@require_GET
def journey_detail(request, public_id):
    report = get_object_or_404(accessible_reports(request.user), public_id=public_id)
    return render(request, "reports/journey_detail.html", {
        "report": report, "links": report.track_links.select_related("track"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def journey_create(request):
    form = JourneyForm(request.POST if request.method == "POST" else None, user=request.user)
    if request.method == "POST" and form.is_valid():
        report = form.save()
        return redirect("journeys:tracks", public_id=report.public_id)
    return render(request, "tracks/form.html", {
        "title": "Новое путешествие", "form": form,
        "back_url": reverse("journeys:list"), "submit_label": "Создать и выбрать треки",
    })


@login_required
@require_http_methods(["GET", "POST"])
def journey_edit(request, public_id):
    report = get_object_or_404(TravelReport, public_id=public_id)
    require_manage(request.user, report)
    form = JourneyForm(request.POST if request.method == "POST" else None,
                       user=request.user, instance=report)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("journeys:detail", public_id=report.public_id)
    return render(request, "tracks/form.html", {
        "title": "Редактировать путешествие", "form": form,
        "back_url": reverse("journeys:detail", args=[report.public_id]),
        "submit_label": "Сохранить",
    })


@login_required
@require_http_methods(["GET", "POST"])
def journey_tracks(request, public_id):
    report = get_object_or_404(TravelReport, public_id=public_id)
    require_manage(request.user, report)
    form = JourneyTracksForm(request.POST if request.method == "POST" else None, report=report)
    if request.method == "POST" and form.is_valid():
        set_report_tracks(request.user, report.pk, form.cleaned_data["track_ids"])
        return redirect("journeys:detail", public_id=report.public_id)
    return render(request, "reports/journey_tracks.html", {
        "report": report, "form": form, "entries": form.entries(),
    })
