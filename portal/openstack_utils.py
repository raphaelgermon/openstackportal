import openstack
from urllib.parse import urlparse, parse_qs
from django.conf import settings
from openstack import exceptions
import json
import urllib3

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
            verify=False 
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

    def get_services(self):
        return list(self.conn.compute.services())

    def get_hypervisors(self):
        return list(self.conn.compute.hypervisors(details=True))

    def get_hypervisor_by_name(self, hostname):
        hyps = list(self.conn.compute.hypervisors(name=hostname))
        return hyps[0] if hyps else None

    def get_instances(self, host_name=None):
        filters = {}
        if host_name:
            filters['compute_host'] = host_name
            filters['all_tenants'] = True
        return list(self.conn.compute.servers(**filters))

    def get_flavors(self):
        return list(self.conn.compute.flavors(is_public=None))

    def get_server_by_uuid(self, uuid):
        return self.conn.compute.get_server(uuid)

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
            print(f"Error fetching volumes for {server_id}: {e}")
            return []

    def get_realtime_stats(self, server_id):
        try:
            return self.conn.compute.get_server_diagnostics(server_id)
        except Exception:
            return None

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
        print(f"DEBUG: Requesting NoVNC console for {server_id}...")
        
        # Method 1: SDK Default
        try:
            print(f"DEBUG: Trying SDK get_server_console_url (novnc)...")
            console = self.conn.compute.get_server_console_url(server_id, console_type='novnc')
            print(f"DEBUG: SDK returned URL: {console['url']}")
            return console['url']
        except Exception as e:
            print(f"DEBUG: SDK console failed: {e}. Trying explicit fallbacks...")

        # Method 2: Explicit Modern 'remote-console' request (Nova 2.6+)
        try:
            url = f"/servers/{server_id}/remote-console"
            body = {"remote_console": {"protocol": "vnc", "type": "novnc"}}
            print(f"DEBUG: Trying POST {url} with {body}")
            resp = self.conn.compute.post(url, json=body, microversion="2.6")
            if resp.status_code == 200:
                print("DEBUG: Explicit remote-console success")
                return resp.json()['remote_console']['url']
            else:
                print(f"DEBUG: Remote-console returned {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"DEBUG: Explicit remote-console failed: {e}")

        # Method 3: Legacy 'os-getVNCConsole'
        try:
            print("DEBUG: Trying legacy os-getVNCConsole...")
            url = f"/servers/{server_id}/action"
            body = {"os-getVNCConsole": {"type": "novnc"}}
            resp = self.conn.compute.post(url, json=body)
            if resp.status_code == 200:
                print("DEBUG: Legacy console success")
                return resp.json()['console']['url']
            else:
                print(f"DEBUG: Legacy console returned {resp.status_code}: {resp.text}")
                if resp.status_code == 404:
                    raise exceptions.ResourceNotFound(message=f"Instance {server_id} or console action not found.")
                
        except Exception as e:
             print(f"DEBUG: Legacy console exception: {e}")
             raise e

        raise Exception("Could not retrieve NoVNC console URL")

    def get_spice_console(self, server_id):
        """
        Get SPICE Console URL. 
        Attempts modern 'remote-console' first, then legacy 'os-getSPICEConsole'.
        """
        print(f"DEBUG: Requesting SPICE console for {server_id}...")

        # Method 1: SDK Default
        try:
            print(f"DEBUG: Trying SDK get_server_console_url (spice-html5)...")
            console = self.conn.compute.get_server_console_url(server_id, console_type='spice-html5')
            url = console['url']
            print(f"DEBUG: SDK returned URL: {url}")
            parsed = urlparse(url)
            token = parse_qs(parsed.query).get('token', [None])[0]
            return {'url': url, 'token': token, 'protocol': 'spice'}
        except Exception as e:
            print(f"DEBUG: SDK default SPICE failed: {e}. Trying manual...")

        # Method 2: Modern 'remote-console'
        try:
            url = f"/servers/{server_id}/remote-console"
            body = {"remote_console": {"protocol": "spice", "type": "spice-html5"}}
            print(f"DEBUG: Trying POST {url} with {body}")
            resp = self.conn.compute.post(url, json=body, microversion="2.6")
            if resp.status_code == 200:
                full_url = resp.json()['remote_console']['url']
                print(f"DEBUG: Remote-console success: {full_url}")
                parsed = urlparse(full_url)
                token = parse_qs(parsed.query).get('token', [None])[0]
                return {'url': full_url, 'token': token, 'protocol': 'spice'}
            else:
                print(f"DEBUG: Remote-console returned {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"DEBUG: Remote-console failed: {e}")

        # Method 3: Legacy
        try:
            print("DEBUG: Trying legacy os-getSPICEConsole...")
            url = f"/servers/{server_id}/action"
            body = {"os-getSPICEConsole": {"type": "spice-html5"}}
            resp = self.conn.compute.post(url, json=body)
            if resp.status_code == 200:
                full_url = resp.json()['console']['url']
                print(f"DEBUG: Legacy console success: {full_url}")
                parsed = urlparse(full_url)
                token = parse_qs(parsed.query).get('token', [None])[0]
                return {'url': full_url, 'token': token, 'protocol': 'spice'}
            else:
                print(f"DEBUG: Legacy console returned {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"DEBUG: Legacy console exception: {e}")
            raise e
            
        raise Exception("Could not retrieve SPICE console URL")