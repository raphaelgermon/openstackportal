import os
from pathlib import Path
from celery.schedules import crontab
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- LDAP Conditional Import ---
try:
    import ldap
    from django_auth_ldap.config import LDAPSearch, GroupOfNamesType
    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False
    print("WARNING: python-ldap module not found. Running in local/no-LDAP mode.")

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'unsafe-dev-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# Hosts should be space-separated in the environment variable
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost 127.0.0.1 [::1]').split(' ')

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'portal',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'portal/templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database Configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

if os.environ.get('DB_HOST'):
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME'),
        'USER': os.environ.get('DB_USER'),
        'HOST': os.environ.get('DB_HOST'),
        'PORT': 5432,
        'PASSWORD': os.environ.get('DB_PASS'),
    }

# Authentication
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

if HAS_LDAP:
    AUTHENTICATION_BACKENDS.insert(0, 'django_auth_ldap.backend.LDAPBackend')
    
    AUTH_LDAP_SERVER_URI = os.environ.get('LDAP_URI', '')
    AUTH_LDAP_BIND_DN = os.environ.get('LDAP_BIND_DN', '')
    AUTH_LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD', '')

    if AUTH_LDAP_SERVER_URI:
        AUTH_LDAP_USER_SEARCH = LDAPSearch(
            os.environ.get('LDAP_USER_SEARCH_BASE', 'dc=example,dc=com'),
            ldap.SCOPE_SUBTREE,
            "(uid=%(user)s)"
        )
        AUTH_LDAP_USER_ATTR_MAP = {
            "first_name": "givenName",
            "last_name": "sn",
            "email": "mail",
        }

# Static Files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / "portal" / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

CELERY_BEAT_SCHEDULE = {
    'sync-openstack-inventory-every-10-mins': {
        'task': 'portal.tasks.sync_inventory',
        'schedule': crontab(minute='*/10'),
    },
    'check-hardware-health-hourly': {
        'task': 'portal.tasks.collect_hardware_health',
        'schedule': crontab(minute=0, hour='*'),
    },
    'sync-openstack-flavors-daily': {
        'task': 'portal.tasks.sync_flavors',
        'schedule': crontab(minute=0, hour=0),
    },
}

# --- JAZZMIN ADMIN THEME SETTINGS ---
JAZZMIN_SETTINGS = {
    # Project Branding
    "site_title": "OpenStack Portal",
    "site_header": "OpenStack Portal",
    "site_brand": "OpenStack Portal",
    "welcome_sign": "Welcome to OpenStack Portal Admin",
    "copyright": "OpenStack Portal",
    "search_model": "portal.Instance",
    
    # Top Menu
    "topmenu_links": [
        {"name": "Back to Dashboard", "url": "dashboard", "permissions": ["auth.view_user"]},
        {"app": "portal"},
    ],
    
    # UI Customization
    "show_ui_builder": False,
    "navigation_expanded": True,
    "icons": {
        "portal.Cluster": "fas fa-globe",
        "portal.PhysicalHost": "fas fa-server",
        "portal.Instance": "fas fa-cubes",
        "portal.Alert": "fas fa-exclamation-triangle",
        "portal.AuditLog": "fas fa-history",
        "portal.Flavor": "fas fa-list",
        "portal.PortalSettings": "fas fa-cogs",
        "portal.AppVersion": "fas fa-code-branch",
    },
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": True,
    "brand_small_text": False,
    "brand_colour": "navbar-dark",
    "accent": "accent-primary",
    "navbar": "navbar-dark",
    "no_navbar_border": False,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "theme": "darkly",  # Dark mode theme
    "dark_mode_theme": "darkly",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success"
    }
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'