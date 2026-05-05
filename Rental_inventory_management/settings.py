"""
Django settings for Rental_inventory_management project.
"""

import os
from pathlib import Path
import cloudinary
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-!u1(+cionufj+$8fdnd#)&c^!yncrrp3pt0w($+pr-elonhwfe'
DEBUG = True
ALLOWED_HOSTS = []

# Cloudinary direct SDK config
cloudinary.config(
    cloud_name="dvemtlkjh",
    api_key="679749273824336",
    api_secret="t4LpyFrIjqUPJ2stsBvDwHbLcA0",
    secure=True,
)

# Optional: keep this only if you also use django-cloudinary-storage elsewhere
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': "dvemtlkjh",
    'API_KEY': "679749273824336",
    'API_SECRET': "t4LpyFrIjqUPJ2stsBvDwHbLcA0",
}

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'inventory',
    # optional, only if installed:
    # 'cloudinary',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'Rental_inventory_management.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'Rental_inventory_management.wsgi.application'

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Production (or when env var is set)
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=not DEBUG,
        )
    }
else:
    # Local development PostgreSQL
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "rental_inventory_sound",
            "USER": "postgres",
            "PASSWORD": "kvan",
            "HOST": "localhost",
            "PORT": "5432",
        }
    }


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

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')