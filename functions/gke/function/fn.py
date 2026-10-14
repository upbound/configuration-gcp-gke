"""Composition function for the GKE API.

A GKE composes a GCP service account and key first. Once the service account is Ready and has
published its email, it composes the cluster, its node pool and an IAM binding for the service
account, plus Helm and Kubernetes ProviderConfigs and a connection Secret for workloads running
in the cluster.

The cluster finds its network in one of two ways. With spec.parameters.networkRef it reads the
named Network XR's published status, the contract configuration-gcp-network offers; without it,
it selects the Network's managed resources by their network-id label, as it always has.

status.gke publishes the resolved project, Workload Identity pool, kubeconfig Secret and running
machine type, so that higher-level configurations can read them from the GKE XR.
"""

import grpc
from crossplane.function import logging, request, resource, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1
from models.io.upbound.platform.gcp.gke import v1alpha1 as gkev1alpha1
from pydantic import ValidationError

from . import core
from .serialize import to_dict

# The name the Network requirement goes by between this function and Crossplane.
NETWORK_REQUIREMENT = "network"


class FunctionRunner(grpcv1.FunctionRunnerService):
    """A FunctionRunner handles gRPC RunFunctionRequests."""

    def __init__(self):
        """Create a new FunctionRunner."""
        self.log = logging.get_logger()

    async def RunFunction(
        self, req: fnv1.RunFunctionRequest, _: grpc.aio.ServicerContext
    ) -> fnv1.RunFunctionResponse:
        """Run the function."""
        log = self.log.bind(tag=req.meta.tag)
        rsp = response.to(req)

        try:
            xr = gkev1alpha1.GKE(**resource.struct_to_dict(req.observed.composite.resource))
        except ValidationError as e:
            response.fatal(rsp, f"invalid GKE: {e}")
            return rsp
        name, namespace, params = xr.metadata.name, xr.metadata.namespace, xr.spec.parameters

        errors = core.validate(params)
        if errors:
            response.fatal(rsp, "; ".join(errors))
            return rsp

        network = None
        if params.networkRef is not None:
            # Requested on every call, not only until it arrives: Crossplane treats requirements
            # as satisfied once they stop changing between calls, and supplies only what the
            # latest response asked for.
            response.require_resources(
                rsp,
                NETWORK_REQUIREMENT,
                core.NETWORK_API_VERSION,
                core.NETWORK_KIND,
                match_name=params.networkRef.name,
                namespace=namespace,
            )
            network = request.get_required_resource(req, NETWORK_REQUIREMENT)

        observed = {
            key: resource.struct_to_dict(r.resource) for key, r in req.observed.resources.items()
        }
        connection_details = {
            key: dict(r.connection_details) for key, r in req.observed.resources.items()
        }

        desired = core.build(
            name,
            namespace,
            params,
            observed=observed,
            connection_details=connection_details,
            network=network,
        )
        for resource_name, model in desired.items():
            resource.update(rsp.desired.resources[resource_name], to_dict(model))
            if resource_name in core.READY_WHEN_COMPOSED:
                rsp.desired.resources[resource_name].ready = fnv1.READY_TRUE
        resource.update(
            rsp.desired.composite,
            {"status": core.status(name, namespace, params, observed=observed)},
        )

        if core.waiting_for_network(params, network):
            # Normal rather than a warning: on a first reconcile the Network is usually being
            # created alongside this GKE, and waiting for it is the expected path.
            ref = params.networkRef.name
            state = "to publish" if network else "to exist and publish"
            response.normal(
                rsp,
                f"Waiting for Network {ref} {state} status.vpc.selfLink and "
                "status.subnetwork.selfLink before composing the cluster",
            )

        log.info("Composed resources", count=len(desired))
        return rsp
