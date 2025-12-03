"""
Unit tests for portal models.
"""

from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal

from portal.models import (
    Cluster, PhysicalHost, Instance, Alert, 
    PortalSettings, ServerCostProfile, Flavor, Volume, AuditLog
)


class PortalSettingsTestCase(TestCase):
    """Tests for PortalSettings singleton model."""

    def test_singleton_creation(self):
        """Test that only one PortalSettings instance can exist."""
        settings1 = PortalSettings.get_settings()
        settings2 = PortalSettings.get_settings()
        
        self.assertEqual(settings1.pk, settings2.pk)
        self.assertEqual(settings1.pk, 1)

    def test_default_values(self):
        """Test default values are set correctly."""
        settings = PortalSettings.get_settings()
        
        self.assertEqual(settings.sync_interval_minutes, 10)
        self.assertEqual(settings.electricity_cost, Decimal('0.1200'))
        self.assertEqual(settings.pue, Decimal('1.50'))

    def test_singleton_update(self):
        """Test updating the singleton."""
        settings = PortalSettings.get_settings()
        settings.sync_interval_minutes = 30
        settings.save()
        
        # Fetch again
        settings2 = PortalSettings.get_settings()
        self.assertEqual(settings2.sync_interval_minutes, 30)
        self.assertEqual(PortalSettings.objects.count(), 1)


class ClusterTestCase(TestCase):
    """Tests for Cluster model."""

    def setUp(self):
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin",
            region_name="RegionOne"
        )

    def test_cluster_creation(self):
        """Test basic cluster creation."""
        self.assertEqual(self.cluster.name, "Test Cluster")
        self.assertEqual(self.cluster.status, 'unknown')

    def test_password_encryption(self):
        """Test password encryption and decryption."""
        raw_password = "super_secret_password"
        self.cluster.set_password(raw_password)
        self.cluster.save()
        
        # Ensure stored password is encrypted (not plaintext)
        self.assertNotEqual(self.cluster.password, raw_password)
        
        # Ensure decryption works
        self.assertEqual(self.cluster.get_password(), raw_password)

    def test_password_empty(self):
        """Test handling of empty password."""
        self.cluster.set_password("")
        self.assertEqual(self.cluster.get_password(), "")

    def test_str_representation(self):
        """Test string representation."""
        self.assertEqual(str(self.cluster), "Test Cluster")


class PhysicalHostTestCase(TestCase):
    """Tests for PhysicalHost model."""

    def setUp(self):
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin"
        )
        self.host = PhysicalHost.objects.create(
            cluster=self.cluster,
            hostname="compute-01",
            ip_address="10.0.0.10",
            cpu_count=64,
            vcpus_used=32,
            memory_mb=256000,
            memory_mb_used=128000
        )

    def test_host_creation(self):
        """Test basic host creation."""
        self.assertEqual(self.host.hostname, "compute-01")
        self.assertEqual(self.host.state, 'up')
        self.assertEqual(self.host.status, 'enabled')

    def test_host_cluster_relationship(self):
        """Test host belongs to cluster."""
        self.assertEqual(self.host.cluster, self.cluster)
        self.assertIn(self.host, self.cluster.hosts.all())

    def test_host_with_cost_profile(self):
        """Test host with server cost profile."""
        profile = ServerCostProfile.objects.create(
            name="Dell R740",
            monthly_amortization=Decimal('200.00'),
            average_watts=350
        )
        self.host.server_model = profile
        self.host.save()
        
        self.assertEqual(self.host.server_model.name, "Dell R740")


class InstanceTestCase(TestCase):
    """Tests for Instance model."""

    def setUp(self):
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin"
        )
        self.host = PhysicalHost.objects.create(
            cluster=self.cluster,
            hostname="compute-01",
            ip_address="10.0.0.10"
        )
        self.instance = Instance.objects.create(
            host=self.host,
            name="test-vm-01",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-123",
            user_id="user-456"
        )

    def test_instance_creation(self):
        """Test basic instance creation."""
        self.assertEqual(self.instance.name, "test-vm-01")
        self.assertEqual(self.instance.status, "ACTIVE")

    def test_instance_uuid_auto_generated(self):
        """Test UUID is auto-generated."""
        self.assertIsNotNone(self.instance.uuid)

    def test_instance_host_relationship(self):
        """Test instance belongs to host."""
        self.assertEqual(self.instance.host, self.host)
        self.assertIn(self.instance, self.host.instances.all())


class VolumeTestCase(TestCase):
    """Tests for Volume model."""

    def setUp(self):
        cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin"
        )
        host = PhysicalHost.objects.create(
            cluster=cluster,
            hostname="compute-01",
            ip_address="10.0.0.10"
        )
        self.instance = Instance.objects.create(
            host=host,
            name="test-vm-01",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-123",
            user_id="user-456"
        )

    def test_volume_creation(self):
        """Test volume creation."""
        volume = Volume.objects.create(
            instance=self.instance,
            name="boot-volume",
            size_gb=100,
            device="/dev/vda",
            status="in-use",
            is_bootable=True
        )
        
        self.assertEqual(volume.name, "boot-volume")
        self.assertEqual(volume.size_gb, 100)
        self.assertTrue(volume.is_bootable)

    def test_volume_instance_relationship(self):
        """Test volume belongs to instance."""
        volume = Volume.objects.create(
            instance=self.instance,
            name="data-volume",
            size_gb=500,
            device="/dev/vdb",
            status="in-use"
        )
        
        self.assertIn(volume, self.instance.volumes.all())


class AlertTestCase(TestCase):
    """Tests for Alert model."""

    def setUp(self):
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin"
        )

    def test_alert_creation(self):
        """Test alert creation."""
        alert = Alert.objects.create(
            source="OpenStack",
            target_cluster=self.cluster,
            title="API High Latency",
            description="Control plane latency > 200ms",
            severity="warning"
        )
        
        self.assertEqual(alert.title, "API High Latency")
        self.assertTrue(alert.is_active)

    def test_alert_severity_choices(self):
        """Test alert severity is validated."""
        alert = Alert.objects.create(
            source="Hardware",
            target_cluster=self.cluster,
            title="PSU Failure",
            description="Power supply redundancy lost",
            severity="critical"
        )
        
        self.assertEqual(alert.severity, "critical")


class ServerCostProfileTestCase(TestCase):
    """Tests for ServerCostProfile model."""

    def test_profile_creation(self):
        """Test cost profile creation."""
        profile = ServerCostProfile.objects.create(
            name="Dell PowerEdge R740",
            monthly_amortization=Decimal('250.00'),
            average_watts=400
        )
        
        self.assertEqual(profile.name, "Dell PowerEdge R740")
        self.assertEqual(profile.monthly_amortization, Decimal('250.00'))
        self.assertEqual(profile.average_watts, 400)

    def test_profile_unique_name(self):
        """Test profile name must be unique."""
        ServerCostProfile.objects.create(
            name="Unique Profile",
            monthly_amortization=Decimal('100.00'),
            average_watts=200
        )
        
        with self.assertRaises(Exception):
            ServerCostProfile.objects.create(
                name="Unique Profile",
                monthly_amortization=Decimal('150.00'),
                average_watts=300
            )


class AuditLogTestCase(TestCase):
    """Tests for AuditLog model."""

    def test_audit_log_creation(self):
        """Test audit log creation."""
        user = User.objects.create_user(username='testuser', password='password')
        
        log = AuditLog.objects.create(
            user=user,
            action="Maintenance Toggle",
            target="compute-01",
            details="Maintenance enabled"
        )
        
        self.assertEqual(log.action, "Maintenance Toggle")
        self.assertEqual(log.user, user)
        self.assertIsNotNone(log.timestamp)

    def test_audit_log_without_user(self):
        """Test audit log can be created without user (system action)."""
        log = AuditLog.objects.create(
            action="Inventory Sync",
            target="cluster-01",
            details="Synced 100 instances"
        )
        
        self.assertIsNone(log.user)

