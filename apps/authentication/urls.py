# -*- encoding: utf-8 -*-
"""Authentication routes, including verified email-link password resets."""

from django.urls import path
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views
from .views import login_view, register_user, logout_view

urlpatterns = [
    path("login/", login_view, name="login"),
    path("register/", register_user, name="register"),
    path("logout/", logout_view, name="logout"),
    path(
        "forgot_password/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/forgot_password.html",
            email_template_name="accounts/password_reset_email.txt",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url="/password_reset_sent/",
        ),
        name="forgot_password",
    ),
    path(
        "password_reset_sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_sent.html",
        ),
        name="password_reset_done",
    ),
    # The old /reset_password/ route must never accept password changes.
    path(
        "reset_password/",
        RedirectView.as_view(pattern_name="forgot_password", permanent=False),
        name="reset_password",
    ),
    path(
        "reset_password/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/reset_password.html",
            success_url="/password_reset_complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password_reset_complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
]
