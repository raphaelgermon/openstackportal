from rest_framework import serializers, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Instance, Cluster
from .openstack_utils import OpenStackClient

class InstanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Instance
        fields = ['uuid', 'name', 'status', 'flavor_name', 'project_id', 'host']

class CreateInstanceSerializer(serializers.Serializer):
    cluster_id = serializers.IntegerField()
    name = serializers.CharField()
    image_id = serializers.CharField()
    flavor_id = serializers.CharField()
    network_id = serializers.CharField()

class MarketplaceViewSet(viewsets.ViewSet):
    def list(self, request):
        instances = Instance.objects.all()
        serializer = InstanceSerializer(instances, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def provision(self, request):
        serializer = CreateInstanceSerializer(data=request.data)
        if serializer.is_valid():
            # Provision logic placeholder
            return Response({'status': 'created'}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
