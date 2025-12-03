"""
Unit tests for portal services.
"""

from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal

from django.test import TestCase

from portal.models import (
    Cluster, PhysicalHost, Instance, Alert,
    PortalSettings, ServerCostProfile, Flavor
)
from portal.services import ClusterService, CostService, InventoryService


class ClusterServiceTestCase(TestCase):
    """Tests for ClusterService."""

    def setUp(self):
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://openstack.example.com:5000/v3",
            username="admin",
            project_name="admin",
            region_name="RegionOne"
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

    def test_get_cluster_stats(self):
        """Test cluster statistics calculation."""
        # Add another host
        PhysicalHost.objects.create(
            cluster=self.cluster,
            hostname="compute-02",
            ip_address="10.0.0.11",
            cpu_count=64,
            vcpus_used=16,
            memory_mb=256000,
            memory_mb_used=64000
        )
        
        stats = ClusterService.get_cluster_stats(self.cluster)
        
        self.assertEqual(stats['total_cpu'], 128)
        self.assertEqual(stats['used_cpu'], 48)
        self.assertEqual(stats['total_mem'], 512000)
        self.assertEqual(stats['used_mem'], 192000)
        self.assertAlmostEqual(stats['cpu_pct'], 37.5, places=1)
        self.assertAlmostEqual(stats['mem_pct'], 37.5, places=1)

    def test_get_cluster_stats_empty_cluster(self):
        """Test stats for cluster with no hosts."""
        empty_cluster = Cluster.objects.create(
            name="Empty Cluster",
            auth_url="https://empty.example.com:5000/v3",
            username="admin",
            project_name="admin"
        )
        
        stats = ClusterService.get_cluster_stats(empty_cluster)
        
        self.assertEqual(stats['total_cpu'], 0)
        self.assertEqual(stats['cpu_pct'], 0)

    def test_is_dummy_cluster(self):
        """Test dummy cluster detection."""
        self.assertFalse(ClusterService.is_dummy_cluster(self.cluster))
        
        fake_cluster = Cluster.objects.create(
            name="Fake Cluster",
            auth_url="https://fake.cloud",
            username="admin",
            project_name="admin"
        )
        self.assertTrue(ClusterService.is_dummy_cluster(fake_cluster))

    def test_get_cluster_alerts(self):
        """Test fetching cluster alerts."""
        # Create alerts
        Alert.objects.create(
            source="OpenStack",
            target_cluster=self.cluster,
            title="Cluster Alert",
            description="Test alert",
            severity="warning"
        )
        Alert.objects.create(
            source="Hardware",
            target_host=self.host,
            title="Host Alert",
            description="Host test alert",
            severity="critical"
        )
        # Inactive alert should not be returned
        Alert.objects.create(
            source="Test",
            target_cluster=self.cluster,
            title="Inactive Alert",
            description="Should not appear",
            severity="info",
            is_active=False
        )
        
        alerts = ClusterService.get_cluster_alerts(self.cluster)
        
        self.assertEqual(len(alerts), 2)

    def test_get_annotated_clusters(self):
        """Test annotated cluster queryset."""
        # Create an alert to test annotation
        Alert.objects.create(
            source="Test",
            target_cluster=self.cluster,
            title="Active Alert",
            description="Test",
            severity="warning"
        )
        
        clusters = ClusterService.get_annotated_clusters()
        cluster = clusters.get(pk=self.cluster.pk)
        
        self.assertTrue(cluster.has_active_alert)


class CostServiceTestCase(TestCase):
    """Tests for CostService."""

    def setUp(self):
        self.settings = PortalSettings.get_settings()
        self.settings.electricity_cost = Decimal('0.12')
        self.settings.pue = Decimal('1.5')
        self.settings.save()
        
        self.profile = ServerCostProfile.objects.create(
            name="Dell R740",
            monthly_amortization=Decimal('200.00'),
            average_watts=400
        )
        
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
            server_model=self.profile
        )
        
        Flavor.objects.create(
            uuid="flavor-1",
            cluster=self.cluster,
            name="m1.small",
            vcpus=2,
            ram_mb=2048,
            disk_gb=20
        )
        
        self.instance = Instance.objects.create(
            host=self.host,
            name="test-vm",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-123",
            user_id="user-456"
        )

    def test_calculate_instance_cost(self):
        """Test instance cost calculation."""
        cost = CostService.calculate_instance_cost(self.instance, self.settings)
        
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 0)
        
        # Manual calculation:
        # Power cost = (400/1000) * 24 * 30 * 0.12 * 1.5 = 51.84
        # Total host cost = 200 + 51.84 = 251.84
        # Cost per vCPU = 251.84 / 64 = 3.935
        # Instance cost = 3.935 * 2 = 7.87
        self.assertAlmostEqual(cost, 7.87, places=1)

    def test_calculate_instance_cost_no_profile(self):
        """Test cost calculation returns None without profile."""
        self.host.server_model = None
        self.host.save()
        self.instance.refresh_from_db()
        
        cost = CostService.calculate_instance_cost(self.instance, self.settings)
        
        self.assertIsNone(cost)

    def test_calculate_instance_cost_no_host(self):
        """Test cost calculation returns None without host."""
        orphan_instance = Instance.objects.create(
            host=None,
            name="orphan-vm",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-123",
            user_id="user-456"
        )
        
        cost = CostService.calculate_instance_cost(orphan_instance, self.settings)
        
        self.assertIsNone(cost)

    def test_calculate_project_costs(self):
        """Test project costs aggregation."""
        # Create another instance in different project
        Instance.objects.create(
            host=self.host,
            name="test-vm-2",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-456",
            user_id="user-456"
        )
        
        result = CostService.calculate_project_costs(self.settings)
        
        self.assertIn('projects', result)
        self.assertIn('total_monthly', result)
        self.assertIn('projected_yearly', result)
        self.assertEqual(len(result['projects']), 2)
        self.assertEqual(result['projected_yearly'], result['total_monthly'] * 12)

    def test_calculate_host_cost(self):
        """Test host cost calculation."""
        cost = CostService.calculate_host_cost(self.host, self.settings)
        
        self.assertIsNotNone(cost)
        self.assertIn('power_cost', cost)
        self.assertIn('amortization', cost)
        self.assertIn('total_cost', cost)
        
        # Power = (400/1000) * 24 * 30 * 0.12 * 1.5 = 51.84
        self.assertAlmostEqual(cost['power_cost'], 51.84, places=1)
        self.assertEqual(cost['amortization'], 200.0)


class InventoryServiceTestCase(TestCase):
    """Tests for InventoryService."""

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
            cpu_count=64
        )

    def test_sync_instance_creates_instance(self):
        """Test syncing a new instance creates it in DB."""
        # Mock server object
        mock_server = Mock()
        mock_server.id = "server-uuid-123"
        mock_server.name = "new-vm"
        mock_server.status = "ACTIVE"
        mock_server.flavor = {'original_name': 'm1.small'}
        mock_server.project_id = "project-1"
        mock_server.user_id = "user-1"
        mock_server.addresses = {
            'provider-net': [{'version': 4, 'addr': '192.168.1.100'}]
        }
        mock_server.image = {'id': 'image-123'}
        mock_server.key_name = 'my-key'
        mock_server.launched_at = None
        
        instance = InventoryService.sync_instance(self.host, mock_server, {})
        
        self.assertEqual(instance.name, "new-vm")
        self.assertEqual(instance.status, "ACTIVE")
        self.assertEqual(instance.ip_address, "192.168.1.100")
        self.assertEqual(instance.host, self.host)

    def test_sync_instance_updates_existing(self):
        """Test syncing an existing instance updates it."""
        existing = Instance.objects.create(
            uuid="existing-uuid",
            host=self.host,
            name="old-name",
            flavor_name="m1.tiny",
            status="BUILD",
            project_id="project-1",
            user_id="user-1"
        )
        
        mock_server = Mock()
        mock_server.id = "existing-uuid"
        mock_server.name = "updated-name"
        mock_server.status = "ACTIVE"
        mock_server.flavor = {'original_name': 'm1.small'}
        mock_server.project_id = "project-1"
        mock_server.user_id = "user-1"
        mock_server.addresses = {}
        mock_server.image = None
        mock_server.key_name = None
        mock_server.launched_at = None
        
        instance = InventoryService.sync_instance(self.host, mock_server, {})
        
        self.assertEqual(Instance.objects.count(), 1)
        self.assertEqual(instance.name, "updated-name")
        self.assertEqual(instance.status, "ACTIVE")

    def test_build_bmc_map_handles_errors(self):
        """Test BMC map building handles errors gracefully."""
        mock_client = Mock()
        mock_client.conn.baremetal.nodes.side_effect = Exception("Ironic not available")
        
        result = InventoryService.build_bmc_map(mock_client)
        
        self.assertEqual(result, {})

    @patch('portal.services.inventory_service.OpenStackClient')
    def test_refresh_host_fake_cluster(self, mock_client):
        """Test refresh_host skips fake clusters."""
        self.cluster.auth_url = "https://fake.cloud"
        self.cluster.save()
        self.host.refresh_from_db()
        
        result = InventoryService.refresh_host(self.host)
        
        self.assertFalse(result)
        mock_client.assert_not_called()


class ServiceIntegrationTestCase(TestCase):
    """Integration tests across services."""

    def setUp(self):
        self.settings = PortalSettings.get_settings()
        
        self.profile = ServerCostProfile.objects.create(
            name="Test Server",
            monthly_amortization=Decimal('100.00'),
            average_watts=300
        )
        
        self.cluster = Cluster.objects.create(
            name="Integration Test Cluster",
            auth_url="https://test.example.com:5000/v3",
            username="admin",
            project_name="admin",
            region_name="TestRegion"
        )
        
        # Create multiple hosts with instances
        for i in range(3):
            host = PhysicalHost.objects.create(
                cluster=self.cluster,
                hostname=f"compute-{i:02d}",
                ip_address=f"10.0.0.{10+i}",
                cpu_count=32,
                vcpus_used=16,
                memory_mb=128000,
                memory_mb_used=64000,
                server_model=self.profile
            )
            
            Flavor.objects.create(
                uuid=f"flavor-{i}",
                cluster=self.cluster,
                name="m1.medium",
                vcpus=4,
                ram_mb=4096,
                disk_gb=40
            )
            
            for j in range(5):
                Instance.objects.create(
                    host=host,
                    name=f"vm-{i}-{j}",
                    flavor_name="m1.medium",
                    status="ACTIVE",
                    project_id=f"project-{j % 2}",
                    user_id="user-1"
                )

    def test_full_cost_calculation_flow(self):
        """Test complete cost calculation across services."""
        # Get cluster stats
        stats = ClusterService.get_cluster_stats(self.cluster)
        
        self.assertEqual(stats['total_cpu'], 96)
        self.assertEqual(stats['used_cpu'], 48)
        
        # Calculate project costs
        costs = CostService.calculate_project_costs(self.settings)
        
        self.assertEqual(len(costs['projects']), 2)
        self.assertGreater(costs['total_monthly'], 0)
        
        # Verify instances are accounted for
        total_instances = sum(p['instance_count'] for p in costs['projects'])
        self.assertEqual(total_instances, 15)

    def test_cluster_with_alerts(self):
        """Test cluster alert handling."""
        # Create alerts
        host = PhysicalHost.objects.filter(cluster=self.cluster).first()
        
        Alert.objects.create(
            source="Test",
            target_cluster=self.cluster,
            title="Cluster Alert",
            description="Test",
            severity="warning"
        )
        Alert.objects.create(
            source="Test",
            target_host=host,
            title="Host Alert",
            description="Test",
            severity="critical"
        )
        
        alerts = ClusterService.get_cluster_alerts(self.cluster)
        annotated = ClusterService.get_annotated_clusters().get(pk=self.cluster.pk)
        
        self.assertEqual(len(alerts), 2)
        self.assertTrue(annotated.has_active_alert)

