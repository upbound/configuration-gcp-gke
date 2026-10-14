"""Composition function for the GKE API.

A GKE composes a GCP service account and key first. Once the service account is Ready and has
published its email, it composes the cluster, its node pool and an IAM binding for the service
account, plus Helm and Kubernetes ProviderConfigs and a connection Secret for workloads running
in the cluster. The cluster finds its network through the network-id labels published by
configuration-gcp-network.

status.gke publishes the resolved project, Workload Identity pool, kubeconfig Secret and running
machine type, so that higher-level configurations can read them from the GKE XR.
"""

import base64

import grpc
from crossplane.function import logging, resource, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1
from models.io.crossplane.m.helm.providerconfig import v1beta1 as helmpcv1beta1
from models.io.crossplane.m.kubernetes.object import v1alpha1 as objectv1alpha1
from models.io.crossplane.m.kubernetes.providerconfig import v1alpha1 as k8spcv1alpha1
from models.io.k8s.api.core import v1 as corev1
from models.io.k8s.apimachinery.pkg.apis.core.meta import v1 as coremetav1
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as metav1
from models.io.upbound.m.gcp.cloudplatform.projectiammember import v1beta1 as iammemberv1beta1
from models.io.upbound.m.gcp.cloudplatform.serviceaccount import v1beta1 as sav1beta1
from models.io.upbound.m.gcp.cloudplatform.serviceaccountkey import v1beta1 as sakeyv1beta1
from models.io.upbound.m.gcp.container.cluster import v1beta1 as clusterv1beta1
from models.io.upbound.m.gcp.container.nodepool import v1beta1 as nodepoolv1beta1
from models.io.upbound.platform.gcp.gke import v1alpha1 as gkev1alpha1
from pydantic import BaseModel, ValidationError

# Composition resource names identify composed resources across reconciles. Renaming one would
# replace the resource it names - for GKE_CLUSTER, the cluster itself. Never rename them.
SERVICE_ACCOUNT = "serviceaccount"
SERVICE_ACCOUNT_KEY = "serviceaccountkey"
PROJECT_IAM_MEMBER = "projectiammember"
GKE_CLUSTER = "gkecluster"
NODE_POOL = "node-pool"
PROVIDER_CONFIG_HELM = "providerConfigHelm"
PROVIDER_CONFIG_KUBERNETES = "providerConfigKubernetes"
WORKLOAD_IDENTITY_SETTINGS = "workloadIdentitySettings"
CONNECTION_SECRET = "connection-secret"

# ProviderConfigs never report a Ready condition, so they're ready as soon as they're composed.
READY_WHEN_COMPOSED = (PROVIDER_CONFIG_HELM, PROVIDER_CONFIG_KUBERNETES)

EXTERNAL_NAME = "crossplane.io/external-name"
NETWORK_ID_LABEL = "networks.gcp.platform.upbound.io/network-id"


def _get(obj, *path):
    """Return the value at path in nested dicts, or None if any step is missing."""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _is_ready(observed: dict) -> bool:
    conditions = _get(observed, "status", "conditions") or []
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def _project_from_service_account_id(sa_id: str) -> str:
    """Return the project in an ID like projects/<project>/serviceAccounts/<email>."""
    parts = sa_id.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return ""


def _provider_config_spec(model, secret_prefix: str, namespace: str):
    """Spec for a Helm or Kubernetes ProviderConfig that targets the composed cluster."""
    return model.Spec(
        credentials=model.Credentials(
            source="Secret",
            secretRef=model.SecretRef(
                key="kubeconfig", name=f"{secret_prefix}-gkecluster", namespace=namespace
            ),
        ),
        identity=model.Identity(
            source="Secret",
            type="GoogleApplicationCredentials",
            secretRef=model.SecretRef(
                key="private_key", name=f"{secret_prefix}-sakey", namespace=namespace
            ),
        ),
    )


def compose(
    xr: gkev1alpha1.GKE,
    observed: dict[str, dict],
    connection_details: dict[str, dict[str, bytes]],
) -> tuple[dict[str, BaseModel], dict]:
    """Return the desired composed resources, keyed by composition resource name, and XR status.

    observed holds the observed composed resources and connection_details their connection
    details, both keyed by composition resource name.
    """
    name = xr.metadata.name
    namespace = xr.metadata.namespace
    params = xr.spec.parameters
    policies = params.managementPolicies or ["*"]
    wi = params.workloadIdentity
    workload_identity = wi is None or wi.enabled is not False

    sa_status = _get(observed, SERVICE_ACCOUNT, "status", "atProvider") or {}
    sa_id = sa_status.get("id") or ""
    sa_email = sa_status.get("email") or ""
    cluster_project = _get(observed, GKE_CLUSTER, "status", "atProvider", "project") or ""

    # An explicit project wins, then the service account's, then the observed cluster's.
    project = params.project or _project_from_service_account_id(sa_id) or cluster_project
    workload_pool = f"{project}.svc.id.goog"

    def mr_spec(model, **fields):
        """Spec shared by every composed GCP managed resource."""
        return model.Spec(
            managementPolicies=policies,
            providerConfigRef=model.ProviderConfigRef(
                kind="ProviderConfig", name=params.providerConfigName or "default"
            ),
            **fields,
        )

    desired: dict[str, BaseModel] = {
        SERVICE_ACCOUNT: sav1beta1.ServiceAccount(
            metadata=metav1.ObjectMeta(annotations={EXTERNAL_NAME: params.id}),
            spec=mr_spec(sav1beta1, forProvider=sav1beta1.ForProvider(displayName=params.id)),
        ),
        SERVICE_ACCOUNT_KEY: sakeyv1beta1.ServiceAccountKey(
            spec=mr_spec(
                sakeyv1beta1,
                forProvider=sakeyv1beta1.ForProvider(
                    serviceAccountIdSelector=sakeyv1beta1.ServiceAccountIdSelector(
                        matchControllerRef=True
                    ),
                ),
                writeConnectionSecretToRef=sakeyv1beta1.WriteConnectionSecretToRef(
                    name=f"{params.id}-sakey"
                ),
            ),
        ),
    }

    # The cluster and node pool run as the service account, and a cluster's node service
    # account can't be changed after creation, so wait until it's Ready and has an email.
    if sa_email and _is_ready(observed.get(SERVICE_ACCOUNT)):
        network = {NETWORK_ID_LABEL: params.id}
        version = {}
        if params.version != "latest":
            version = {"nodeVersion": params.version, "minMasterVersion": params.version}

        desired[PROJECT_IAM_MEMBER] = iammemberv1beta1.ProjectIAMMember(
            metadata=metav1.ObjectMeta(annotations={EXTERNAL_NAME: params.id}),
            spec=mr_spec(
                iammemberv1beta1,
                forProvider=iammemberv1beta1.ForProvider(
                    role="roles/container.admin",
                    member=f"serviceAccount:{sa_email}",
                    project=project,
                ),
            ),
        )
        desired[GKE_CLUSTER] = clusterv1beta1.Cluster(
            spec=mr_spec(
                clusterv1beta1,
                forProvider=clusterv1beta1.ForProvider(
                    location=params.region,
                    deletionProtection=False,
                    enableIntranodeVisibility=True,
                    initialNodeCount=1,
                    ipAllocationPolicy=clusterv1beta1.IpAllocationPolicy(
                        clusterSecondaryRangeName="pods",
                        servicesSecondaryRangeName="services",
                    ),
                    loggingService="logging.googleapis.com/kubernetes",
                    monitoringService="monitoring.googleapis.com/kubernetes",
                    networkSelector=clusterv1beta1.NetworkSelector(matchLabels=network),
                    subnetworkSelector=clusterv1beta1.SubnetworkSelector(matchLabels=network),
                    nodeConfig=clusterv1beta1.NodeConfig(serviceAccount=sa_email),
                    workloadIdentityConfig=(
                        clusterv1beta1.WorkloadIdentityConfig(workloadPool=workload_pool)
                        if workload_identity
                        else None
                    ),
                    **version,
                ),
                writeConnectionSecretToRef=clusterv1beta1.WriteConnectionSecretToRef(
                    name=f"{params.id}-gkecluster"
                ),
            ),
        )
        desired[NODE_POOL] = nodepoolv1beta1.NodePool(
            spec=mr_spec(
                nodepoolv1beta1,
                forProvider=nodepoolv1beta1.ForProvider(
                    location=params.region,
                    autoscaling=nodepoolv1beta1.Autoscaling(
                        maxNodeCount=params.nodes.count, minNodeCount=params.nodes.count
                    ),
                    clusterSelector=nodepoolv1beta1.ClusterSelector(matchControllerRef=True),
                    initialNodeCount=params.nodes.count,
                    management=nodepoolv1beta1.Management(autoRepair=True, autoUpgrade=True),
                    maxPodsPerNode=55,
                    nodeConfig=nodepoolv1beta1.NodeConfig(
                        diskSizeGb=params.nodes.diskSizeGb or 20,
                        imageType="COS_CONTAINERD",
                        machineType=params.nodes.instanceType,
                        metadata={"disable-legacy-endpoints": "true"},
                        oauthScopes=["https://www.googleapis.com/auth/cloud-platform"],
                        preemptible=True,
                        shieldedInstanceConfig=nodepoolv1beta1.ShieldedInstanceConfig(
                            enableIntegrityMonitoring=True, enableSecureBoot=True
                        ),
                        serviceAccount=sa_email,
                        workloadMetadataConfig=(
                            nodepoolv1beta1.WorkloadMetadataConfig(mode="GKE_METADATA")
                            if workload_identity
                            else None
                        ),
                    ),
                ),
            ),
        )

        # Both ProviderConfigs are named after the cluster ID, which is how other APIs (e.g.
        # Helm Releases) target this cluster.
        for resource_name, model in (
            (PROVIDER_CONFIG_HELM, helmpcv1beta1),
            (PROVIDER_CONFIG_KUBERNETES, k8spcv1alpha1),
        ):
            desired[resource_name] = model.ProviderConfig(
                metadata=metav1.ObjectMeta(name=params.id, namespace=namespace),
                spec=_provider_config_spec(model, params.id, namespace),
            )

        settings_name = f"{params.id}-workload-identity-settings"
        desired[WORKLOAD_IDENTITY_SETTINGS] = objectv1alpha1.Object(
            metadata=metav1.ObjectMeta(name=settings_name, namespace=namespace),
            spec=objectv1alpha1.Spec(
                managementPolicies=["Create", "Observe", "Update", "LateInitialize"],
                providerConfigRef=objectv1alpha1.ProviderConfigRef(
                    kind="ProviderConfig", name=params.id
                ),
                forProvider=objectv1alpha1.ForProvider(
                    manifest={
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": settings_name, "namespace": "default"},
                        "data": {"gkeProject": project} if project else {},
                    },
                ),
            ),
        )

        # Forward the cluster's kubeconfig to consumers of this XR. Secret data is base64, and
        # connection details arrive as raw bytes.
        data = {}
        if GKE_CLUSTER in observed:
            kubeconfig = connection_details.get(GKE_CLUSTER, {}).get("kubeconfig")
            data["kubeconfig"] = base64.b64encode(kubeconfig).decode() if kubeconfig else ""
        desired[CONNECTION_SECRET] = corev1.Secret(
            metadata=coremetav1.ObjectMeta(
                name=f"{name}-connection",
                namespace=namespace,
                labels={"crossplane.io/composite": name},
            ),
            type="connection.crossplane.io/v1alpha1",
            data=data,
        )

    # upjet serializes nodeConfig as an object or a single-element list depending on the
    # provider version, so accept both.
    node_config = _get(observed, NODE_POOL, "status", "atProvider", "nodeConfig")
    if isinstance(node_config, list):
        node_config = node_config[0] if node_config else {}
    machine_type = _get(node_config, "machineType") or params.nodes.instanceType

    status = {
        "gke": {
            "clusterName": _get(observed, GKE_CLUSTER, "metadata", "name") or params.id,
            "project": project,
            "workloadPool": workload_pool,
            "kubeconfigSecretRef": {
                "name": f"{name}-connection",
                "namespace": namespace,
                "key": "kubeconfig",
            },
            "nodePool": {"instanceType": machine_type},
        },
    }

    return desired, status


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

        observed = {
            name: resource.struct_to_dict(r.resource) for name, r in req.observed.resources.items()
        }
        connection_details = {
            name: dict(r.connection_details) for name, r in req.observed.resources.items()
        }

        desired, status = compose(xr, observed, connection_details)
        for name, r in desired.items():
            # model_dump keeps schema defaults (e.g. Object watch: false) in the desired state.
            resource.update(
                rsp.desired.resources[name], r.model_dump(exclude_none=True, by_alias=True)
            )
            if name in READY_WHEN_COMPOSED:
                rsp.desired.resources[name].ready = fnv1.READY_TRUE
        resource.update(rsp.desired.composite, {"status": status})

        log.info("Composed resources", count=len(desired))
        return rsp
