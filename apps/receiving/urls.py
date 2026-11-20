from django.urls import path

from . import views


urlpatterns = [
    path("", views.receiving_dashboard, name="receiving_dashboard"),
    path("laboratory-register/", views.laboratory_receiving_register, name="laboratory_receiving_register"),
    path("laboratory-register/pdf/", views.laboratory_receiving_register_pdf, name="laboratory_receiving_register_pdf"),
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
    path("<uuid:receiving_id>/laboratory/", views.laboratory_receiving_workspace, name="laboratory_receiving_workspace"),
    path("<uuid:receiving_id>/laboratory/accessions/add/", views.laboratory_accession_add, name="laboratory_accession_add"),
    path("laboratory/accessions/<int:accession_id>/edit/", views.laboratory_accession_edit, name="laboratory_accession_edit"),
    path("laboratory/accessions/<int:accession_id>/delete/", views.laboratory_accession_delete, name="laboratory_accession_delete"),
    path("laboratory/accessions/<int:accession_id>/organisms/save/", views.laboratory_organism_matrix_save, name="laboratory_organism_matrix_save"),
    path("laboratory/accessions/<int:accession_id>/organisms/add/", views.laboratory_organism_add, name="laboratory_organism_add"),
    path("laboratory/accessions/<int:accession_id>/create-batch/", views.laboratory_batch_create, name="laboratory_batch_create"),
]
