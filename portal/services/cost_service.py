"""
Cost calculation business logic.
"""

import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal

from portal.models import Instance, Flavor, PortalSettings

logger = logging.getLogger(__name__)


class CostService:
    """Service class for cost calculations."""

    @staticmethod
    def calculate_instance_cost(instance: Instance, settings_obj: Optional[PortalSettings] = None) -> Optional[float]:
        """
        Calculate monthly cost for an instance.
        
        Formula:
        1. Host Power Cost = (Watts/1000) * 24hrs * 30days * €/kWh * PUE
        2. Host Total Cost = Amortization + Power Cost
        3. Cost per vCPU = Host Total / Host CPU Count
        4. Instance Cost = Cost per vCPU * Instance vCPUs
        
        Args:
            instance: Instance to calculate cost for
            settings_obj: Portal settings (fetched if not provided)
        
        Returns:
            Monthly cost in currency units, or None if cannot calculate
        """
        if not instance.host or not instance.host.server_model:
            return None
        
        if settings_obj is None:
            settings_obj = PortalSettings.get_settings()
        
        host = instance.host
        profile = host.server_model
        
        # 1. Calculate Host Monthly Power Cost
        power_cost = (
            (profile.average_watts / 1000) * 24 * 30 * 
            float(settings_obj.electricity_cost) * 
            float(settings_obj.pue)
        )
        
        # 2. Total Host Monthly Cost (Amortization + Power)
        host_total_cost = float(profile.monthly_amortization) + power_cost
        
        # 3. Cost per vCPU on this host
        if host.cpu_count == 0:
            return 0.0
        cost_per_vcpu = host_total_cost / host.cpu_count
        
        # 4. Instance Cost based on Flavor
        vcpus = CostService._get_instance_vcpus(instance, host.cluster)
        
        return round(cost_per_vcpu * vcpus, 2)

    @staticmethod
    def _get_instance_vcpus(instance: Instance, cluster) -> int:
        """Get the number of vCPUs for an instance from its flavor."""
        try:
            flavor = Flavor.objects.filter(
                name=instance.flavor_name, 
                cluster=cluster
            ).first()
            return flavor.vcpus if flavor else 1
        except Exception:
            return 1

    @staticmethod
    def calculate_project_costs(settings_obj: Optional[PortalSettings] = None) -> Dict[str, Any]:
        """
        Calculate costs grouped by project.
        
        Returns:
            Dict with 'projects', 'total_monthly', 'projected_yearly'
        """
        if settings_obj is None:
            settings_obj = PortalSettings.get_settings()
            
        instances = Instance.objects.select_related(
            'host__server_model', 
            'host__cluster'
        ).all()
        
        projects = {}
        total_monthly_cost = 0.0
        
        for inst in instances:
            cost = CostService.calculate_instance_cost(inst, settings_obj) or 0.0
            pid = inst.project_id
            
            if pid not in projects:
                projects[pid] = {
                    'id': pid, 
                    'instance_count': 0, 
                    'total_cost': 0.0,
                    'vcpus': 0
                }
            
            projects[pid]['instance_count'] += 1
            projects[pid]['total_cost'] += cost
            total_monthly_cost += cost

        project_list = sorted(
            projects.values(), 
            key=lambda x: x['total_cost'], 
            reverse=True
        )
        
        return {
            'projects': project_list,
            'total_monthly': round(total_monthly_cost, 2),
            'projected_yearly': round(total_monthly_cost * 12, 2)
        }

    @staticmethod
    def calculate_host_cost(host, settings_obj: Optional[PortalSettings] = None) -> Optional[Dict[str, float]]:
        """
        Calculate monthly costs for a physical host.
        
        Returns:
            Dict with 'power_cost', 'amortization', 'total_cost'
        """
        if not host.server_model:
            return None
            
        if settings_obj is None:
            settings_obj = PortalSettings.get_settings()
        
        profile = host.server_model
        
        power_cost = (
            (profile.average_watts / 1000) * 24 * 30 * 
            float(settings_obj.electricity_cost) * 
            float(settings_obj.pue)
        )
        
        return {
            'power_cost': round(power_cost, 2),
            'amortization': float(profile.monthly_amortization),
            'total_cost': round(float(profile.monthly_amortization) + power_cost, 2)
        }

    @staticmethod
    def calculate_cluster_cost(cluster, settings_obj: Optional[PortalSettings] = None) -> Dict[str, Any]:
        """
        Calculate total costs for a cluster.
        
        Returns:
            Dict with 'total_monthly', 'host_count', 'instance_count', 'avg_per_instance'
        """
        if settings_obj is None:
            settings_obj = PortalSettings.get_settings()
            
        instances = Instance.objects.filter(
            host__cluster=cluster
        ).select_related('host__server_model')
        
        total_cost = 0.0
        instance_count = 0
        
        for inst in instances:
            cost = CostService.calculate_instance_cost(inst, settings_obj) or 0.0
            total_cost += cost
            instance_count += 1
        
        return {
            'total_monthly': round(total_cost, 2),
            'host_count': cluster.hosts.count(),
            'instance_count': instance_count,
            'avg_per_instance': round(total_cost / instance_count, 2) if instance_count > 0 else 0.0
        }

