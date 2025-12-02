import csv
import os
import requests
from requests.auth import HTTPBasicAuth

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse, JsonResponse
from django.db.models import Sum, Count, Q, Exists, OuterRef, Prefetch
from django.conf import settings
from django.core.management import call_command
from .models import Cluster, PhysicalHost, Instance, Alert, AuditLog, PortalSettings, Flavor, ServerCostProfile
from .openstack_utils import OpenStackClient
import random
from django.utils.dateparse import parse_datetime
from .tasks import sync_flavors, sync_openmanage, sync_inventory

def get_app_version():
    try:
        version_path = os.path.join(settings.BASE_DIR, 'version.txt')
        if os.path.exists(version_path):
            with open(version_path, 'r') as f:
                return f.read().strip()
    except Exception:
        pass
    return "dev"

def get_annotated_clusters():
    """
    Returns clusters annotated with 'has_active_alert' boolean,
    and pre-fetches hosts also annotated with 'has_active_alert'.
    """
    # Subqueries to check for existence of active alerts
    host_alerts = Alert.objects.filter(target_host=OuterRef('pk'), is_active=True)
    cluster_alerts = Alert.objects.filter(target_cluster=OuterRef('pk'), is_active=True)
    
    # Annotate hosts first
    hosts_qs = PhysicalHost.objects.annotate(
        has_active_alert=Exists(host_alerts)
    )
    
    # Annotate clusters and use Prefetch to load the annotated hosts
    return Cluster.objects.annotate(
        has_active_alert=Exists(cluster_alerts)
    ).prefetch_related(
        Prefetch('hosts', queryset=hosts_qs),
        'hosts__instances'
    ).order_by('region_name', 'name')

def get_sidebar_context():
    """Helper to generate sidebar data for full-page reloads"""
    # Use the annotated queryset so the sidebar shows icons correctly
    clusters = get_annotated_clusters()
    alerts_count = Alert.objects.filter(is_active=True).count()
    return {
        'clusters': clusters,
        'global_alert_count': alerts_count,
        'app_version': get_app_version()
    }

def render_page(request, template_name, context, page_type='overview'):
    """Smart renderer for HTMX vs Full Page"""
    if request.headers.get('HX-Request'):
        return render(request, template_name, context)
    
    full_context = get_sidebar_context()
    full_context.update(context)
    full_context['page_type'] = page_type
    full_context['page_template'] = template_name 
    return render(request, 'portal/dashboard.html', full_context)

def calculate_instance_cost(instance, settings_obj):
    """
    Helper to calculate monthly cost for an instance.
    Returns None if cost cannot be calculated (e.g. missing hardware model).
    """
    if not instance.host or not instance.host.server_model:
        return None
    
    host = instance.host
    profile = host.server_model
    
    # 1. Calculate Host Monthly Power Cost
    # Formula: Watts / 1000 * 24hrs * 30days * Cost/kWh * PUE
    power_cost = (profile.average_watts / 1000) * 24 * 30 * float(settings_obj.electricity_cost) * float(settings_obj.pue)
    
    # 2. Total Host Monthly Cost (Amortization + Power)
    host_total_cost = float(profile.monthly_amortization) + power_cost
    
    # 3. Cost per vCPU on this host
    if host.cpu_count == 0: return 0.0
    cost_per_vcpu = host_total_cost / host.cpu_count
    
    # 4. Instance Cost based on Flavor
    try:
        flavor = Flavor.objects.filter(name=instance.flavor_name, cluster=host.cluster).first()
        vcpus = flavor.vcpus if flavor else 1 
    except:
        vcpus = 1
        
    return round(cost_per_vcpu * vcpus, 2)

@login_required
def cost_dashboard(request):
    """Financial Overview"""
    portal_settings = PortalSettings.get_settings()
    instances = Instance.objects.select_related('host__server_model', 'host__cluster').all()
    
    # Group by Project
    projects = {}
    total_monthly_cost = 0.0
    
    for inst in instances:
        # Treat None as 0.0 for aggregation
        cost = calculate_instance_cost(inst, portal_settings) or 0.0
        pid = inst.project_id
        
        if pid not in projects:
            projects[pid] = {'id': pid, 'instance_count': 0, 'total_cost': 0.0, 'vcpus': 0}
        
        projects[pid]['instance_count'] += 1
        projects[pid]['total_cost'] += cost
        total_monthly_cost += cost

    project_list = sorted(projects.values(), key=lambda x: x['total_cost'], reverse=True)
    
    context = {
        'projects': project_list,
        'total_monthly_cost': round(total_monthly_cost, 2),
        'projected_yearly': round(total_monthly_cost * 12, 2),
        'settings': portal_settings
    }
    return render_page(request, 'portal/partials/cost_dashboard.html', context, 'cost')


@login_required
def dashboard(request):
    # Use the annotated queryset for calculations too
    clusters = get_annotated_clusters()
    
    total_cores = sum(h.cpu_count for c in clusters for h in c.hosts.all())
    total_vms = Instance.objects.count()
    
    regions_data = []
    region_names = set(c.region_name for c in clusters)
    
    for region in sorted(region_names):
        region_clusters = [c for c in clusters if c.region_name == region]
        # We can filter the prefetched list in python to avoid re-querying DB
        region_hosts = [h for c in region_clusters for h in c.hosts.all()]
        
        # Calculate stats manually since we are iterating lists now (or rely on separate aggregates if preferred)
        # For simplicity/performance on Dashboard, we can stick to DB aggregates for the Region Summary
        # Re-querying strictly for stats aggregation is fine and cleaner code-wise:
        db_region_hosts = PhysicalHost.objects.filter(cluster__in=region_clusters)
        stats = db_region_hosts.aggregate(
            total_cpu=Sum('cpu_count'), used_cpu=Sum('vcpus_used'),
            total_mem=Sum('memory_mb'), used_mem=Sum('memory_mb_used')
        )
        
        total_cpu = stats['total_cpu'] or 0
        used_cpu = stats['used_cpu'] or 0
        total_mem = stats['total_mem'] or 0
        used_mem = stats['used_mem'] or 0
        
        cpu_pct = (used_cpu / total_cpu * 100) if total_cpu > 0 else 0
        mem_pct = (used_mem / total_mem * 100) if total_mem > 0 else 0
        
        clusters_data = []
        for cluster in region_clusters:
            # c_hosts is already prefetched via get_annotated_clusters
            c_hosts = cluster.hosts.all()
            
            # Simple python aggregation for the cluster list in the card
            c_tc = sum(h.cpu_count for h in c_hosts)
            c_uc = sum(h.vcpus_used for h in c_hosts)
            c_tm = sum(h.memory_mb for h in c_hosts)
            c_um = sum(h.memory_mb_used for h in c_hosts)
            
            clusters_data.append({
                'id': cluster.id, 'name': cluster.name,
                'node_count': len(c_hosts),
                'instance_count': Instance.objects.filter(host__cluster=cluster).count(),
                'cpu_usage': f"{c_uc}/{c_tc}",
                'cpu_pct': round((c_uc / c_tc * 100) if c_tc > 0 else 0, 1),
                'mem_usage_gb': f"{c_um//1024}/{c_tm//1024} GB",
                'mem_pct': round((c_um / c_tm * 100) if c_tm > 0 else 0, 1),
                'has_alert': cluster.has_active_alert # Pass alert status to card
            })

        regions_data.append({
            'name': region,
            'cluster_count': len(region_clusters),
            'host_count': len(region_hosts),
            'instance_count': Instance.objects.filter(host__cluster__region_name=region).count(),
            'cpu_usage': f"{used_cpu}/{total_cpu}",
            'cpu_pct': round(cpu_pct, 1),
            'mem_usage_gb': f"{used_mem//1024}/{total_mem//1024} GB",
            'mem_pct': round(mem_pct, 1),
            'clusters_list': clusters_data
        })

    alerts = Alert.objects.filter(is_active=True).order_by('-created_at')[:10]

    context = {
        'regions': regions_data,
        'alerts': alerts,
        'total_cores': total_cores,
        'total_vms': total_vms
    }
    return render_page(request, 'portal/partials/global_overview.html', context, 'overview')

@login_required
def all_instances(request):
    instances = Instance.objects.select_related('host__cluster').all().order_by('name')
    return render_page(request, 'portal/partials/all_instances.html', {'instances': instances}, 'all_instances')

@login_required
def all_nodes(request):
    nodes = PhysicalHost.objects.select_related('cluster').all().order_by('hostname')
    return render_page(request, 'portal/partials/all_nodes.html', {'nodes': nodes}, 'all_nodes')

@login_required
def all_flavors(request):
    flavors = Flavor.objects.select_related('cluster').all().order_by('name')
    last_update = AuditLog.objects.filter(action="Flavor Sync Success").order_by('-timestamp').first()
    return render_page(request, 'portal/partials/all_flavors.html', {'flavors': flavors, 'last_update': last_update.timestamp if last_update else None}, 'all_flavors')

@login_required
def refresh_flavors(request):
    from .tasks import sync_flavors
    sync_flavors()
    flavors = Flavor.objects.select_related('cluster').all().order_by('name')
    last_update = AuditLog.objects.filter(action="Flavor Sync Success").order_by('-timestamp').first()
    return render(request, 'portal/partials/all_flavors.html', {'flavors': flavors, 'last_update': last_update.timestamp if last_update else None})

@login_required
def cluster_details(request, cluster_id):
    cluster = get_object_or_404(Cluster, pk=cluster_id)
    
    if request.GET.get('refresh') and "fake" not in cluster.auth_url:
         try:
             client = OpenStackClient(cluster)
             # 1. Sync Services
             for svc in client.get_services():
                 ClusterService.objects.update_or_create(cluster=cluster, binary=svc.binary, host=svc.host, defaults={'zone': getattr(svc, 'availability_zone', 'nova'), 'status': svc.status, 'state': svc.state})
             
             # 2. Sync Host Stats (Needed for aggregation)
             # Note: We don't do full sync_inventory here to be fast, just stats if possible,
             # but full sync is handled by task. Refresh here implies we want updated DB view.
             # We will rely on the scheduled task for heavy lifting, or trigger it:
             from .tasks import sync_inventory
             sync_inventory.delay()
         except: pass

    hosts = cluster.hosts.all()
    
    # --- AGGREGATE HOST STATS ---
    aggs = hosts.aggregate(
        total_cpu=Sum('cpu_count'), 
        used_cpu=Sum('vcpus_used'), 
        total_mem=Sum('memory_mb'), 
        used_mem=Sum('memory_mb_used')
    )
    
    # Normalize None to 0
    stats = {
        'total_cpu': aggs['total_cpu'] or 0,
        'used_cpu': aggs['used_cpu'] or 0,
        'total_mem': aggs['total_mem'] or 0,
        'used_mem': aggs['used_mem'] or 0
    }
    
    # Calculate percentages
    cpu_pct = (stats['used_cpu'] / stats['total_cpu'] * 100) if stats['total_cpu'] > 0 else 0
    mem_pct = (stats['used_mem'] / stats['total_mem'] * 100) if stats['total_mem'] > 0 else 0
    
    # Add GB versions for display
    stats['total_mem_gb'] = stats['total_mem'] // 1024
    stats['used_mem_gb'] = stats['used_mem'] // 1024

    services = cluster.services.all().order_by('binary', 'host')
    alerts = Alert.objects.filter(target_cluster=cluster, is_active=True) | Alert.objects.filter(target_host__cluster=cluster, is_active=True)
    instances = Instance.objects.filter(host__cluster=cluster).select_related('host').order_by('name')

    context = {
        'cluster': cluster,
        'node_count': hosts.count(),
        'instance_count': instances.count(),
        'stats': stats,          # Passing the stats dict for template access
        'cpu_pct': round(cpu_pct, 1),
        'mem_pct': round(mem_pct, 1),
        'services': services,
        'alerts': alerts,
        'instances': instances
    }
    return render_page(request, 'portal/partials/cluster_details.html', context, 'cluster')


@login_required
def node_details(request, host_id):
    host = get_object_or_404(PhysicalHost, pk=host_id)
    
    # --- REFRESH LOGIC ---
    if request.GET.get('refresh'):
        if "fake" not in host.cluster.auth_url:
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
                    
                    # Also refresh instances on this node
                    instances = client.get_instances(host_name=host.hostname)
                    for server in instances:
                        Instance.objects.update_or_create(uuid=server.id, defaults={'host': host, 'name': server.name, 'status': server.status, 'flavor_name': server.flavor.get('original_name', 'unknown'), 'project_id': server.project_id, 'user_id': server.user_id})
            except Exception as e:
                print(f"Node refresh failed: {e}")

    return render_page(request, 'portal/partials/node_details.html', {'host': host}, 'node')

@login_required
def instance_details(request, instance_uuid):
    instance = get_object_or_404(Instance, pk=instance_uuid)
    
    # Ensure instance has context
    if not instance.host or not instance.host.cluster:
        return render_page(request, 'portal/partials/instance_details.html', {'instance': instance}, 'instance')
        
    cluster = instance.host.cluster
    is_dummy = "example.com" in cluster.auth_url or "inventory.local" in cluster.auth_url or "fake" in cluster.auth_url
    settings_obj = PortalSettings.get_settings()
    monthly_cost = calculate_instance_cost(instance, settings_obj)
    if request.GET.get('refresh'):
        
        if is_dummy:
            if instance.status == 'ACTIVE':
                instance.last_cpu_usage_pct = round(random.uniform(1.0, 99.0), 1)
                instance.last_ram_usage_mb = max(512, instance.last_ram_usage_mb + random.uniform(-100, 100))

                instance.save()
        else:
            try:
                client = OpenStackClient(cluster)
                server = client.get_server_by_uuid(instance.uuid)
                if server:
                    instance.name = server.name
                    instance.status = server.status
                    instance.key_name = server.key_name
                    if server.launched_at:
                         instance.launched_at = parse_datetime(server.launched_at)
                    
                    # Map Image Name if available in dict
                    if server.image:
                        instance.image_name = server.image.get('id', 'Unknown ID')

                    # Network IP Extraction
                    if server.addresses:
                        ip_found = False
                        for net_name, addrs in server.addresses.items():
                            for addr in addrs:
                                if addr.get('version') == 4:
                                    instance.ip_address = addr.get('addr')
                                    instance.network_name = net_name
                                    ip_found = True
                                    break
                            if ip_found: break
                    
                    instance.save()
                
                # Real-time stats
                stats = client.get_realtime_stats(instance.uuid)
                if stats:
                    mem_kb = stats.get('memory') or stats.get('memory-actual')
                    if mem_kb: instance.last_ram_usage_mb = round(float(mem_kb) / 1024.0, 2)
                    cpu_util = stats.get('cpu_util')
                    if cpu_util is not None: instance.last_cpu_usage_pct = float(cpu_util)
                    instance.save()
                # Calculate Cost
                settings_obj = PortalSettings.get_settings()
                monthly_cost = calculate_instance_cost(instance, settings_obj)
            except Exception as e:
                print(f"Instance refresh failed for cluster {cluster.name}: {e}")

    return render_page(request, 'portal/partials/instance_details.html', {
        'instance': instance,
        'monthly_cost': monthly_cost
    }, 'instance')

@login_required
def global_search(request):
    query = request.GET.get('q', '')
    context = {'has_results': False}
    if len(query) >= 2:
        hosts = PhysicalHost.objects.filter(Q(hostname__icontains=query) | Q(ip_address__icontains=query))[:3]
        instances = Instance.objects.filter(Q(name__icontains=query) | Q(uuid__icontains=query))[:5]
        clusters = Cluster.objects.filter(name__icontains=query)[:2]
        context = {
            'hosts': hosts, 'instances': instances, 'clusters': clusters,
            'has_results': any([hosts, instances, clusters]), 'query': query
        }
    return render(request, 'portal/partials/search_results.html', context)

@login_required
def instance_console(request, instance_uuid):
    instance = get_object_or_404(Instance, pk=instance_uuid)
    console_type = request.GET.get('type', 'novnc')
    
    print(f"DEBUG: Fetching {console_type} console for {instance.uuid} on cluster {instance.host.cluster.name}")

    if "example.com" in instance.host.cluster.auth_url or "fake" in instance.host.cluster.auth_url: 
        return JsonResponse({'url': '#dummy-console'})

    try:
        client = OpenStackClient(instance.host.cluster)
        if console_type == 'spice': 
            data = client.get_spice_console(instance.uuid)
            print(f"DEBUG: SPICE URL retrieved: {data.get('url')}")
            return JsonResponse(data)
        else:
            url = client.get_novnc_console(instance.uuid)
            print(f"DEBUG: NoVNC URL retrieved: {url}")
            return JsonResponse({'url': url, 'type': 'novnc'})
    except Exception as e:
        print(f"ERROR: Console fetch failed: {e}")
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def logs_view(request):
    logs = AuditLog.objects.all().order_by('-timestamp')[:1000]
    return render_page(request, 'portal/partials/logs.html', {'logs': logs}, 'logs')

@login_required
def about(request):
    return render_page(request, 'portal/partials/about.html', {}, 'about')

@login_required
def toggle_maintenance(request, host_id):
    host = get_object_or_404(PhysicalHost, pk=host_id)
    if not host.is_maintenance:
        reason = request.POST.get('reason', 'No reason provided.')
        log_detail = f"Maintenance Enabled. Reason: {reason}"
    else:
        log_detail = "Maintenance Disabled."
    
    host.is_maintenance = not host.is_maintenance
    host.save()
    
    AuditLog.objects.create(user=request.user, action="Maintenance Toggle", target=host.hostname, details=log_detail)
    return render_page(request, 'portal/partials/node_details.html', {'host': host}, 'node')

@login_required
def schedule_snapshot(request, instance_uuid):
    AuditLog.objects.create(user=request.user, action="Snapshot Scheduled", target=str(instance_uuid))
    return HttpResponse('<span class="text-green-500">Snapshot Scheduled</span>')

@login_required
def export_instances_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="all_instances.csv"'
    writer = csv.writer(response)
    writer.writerow(['Name', 'UUID', 'Cluster', 'Host', 'IP Address', 'Status', 'Flavor'])
    instances = Instance.objects.select_related('host__cluster').all().iterator()
    for i in instances:
        writer.writerow([i.name, i.uuid, i.host.cluster.name, i.host.hostname, i.ip_address, i.status, i.flavor_name])
    return response

@login_required
def export_nodes_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="all_nodes.csv"'
    writer = csv.writer(response)
    writer.writerow(['Hostname', 'Cluster', 'IP', 'iDRAC', 'State', 'vCPU Used', 'vCPU Total', 'RAM Used', 'RAM Total'])
    nodes = PhysicalHost.objects.select_related('cluster').all().iterator()
    for n in nodes:
        writer.writerow([n.hostname, n.cluster.name, n.ip_address, n.idrac_ip, n.state, n.vcpus_used, n.cpu_count, n.memory_mb_used, n.memory_mb])
    return response

@login_required
def export_logs_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="system_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Timestamp', 'User', 'Action', 'Target', 'Details'])
    logs = AuditLog.objects.all().iterator()
    for l in logs:
        user = l.user.username if l.user else 'System'
        writer.writerow([l.timestamp, user, l.action, l.target, l.details])
    return response


@user_passes_test(lambda u: u.is_superuser)
def admin_settings(request):
    portal_settings = PortalSettings.get_settings()
    clusters = Cluster.objects.all().order_by('region_name', 'name')
    cost_profiles = ServerCostProfile.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_cluster':
            try:
                c = Cluster(name=request.POST.get('name'), auth_url=request.POST.get('auth_url'), region_name=request.POST.get('region'), username=request.POST.get('username'), project_name=request.POST.get('project'))
                c.set_password(request.POST.get('password'))
                c.save()
                AuditLog.objects.create(user=request.user, action="Cluster Added", target=c.name, details=f"Region: {c.region_name}")
                sync_inventory.delay()
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': Cluster.objects.all().order_by('region_name', 'name'), 'cost_profiles': cost_profiles, 'success': f"Cluster '{c.name}' added."})
            except Exception as e:
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': Cluster.objects.all().order_by('region_name', 'name'), 'cost_profiles': cost_profiles, 'error': str(e)})
        
        elif action == 'delete_cluster':
            try:
                cluster = Cluster.objects.get(pk=request.POST.get('cluster_id'))
                name = cluster.name
                cluster.delete()
                AuditLog.objects.create(user=request.user, action="Cluster Deleted", target=name)
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': Cluster.objects.all().order_by('region_name', 'name'), 'cost_profiles': cost_profiles, 'success': f"Cluster '{name}' deleted."})
            except Exception as e:
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': Cluster.objects.all().order_by('region_name', 'name'), 'cost_profiles': cost_profiles, 'error': str(e)})

        elif action == 'save_cost_settings':
            try:
                portal_settings.electricity_cost = request.POST.get('electricity_cost')
                portal_settings.pue = request.POST.get('pue')
                portal_settings.save()
                AuditLog.objects.create(user=request.user, action="Settings Update", target="Financial", details="Updated electricity rates")
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'success': "Financial settings saved."})
            except Exception as e:
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'error': f"Error saving financials: {e}"})

        elif action == 'add_profile':
            try:
                ServerCostProfile.objects.create(
                    name=request.POST.get('name'),
                    monthly_amortization=request.POST.get('amortization'),
                    average_watts=request.POST.get('watts')
                )
                cost_profiles = ServerCostProfile.objects.all()
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'success': "Cost profile added."})
            except Exception as e:
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'error': f"Error adding profile: {e}"})

        elif action == 'delete_profile':
            try:
                ServerCostProfile.objects.filter(id=request.POST.get('profile_id')).delete()
                cost_profiles = ServerCostProfile.objects.all()
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'success': "Profile deleted."})
            except Exception as e:
                return render(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles, 'error': str(e)})

        elif action == 'save_settings':
            try:
                interval = int(request.POST.get('sync_interval'))
                portal_settings.sync_interval_minutes = interval
                portal_settings.ome_url = request.POST.get('ome_url')
                portal_settings.ome_username = request.POST.get('ome_username')
                
                # Only update password if provided (allows user to leave it blank to keep existing)
                if request.POST.get('ome_password'):
                    portal_settings.ome_password = request.POST.get('ome_password')

                # --- OME Connection Test ---
                if request.POST.get('test_ome'):
                    test_url = portal_settings.ome_url
                    test_user = portal_settings.ome_username
                    test_pass = request.POST.get('ome_password') or portal_settings.ome_password
                    
                    if not test_url or not test_user:
                        raise ValueError("OME URL and Username are required for testing.")
                        
                    try:
                        # Simple synchronous check
                        resp = requests.get(
                            f"{test_url.rstrip('/')}/api/DeviceService/Devices",
                            auth=HTTPBasicAuth(test_user, test_pass),
                            verify=False,
                            timeout=5,
                            params={'$top': 1}
                        )
                        resp.raise_for_status()
                        # Trigger full sync if successful
                        sync_openmanage.delay()
                    except Exception as e:
                         return render(request, 'portal/partials/admin_settings.html', {
                            'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles,
                            'error': f"Connection Test Failed: {e}"
                        })

                portal_settings.save()
                AuditLog.objects.create(user=request.user, action="Settings Update", target="Portal Settings", details=f"Sync interval: {interval}m")
                
                return render(request, 'portal/partials/admin_settings.html', {
                    'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles,
                    'success': "Settings updated & OME Connection Verified." if request.POST.get('test_ome') else "Settings updated."
                })
            except ValueError: pass

    return render_page(request, 'portal/partials/admin_settings.html', {'settings': portal_settings, 'clusters': clusters, 'cost_profiles': cost_profiles}, 'admin')