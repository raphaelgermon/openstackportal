from django.contrib import admin
from .models import Cluster, PhysicalHost, Instance, Alert, AuditLog

@admin.register(Cluster)
class ClusterAdmin(admin.ModelAdmin):
    list_display = ('name', 'auth_url', 'region_name')

@admin.register(PhysicalHost)
class PhysicalHostAdmin(admin.ModelAdmin):
    list_display = ('hostname', 'cluster', 'ip_address', 'state', 'status', 'is_maintenance')
    list_filter = ('cluster', 'state', 'is_maintenance')
    actions = ['enable_maintenance', 'disable_maintenance']

    def enable_maintenance(self, request, queryset):
        queryset.update(is_maintenance=True)
    
    def disable_maintenance(self, request, queryset):
        queryset.update(is_maintenance=False)

@admin.register(Instance)
class InstanceAdmin(admin.ModelAdmin):
    list_display = ('name', 'uuid', 'host', 'status')
    list_filter = ('status', 'host')

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('title', 'severity', 'target_host', 'is_active')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'target')
