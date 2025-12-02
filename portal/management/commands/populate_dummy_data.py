import random
import uuid
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.models import User
from portal.models import Cluster, PhysicalHost, Instance, Alert, ClusterService, AuditLog, Flavor, Volume, ServerCostProfile

class Command(BaseCommand):
    help = 'Populates the database with massive dummy OpenStack inventory and logs'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Deleting old data...'))
        Instance.objects.all().delete()
        PhysicalHost.objects.all().delete()
        ClusterService.objects.all().delete()
        Flavor.objects.all().delete()
        Cluster.objects.all().delete()
        Alert.objects.all().delete()
        AuditLog.objects.all().delete()
        Volume.objects.all().delete() 
        ServerCostProfile.objects.all().delete()
        
        # Note: We are not deleting AppVersion here to preserve history if it exists, 
        # or you can uncomment the line below to reset it.
        # AppVersion.objects.all().delete() 

        # --- Populate Inventory ---
        # Requirement: 3 Regions, 2 Clusters each
        CLUSTERS_CONFIG = {
            'APAC': ['Singapore-Prod-01', 'Tokyo-GPU-Cluster'],
            'EMEA': ['London-Gen-01', 'Frankfurt-Fin-02'],
            'AMER': ['NVirginia-Primary', 'Oregon-DR-Site']
        }
        
        os_types = [
            {'name': 'Ubuntu 22.04 LTS', 'flavor': 'm1.medium', 'prefix': 'ub', 'img_id': 'a4b5-2204'},
            {'name': 'Windows Server 2022', 'flavor': 'win.large', 'prefix': 'win', 'img_id': 'w2k22-v5'},
            {'name': 'CentOS Stream 9', 'flavor': 'm1.large', 'prefix': 'cos', 'img_id': 'c9-stream'},
        ]
        profiles = [
            ServerCostProfile.objects.create(name="Dell PowerEdge R640", monthly_amortization=150.00, average_watts=250),
            ServerCostProfile.objects.create(name="Dell PowerEdge R740xd", monthly_amortization=280.00, average_watts=450),
            ServerCostProfile.objects.create(name="HP ProLiant DL380 Gen10", monthly_amortization=220.00, average_watts=350),
        ]

        flavors_template = [
            ('m1.tiny', 1, 512, 1, True),
            ('m1.small', 1, 2048, 20, True),
            ('m1.medium', 2, 4096, 40, True),
            ('m1.large', 4, 8192, 80, True),
            ('m1.xlarge', 8, 16384, 160, True),
            ('gpu.small', 4, 16384, 40, False),
        ]

        total_vms = 0

        for az, cluster_names in CLUSTERS_CONFIG.items():
            self.stdout.write(self.style.SUCCESS(f'Creating Availability Zone: {az}'))
            
            for c_idx, cluster_name in enumerate(cluster_names):
                cluster = Cluster.objects.create(
                    name=cluster_name,
                    region_name=az,
                    auth_url="https://fake.cloud",
                    username="admin", password="x", project_name="admin"
                )
                cluster.set_password("x")

                # Services
                services = [('nova-api', 'up'), ('nova-scheduler', 'up'), ('neutron-server', 'up')]
                for binary, state in services:
                    ClusterService.objects.create(
                        cluster=cluster, binary=binary, host='controller-01',
                        zone='internal', status='enabled', state=state, version='2023.2'
                    )

                # Flavors
                for fname, vcpus, ram, disk, public in flavors_template:
                    Flavor.objects.create(uuid=str(uuid.uuid4()), cluster=cluster, name=fname, vcpus=vcpus, ram_mb=ram, disk_gb=disk, is_public=public)

                # Hosts
                # 5 to 8 hosts per cluster * 6 clusters = ~30 to 48 hosts
                num_hosts = random.randint(5, 8)
                hosts = []
                for h_idx in range(num_hosts):
                    host = PhysicalHost.objects.create(
                        cluster=cluster,
                        hostname=f"{cluster_name.lower()}-node-{h_idx:02d}",
                        ip_address=f"10.0.{c_idx}.{h_idx+10}",
                        cpu_count=64, vcpus_used=random.randint(0, 60),
                        memory_mb=256000, memory_mb_used=random.randint(10000, 200000),
                        state='up', status='enabled',
                        server_model=random.choice(profiles) # Assign random cost profile
                    )
                    hosts.append(host)
        

                # Instances
                # ~15 VMs per host * ~40 hosts = ~600 VMs total (close to 500 target)
                for _ in range(random.randint(12, 18)):
                    host = random.choice(hosts)
                    os_choice = random.choice(os_types)
                    
                    # New Fields Population
                    launched_time = timezone.now() - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
                    
                    inst =Instance.objects.create(
                        host=host, 
                        name=f"{os_choice['prefix']}-{str(uuid.uuid4())[:8]}",
                        flavor_name=os_choice['flavor'],
                        status='ACTIVE',
                        project_id="p1", user_id="u1",
                        last_cpu_usage_pct=random.uniform(1, 80),
                        last_ram_usage_mb=random.uniform(1024, 8192),
                        ip_address=f"192.168.1.{random.randint(2,254)}",
                        
                        # New Fields
                        image_name=os_choice['img_id'],
                        key_name="prod-keypair-rsa",
                        launched_at=launched_time
                    )
                    total_vms += 1
                    # --- CREATE VOLUMES ---
                    # Boot volume
                    Volume.objects.create(
                        uuid=str(uuid.uuid4()), instance=inst, name=f"vol-{inst.name}-root",
                        size_gb=random.choice([40, 80, 100]), device="/dev/vda", status="in-use", is_bootable=True
                    )
                    # Extra volume (50% chance)
                    if random.choice([True, False]):
                        Volume.objects.create(
                            uuid=str(uuid.uuid4()), instance=inst, name=f"vol-{inst.name}-data",
                            size_gb=random.choice([100, 500]), device="/dev/vdb", status="in-use", is_bootable=False
                        )
        # Logs
        admin_user, _ = User.objects.get_or_create(username="admin")
        AuditLog.objects.create(user=admin_user, action="Init", target="System", details="Dummy data populated")
        
        # --- Alerts Generation (Target: 15) ---
        self.stdout.write(self.style.SUCCESS('Generating 15 active alerts...'))
        
        all_clusters = list(Cluster.objects.all())
        all_hosts = list(PhysicalHost.objects.all())
        
        alert_templates = [
            ("Hardware/Fan", "Fan speed below threshold", "warning"),
            ("Hardware/PSU", "Power Supply redundancy lost", "critical"),
            ("Hardware/Disk", "Predictive failure on /dev/sda", "warning"),
            ("OpenStack/Nova", "Service nova-compute down", "critical"),
            ("Network", "High packet loss on bond0", "warning"),
        ]

        # Create 15 alerts
        for _ in range(15):
            # Mix of Cluster and Host alerts
            if random.choice([True, False]) and all_hosts:
                # Host Alert
                target = random.choice(all_hosts)
                template = random.choice(alert_templates)
                Alert.objects.create(
                    source=template[0].split("/")[0], target_host=target,
                    title=template[0], description=f"{template[1]} on host {target.hostname}",
                    severity=template[2], is_active=True,
                    created_at=timezone.now() - timedelta(minutes=random.randint(5, 120))
                )
            elif all_clusters:
                # Cluster Alert
                target = random.choice(all_clusters)
                Alert.objects.create(
                    source="OpenStack", target_cluster=target,
                    title="API High Latency", description="Control plane latency > 200ms",
                    severity="warning", is_active=True,
                    created_at=timezone.now() - timedelta(minutes=random.randint(5, 120))
                )
        help = 'Populates database with dummy data'

        def handle(self, *args, **options):
            # ... (Previous deletions) ...
            ServerCostProfile.objects.all().delete()

            # Create Cost Profiles
            profiles = [
                ServerCostProfile.objects.create(name="Dell PowerEdge R640", monthly_amortization=150.00, average_watts=250),
                ServerCostProfile.objects.create(name="Dell PowerEdge R740xd", monthly_amortization=280.00, average_watts=450),
                ServerCostProfile.objects.create(name="HP ProLiant DL380 Gen10", monthly_amortization=220.00, average_watts=350),
            ]
        self.stdout.write(self.style.SUCCESS(f'Successfully generated {total_vms} instances across {len(all_clusters)} clusters.'))