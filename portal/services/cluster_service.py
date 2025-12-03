"""
Cluster-related business logic.
"""

import logging
from typing import Optional, Dict, Any, List
from django.db.models import Sum, Exists, OuterRef, Prefetch

from portal.models import Cluster, PhysicalHost, Instance, Alert, ClusterService as ClusterServiceModel
from portal.openstack_utils import OpenStackClient

logger = logging.getLogger(__name__)


class ClusterService:
    """Service class for cluster operations."""

    @staticmethod
    def get_annotated_clusters():
        """
        Returns clusters annotated with 'has_active_alert' boolean,
        and pre-fetches hosts also annotated with 'has_active_alert'.
        """
        host_alerts = Alert.objects.filter(target_host=OuterRef('pk'), is_active=True)
        cluster_alerts = Alert.objects.filter(target_cluster=OuterRef('pk'), is_active=True)
        
        hosts_qs = PhysicalHost.objects.annotate(
            has_active_alert=Exists(host_alerts)
        )
        
        return Cluster.objects.annotate(
            has_active_alert=Exists(cluster_alerts)
        ).prefetch_related(
            Prefetch('hosts', queryset=hosts_qs),
            'hosts__instances'
        ).order_by('region_name', 'name')

    @staticmethod
    def get_cluster_stats(cluster: Cluster) -> Dict[str, Any]:
        """
        Calculate aggregated statistics for a cluster.
        
        Returns:
            Dict with total_cpu, used_cpu, total_mem, used_mem, and percentages
        """
        hosts = cluster.hosts.all()
        
        aggs = hosts.aggregate(
            total_cpu=Sum('cpu_count'), 
            used_cpu=Sum('vcpus_used'), 
            total_mem=Sum('memory_mb'), 
            used_mem=Sum('memory_mb_used')
        )
        
        stats = {
            'total_cpu': aggs['total_cpu'] or 0,
            'used_cpu': aggs['used_cpu'] or 0,
            'total_mem': aggs['total_mem'] or 0,
            'used_mem': aggs['used_mem'] or 0
        }
        
        # Calculate percentages
        stats['cpu_pct'] = round(
            (stats['used_cpu'] / stats['total_cpu'] * 100) if stats['total_cpu'] > 0 else 0, 
            1
        )
        stats['mem_pct'] = round(
            (stats['used_mem'] / stats['total_mem'] * 100) if stats['total_mem'] > 0 else 0, 
            1
        )
        
        # Add GB versions for display
        stats['total_mem_gb'] = stats['total_mem'] // 1024
        stats['used_mem_gb'] = stats['used_mem'] // 1024
        
        return stats

    @staticmethod
    def refresh_cluster_services(cluster: Cluster) -> int:
        """
        Refresh OpenStack services for a cluster.
        
        Returns:
            Number of services synced
        """
        if "fake" in cluster.auth_url:
            logger.info(f"Skipping refresh for fake cluster {cluster.name}")
            return 0
            
        try:
            client = OpenStackClient(cluster)
            services = client.get_services()
            count = 0
            
            for svc in services:
                ClusterServiceModel.objects.update_or_create(
                    cluster=cluster, 
                    binary=svc.binary, 
                    host=svc.host,
                    defaults={
                        'zone': getattr(svc, 'availability_zone', 'nova'),
                        'status': svc.status,
                        'state': svc.state
                    }
                )
                count += 1
            
            logger.info(f"Synced {count} services for cluster {cluster.name}")
            return count
            
        except Exception as e:
            logger.error(f"Failed to refresh services for cluster {cluster.name}: {e}")
            raise

    @staticmethod
    def get_cluster_alerts(cluster: Cluster) -> List[Alert]:
        """Get all active alerts for a cluster and its hosts."""
        return (
            Alert.objects.filter(target_cluster=cluster, is_active=True) | 
            Alert.objects.filter(target_host__cluster=cluster, is_active=True)
        ).order_by('-created_at')

    @staticmethod
    def is_dummy_cluster(cluster: Cluster) -> bool:
        """Check if cluster is a dummy/fake cluster for testing."""
        dummy_patterns = ["example.com", "inventory.local", "fake"]
        return any(pattern in cluster.auth_url for pattern in dummy_patterns)

    @staticmethod
    def test_cluster_connection(cluster: Cluster) -> Dict[str, Any]:
        """
        Test connection to a cluster.
        
        Returns:
            Dict with 'success', 'version', and optionally 'error'
        """
        try:
            client = OpenStackClient(cluster)
            version = client.get_cluster_release()
            return {
                'success': True,
                'version': version,
                'status': 'online'
            }
        except Exception as e:
            logger.error(f"Connection test failed for cluster {cluster.name}: {e}")
            return {
                'success': False,
                'error': str(e),
                'status': 'offline'
            }

