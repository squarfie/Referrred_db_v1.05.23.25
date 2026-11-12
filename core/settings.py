# -*- encoding: utf-8 -*-
"""
Django settings
"""

import os
from decouple import config
from unipath import Path as Unipath

# Build paths inside the project
BASE_DIR = Unipath(__file__).ancestor(2)
CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG')


# Allowed Hosts
ALLOWED_HOSTS = [
    config('SERVER', default=''),  # Use SERVER from .env if exists
    'localhost',
    '127.0.0.1',
    '10.10.103.54',
    'mariseru.pythonanywhere.com',  # Your PythonAnywhere domain
]
# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.home',  # Your custom app
    'apps.authentication.apps.AuthenticationConfig',
    'widget_tweaks',
    'phonenumber_field',
    'phonenumbers',
    'apps.wgs_app.apps.WgsAppConfig', 
    'apps.home_final.apps.HomeFinalConfig',
    'apps.verification.apps.VerificationConfig',
    "apps.receiving.apps.ReceivingConfig",

    # "apps.wgs_app",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Serve static files with WhiteNoise
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'      # this sends users to the home app (dashboard)
LOGOUT_REDIRECT_URL = 'home'

# Rollout safety: keep disabled until legitimate legacy users are linked to ARSP staff.
ACCOUNT_APPROVAL_REQUIRE_STAFF_LINK = True



TEMPLATE_DIR = os.path.join(CORE_DIR, "apps/templates")
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # 'DIRS': [TEMPLATE_DIR],
        # 'DIRS': [BASE_DIR / "apps" / "templates"],
        'DIRS': [os.path.join(CORE_DIR, "apps/templates")],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.home.context_processors.user_access',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT', cast=int),
    }
}

# Password reset email delivery: set these values in the deployment .env.
# The SMTP backend is used by default. Never use console/file/dummy backends in production.
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@localhost")
# Reset tokens are one-time in practice: password changes invalidate them.
PASSWORD_RESET_TIMEOUT = 3600  # 1 hour

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

STATIC_ROOT = config('STATIC_ROOT')
STATICFILES_DIRS = [config('STATICFILES_DIRS')]

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = config('MEDIA_ROOT')


# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

if not DEBUG:
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

TIME_ZONE = "Asia/Manila"


# Allow more form fields for large antibiotic datasets
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
PHONENUMBER_DEFAULT_REGION = 'PH'

# Canonical public origin used by printable receiving QR labels. Configure this
# in production so labels remain stable regardless of the host used to print.
RECEIVING_QR_BASE_URL = config("RECEIVING_QR_BASE_URL", default="").rstrip("/")

# # logout automatically after 15 minutes of inactivity
# SESSION_COOKIE_AGE = 900
# # Set SESSION_SAVE_EVERY_REQUEST to True to reset the session expiration time on every request
# SESSION_SAVE_EVERY_REQUEST = True

