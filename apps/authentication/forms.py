# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User, AbstractBaseUser


def standardize_person_name(value):
    parts = str(value or "").strip().split()
    if not parts:
        return ""

    def title_piece(piece):
        segments = piece.split("-")
        titled_segments = []
        for segment in segments:
            if not segment:
                titled_segments.append(segment)
                continue
            apostrophe_parts = segment.split("'")
            titled_segments.append("'".join(
                part[:1].upper() + part[1:].lower() if part else part
                for part in apostrophe_parts
            ))
        return "-".join(titled_segments)

    return " ".join(title_piece(part) for part in parts)


class LoginForm(forms.Form):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "placeholder": "Username",
                "class": "form-control",
                "autocomplete": "username",
            }
        ))
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Password",
                "class": "form-control",
                "autocomplete": "current-password",
            }
        ))

class SignUpForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "First Name",
                "class": "form-control"
            }
        ))
    middle_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Middle Name",
                "class": "form-control"
            }
        ))
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Last Name",
                "class": "form-control"
            }
        ))
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "placeholder": "Username",
                "class": "form-control"
            }
        ))
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Email",
                "class": "form-control"
            }
        ))
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Password",
                "class": "form-control"
            }
        ))
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Password check",
                "class": "form-control"
            }
        ))

    class Meta:
        model = User
        fields = ('first_name', 'middle_name', 'last_name', 'username', 'email', 'password1', 'password2')

    def clean_first_name(self):
        return standardize_person_name(self.cleaned_data.get("first_name"))

    def clean_middle_name(self):
        return standardize_person_name(self.cleaned_data.get("middle_name"))

    def clean_last_name(self):
        return standardize_person_name(self.cleaned_data.get("last_name"))

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()


