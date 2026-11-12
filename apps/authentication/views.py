# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from .forms import *
from django.contrib import messages
from django.contrib.auth.models import User
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from apps.home.models import UserProfile, arsStaff_Details
from .models import UserApproval


def login_view(request):
    form = LoginForm(request.POST or None)
    msg = None

    if request.method == "POST":
        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user = authenticate(username=username, password=password)
            if user is not None:
                if (
                    not user.is_superuser
                    and getattr(settings, "ACCOUNT_APPROVAL_REQUIRE_STAFF_LINK", False)
                    and not arsStaff_Details.objects.filter(User_Account=user).exists()
                ):
                    msg = "Your account is not linked to an approved ARSP staff record yet."
                    return render(request, "accounts/login.html", {"form": form, "msg": msg, "next": request.POST.get("next") or request.GET.get("next", "")})
                login(request, user)
                if request.POST.get("remember_me") == "on":
                    request.session.set_expiry(60 * 60 * 24 * 30)
                else:
                    request.session.set_expiry(0)
                next_url = request.POST.get("next") or request.GET.get("next")
                if next_url and url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("/")
            elif User.objects.filter(username=username).exists():
                account = User.objects.filter(username=username).first()
                if account and not account.is_active:
                    approval = getattr(account, "account_approval", None)
                    if approval and approval.status == UserApproval.STATUS_DECLINED:
                        msg = "Your registration was declined. Please contact an administrator."
                    else:
                        msg = "Your account is awaiting administrator approval."
                else:
                    msg = 'Password is incorrect for this username.'
            else:
                msg = 'Username not found.'
        else:
            msg = 'Error validating the form'

    return render(request, "accounts/login.html", {"form": form, "msg": msg})


def register_user(request):
    msg = None
    success = False

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            form.save_m2m()
            UserApproval.objects.create(
                user=user,
                status=UserApproval.STATUS_PENDING,
            )
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"Middle_Name": form.cleaned_data.get("middle_name", "")},
            )

            msg = (
                "Registration successful. Your account is awaiting administrator approval. "
                "You will be able to log in after your account has been approved."
            )
            success = True
        else:
            msg = 'Form is not valid'
    else:
        form = SignUpForm()

    return render(request, "accounts/register.html", {"form": form, "msg": msg, "success": success})


# Logging out
def logout_view(request):
    logout(request)  # Logs out the user
    return redirect("login")
