from django.urls import path
from . import scan_views, views, workflow_views, prebatch_views

urlpatterns = [
    path("", views.receiving_dashboard, name="receiving_dashboard"),
    path("secretariat/", views.secretariat_receiving_dashboard, name="secretariat_receiving_dashboard"),
    path("new/", views.receiving_create, name="receiving_create"),
    path("workflow/", workflow_views.workflow_dashboard, name="receiving_workflow_dashboard"),
    path("role-handoffs/", workflow_views.role_handoffs, name="receiving_role_handoffs"),
    path("role-handoffs/<int:batch_id>/", workflow_views.role_handoff_detail, name="receiving_role_handoff_detail"),
    path("dmu-handoff/<int:handoff_id>/confirm/", prebatch_views.confirm_dmu_handoff, name="receiving_dmu_confirm"),
    path("dmu-handoff/<int:handoff_id>/create-batch/", prebatch_views.create_dmu_handoff_batch, name="receiving_dmu_create_batch"),
    path("workflow/<int:batch_id>/", workflow_views.workflow_detail, name="receiving_workflow_detail"),
    path("workflow/<int:batch_id>/endorse/", workflow_views.workflow_endorse, name="receiving_workflow_endorse"),
    path("workflow/<int:batch_id>/confirm/", workflow_views.workflow_confirm, name="receiving_workflow_confirm"),
    path("workflow/<int:batch_id>/undo/", workflow_views.workflow_undo, name="receiving_workflow_undo"),
    path("<uuid:receiving_id>/delete-permanently/", views.receiving_delete_permanently, name="receiving_delete_permanently"),
    path("<uuid:receiving_id>/", views.receiving_detail, name="receiving_detail"),
    path("<uuid:receiving_id>/qr.png", views.receiving_qr, name="receiving_qr"),
    path("<uuid:receiving_id>/scan/", scan_views.receiving_scan, name="receiving_scan"),
    path("<uuid:receiving_id>/label/", views.receiving_label, name="receiving_label"),
    path("<uuid:receiving_id>/endorse/", views.receiving_endorse, name="receiving_endorse"),
]
