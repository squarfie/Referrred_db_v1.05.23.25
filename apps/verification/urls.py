from django.urls import path

from . import views


urlpatterns = [
    path("", views.verification_dashboard, name="verification_dashboard"),
    path("batch/<int:batch_id>/send/", views.send_batch_to_verifier, name="send_batch_to_verifier"),
    path("case/<int:case_id>/", views.verification_case_detail, name="verification_case_detail"),
    path("case/<int:case_id>/comments/bulk/", views.bulk_case_comment_action, name="bulk_case_comment_action"),
    path("case/<int:case_id>/comments/<int:comment_id>/delete/", views.delete_verification_comment, name="delete_verification_comment"),
    path("case/<int:case_id>/withdraw/", views.withdraw_verification_case, name="withdraw_verification_case"),
    path("final/<int:final_id>/comments/bulk/", views.bulk_final_comment_action, name="bulk_final_comment_action"),
    path("final/<int:final_id>/comments/<int:comment_id>/action/", views.final_comment_action, name="final_comment_action"),
    path("raw/<int:raw_id>/comments/<int:comment_id>/action/", views.raw_comment_action, name="raw_comment_action"),
]
