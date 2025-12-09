from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from rest_framework import routers
from portal.api import MarketplaceViewSet
from portal import views

# Imports for Static Files
from django.conf import settings
from django.conf.urls.static import static

router = routers.DefaultRouter()
router.register(r'marketplace', MarketplaceViewSet, basename='marketplace')

urlpatterns = [
    path('admin/settings/', views.admin_settings, name='admin_settings'),    

    path('admin/', admin.site.urls),
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('api/v1/', include(router.urls)),
    
    # DataTable API
    path('api/dt/instances/', views.api_instance_datatable, name='api_instance_datatable'),
    
    path('portal/export/instances/', views.export_instances_csv, name='export_instances_csv'),
    path('portal/export/nodes/', views.export_nodes_csv, name='export_nodes_csv'),
    path('portal/export/logs/', views.export_logs_csv, name='export_logs_csv'),
    
    # Inventory Routes
    path('portal/all-instances/', views.all_instances, name='all_instances'), 
    path('portal/all-nodes/', views.all_nodes, name='all_nodes'),
    path('portal/all-flavors/', views.all_flavors, name='all_flavors'), 
    path('portal/refresh-flavors/', views.refresh_flavors, name='refresh_flavors'), 

    # Portal Views
    path('', views.dashboard, name='dashboard'),
    path('about/', views.about, name='about'), 
    path('cost/', views.cost_dashboard, name='cost_dashboard'),    
    path('portal/search/', views.global_search, name='global_search'),
    path('logs/', views.logs_view, name='logs'), 
    path('portal/cluster/<int:cluster_id>/', views.cluster_details, name='cluster_details'),
    path('portal/node/<int:host_id>/', views.node_details, name='node_details'),
    path('portal/instance/<uuid:instance_uuid>/details/', views.instance_details, name='instance_details'),
    path('portal/console/<uuid:instance_uuid>/', views.instance_console, name='instance_console'),
    path('portal/node/<int:host_id>/toggle-maintenance/', views.toggle_maintenance, name='toggle_maintenance'),
    path('portal/instance/<uuid:instance_uuid>/snapshot/', views.schedule_snapshot, name='schedule_snapshot'),

    path('clusters/', views.cluster_list, name='cluster_list'),
    path('cluster/<int:pk>/', views.cluster_details, name='cluster_details'),
    path('host/<int:pk>/', views.host_detail, name='host_detail'),
]


# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
