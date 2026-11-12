# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from django.contrib import admin
from django.urls import path, include  # add this

urlpatterns = [
    path('admin/', admin.site.urls),          # Django admin route
    path("", include("apps.authentication.urls")), # Auth routes - login / register
    path("wgs/", include("apps.wgs_app.urls")),
    path("final/", include("apps.home_final.urls")),
    path("verification/", include("apps.verification.urls")),
    path("receiving/", include("apps.receiving.urls")),
    path("", include("apps.home.urls")),             # UI Kits Html files
  
]
