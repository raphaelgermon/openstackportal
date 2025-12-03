import os
from pathlib import Path
from celery.schedules import crontab
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file explicitly
load_dotenv(BASE_DIR / '.env')

# --- LDAP Conditional Setup ---
# We only enable LDAP if the libraries are installed AND the URI is set in .env
try:
    import ldap
    from django_auth_ldap.config import LDAPSearch, GroupOfNamesType
    HAS_LDAP_LIBS = True
except ImportError:
    HAS_LDAP_LIBS = False
    print("WARNING: python-ldap module not found. Running in local/no-LDAP mode.")

# Check if URI is configured
LDAP_CONFIGURED = HAS_LDAP_LIBS and os.environ.get('LDAP_URI')

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
    'django_celery_beat',
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

# --- AUTHENTICATION CONFIGURATION ---
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend', # Default local DB backend
]

# Only inject LDAP backend if fully configured
if LDAP_CONFIGURED:
    AUTHENTICATION_BACKENDS.insert(0, 'django_auth_ldap.backend.LDAPBackend')
    
    AUTH_LDAP_SERVER_URI = os.environ.get('LDAP_URI')
    AUTH_LDAP_BIND_DN = os.environ.get('LDAP_BIND_DN', '')
    AUTH_LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD', '')
    
    # Ensure search base has a default to avoid errors if env var is missing
    search_base = os.environ.get('LDAP_USER_SEARCH_BASE', 'dc=example,dc=com')

    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        search_base,
        ldap.SCOPE_SUBTREE,
        "(uid=%(user)s)"
    )

    AUTH_LDAP_USER_ATTR_MAP = {
        "first_name": "givenName",
        "last_name": "sn",
        "email": "mail",
    }
    
    # --- CONFIGURATION DES GROUPES LDAP ---
    
    # 1. Type de groupe (Adaptez name_attr si besoin, 'cn' est standard)
    AUTH_LDAP_GROUP_TYPE = GroupOfNamesType(name_attr="cn")

    # 2. Où chercher les groupes
    # Modifiez "ou=groups,dc=example,dc=com" pour pointer vers votre OU de groupes
    AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
        os.environ.get('LDAP_GROUP_SEARCH_BASE', search_base),
        ldap.SCOPE_SUBTREE,
        "(objectClass=groupOfNames)" # Utilisez "(objectClass=group)" pour Active Directory
    )

    # 3. Mapping Droits Admin
    # Si un utilisateur est dans ce groupe, il devient Admin Django automatiquement
    AUTH_LDAP_USER_FLAGS_BY_GROUP = {
        "is_active": "cn=portal-users,ou=groups,dc=example,dc=com", # Groupe requis pour se connecter (optionnel)
        "is_staff": "cn=portal-admins,ou=groups,dc=example,dc=com",  # Accès à l'admin Django
        "is_superuser": "cn=portal-admins,ou=groups,dc=example,dc=com" # Droits complets
    }
    
    # Toujours mettre à jour les droits à la connexion
    AUTH_LDAP_ALWAYS_UPDATE_USER = True

    # --- LOGGING LDAP (DEBUG) ---
    # Cela affichera les détails de la connexion LDAP dans la console
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
            },
        },
        "loggers": {
            "django_auth_ldap": {
                "level": "DEBUG",
                "handlers": ["console"],
            },
        },
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

JAZZMIN_SETTINGS = {
    "site_title": "OpenStack Portal",
    "site_header": "OpenStack Portal",
    "site_brand": "OpenStack Portal",
    "welcome_sign": "Welcome to OpenStack Portal Admin",
    "copyright": "OpenStack Portal",
    "search_model": "portal.Instance",
    "topmenu_links": [
        {"name": "Back to Dashboard", "url": "dashboard", "permissions": ["auth.view_user"]},
        {"app": "portal"},
    ],
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
        "portal.Volume": "fas fa-hdd",
        "portal.ServerCostProfile": "fas fa-file-invoice-dollar",
        "django_celery_beat.PeriodicTask": "fas fa-clock",
    },
}

JAZZMIN_UI_TWEAKS = {
    "theme": "darkly",
    "dark_mode_theme": "darkly",
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# iDRAC/Redfish Defaults (override via environment)
IDRAC_DEFAULT_USER = os.environ.get('IDRAC_DEFAULT_USER', 'root')
IDRAC_DEFAULT_PASSWORD = os.environ.get('IDRAC_DEFAULT_PASSWORD', 'calvin')

# OpenStack SDK settings
OPENSTACK_CONNECT_TIMEOUT = int(os.environ.get('OPENSTACK_CONNECT_TIMEOUT', 10))
OPENSTACK_READ_TIMEOUT = int(os.environ.get('OPENSTACK_READ_TIMEOUT', 60))
