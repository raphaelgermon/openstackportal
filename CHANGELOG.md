# Changelog - OpenStack Portal

## [Unreleased] - 2025-12-03

### 🐛 Bug Fixes

#### Bug #1: Missing ClusterService Import
- **File**: `portal/views.py`
- **Issue**: `ClusterService` was used in `cluster_details()` but never imported, causing a `NameError` at runtime.
- **Solution**: Added `ClusterService` import from `portal.models`.

#### Bug #2: `alerts` Variable Not Passed to Template
- **File**: `portal/views.py` (function `node_details`)
- **Issue**: Template `node_details.html` used `{% if alerts %}` but the variable was never passed to the context.
- **Solution**: Added query to fetch host alerts and passed them to context.

```python
# Before
return render_page(request, 'portal/partials/node_details.html', {'host': host}, 'node')

# After
alerts = Alert.objects.filter(target_host=host, is_active=True).order_by('-created_at')
return render_page(request, 'portal/partials/node_details.html', {'host': host, 'alerts': alerts}, 'node')
```

#### Bug #3: RAM Progress Bar Hardcoded at 40%
- **File**: `portal/templates/portal/partials/instance_details.html`
- **Issue**: RAM progress bar always displayed 40% instead of actual value.
- **Solution**: Used `{% widthratio %}` template tag to dynamically calculate percentage.

```html
<!-- Before -->
<div class="bg-purple-500 h-2 rounded-full" style="width: 40%"></div>

<!-- After -->
{% widthratio instance.last_ram_usage_mb 8192 100 as ram_pct %}
<div class="bg-purple-500 h-2 rounded-full" style="width: {{ ram_pct|default:0 }}%"></div>
```

#### Bug #4: Dead Code in `tasks.py`
- **File**: `portal/tasks.py`
- **Issue**: Empty loop at the end of `collect_hardware_health()` that did nothing.
- **Solution**: Removed dead code.

```python
# Removed:
for host in hosts:
    # Redfish logic...
    pass
```

#### Bug #5: Duplicate `handle()` Definition in `populate_dummy_data.py`
- **File**: `portal/management/commands/populate_dummy_data.py`
- **Issue**: A `handle()` method was defined inside the `handle()` method.
- **Solution**: Removed duplicate/dead code.

---

### 📝 Logging Improvements

#### Replaced All `print()` Statements with Structured Logging

**Modified files**:
- `portal/tasks.py`
- `portal/openstack_utils.py`
- `portal/views.py`

**Example change**:
```python
# Before
print(f">>> STARTING INVENTORY SYNC TASK")
print(f"  [{cluster.name}] ERROR: {e}")

# After
import logging
logger = logging.getLogger(__name__)

logger.info("Starting inventory sync task")
logger.error(f"[{cluster.name}] ERROR: {e}", exc_info=True)
```

**Log levels used**:
| Level | Usage |
|-------|-------|
| `DEBUG` | Technical details, progress |
| `INFO` | Normal events (sync completed, etc.) |
| `WARNING` | Non-blocking errors |
| `ERROR` | Errors with stack trace |

---

### 🔐 Security Improvements

#### Externalized iDRAC Credentials
- **Files**: `config/settings.py`, `portal/tasks.py`
- **Issue**: iDRAC credentials hardcoded (`root/calvin`) in source code.
- **Solution**: Configurable environment variables.

```python
# config/settings.py
IDRAC_DEFAULT_USER = os.environ.get('IDRAC_DEFAULT_USER', 'root')
IDRAC_DEFAULT_PASSWORD = os.environ.get('IDRAC_DEFAULT_PASSWORD', 'calvin')

# portal/tasks.py
IDRAC_DEFAULT_USER = getattr(settings, 'IDRAC_DEFAULT_USER', 'root')
IDRAC_DEFAULT_PASSWORD = getattr(settings, 'IDRAC_DEFAULT_PASSWORD', 'calvin')
```

#### Configurable OpenStack Timeouts
```python
# config/settings.py
OPENSTACK_CONNECT_TIMEOUT = int(os.environ.get('OPENSTACK_CONNECT_TIMEOUT', 10))
OPENSTACK_READ_TIMEOUT = int(os.environ.get('OPENSTACK_READ_TIMEOUT', 60))
```

---

### 🔄 Retry Logic with Tenacity

- **File**: `portal/openstack_utils.py`
- **Dependency added**: `tenacity>=8.2.0`

**Retry decorator for OpenStack API calls**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

openstack_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((
        ks_exceptions.ConnectFailure,
        ks_exceptions.ConnectTimeout,
        ks_exceptions.GatewayTimeout,
        ks_exceptions.ServiceUnavailable,
    )),
    before_sleep=lambda retry_state: logger.warning(
        f"OpenStack API call failed, retrying..."
    )
)
```

**Decorated methods**:
- `get_services()`
- `get_hypervisors()`
- `get_hypervisor_by_name()`
- `get_instances()`
- `get_flavors()`
- `get_server_by_uuid()`
- `get_attached_volumes()`
- `get_realtime_stats()`

---

### 🏗️ Architecture: Service Layer

**New files created**:

```
portal/services/
├── __init__.py
├── cluster_service.py    # Cluster business logic
├── inventory_service.py  # OpenStack synchronization
└── cost_service.py       # Financial calculations
```

#### ClusterService (`cluster_service.py`)
```python
class ClusterService:
    @staticmethod
    def get_annotated_clusters()        # Clusters with alert indicators
    @staticmethod
    def get_cluster_stats(cluster)      # Aggregated CPU/RAM stats
    @staticmethod
    def get_cluster_alerts(cluster)     # Active alerts
    @staticmethod
    def is_dummy_cluster(cluster)       # Detect fake cluster
    @staticmethod
    def test_cluster_connection(cluster) # Connection test
    @staticmethod
    def refresh_cluster_services(cluster) # Sync OpenStack services
```

#### CostService (`cost_service.py`)
```python
class CostService:
    @staticmethod
    def calculate_instance_cost(instance, settings)
    @staticmethod
    def calculate_project_costs(settings)
    @staticmethod
    def calculate_host_cost(host, settings)
    @staticmethod
    def calculate_cluster_cost(cluster, settings)
```

#### InventoryService (`inventory_service.py`)
```python
class InventoryService:
    @staticmethod
    def sync_hypervisor(client, cluster, hyp, bmc_map, stats_map)
    @staticmethod
    def sync_instance(host, server, volume_map)
    @staticmethod
    def build_bmc_map(client)
    @staticmethod
    def build_hypervisor_stats_map(client, cluster_name)
    @staticmethod
    def build_instance_map(client, cluster_name)
    @staticmethod
    def build_volume_map(client, cluster_name)
    @staticmethod
    def refresh_host(host)
```

#### Refactoring `views.py`
```python
# Import services
from .services import ClusterService as ClusterSvc, CostService, InventoryService

# Delegate to services
def get_annotated_clusters():
    return ClusterSvc.get_annotated_clusters()

def calculate_instance_cost(instance, settings_obj):
    return CostService.calculate_instance_cost(instance, settings_obj)
```

---

### 🧪 Unit Tests

**New files created**:

```
portal/tests/
├── __init__.py
├── conftest.py        # Pytest fixtures
├── test_models.py     # 22 tests for models
├── test_services.py   # 15 tests for services
└── test_views.py      # 25+ tests for views
```

**Pytest configuration** (`pytest.ini`):
```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings
python_files = tests.py test_*.py *_tests.py
addopts = -v --tb=short
testpaths = portal/tests
```

**Dependencies added**:
```
pytest>=7.4.0
pytest-django>=4.5.0
pytest-cov>=4.1.0
```

**Commands to run tests**:
```bash
# Django test runner
python manage.py test portal.tests -v 2

# Pytest
pytest portal/tests/ -v

# With coverage
pytest portal/tests/ --cov=portal --cov-report=html
```

---

### 🐳 Docker: Automatic Migrations

**Issue fixed**: Error `no such table: auth_user` on startup.

**New files**:

#### `entrypoint.sh`
```bash
#!/bin/bash
set -e

echo "Waiting for database..."
sleep 2

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear 2>/dev/null || true

echo "Starting server..."
exec "$@"
```

#### Changes to `docker-compose.yml`
```yaml
web:
  build: .
  entrypoint: ["/app/entrypoint.sh"]  # ADDED
  command: python manage.py runserver 0.0.0.0:10000
  volumes:
    - .:/app
    - sqlite_data:/app/data  # ADDED - DB persistence

volumes:
  sqlite_data:  # ADDED
```

#### Changes to `Dockerfile`
```dockerfile
# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh  # ADDED
```

---

### 📦 Dependencies Added

```diff
# requirements.txt
+ tenacity>=8.2.0
+ pytest>=7.4.0
+ pytest-django>=4.5.0
+ pytest-cov>=4.1.0
```

---

### 📁 Summary of Modified Files

| File | Type of Change |
|------|----------------|
| `portal/views.py` | Bug fixes, imports, service refactoring |
| `portal/tasks.py` | Logging, externalized credentials, dead code |
| `portal/openstack_utils.py` | Logging, retry logic, timeouts |
| `portal/templates/portal/partials/instance_details.html` | RAM bar bug fix |
| `portal/management/commands/populate_dummy_data.py` | Removed dead code |
| `config/settings.py` | iDRAC and OpenStack variables |
| `requirements.txt` | New dependencies |
| `docker-compose.yml` | Entrypoint, volumes |
| `Dockerfile` | chmod entrypoint |

### 📁 New Files Created

| File | Description |
|------|-------------|
| `portal/services/__init__.py` | Services export |
| `portal/services/cluster_service.py` | Cluster service |
| `portal/services/inventory_service.py` | Inventory service |
| `portal/services/cost_service.py` | Cost service |
| `portal/tests/__init__.py` | Tests package |
| `portal/tests/conftest.py` | Pytest fixtures |
| `portal/tests/test_models.py` | Model tests |
| `portal/tests/test_services.py` | Service tests |
| `portal/tests/test_views.py` | View tests |
| `pytest.ini` | Pytest configuration |
| `entrypoint.sh` | Docker startup script |
| `CHANGELOG.md` | This file |

---

### 🚀 Update Instructions

```bash
# 1. Update dependencies
pip install -r requirements.txt

# 2. Apply migrations (if needed)
python manage.py migrate

# 3. Restart Docker
docker-compose down
docker-compose up --build

# 4. Run tests
python manage.py test portal.tests
```
