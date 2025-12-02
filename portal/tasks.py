from celery import shared_task
from django.utils import timezone
from django.conf import settings
# Ensure PortalSettings and Volume are imported here
from .models import Cluster, PhysicalHost, Instance, Alert, ClusterService, AuditLog, Flavor, PortalSettings, Volume
from .openstack_utils import OpenStackClient
import redfish
import json
import requests
import traceback
import os
from requests.auth import HTTPBasicAuth
from keystoneauth1 import exceptions as ka_exceptions
from django.utils.dateparse import parse_datetime

# Configuration for iDRAC connections (Direct Redfish fallback)
IDRAC_DEFAULT_USER = os.environ.get("IDRAC_USER", "root")
IDRAC_DEFAULT_PASSWORD = os.environ.get("IDRAC_PASSWORD", "calvin")

@shared_task
def sync_inventory():
    """
    Syncs OpenStack Services, Hypervisors, Instances, and Volumes.
    Includes detailed debug logging for Hypervisor stats.
    """
    print(">>> STARTING INVENTORY SYNC TASK")
    
    clusters = Cluster.objects.all()
    for cluster in clusters:
        print(f"--- Processing Cluster: {cluster.name} ---")
        try:
            client = OpenStackClient(cluster)
            detected_version = client.get_cluster_release()

            if cluster.status != 'online':
                cluster.status = 'online'
                cluster.save()

            # 1. Services
            services = client.get_services()
            for svc in services:
                ClusterService.objects.update_or_create(
                    cluster=cluster, binary=svc.binary, host=svc.host,
                    defaults={'zone': getattr(svc, 'availability_zone', 'nova'), 'status': svc.status, 'state': svc.state, 'version': detected_version}
                )

            # 2. Ironic (BMC)
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

            # 3. Hypervisors (Hosts)
            print(f"  [{cluster.name}] Fetching Hypervisors...")
            hypervisors = client.get_hypervisors()
            print(f"  [{cluster.name}] Found {len(hypervisors)} hypervisors.")
            
            # --- FETCH RAW STATS (Optimization) ---
            # Fetch all details once using raw API to bypass SDK issues
            raw_stats_map = {}
            try:
                print(f"  [{cluster.name}] Fetching raw hypervisor details via /os-hypervisors/detail...")
                raw_resp = client.conn.compute.get('/os-hypervisors/detail')
                if raw_resp.status_code == 200:
                    raw_list = raw_resp.json().get('hypervisors', [])
                    for h in raw_list:
                        # Map by hostname
                        raw_stats_map[h.get('hypervisor_hostname')] = h
                    print(f"  [{cluster.name}] Successfully mapped stats for {len(raw_stats_map)} hosts.")
            except Exception as e:
                print(f"  [{cluster.name}] Failed to fetch raw stats: {e}")

            for hyp in hypervisors:
                found_idrac_ip = bmc_map.get(hyp.name) or bmc_map.get(hyp.id)
                
                # --- USE RAW STATS ONLY ---
                raw_data = raw_stats_map.get(hyp.name, {})
                
                cpu_count = raw_data.get('vcpus') or 0
                vcpus_used = raw_data.get('vcpus_used') or 0
                memory_mb = raw_data.get('memory_mb') or 0
                memory_mb_used = raw_data.get('memory_mb_used') or 0

                print(f"    > Host: {hyp.name} [CPUs: {vcpus_used}/{cpu_count}, RAM: {memory_mb_used}/{memory_mb}]")
                
                # Host IP Fallback
                host_ip = hyp.host_ip if hyp.host_ip else '0.0.0.0'

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
                
                # 4. Instances
                instances = client.get_instances(host_name=host.hostname)
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
                    
                    # Extract Image Info
                    image_name = 'N/A'
                    if server.image:
                        if isinstance(server.image, dict):
                            image_name = server.image.get('id') or 'Unknown ID'
                        elif isinstance(server.image, str):
                            image_name = server.image
                    
                    # Timezone awareness
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
                    
                    # 5. Volumes
                    try:
                        volumes = client.get_attached_volumes(server.id)
                        for vol in volumes:
                            Volume.objects.update_or_create(
                                uuid=vol['uuid'],
                                defaults={
                                    'instance': inst_obj,
                                    'name': vol.get('name') or '',
                                    'size_gb': vol.get('size') or 0,
                                    'device': vol.get('device') or '',
                                    'status': vol.get('status', 'unknown'),
                                    'is_bootable': vol.get('bootable', False)
                                }
                            )
                    except Exception as e:
                        print(f"      ! Volume sync error for {server.name}: {e}")

            AuditLog.objects.create(action="Inventory Sync Success", target=cluster.name, details=f"Synced {len(hypervisors)} hosts.")

        except ka_exceptions.EndpointNotFound:
            print(f"  [{cluster.name}] Endpoint Not Found.")
            if cluster.status != 'offline':
                cluster.status = 'offline'
                cluster.save()
        except Exception as e:
            print(f"  [{cluster.name}] ERROR: {e}")
            traceback.print_exc()
            if cluster.status != 'offline':
                cluster.status = 'offline'
                cluster.save()

    print("<<< FINISHED INVENTORY SYNC TASK")


@shared_task
def sync_flavors():
    """
    Collects Flavor definitions from all clusters.
    """
    print(">>> STARTING FLAVOR SYNC")
    for cluster in Cluster.objects.all():
        try:
            print(f"  [{cluster.name}] Syncing flavors...")
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
            print(f"  [{cluster.name}] Synced {count} flavors.")
            AuditLog.objects.create(
                action="Flavor Sync Success",
                target=cluster.name,
                details=f"Synced {count} flavors."
            )
        except Exception as e:
            print(f"  [{cluster.name}] Flavor sync error: {e}")
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
        print("OME Sync Skipped: No URL/Username configured.")
        return

    base_url = portal_settings.ome_url.rstrip('/')
    auth = HTTPBasicAuth(portal_settings.ome_username, portal_settings.ome_password)
    
    print(f"Connecting to OME: {base_url}")
    
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
            
            print(f"OME Sync: Updated {synced_count} hosts.")
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
        print(f"OpenManage Sync Failed: {e}")
        AuditLog.objects.create(action="OME Sync Failed", target="OpenManage", details=str(e))

@shared_task
def collect_hardware_health():
    """
    Connects to physical hosts via Redfish (iDRAC) to check actual hardware health.
    Fallback if OME is not used.
    """
    hosts = PhysicalHost.objects.exclude(idrac_ip__isnull=True).exclude(idrac_ip__exact='')
    print(f"Starting Redfish hardware poll for {hosts.count()} hosts.")

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
                    print(f"  [{host.hostname}] Health Issue: {health}")
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