"""
Pytest configuration and fixtures for portal tests.
"""

import pytest
from decimal import Decimal

from django.contrib.auth.models import User

from portal.models import (
    Cluster, PhysicalHost, Instance, Alert,
    PortalSettings, ServerCostProfile, Flavor
)


@pytest.fixture
def user(db):
    """Create a regular test user."""
    return User.objects.create_user(
        username='testuser',
        password='testpass123',
        email='test@example.com'
    )


@pytest.fixture
def admin_user(db):
    """Create an admin/superuser."""
    return User.objects.create_superuser(
        username='admin',
        password='adminpass123',
        email='admin@example.com'
    )


@pytest.fixture
def portal_settings(db):
    """Get or create portal settings."""
    settings = PortalSettings.get_settings()
    settings.electricity_cost = Decimal('0.12')
    settings.pue = Decimal('1.5')
    settings.save()
    return settings


@pytest.fixture
def cost_profile(db):
    """Create a server cost profile."""
    return ServerCostProfile.objects.create(
        name="Dell PowerEdge R740",
        monthly_amortization=Decimal('200.00'),
        average_watts=400
    )


@pytest.fixture
def cluster(db):
    """Create a test cluster."""
    return Cluster.objects.create(
        name="Test Cluster",
        auth_url="https://fake.cloud",
        username="admin",
        project_name="admin",
        region_name="TestRegion"
    )


@pytest.fixture
def host(db, cluster, cost_profile):
    """Create a test physical host."""
    return PhysicalHost.objects.create(
        cluster=cluster,
        hostname="compute-01",
        ip_address="10.0.0.10",
        cpu_count=64,
        vcpus_used=32,
        memory_mb=256000,
        memory_mb_used=128000,
        server_model=cost_profile
    )


@pytest.fixture
def flavor(db, cluster):
    """Create a test flavor."""
    return Flavor.objects.create(
        uuid="flavor-test-123",
        cluster=cluster,
        name="m1.small",
        vcpus=2,
        ram_mb=2048,
        disk_gb=20
    )


@pytest.fixture
def instance(db, host, flavor):
    """Create a test instance."""
    return Instance.objects.create(
        host=host,
        name="test-vm-01",
        flavor_name="m1.small",
        status="ACTIVE",
        project_id="project-123",
        user_id="user-456",
        ip_address="192.168.1.100"
    )


@pytest.fixture
def alert(db, cluster):
    """Create a test alert."""
    return Alert.objects.create(
        source="Test",
        target_cluster=cluster,
        title="Test Alert",
        description="Test alert description",
        severity="warning"
    )


@pytest.fixture
def authenticated_client(client, user):
    """Return a Django test client logged in as regular user."""
    client.login(username='testuser', password='testpass123')
    return client


@pytest.fixture
def admin_client(client, admin_user):
    """Return a Django test client logged in as admin."""
    client.login(username='admin', password='adminpass123')
    return client

