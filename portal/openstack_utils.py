import logging
import urllib3

import openstack
from urllib.parse import urlparse, parse_qs
from django.conf import settings
from openstack import exceptions
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from keystoneauth1 import exceptions as ks_exceptions

logger = logging.getLogger(__name__)

# Retry decorator for transient OpenStack errors
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
        f"OpenStack API call failed, retrying in {retry_state.next_action.sleep}s... "
        f"(attempt {retry_state.attempt_number}/3)"
    )
)

# Suppress InsecureRequestWarning to keep logs clean when using self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OpenStackClient:
    def __init__(self, cluster_obj):
        self.conn = openstack.connect(
            auth_url=cluster_obj.auth_url,
            project_name=cluster_obj.project_name,
            username=cluster_obj.username,
            password=cluster_obj.get_password(),
            region_name=cluster_obj.region_name,
            user_domain_name=cluster_obj.user_domain_name,
            project_domain_name=cluster_obj.project_domain_name,
            verify=False,
            connect_timeout=getattr(settings, 'OPENSTACK_CONNECT_TIMEOUT', 10),
            read_timeout=getattr(settings, 'OPENSTACK_READ_TIMEOUT', 60),
        )

    def get_cluster_release(self):
        try:
            versions = self.conn.compute.versions()
            max_version = 0.0
            for v in versions:
                try:
                    if hasattr(v, 'version') and v.version:
                        ver_float = float(v.version)
                        if ver_float > max_version:
                            max_version = ver_float
                except (ValueError, TypeError):
                    continue
            if max_version >= 2.95: return "2023.2 (Bobcat)"
            if max_version >= 2.93: return "2023.1 (Antelope)"
            if max_version >= 2.90: return "Zed"
            return f"Unknown (API v{max_version})"
        except Exception:
            return "Unknown"

    @openstack_retry
    def get_services(self):
        return list(self.conn.compute.services())

    @openstack_retry
    def get_hypervisors(self):
        return list(self.conn.compute.hypervisors(details=True))

    @openstack_retry
    def get_hypervisor_by_name(self, hostname):
        hyps = list(self.conn.compute.hypervisors(name=hostname))
        return hyps[0] if hyps else None

    @openstack_retry
    def get_instances(self, host_name=None):
        filters = {}
        if host_name:
            filters['compute_host'] = host_name
            filters['all_tenants'] = True
        return list(self.conn.compute.servers(**filters))

    @openstack_retry
    def get_flavors(self):
        return list(self.conn.compute.flavors(is_public=None))

    @openstack_retry
    def get_server_by_uuid(self, uuid):
        return self.conn.compute.get_server(uuid)

    @openstack_retry
    def get_attached_volumes(self, server_id):
        try:
            server = self.conn.compute.get_server(server_id)
            if not server or not hasattr(server, 'attached_volumes'):
                return []
            volumes = []
            for attachment in server.attached_volumes:
                vol_id = attachment['id']
                try:
                    vol_data = self.conn.block_storage.get_volume(vol_id)
                    if vol_data:
                        volumes.append({
                            'uuid': vol_data.id,
                            'name': vol_data.name or vol_id[:8],
                            'size': vol_data.size,
                            'device': attachment.get('device', ''),
                            'status': vol_data.status,
                            'bootable': vol_data.is_bootable
                        })
                except Exception:
                    volumes.append({
                        'uuid': vol_id, 'name': 'Unknown Volume', 'size': 0,
                        'device': attachment.get('device', ''), 'status': 'unknown', 'bootable': False
                    })
            return volumes
        except Exception as e:
            logger.error(f"Error fetching volumes for {server_id}: {e}")
            return []

    @openstack_retry
    def get_realtime_stats(self, server_id):
        try:
            return self.conn.compute.get_server_diagnostics(server_id)
        except Exception:
            return None

    @openstack_retry
    def get_all_servers(self, all_tenants=True):
        """Fetch all servers with retry logic."""
        return list(self.conn.compute.servers(details=True, all_tenants=all_tenants))

    @openstack_retry
    def get_all_volumes(self, all_tenants=True):
        """Fetch all volumes with retry logic."""
        return list(self.conn.block_storage.volumes(all_tenants=all_tenants))

    def migrate_instance(self, server_id):
        self.conn.compute.live_migrate_server(server_id, block_migration='auto')

    def evacuate_host(self, host_name):
        instances = self.get_instances(host_name=host_name)
        for instance in instances:
            self.migrate_instance(instance.id)
        return len(instances)

    def get_novnc_console(self, server_id):
        """
        Get NoVNC Console URL. 
        Attempts modern 'remote-console' first, then legacy 'os-getVNCConsole'.
        """
        logger.debug(f"Requesting NoVNC console for {server_id}...")
        
        # Method 1: SDK Default
        try:
            logger.debug("Trying SDK get_server_console_url (novnc)...")
            console = self.conn.compute.get_server_console_url(server_id, console_type='novnc')
            logger.debug("SDK returned console URL successfully")
            return console['url']
        except Exception as e:
            logger.debug(f"SDK console failed: {e}. Trying explicit fallbacks...")

        # Method 2: Explicit Modern 'remote-console' request (Nova 2.6+)
        try:
            url = f"/servers/{server_id}/remote-console"
            body = {"remote_console": {"protocol": "vnc", "type": "novnc"}}
            logger.debug(f"Trying POST {url}")
            resp = self.conn.compute.post(url, json=body, microversion="2.6")
            if resp.status_code == 200:
                logger.debug("Explicit remote-console success")
                return resp.json()['remote_console']['url']
            else:
                logger.debug(f"Remote-console returned {resp.status_code}")
        except Exception as e:
            logger.debug(f"Explicit remote-console failed: {e}")

        # Method 3: Legacy 'os-getVNCConsole'
        try:
            logger.debug("Trying legacy os-getVNCConsole...")
            url = f"/servers/{server_id}/action"
            body = {"os-getVNCConsole": {"type": "novnc"}}
            resp = self.conn.compute.post(url, json=body)
            if resp.status_code == 200:
                logger.debug("Legacy console success")
                return resp.json()['console']['url']
            else:
                logger.debug(f"Legacy console returned {resp.status_code}")
                if resp.status_code == 404:
                    raise exceptions.ResourceNotFound(message=f"Instance {server_id} or console action not found.")
                
        except Exception as e:
            logger.debug(f"Legacy console exception: {e}")
            raise e

        raise Exception("Could not retrieve NoVNC console URL")

    def get_spice_console(self, server_id):
        """
        Get SPICE Console URL. 
        Attempts modern 'remote-console' first, then legacy 'os-getSPICEConsole'.
        """
        logger.debug(f"Requesting SPICE console for {server_id}...")

        # Method 1: SDK Default
        try:
            logger.debug("Trying SDK get_server_console_url (spice-html5)...")
            console = self.conn.compute.get_server_console_url(server_id, console_type='spice-html5')
            url = console['url']
            logger.debug("SDK returned SPICE URL successfully")
            parsed = urlparse(url)
            token = parse_qs(parsed.query).get('token', [None])[0]
            return {'url': url, 'token': token, 'protocol': 'spice'}
        except Exception as e:
            logger.debug(f"SDK default SPICE failed: {e}. Trying manual...")

        # Method 2: Modern 'remote-console'
        try:
            url = f"/servers/{server_id}/remote-console"
            body = {"remote_console": {"protocol": "spice", "type": "spice-html5"}}
            logger.debug(f"Trying POST {url}")
            resp = self.conn.compute.post(url, json=body, microversion="2.6")
            if resp.status_code == 200:
                full_url = resp.json()['remote_console']['url']
                logger.debug("Remote-console SPICE success")
                parsed = urlparse(full_url)
                token = parse_qs(parsed.query).get('token', [None])[0]
                return {'url': full_url, 'token': token, 'protocol': 'spice'}
            else:
                logger.debug(f"Remote-console returned {resp.status_code}")
        except Exception as e:
            logger.debug(f"Remote-console failed: {e}")

        # Method 3: Legacy
        try:
            logger.debug("Trying legacy os-getSPICEConsole...")
            url = f"/servers/{server_id}/action"
            body = {"os-getSPICEConsole": {"type": "spice-html5"}}
            resp = self.conn.compute.post(url, json=body)
            if resp.status_code == 200:
                full_url = resp.json()['console']['url']
                logger.debug("Legacy SPICE console success")
                parsed = urlparse(full_url)
                token = parse_qs(parsed.query).get('token', [None])[0]
                return {'url': full_url, 'token': token, 'protocol': 'spice'}
            else:
                logger.debug(f"Legacy console returned {resp.status_code}")
        except Exception as e:
            logger.debug(f"Legacy console exception: {e}")
            raise e
            
        raise Exception("Could not retrieve SPICE console URL")