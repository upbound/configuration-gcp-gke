"""Build the composed resources and status for one GKE XR.

Resources are constructed from the pydantic models `up` generates from the providers' CRDs, so
field names and nesting are checked at construction rather than rejected later by the API server.
They're returned rather than written anywhere: this module knows how to build a GKE cluster, not
how to talk to Crossplane, which keeps it free of the SDK and makes the composed set - including
what is deliberately not composed - directly assertable in tests.
"""

import base64
import re
from dataclasses import dataclass

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
from pydantic import BaseModel

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

# The cluster needs the network, and everything else here is the cluster or reaches into it.
NETWORK_DEPENDENT = (
    GKE_CLUSTER,
    NODE_POOL,
    PROVIDER_CONFIG_HELM,
    PROVIDER_CONFIG_KUBERNETES,
    WORKLOAD_IDENTITY_SETTINGS,
    CONNECTION_SECRET,
)

# ProviderConfigs never report a Ready condition, so they're ready as soon as they're composed.
READY_WHEN_COMPOSED = (PROVIDER_CONFIG_HELM, PROVIDER_CONFIG_KUBERNETES)

# The Network XR a networkRef names, requested from Crossplane as a required resource.
NETWORK_API_VERSION = "gcp.platform.upbound.io/v1alpha1"
NETWORK_KIND = "Network"

EXTERNAL_NAME = "crossplane.io/external-name"
NETWORK_ID_LABEL = "networks.gcp.platform.upbound.io/network-id"

# What Kubernetes accepts as an object name (a DNS-1123 subdomain).
OBJECT_NAME = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")

# The secondary range names configuration-gcp-network has always created. The label path has no
# other way to learn them.
PODS_RANGE = "pods"
SERVICES_RANGE = "services"


@dataclass(frozen=True)
class NetworkWiring:
    """Where the cluster runs, as its network, subnetwork and secondary range fields take it."""

    network: str | None
    subnetwork: str | None
    pods_range: str | None
    services_range: str | None


def _get(obj, *path):
    """Return the value at path in nested dicts, or None if any step is missing."""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _single(value) -> dict:
    """Return a block upjet serializes as an object or a one-element list as a dict.

    Which shape appears depends on the provider version, so accept both.
    """
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _is_ready(observed: dict | None) -> bool:
    conditions = _get(observed, "status", "conditions") or []
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def _project_from_service_account_id(sa_id: str) -> str:
    """Return the project in an ID like projects/<project>/serviceAccounts/<email>."""
    parts = sa_id.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return ""


def _project(params: gkev1alpha1.Parameters, observed: dict[str, dict]) -> str:
    """An explicit project wins, then the service account's, then the observed cluster's."""
    sa_id = _get(observed, SERVICE_ACCOUNT, "status", "atProvider", "id") or ""
    cluster_project = _get(observed, GKE_CLUSTER, "status", "atProvider", "project") or ""
    return params.project or _project_from_service_account_id(sa_id) or cluster_project


def network_wiring(network: dict | None) -> NetworkWiring | None:
    """Return the wiring a Network XR publishes in its status, or None until it has.

    Both selfLinks are the Network's signal that its network and subnetwork exist in GCP; until
    then a cluster would be created against something that isn't there. The range names fall back
    to the ones the Network has always created, rather than holding the cluster back over a field
    that the Network publishes alongside the selfLinks anyway.
    """
    network_link = _get(network, "status", "vpc", "selfLink")
    subnetwork_link = _get(network, "status", "subnetwork", "selfLink")
    if not (network_link and subnetwork_link):
        return None
    ranges = _get(network, "status", "subnetwork", "secondaryRanges") or {}
    return NetworkWiring(
        network=network_link,
        subnetwork=subnetwork_link,
        pods_range=ranges.get("pods") or PODS_RANGE,
        services_range=ranges.get("services") or SERVICES_RANGE,
    )


def _observed_wiring(cluster: dict | None) -> NetworkWiring:
    """Return the wiring an existing cluster was composed with.

    Used to keep composing a cluster that already exists while its Network's status is
    unavailable. Re-emitting what it already has is what keeps it; dropping it from the desired
    state would delete it, and changing its network would replace it.
    """
    for_provider = _get(cluster, "spec", "forProvider") or {}
    ip_allocation = _single(for_provider.get("ipAllocationPolicy"))
    return NetworkWiring(
        network=for_provider.get("network"),
        subnetwork=for_provider.get("subnetwork"),
        pods_range=ip_allocation.get("clusterSecondaryRangeName"),
        services_range=ip_allocation.get("servicesSecondaryRangeName"),
    )


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


def _cluster_network(params: gkev1alpha1.Parameters, wiring: NetworkWiring | None) -> dict:
    """The cluster's network fields: selectors by default, the Network's status with networkRef.

    The two are exclusive. A networkRef cluster takes its network by value from a published
    contract; selecting the Network's managed resources by label as well would reach back into
    its implementation, which is what networkRef exists to stop.
    """
    if params.networkRef is None:
        labels = {NETWORK_ID_LABEL: params.id}
        return {
            "networkSelector": clusterv1beta1.NetworkSelector(matchLabels=labels),
            "subnetworkSelector": clusterv1beta1.SubnetworkSelector(matchLabels=labels),
            "ipAllocationPolicy": clusterv1beta1.IpAllocationPolicy(
                clusterSecondaryRangeName=PODS_RANGE,
                servicesSecondaryRangeName=SERVICES_RANGE,
            ),
        }
    wiring = wiring or NetworkWiring(None, None, None, None)
    ranges = None
    if wiring.pods_range or wiring.services_range:
        ranges = clusterv1beta1.IpAllocationPolicy(
            clusterSecondaryRangeName=wiring.pods_range,
            servicesSecondaryRangeName=wiring.services_range,
        )
    # selfLinks are what the provider's reference resolvers write for these fields, so moving an
    # existing GKE from label selectors to networkRef leaves the cluster unchanged.
    return {
        "network": wiring.network,
        "subnetwork": wiring.subnetwork,
        "ipAllocationPolicy": ranges,
    }


def validate(params: gkev1alpha1.Parameters) -> list[str]:
    """Return fatal configuration errors.

    A networkRef naming something no Network could be called would otherwise hold the cluster
    back forever, reported only as a Normal "waiting" result. Failing makes the mistake visible.
    """
    errors: list[str] = []
    ref = params.networkRef
    if ref is not None and (len(ref.name) > 253 or not OBJECT_NAME.fullmatch(ref.name)):
        errors.append(
            f"spec.parameters.networkRef.name {ref.name!r} is not a valid Kubernetes object name"
        )
    return errors


def waiting_for_network(params: gkev1alpha1.Parameters, network: dict | None) -> bool:
    """Whether the cluster is held back until the referenced Network publishes its status."""
    return params.networkRef is not None and network_wiring(network) is None


def build(
    xr_name: str,
    namespace: str,
    params: gkev1alpha1.Parameters,
    *,
    observed: dict[str, dict] | None = None,
    connection_details: dict[str, dict[str, bytes]] | None = None,
    network: dict | None = None,
) -> dict[str, BaseModel]:
    """Return the composed resources, as models, keyed by composition resource name.

    observed holds the observed composed resources and connection_details their connection
    details, both keyed by composition resource name. network is the Network XR that
    params.networkRef names, as Crossplane supplied it, or None if it's unset or doesn't exist.

    A service account and key are always composed. Once the service account is Ready and has an
    email, so is everything that runs as it. With a networkRef, the cluster and everything that
    depends on it additionally wait for the Network to publish its status; any of them that
    already exist are kept meanwhile, the cluster with the network it already has.
    """
    observed = observed or {}
    connection_details = connection_details or {}
    policies = params.managementPolicies or ["*"]
    wi = params.workloadIdentity
    workload_identity = wi is None or wi.enabled is not False
    sa_email = _get(observed, SERVICE_ACCOUNT, "status", "atProvider", "email") or ""
    project = _project(params, observed)
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
    if not (sa_email and _is_ready(observed.get(SERVICE_ACCOUNT))):
        return desired

    waiting = waiting_for_network(params, network)
    wiring = _observed_wiring(observed.get(GKE_CLUSTER)) if waiting else network_wiring(network)

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
                loggingService="logging.googleapis.com/kubernetes",
                monitoringService="monitoring.googleapis.com/kubernetes",
                nodeConfig=clusterv1beta1.NodeConfig(serviceAccount=sa_email),
                workloadIdentityConfig=(
                    clusterv1beta1.WorkloadIdentityConfig(workloadPool=workload_pool)
                    if workload_identity
                    else None
                ),
                **_cluster_network(params, wiring),
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

    # Both ProviderConfigs are named after the cluster ID, which is how other APIs (e.g. Helm
    # Releases) target this cluster.
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
            name=f"{xr_name}-connection",
            namespace=namespace,
            labels={"crossplane.io/composite": xr_name},
        ),
        type="connection.crossplane.io/v1alpha1",
        data=data,
    )

    if waiting:
        # Nothing new is composed until the Network has published where to run, and nothing
        # that already exists is dropped: a composed resource missing from the desired state is
        # deleted, and this cluster has no deletion protection.
        for resource_name in NETWORK_DEPENDENT:
            if resource_name not in observed:
                del desired[resource_name]

    return desired


def status(
    xr_name: str,
    namespace: str,
    params: gkev1alpha1.Parameters,
    *,
    observed: dict[str, dict] | None = None,
) -> dict:
    """Return the XR's status.gke, for higher-level configurations to read.

    Everything here is known from the parameters alone and refined from observed state once the
    composed resources exist, so it's published from the first reconcile.
    """
    observed = observed or {}
    project = _project(params, observed)
    node_config = _single(_get(observed, NODE_POOL, "status", "atProvider", "nodeConfig"))
    return {
        "gke": {
            "clusterName": _get(observed, GKE_CLUSTER, "metadata", "name") or params.id,
            "project": project,
            "workloadPool": f"{project}.svc.id.goog",
            "kubeconfigSecretRef": {
                "name": f"{xr_name}-connection",
                "namespace": namespace,
                "key": "kubeconfig",
            },
            "nodePool": {
                "instanceType": node_config.get("machineType") or params.nodes.instanceType
            },
        },
    }
