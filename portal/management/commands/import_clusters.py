import yaml
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from portal.models import Cluster

class Command(BaseCommand):
    help = 'Imports OpenStack cluster configuration from clusters.yaml'

    def handle(self, *args, **options):
        config_path = os.path.join(settings.BASE_DIR, 'clusters.yaml')
        
        if not os.path.exists(config_path):
            self.stdout.write(self.style.ERROR(f'Configuration file not found: {config_path}'))
            return

        with open(config_path, 'r') as file:
            try:
                data = yaml.safe_load(file)
                clusters = data.get('clusters', [])
                
                for c_data in clusters:
                    cluster, created = Cluster.objects.update_or_create(
                        name=c_data['name'],
                        defaults={
                            'region_name': c_data.get('region', 'RegionOne'),
                            'auth_url': c_data['auth_url'],
                            'project_domain_name': c_data.get('project_domain', 'default'),
                            'user_domain_name': c_data.get('user_domain', 'default'),
                            'username': c_data['username'],
                            'password': c_data['password'],
                            'project_name': c_data['project_name'],
                        }
                    )
                    status = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f'{status} cluster: {cluster.name}'))
                    
            except yaml.YAMLError as e:
                self.stdout.write(self.style.ERROR(f'Error parsing YAML: {e}'))