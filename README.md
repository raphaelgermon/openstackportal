# OpenStack Management Portal

## Option 1: Run with Docker (Easiest)

1. Run `docker-compose up --build`
2. Access at http://localhost:10000

## Option 2: Run Locally with Virtual Environment (venv)

If you prefer running outside Docker:

### 1. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```
*Note: If `python-ldap` fails to install due to missing system libraries (libldap2-dev, etc.), you can remove `django-auth-ldap` and `python-ldap` from `requirements.txt`. The app is configured to run without LDAP if these are missing.*

### 3. Run Redis (Required for Celery)

You need a local Redis instance running on port 6379, or update `CELERY_BROKER_URL` in `config/settings.py`.

### 4. Run the Server

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:10000
```
Access at http://localhost:10000
