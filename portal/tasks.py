import logging
import time
import traceback
from collections import defaultdict

import redfish
import requests
from requests.auth import HTTPBasicAuth
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.utils.dateparse import parse_datetime
from keystoneauth1 import exceptions as ka_exceptions

from .models import Cluster, PhysicalHost, Instance, Alert, ClusterService, AuditLog, Flavor, PortalSettings, Volume
from .openstack_utils import OpenStackClient

logger = logging.getLogger(__name__)

# iDRAC credentials - should be configured in settings/environment
IDRAC_DEFAULT_USER = getattr(settings, 'IDRAC_DEFAULT_USER', 'root')
IDRAC_DEFAULT_PASSWORD = getattr(settings, 'IDRAC_DEFAULT_PASSWORD', 'calvin')

@shared_task
def sync_inventory():
    """
    Syncs OpenStack Services, Hypervisors, Instances, and Volumes.
    Optimized to reduce API calls to the OpenStack controller.
    """
    task_start = time.time()
    logger.info("Starting inventory sync task")
    
    clusters = Cluster.objects.all()
    for cluster in clusters:
        logger.info(f"Processing cluster: {cluster.name}")
        cluster_start = time.time()
        try:
            client = OpenStackClient(cluster)
            detected_version = client.get_cluster_release()

            if cluster.status != 'online':
                cluster.status = 'online'
                cluster.save()

            # 1. Services
            t0 = time.time()
            services = client.get_services()
            for svc in services:
                ClusterService.objects.update_or_create(
                    cluster=cluster, binary=svc.binary, host=svc.host,
                    defaults={'zone': getattr(svc, 'availability_zone', 'nova'), 'status': svc.status, 'state': svc.state, 'version': detected_version}
                )
            logger.debug(f"[{cluster.name}] Services synced in {time.time() - t0:.2f}s")

            # 2. Ironic (BMC) - One bulk call usually not available via SDK, so iterating list is fast enough (internal DB)
            t0 = time.time()
            bmc_map = {}
            try:
                for node in client.conn.baremetal.nodes():
                    driver_info = node.driver_info
                    address = driver_info.get('redfish_address') or driver_info.get('ipmi_address') or driver_info.get('drac_address')
                    if address:
                        address = address.replace('https://', '').replace('http://', '').split('/')[0]
                        if node.name: bmc_map[node.name] = address
                        bmc_map[node.id] = address
                        if node.instance_id: bmc_map[node.instance_id] = address
            except Exception: pass
            logger.debug(f"[{cluster.name}] BMC mapped in {time.time() - t0:.2f}s")

            # 3. Hypervisors (Hosts)
            t0 = time.time()
            logger.debug(f"[{cluster.name}] Fetching Hypervisor List...")
            hypervisors = client.get_hypervisors() # 1st API Call (Summary)
            logger.debug(f"[{cluster.name}] Hypervisor list ({len(hypervisors)}) fetched in {time.time() - t0:.2f}s")
            
            # --- OPTIMIZATION 1: Fetch ALL Host details in 1 Call ---
            t0 = time.time()
            hypervisor_stats_map = {}
            try:
                logger.debug(f"[{cluster.name}] Fetching bulk usage stats...")
                raw_resp = client.conn.compute.get('/os-hypervisors/detail')
                if raw_resp.status_code == 200:
                    raw_list = raw_resp.json().get('hypervisors', [])
                    for h in raw_list:
                        hypervisor_stats_map[h.get('hypervisor_hostname')] = h
            except Exception as e:
                logger.warning(f"[{cluster.name}] Failed to fetch bulk stats: {e}")
            logger.debug(f"[{cluster.name}] Bulk stats fetched in {time.time() - t0:.2f}s")

            # --- OPTIMIZATION 2: Fetch ALL Instances & Volumes in Bulk ---
            # Instead of N+1 calls inside the loop, we fetch everything once and map it in memory.
            logger.debug(f"[{cluster.name}] Fetching ALL Instances & Volumes (Bulk)...")
            
            t0 = time.time()
            host_instance_map = defaultdict(list)
            try:
                # Fetch all servers across all tenants with details
                all_servers = list(client.conn.compute.servers(details=True, all_tenants=True))
                for srv in all_servers:
                    # Determine which host this instance belongs to
                    h_name = srv.hypervisor_hostname or srv.compute_host
                    if h_name:
                        host_instance_map[h_name].append(srv)
            except Exception as e:
                logger.warning(f"[{cluster.name}] Failed to bulk fetch instances: {e}")
            logger.debug(f"[{cluster.name}] {len(host_instance_map)} Hosts mapped with instances in {time.time() - t0:.2f}s")

            t0 = time.time()
            instance_volume_map = defaultdict(list)
            try:
                # Fetch all volumes across all tenants
                all_volumes = list(client.conn.block_storage.volumes(all_tenants=True))
                for vol in all_volumes:
                    # A volume can be attached to multiple instances (rare, but possible in multi-attach)
                    for attachment in vol.attachments:
                        server_id = attachment.get('server_id')
                        if server_id:
                            instance_volume_map[server_id].append(vol)
            except Exception as e:
                logger.warning(f"[{cluster.name}] Failed to bulk fetch volumes: {e}")
            logger.debug(f"[{cluster.name}] {len(instance_volume_map)} Instances mapped with volumes in {time.time() - t0:.2f}s")

            logger.debug(f"[{cluster.name}] Processing {len(hypervisors)} hypervisors...")
            
            loop_start = time.time()
            for i, hyp in enumerate(hypervisors):
                # Progress monitor
                if (i + 1) % 5 == 0:
                    logger.debug(f"Processing host {i+1}/{len(hypervisors)} ({hyp.name})...")

                found_idrac_ip = bmc_map.get(hyp.name) or bmc_map.get(hyp.id)
                raw_stats = hypervisor_stats_map.get(hyp.name, {})
                
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
                    'openstack_version': detected_version
                }
                if found_idrac_ip:
                    host_values['idrac_ip'] = found_idrac_ip

                host, created = PhysicalHost.objects.update_or_create(
                    cluster=cluster,
                    hostname=hyp.name,
                    defaults=host_values
                )
                
                # 4. Instances (Look up from bulk map)
                instances = host_instance_map.get(host.hostname, [])
                
                for server in instances:
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
                            if ip_address: break
                    
                    image_name = 'N/A'
                    if server.image:
                        if isinstance(server.image, dict):
                            image_name = server.image.get('id') or 'Unknown ID'
                        elif isinstance(server.image, str):
                            image_name = server.image
                    
                    launched_at = None
                    if server.launched_at:
                        launched_at = parse_datetime(server.launched_at)
                        if timezone.is_naive(launched_at):
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
                    
                    # 5. Volumes (Look up from bulk map)
                    try:
                        volumes = instance_volume_map.get(server.id, [])
                        for vol in volumes:
                            # Note: vol is now an SDK object, not a dict
                            Volume.objects.update_or_create(
                                uuid=vol.id,
                                defaults={
                                    'instance': inst_obj,
                                    'name': vol.name or '',
                                    'size_gb': vol.size or 0,
                                    'device': vol.attachments[0].get('device') if vol.attachments else '',
                                    'status': vol.status or 'unknown',
                                    'is_bootable': getattr(vol, 'bootable', False)
                                }
                            )
                    except Exception: pass
            
            logger.info(f"[{cluster.name}] Processing loop finished in {time.time() - loop_start:.2f}s")
            AuditLog.objects.create(action="Inventory Sync Success", target=cluster.name, details=f"Synced {len(hypervisors)} hosts in {time.time() - cluster_start:.2f}s.")

        except ka_exceptions.EndpointNotFound:
            logger.error(f"[{cluster.name}] Endpoint Not Found.")
            if cluster.status != 'offline':
                cluster.status = 'offline'
                cluster.save()
        except Exception as e:
            logger.error(f"[{cluster.name}] ERROR: {e}", exc_info=True)
            if cluster.status != 'offline':
                cluster.status = 'offline'
                cluster.save()

    logger.info(f"Finished inventory sync task (Total: {time.time() - task_start:.2f}s)")

@shared_task
def sync_flavors():
    """
    Collects Flavor definitions from all clusters.
    """
    logger.info("Starting flavor sync")
    for cluster in Cluster.objects.all():
        try:
            logger.debug(f"[{cluster.name}] Syncing flavors...")
            client = OpenStackClient(cluster)
            flavors = client.get_flavors()
            count = 0
            for f in flavors:
                Flavor.objects.update_or_create(
                    uuid=f.id,
                    cluster=cluster,
                    defaults={
                        'name': f.name,
                        'vcpus': f.vcpus,
                        'ram_mb': f.ram,
                        'disk_gb': f.disk,
                        'is_public': f.is_public
                    }
                )
                count += 1
            logger.info(f"[{cluster.name}] Synced {count} flavors.")
            AuditLog.objects.create(
                action="Flavor Sync Success",
                target=cluster.name,
                details=f"Synced {count} flavors."
            )
        except Exception as e:
            logger.error(f"[{cluster.name}] Flavor sync error: {e}")
            AuditLog.objects.create(
                action="Flavor Sync Failed",
                target=cluster.name,
                details=str(e)
            )

@shared_task
def sync_openmanage():
    """
    Connects to Dell OpenManage Enterprise (OME) to fetch hardware inventory and alerts.
    """
    # Using 'portal_settings' to avoid shadowing global 'settings'
    portal_settings = PortalSettings.get_settings()
    
    if not portal_settings.ome_url or not portal_settings.ome_username:
        logger.info("OME Sync Skipped: No URL/Username configured.")
        return

    base_url = portal_settings.ome_url.rstrip('/')
    auth = HTTPBasicAuth(portal_settings.ome_username, portal_settings.ome_password)
    
    logger.info(f"Connecting to OME: {base_url}")
    
    try:
        # 1. Fetch Devices
        resp = requests.get(f"{base_url}/api/DeviceService/Devices", auth=auth, verify=False, timeout=30)
        if resp.status_code == 200:
            devices = resp.json().get('value', [])
            synced_count = 0
            
            for device in devices:
                # Try matching by Management IP (iDRAC IP)
                mgmt_ip = None
                if device.get('DeviceManagement'):
                     mgmt_ip = device.get('DeviceManagement')[0].get('NetworkAddress')
                
                host = None
                if mgmt_ip:
                    host = PhysicalHost.objects.filter(idrac_ip=mgmt_ip).first()
                
                if not host:
                    # Fallback: match by hostname if it matches OME DeviceName
                    host = PhysicalHost.objects.filter(hostname__iexact=device.get('DeviceName')).first()
                
                if host:
                    # Update Hardware Info
                    host.service_tag = device.get('DeviceServiceTag', '')
                    # OME 'Model' often contains the server model name
                    host.cpu_model = device.get('Model', '') 
                    
                    # Status mapping (Simplified)
                    # OME Status: 1000=OK, 3000=Critical, 2000=Warning
                    health_status = str(device.get('Status', 'Unknown'))
                    if '1000' in health_status: 
                        host.hardware_health = 'OK'
                    elif '3000' in health_status: 
                        host.hardware_health = 'Critical'
                    else: 
                        host.hardware_health = 'Warning'
                    
                    host.save()
                    synced_count += 1
            
            logger.info(f"OME Sync: Updated {synced_count} hosts.")
            AuditLog.objects.create(action="OME Sync Success", target="OpenManage", details=f"Updated {synced_count} hosts from OME.")

        # 2. Fetch Active Alerts
        alert_resp = requests.get(f"{base_url}/api/AlertService/Alerts?$filter=SeverityType ne 'Normal'", auth=auth, verify=False, timeout=30)
        if alert_resp.status_code == 200:
            alerts = alert_resp.json().get('value', [])
            for alert in alerts:
                src_ip = alert.get('MachineAddress')
                host = PhysicalHost.objects.filter(idrac_ip=src_ip).first()
                
                if host:
                    Alert.objects.get_or_create(
                        target_host=host,
                        title=alert.get('MessageId', 'OME Alert'),
                        defaults={
                            'source': 'OpenManage',
                            'description': alert.get('Message', 'Hardware Alert'),
                            'severity': 'critical' if 'Critical' in str(alert.get('SeverityType')) else 'warning',
                            'is_active': True
                        }
                    )

    except Exception as e:
        logger.error(f"OpenManage Sync Failed: {e}")
        AuditLog.objects.create(action="OME Sync Failed", target="OpenManage", details=str(e))

@shared_task
def collect_hardware_health():
    """
    Connects to physical hosts via Redfish (iDRAC) to check actual hardware health.
    Fallback if OME is not used.
    """
    hosts = PhysicalHost.objects.exclude(idrac_ip__isnull=True).exclude(idrac_ip__exact='')
    logger.info(f"Starting Redfish hardware poll for {hosts.count()} hosts.")

    for host in hosts:
        redfish_client = None
        try:
            redfish_client = redfish.redfish_client(
                base_url=f"https://{host.idrac_ip}",
                username=IDRAC_DEFAULT_USER,
                password=IDRAC_DEFAULT_PASSWORD,
                default_prefix='/redfish/v1',
                timeout=10
            )
            redfish_client.login(auth="session")

            # Check System Health
            sys_resp = redfish_client.get("/redfish/v1/Systems/System.Embedded.1")
            if sys_resp.status != 200:
                sys_resp = redfish_client.get("/redfish/v1/Systems/1")
            
            if sys_resp.status == 200:
                health = sys_resp.dict.get('Status', {}).get('Health', 'Unknown')
                if health in ['Warning', 'Critical']:
                    logger.warning(f"[{host.hostname}] Health Issue: {health}")
                    Alert.objects.get_or_create(
                        target_host=host,
                        title=f"System Health: {health}",
                        defaults={
                            'source': "Redfish",
                            'description': f"Global system status reported as {health}",
                            'severity': 'critical' if health == 'Critical' else 'warning',
                            'is_active': True
                        }
                    )
                    # Log the issue finding
                    AuditLog.objects.create(
                        action="Hardware Issue Detected",
                        target=host.hostname,
                        details=f"Redfish reported health: {health}"
                    )

        except Exception as e:
            pass
            
        finally:
            if redfish_client:
                redfish_client.logout()
