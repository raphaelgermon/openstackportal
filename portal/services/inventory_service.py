"""
Inventory synchronization business logic.
"""

import logging
from typing import Optional, Dict, Any, List
from collections import defaultdict

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.models import (
    Cluster, PhysicalHost, Instance, Volume, 
    ClusterService, Flavor, AuditLog
)
from portal.openstack_utils import OpenStackClient

logger = logging.getLogger(__name__)


class InventoryService:
    """Service class for inventory synchronization operations."""

    @staticmethod
    def sync_hypervisor(client: OpenStackClient, cluster: Cluster, hyp, bmc_map: Dict, stats_map: Dict) -> PhysicalHost:
        """
        Sync a single hypervisor to the database.
        
        Args:
            client: OpenStack client
            cluster: Parent cluster
            hyp: Hypervisor object from OpenStack
            bmc_map: BMC/iDRAC IP mapping
            stats_map: Pre-fetched hypervisor statistics
        
        Returns:
            PhysicalHost instance
        """
        found_idrac_ip = bmc_map.get(hyp.name) or bmc_map.get(hyp.id)
        raw_stats = stats_map.get(hyp.name, {})
        
        cpu_count = raw_stats.get('vcpus') or hyp.vcpus or 0
        vcpus_used = raw_stats.get('vcpus_used') or hyp.vcpus_used or 0
        memory_mb = raw_stats.get('memory_mb') or hyp.memory_size or 0
        memory_mb_used = raw_stats.get('memory_mb_used') or hyp.memory_used or 0
        host_ip = raw_stats.get('host_ip') or hyp.host_ip or '0.0.0.0'

        host_values = {
            'ip_address': host_ip,
            'cpu_count': cpu_count,
            'vcpus_used': vcpus_used,
            'memory_mb': memory_mb,
            'memory_mb_used': memory_mb_used,
            'state': hyp.state,
            'status': hyp.status,
        }
        
        if found_idrac_ip:
            host_values['idrac_ip'] = found_idrac_ip

        host, created = PhysicalHost.objects.update_or_create(
            cluster=cluster,
            hostname=hyp.name,
            defaults=host_values
        )
        
        return host

    @staticmethod
    def sync_instance(host: PhysicalHost, server, volume_map: Dict) -> Instance:
        """
        Sync a single instance to the database.
        
        Args:
            host: Parent host
            server: Server object from OpenStack
            volume_map: Pre-fetched volume mapping
        
        Returns:
            Instance object
        """
        # Extract Network Info
        ip_address = None
        network_name = 'provider-net'
        if server.addresses:
            for net_name, addrs in server.addresses.items():
                for addr in addrs:
                    if addr.get('version') == 4:
                        ip_address = addr.get('addr')
                        network_name = net_name
                        break
                if ip_address:
                    break
        
        # Extract image name
        image_name = 'N/A'
        if server.image:
            if isinstance(server.image, dict):
                image_name = server.image.get('id') or 'Unknown ID'
            elif isinstance(server.image, str):
                image_name = server.image
        
        # Parse launch time
        launched_at = None
        if server.launched_at:
            launched_at = parse_datetime(server.launched_at)
            if launched_at and timezone.is_naive(launched_at):
                launched_at = timezone.make_aware(launched_at)

        inst_obj, created = Instance.objects.update_or_create(
            uuid=server.id,
            defaults={
                'host': host,
                'name': server.name,
                'status': server.status,
                'flavor_name': server.flavor.get('original_name', 'unknown'),
                'project_id': server.project_id,
                'user_id': server.user_id,
                'ip_address': ip_address,
                'network_name': network_name,
                'image_name': image_name,
                'key_name': server.key_name or '-',
                'launched_at': launched_at
            }
        )
        
        # Sync volumes
        InventoryService._sync_instance_volumes(inst_obj, server.id, volume_map)
        
        return inst_obj

    @staticmethod
    def _sync_instance_volumes(instance: Instance, server_id: str, volume_map: Dict) -> None:
        """Sync volumes for an instance."""
        try:
            volumes = volume_map.get(server_id, [])
            for vol in volumes:
                Volume.objects.update_or_create(
                    uuid=vol.id,
                    defaults={
                        'instance': instance,
                        'name': vol.name or '',
                        'size_gb': vol.size or 0,
                        'device': vol.attachments[0].get('device') if vol.attachments else '',
                        'status': vol.status or 'unknown',
                        'is_bootable': getattr(vol, 'bootable', False)
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to sync volumes for instance {instance.uuid}: {e}")

    @staticmethod
    def build_bmc_map(client: OpenStackClient) -> Dict[str, str]:
        """
        Build a mapping of node names/IDs to BMC/iDRAC IPs from Ironic.
        
        Returns:
            Dict mapping node identifiers to BMC IP addresses
        """
        bmc_map = {}
        try:
            for node in client.conn.baremetal.nodes():
                driver_info = node.driver_info
                address = (
                    driver_info.get('redfish_address') or 
                    driver_info.get('ipmi_address') or 
                    driver_info.get('drac_address')
                )
                if address:
                    address = address.replace('https://', '').replace('http://', '').split('/')[0]
                    if node.name:
                        bmc_map[node.name] = address
                    bmc_map[node.id] = address
                    if node.instance_id:
                        bmc_map[node.instance_id] = address
        except Exception as e:
            logger.debug(f"BMC mapping not available: {e}")
        
        return bmc_map

    @staticmethod
    def build_hypervisor_stats_map(client: OpenStackClient, cluster_name: str) -> Dict[str, Dict]:
        """
        Fetch bulk hypervisor statistics.
        
        Returns:
            Dict mapping hostname to stats dict
        """
        stats_map = {}
        try:
            raw_resp = client.conn.compute.get('/os-hypervisors/detail')
            if raw_resp.status_code == 200:
                raw_list = raw_resp.json().get('hypervisors', [])
                for h in raw_list:
                    stats_map[h.get('hypervisor_hostname')] = h
        except Exception as e:
            logger.warning(f"[{cluster_name}] Failed to fetch bulk stats: {e}")
        
        return stats_map

    @staticmethod
    def build_instance_map(client: OpenStackClient, cluster_name: str) -> Dict[str, List]:
        """
        Build a mapping of hostnames to instances.
        
        Returns:
            Dict mapping hostname to list of server objects
        """
        host_instance_map = defaultdict(list)
        try:
            all_servers = list(client.conn.compute.servers(details=True, all_tenants=True))
            for srv in all_servers:
                h_name = srv.hypervisor_hostname or srv.compute_host
                if h_name:
                    host_instance_map[h_name].append(srv)
        except Exception as e:
            logger.warning(f"[{cluster_name}] Failed to bulk fetch instances: {e}")
        
        return host_instance_map

    @staticmethod
    def build_volume_map(client: OpenStackClient, cluster_name: str) -> Dict[str, List]:
        """
        Build a mapping of instance IDs to volumes.
        
        Returns:
            Dict mapping server_id to list of volume objects
        """
        volume_map = defaultdict(list)
        try:
            all_volumes = list(client.conn.block_storage.volumes(all_tenants=True))
            for vol in all_volumes:
                for attachment in vol.attachments:
                    server_id = attachment.get('server_id')
                    if server_id:
                        volume_map[server_id].append(vol)
        except Exception as e:
            logger.warning(f"[{cluster_name}] Failed to bulk fetch volumes: {e}")
        
        return volume_map

    @staticmethod
    def refresh_host(host: PhysicalHost) -> bool:
        """
        Refresh a single host's data from OpenStack.
        
        Returns:
            True if successful, False otherwise
        """
        if "fake" in host.cluster.auth_url:
            return False
            
        try:
            client = OpenStackClient(host.cluster)
            hyp = client.get_hypervisor_by_name(host.hostname)
            
            if hyp:
                host.ip_address = hyp.host_ip
                host.cpu_count = hyp.vcpus
                host.vcpus_used = hyp.vcpus_used
                host.memory_mb = hyp.memory_size
                host.memory_mb_used = hyp.memory_used
                host.state = hyp.state
                host.status = hyp.status
                host.save()
                
                # Refresh instances on this node
                instances = client.get_instances(host_name=host.hostname)
                for server in instances:
                    Instance.objects.update_or_create(
                        uuid=server.id,
                        defaults={
                            'host': host,
                            'name': server.name,
                            'status': server.status,
                            'flavor_name': server.flavor.get('original_name', 'unknown'),
                            'project_id': server.project_id,
                            'user_id': server.user_id
                        }
                    )
                
                return True
                
        except Exception as e:
            logger.error(f"Host refresh failed for {host.hostname}: {e}")
        
        return False

