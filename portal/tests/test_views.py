"""
Unit tests for portal views.
"""

from unittest.mock import patch, Mock
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

from portal.models import (
    Cluster, PhysicalHost, Instance, Alert,
    PortalSettings, ServerCostProfile, Flavor, AuditLog
)


class ViewTestCase(TestCase):
    """Base test case with common setup for view tests."""

    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.admin_user = User.objects.create_superuser(
            username='admin',
            password='adminpass123',
            email='admin@example.com'
        )
        
        # Create test data
        self.cluster = Cluster.objects.create(
            name="Test Cluster",
            auth_url="https://fake.cloud",  # Fake to avoid real API calls
            username="admin",
            project_name="admin",
            region_name="TestRegion"
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
        
        self.instance = Instance.objects.create(
            host=self.host,
            name="test-vm-01",
            flavor_name="m1.small",
            status="ACTIVE",
            project_id="project-123",
            user_id="user-456",
            ip_address="192.168.1.100"
        )
        
        # Django test client
        self.client = Client()


class DashboardViewTestCase(ViewTestCase):
    """Tests for dashboard view."""

    def test_dashboard_requires_login(self):
        """Test dashboard redirects to login when not authenticated."""
        response = self.client.get(reverse('dashboard'))
        
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_dashboard_loads_for_authenticated_user(self):
        """Test dashboard loads correctly for authenticated user."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('dashboard'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Global Overview')

    def test_dashboard_shows_cluster_data(self):
        """Test dashboard displays cluster information."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('dashboard'))
        
        self.assertContains(response, self.cluster.name)
        self.assertContains(response, 'TestRegion')

    def test_dashboard_htmx_request(self):
        """Test dashboard returns partial for HTMX requests."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('dashboard'),
            HTTP_HX_REQUEST='true'
        )
        
        self.assertEqual(response.status_code, 200)
        # HTMX requests should return partial template (no full page wrapper)
        self.assertNotContains(response, '<!DOCTYPE html>')


class ClusterDetailViewTestCase(ViewTestCase):
    """Tests for cluster detail view."""

    def test_cluster_detail_loads(self):
        """Test cluster detail page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('cluster_details', args=[self.cluster.id])
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cluster.name)

    def test_cluster_detail_shows_hosts(self):
        """Test cluster detail shows host information."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('cluster_details', args=[self.cluster.id])
        )
        
        self.assertContains(response, self.host.hostname)

    def test_cluster_detail_shows_instances(self):
        """Test cluster detail shows instances."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('cluster_details', args=[self.cluster.id])
        )
        
        self.assertContains(response, self.instance.name)

    def test_cluster_detail_404_for_invalid_id(self):
        """Test cluster detail returns 404 for invalid ID."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('cluster_details', args=[99999])
        )
        
        self.assertEqual(response.status_code, 404)


class NodeDetailViewTestCase(ViewTestCase):
    """Tests for node detail view."""

    def test_node_detail_loads(self):
        """Test node detail page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('node_details', args=[self.host.id])
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.host.hostname)

    def test_node_detail_shows_instances(self):
        """Test node detail shows running instances."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('node_details', args=[self.host.id])
        )
        
        self.assertContains(response, self.instance.name)

    def test_node_detail_shows_alerts(self):
        """Test node detail shows alerts."""
        Alert.objects.create(
            source="Hardware",
            target_host=self.host,
            title="Test Alert",
            description="Test description",
            severity="warning"
        )
        
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('node_details', args=[self.host.id])
        )
        
        self.assertContains(response, 'Test Alert')


class InstanceDetailViewTestCase(ViewTestCase):
    """Tests for instance detail view."""

    def test_instance_detail_loads(self):
        """Test instance detail page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('instance_details', args=[self.instance.uuid])
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.instance.name)

    def test_instance_detail_shows_host_info(self):
        """Test instance detail shows host information."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('instance_details', args=[self.instance.uuid])
        )
        
        self.assertContains(response, self.host.hostname)

    def test_instance_detail_shows_status(self):
        """Test instance detail shows status."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(
            reverse('instance_details', args=[self.instance.uuid])
        )
        
        self.assertContains(response, 'ACTIVE')


class AllInstancesViewTestCase(ViewTestCase):
    """Tests for all instances view."""

    def test_all_instances_loads(self):
        """Test all instances page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('all_instances'))
        
        self.assertEqual(response.status_code, 200)

    def test_all_instances_shows_data(self):
        """Test all instances shows instance data."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('all_instances'))
        
        self.assertContains(response, self.instance.name)


class AllNodesViewTestCase(ViewTestCase):
    """Tests for all nodes view."""

    def test_all_nodes_loads(self):
        """Test all nodes page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('all_nodes'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.host.hostname)


class SearchViewTestCase(ViewTestCase):
    """Tests for global search view."""

    def test_search_requires_login(self):
        """Test search requires authentication."""
        response = self.client.get(reverse('global_search'), {'q': 'test'})
        
        self.assertEqual(response.status_code, 302)

    def test_search_finds_hosts(self):
        """Test search finds hosts by hostname."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('global_search'), {'q': 'compute'})
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.host.hostname)

    def test_search_finds_instances(self):
        """Test search finds instances by name."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('global_search'), {'q': 'test-vm'})
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.instance.name)

    def test_search_minimum_length(self):
        """Test search requires minimum query length."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('global_search'), {'q': 'a'})
        
        self.assertEqual(response.status_code, 200)
        # Should not return results for single character


class AdminSettingsViewTestCase(ViewTestCase):
    """Tests for admin settings view."""

    def test_admin_settings_requires_superuser(self):
        """Test admin settings requires superuser."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('admin_settings'))
        
        # Non-superuser should be redirected
        self.assertEqual(response.status_code, 302)

    def test_admin_settings_loads_for_superuser(self):
        """Test admin settings loads for superuser."""
        self.client.login(username='admin', password='adminpass123')
        
        response = self.client.get(reverse('admin_settings'))
        
        self.assertEqual(response.status_code, 200)

    def test_add_cluster(self):
        """Test adding a new cluster."""
        self.client.login(username='admin', password='adminpass123')
        
        response = self.client.post(reverse('admin_settings'), {
            'action': 'add_cluster',
            'name': 'New Cluster',
            'auth_url': 'https://new.example.com:5000/v3',
            'region': 'NewRegion',
            'username': 'admin',
            'project': 'admin',
            'password': 'secret'
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Cluster.objects.filter(name='New Cluster').exists())

    def test_delete_cluster(self):
        """Test deleting a cluster."""
        self.client.login(username='admin', password='adminpass123')
        
        response = self.client.post(reverse('admin_settings'), {
            'action': 'delete_cluster',
            'cluster_id': self.cluster.id
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cluster.objects.filter(pk=self.cluster.id).exists())


class MaintenanceModeViewTestCase(ViewTestCase):
    """Tests for maintenance mode toggle."""

    def test_toggle_maintenance_on(self):
        """Test enabling maintenance mode."""
        self.client.login(username='testuser', password='testpass123')
        
        self.assertFalse(self.host.is_maintenance)
        
        response = self.client.post(
            reverse('toggle_maintenance', args=[self.host.id]),
            {'reason': 'Scheduled maintenance'}
        )
        
        self.assertEqual(response.status_code, 200)
        self.host.refresh_from_db()
        self.assertTrue(self.host.is_maintenance)

    def test_toggle_maintenance_creates_audit_log(self):
        """Test maintenance toggle creates audit log."""
        self.client.login(username='testuser', password='testpass123')
        
        self.client.post(
            reverse('toggle_maintenance', args=[self.host.id]),
            {'reason': 'Test reason'}
        )
        
        log = AuditLog.objects.filter(
            action="Maintenance Toggle",
            target=self.host.hostname
        ).first()
        
        self.assertIsNotNone(log)
        self.assertIn('Test reason', log.details)


class CostDashboardViewTestCase(ViewTestCase):
    """Tests for cost dashboard view."""

    def setUp(self):
        super().setUp()
        
        # Add cost profile
        self.profile = ServerCostProfile.objects.create(
            name="Test Server",
            monthly_amortization=Decimal('200.00'),
            average_watts=400
        )
        self.host.server_model = self.profile
        self.host.save()
        
        Flavor.objects.create(
            uuid="flavor-1",
            cluster=self.cluster,
            name="m1.small",
            vcpus=2,
            ram_mb=2048,
            disk_gb=20
        )

    def test_cost_dashboard_loads(self):
        """Test cost dashboard page loads."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('cost_dashboard'))
        
        self.assertEqual(response.status_code, 200)

    def test_cost_dashboard_shows_projects(self):
        """Test cost dashboard shows project costs."""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('cost_dashboard'))
        
        self.assertContains(response, 'project-123')


class LogsViewTestCase(ViewTestCase):
    """Tests for logs view."""

    def test_logs_view_loads(self):
        """Test logs view loads."""
        AuditLog.objects.create(
            user=self.user,
            action="Test Action",
            target="Test Target",
            details="Test details"
        )
        
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.get(reverse('logs'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Action')

