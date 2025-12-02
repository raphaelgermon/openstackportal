from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
import uuid
import base64
from cryptography.fernet import Fernet

# --- Encryption Helper ---
def get_cipher():
    key = settings.SECRET_KEY[:32].encode()
    if len(key) < 32:
        key = key.ljust(32, b'=')
    return Fernet(base64.urlsafe_b64encode(key))

class PortalSettings(models.Model):
    """Singleton model for dynamic portal settings"""
    sync_interval_minutes = models.IntegerField(default=10, help_text="Inventory collection frequency in minutes")
    
    # OME Integration
    ome_url = models.URLField(blank=True, null=True)
    ome_username = models.CharField(max_length=100, blank=True)
    ome_password = models.CharField(max_length=100, blank=True)
    
    # Cost Settings
    electricity_cost = models.DecimalField(max_digits=6, decimal_places=4, default=0.1200, help_text="Cost per kWh")
    pue = models.DecimalField(max_digits=4, decimal_places=2, default=1.50, help_text="Power Usage Effectiveness")

    def save(self, *args, **kwargs):
        self.pk = 1
        super(PortalSettings, self).save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Portal Configuration"

class ServerCostProfile(models.Model):
    """Financial profile for a specific hardware model"""
    name = models.CharField(max_length=100, unique=True, help_text="e.g. Dell PowerEdge R740")
    monthly_amortization = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Monthly hardware cost (CAPEX/Lease)")
    average_watts = models.IntegerField(default=300, help_text="Average power consumption in Watts")

    def __str__(self):
        return self.name

class AppVersion(models.Model):
    version_number = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=100)
    description = models.TextField()
    release_date = models.DateField(auto_now_add=True)
    is_current = models.BooleanField(default=False)

    def __str__(self):
        return self.version_number
    
    def save(self, *args, **kwargs):
        if self.is_current:
            AppVersion.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

class VersionFeature(models.Model):
    version = models.ForeignKey(AppVersion, on_delete=models.CASCADE, related_name='features')
    text = models.CharField(max_length=255)

    def __str__(self):
        return self.text

class Cluster(models.Model):
    name = models.CharField(max_length=100)
    auth_url = models.URLField(help_text="Keystone Auth URL")
    project_domain_name = models.CharField(max_length=50, default='Default')
    user_domain_name = models.CharField(max_length=50, default='Default')
    username = models.CharField(max_length=100)
    password = models.TextField(help_text="Stored encrypted") 
    project_name = models.CharField(max_length=100)
    region_name = models.CharField(max_length=100, default='RegionOne')
    status = models.CharField(max_length=20, default='unknown')

    def set_password(self, raw_password):
        if not raw_password: return
        cipher = get_cipher()
        self.password = cipher.encrypt(raw_password.encode()).decode()

    def get_password(self):
        if not self.password: return ""
        try:
            cipher = get_cipher()
            return cipher.decrypt(self.password.encode()).decode()
        except Exception: return ""

    def __str__(self):
        return self.name

class Flavor(models.Model):
    uuid = models.CharField(max_length=64, primary_key=True)
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name='flavors')
    name = models.CharField(max_length=255)
    vcpus = models.IntegerField()
    ram_mb = models.IntegerField()
    disk_gb = models.IntegerField()
    is_public = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class ClusterService(models.Model):
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name='services')
    binary = models.CharField(max_length=100)
    host = models.CharField(max_length=100)
    zone = models.CharField(max_length=100, default='nova')
    status = models.CharField(max_length=20)
    state = models.CharField(max_length=20)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.CharField(max_length=50, default='Unknown')

    def __str__(self):
        return f"{self.binary} on {self.host}"

class PhysicalHost(models.Model):
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name='hosts')
    hostname = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    idrac_ip = models.GenericIPAddressField(null=True, blank=True)
    is_maintenance = models.BooleanField(default=False)
    state = models.CharField(max_length=20, default='up')
    status = models.CharField(max_length=20, default='enabled')
    
    cpu_count = models.IntegerField(default=0)
    vcpus_used = models.IntegerField(default=0)
    memory_mb = models.IntegerField(default=0)
    memory_mb_used = models.IntegerField(default=0)
    
    # Hardware Info
    service_tag = models.CharField(max_length=100, blank=True)
    cpu_model = models.CharField(max_length=100, blank=True)
    
    # NEW: Link to cost profile (Optional: can be null if unknown)
    server_model = models.ForeignKey(ServerCostProfile, on_delete=models.SET_NULL, null=True, blank=True)
    
    bios_version = models.CharField(max_length=50, blank=True)
    idrac_version = models.CharField(max_length=50, blank=True)
    hardware_health = models.CharField(max_length=20, default='Unknown')
    
    serial_number = models.CharField(max_length=100, blank=True)
    os_version = models.CharField(max_length=100, blank=True)
    docker_version = models.CharField(max_length=50, blank=True)
    openstack_version = models.CharField(max_length=50, blank=True)
    kvm_version = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.hostname

class Instance(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4)
    host = models.ForeignKey(PhysicalHost, on_delete=models.CASCADE, null=True, related_name='instances')
    name = models.CharField(max_length=200)
    flavor_name = models.CharField(max_length=100)
    status = models.CharField(max_length=50)
    project_id = models.CharField(max_length=64)
    user_id = models.CharField(max_length=64)
    
    image_name = models.CharField(max_length=255, blank=True, default="N/A")
    key_name = models.CharField(max_length=255, blank=True, default="-")
    launched_at = models.DateTimeField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    network_name = models.CharField(max_length=100, default='provider-net')

    last_cpu_usage_pct = models.FloatField(default=0.0)
    last_ram_usage_mb = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Volume(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4)
    instance = models.ForeignKey(Instance, on_delete=models.CASCADE, related_name='volumes')
    name = models.CharField(max_length=255, blank=True)
    size_gb = models.IntegerField()
    device = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50)
    is_bootable = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.name} ({self.size_gb}GB)"

class Alert(models.Model):
    SEVERITY_CHOICES = [('critical', 'Critical'), ('warning', 'Warning'), ('info', 'Info')]
    source = models.CharField(max_length=50)
    target_host = models.ForeignKey(PhysicalHost, on_delete=models.CASCADE, null=True, blank=True)
    target_cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    is_active = models.BooleanField(default=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=255)
    target = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.TextField(blank=True)