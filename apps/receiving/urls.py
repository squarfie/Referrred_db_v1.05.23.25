from django.urls import path

from . import views


urlpatterns = [
    path("", views.receiving_dashboard, name="receiving_dashboard"),
    path("new/", views.receiving_create, name="receiving_create"),
    path("<uuid:receiving_id>/delete-permanently/", views.receiving_delete_permanently, name="receiving_delete_permanently"),
    path("<uuid:receiving_id>/", views.receiving_detail, name="receiving_detail"),
    path("<uuid:receiving_id>/qr.png", views.receiving_qr, name="receiving_qr"),
    path("<uuid:receiving_id>/label/", views.receiving_label, name="receiving_label"),
    path("<uuid:receiving_id>/endorse/", views.receiving_endorse, name="receiving_endorse"),
    path(
        "<uuid:receiving_id>/laboratory-receipt/",
        views.receiving_lab_confirm,
        name="receiving_lab_confirm",
    ),
]
