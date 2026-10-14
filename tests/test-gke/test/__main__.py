"""Generate the GKE CompositionTests and print them as YAML."""

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.compositiontest import v1alpha1 as compositiontest

COMPOSITION = "apis/gke/composition.yaml"
XRD = "apis/gke/definition.yaml"
XR = "examples/gke-xr.yaml"

ID = "configuration-gcp-gke"
NAMESPACE = "default"
PROJECT = "test-project-123"
SA_EMAIL = f"test-sa@{PROJECT}.iam.gserviceaccount.com"

CLOUDPLATFORM = "cloudplatform.gcp.m.upbound.io/v1beta1"
CONTAINER = "container.gcp.m.upbound.io/v1beta1"

COMPOSITE_LABELS = {"crossplane.io/composite": ID}
PROVIDER_CONFIG_REF = {"kind": "ProviderConfig", "name": "default"}
NETWORK_SELECTOR = {"matchLabels": {"networks.gcp.platform.upbound.io/network-id": ID}}

# The helm and kubernetes ProviderConfigs connect to the composed cluster the same way.
CLUSTER_PROVIDER_CONFIG_SPEC = {
    "credentials": {
        "secretRef": {"key": "kubeconfig", "name": f"{ID}-gkecluster", "namespace": NAMESPACE},
        "source": "Secret",
    },
    "identity": {
        "secretRef": {"key": "private_key", "name": f"{ID}-sakey", "namespace": NAMESPACE},
        "source": "Secret",
        "type": "GoogleApplicationCredentials",
    },
}


def observed(api_version: str, kind: str, resource_name: str, spec: dict, status: dict) -> dict:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {
            "name": ID,
            "namespace": NAMESPACE,
            "annotations": {"crossplane.io/composition-resource-name": resource_name},
        },
        "spec": {"providerConfigRef": PROVIDER_CONFIG_REF} | spec,
        "status": status,
    }


# A ServiceAccount that is Ready, with a cluster and node pool that already exist.
OBSERVED = [
    observed(
        CLOUDPLATFORM,
        "ServiceAccount",
        "serviceaccount",
        {"forProvider": {"displayName": "test-service-account"}},
        {
            "atProvider": {
                "id": f"projects/{PROJECT}/serviceAccounts/{SA_EMAIL}",
                "email": SA_EMAIL,
            },
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "lastTransitionTime": "2024-01-01T00:00:00Z",
                    "reason": "Available",
                },
            ],
        },
    ),
    observed(
        CONTAINER,
        "Cluster",
        "gkecluster",
        {"forProvider": {"location": "us-west2"}},
        {"atProvider": {"project": PROJECT}},
    ),
    observed(
        CONTAINER,
        "NodePool",
        "node-pool",
        {"forProvider": {"location": "us-west2"}},
        {"atProvider": {"nodeConfig": {"machineType": "n2-standard-4"}}},
    ),
]

# The same GKE as examples/gke-xr.yaml, wired to a Network by name rather than by label.
NETWORK = "configuration-gcp-gke-network"
XR_WITH_NETWORK_REF = {
    "apiVersion": "gcp.platform.upbound.io/v1alpha1",
    "kind": "GKE",
    "metadata": {"name": ID, "namespace": NAMESPACE},
    "spec": {
        "parameters": {
            "id": ID,
            "project": PROJECT,
            "region": "us-west2",
            "version": "1.34",
            "nodes": {"count": 1, "instanceType": "e2-standard-2", "diskSizeGb": 20},
            "managementPolicies": ["*"],
            "providerConfigName": "default",
            "networkRef": {"name": NETWORK},
        },
    },
}

COMPUTE = "https://www.googleapis.com/compute/v1"
NETWORK_SELF_LINK = f"{COMPUTE}/projects/{PROJECT}/global/networks/{ID}"
SUBNETWORK_SELF_LINK = f"{COMPUTE}/projects/{PROJECT}/regions/us-west2/subnetworks/{ID}-subnetwork"


def network(published: bool) -> dict:
    """The Network XR the GKE references, with the status configuration-gcp-network publishes.

    Names, region, CIDR and secondary ranges are published from the start; ids and selfLinks only
    once the network and subnetwork exist in GCP.
    """
    status = {
        "vpc": {"name": ID},
        "subnetwork": {
            "name": f"{ID}-subnetwork",
            "region": "us-west2",
            "ipCidrRange": "172.16.0.0/16",
            "secondaryRanges": {"pods": "pods", "services": "services"},
        },
    }
    if published:
        status["vpc"] |= {
            "id": f"projects/{PROJECT}/global/networks/{ID}",
            "selfLink": NETWORK_SELF_LINK,
        }
        status["subnetwork"] |= {
            "id": f"projects/{PROJECT}/regions/us-west2/subnetworks/{ID}-subnetwork",
            "selfLink": SUBNETWORK_SELF_LINK,
        }
    return {
        "apiVersion": "gcp.platform.upbound.io/v1alpha1",
        "kind": "Network",
        "metadata": {"name": NETWORK, "namespace": NAMESPACE},
        "spec": {"parameters": {"id": ID, "region": "us-west2"}},
        "status": status,
    }


# Resources that only need the service account, composed whether or not the Network is ready.
SERVICE_ACCOUNT_RESOURCES = [
    {
        "apiVersion": CLOUDPLATFORM,
        "kind": "ServiceAccount",
        "metadata": {"annotations": {"crossplane.io/external-name": ID}},
        "spec": {"forProvider": {"displayName": ID}},
    },
    {
        "apiVersion": CLOUDPLATFORM,
        "kind": "ServiceAccountKey",
        "spec": {"writeConnectionSecretToRef": {"name": f"{ID}-sakey"}},
    },
    {
        "apiVersion": CLOUDPLATFORM,
        "kind": "ProjectIAMMember",
        "spec": {"forProvider": {"member": f"serviceAccount:{SA_EMAIL}", "project": PROJECT}},
    },
]


def network_ref_test(name: str, published: bool, assert_resources: list[dict]):
    return compositiontest.CompositionTest(
        metadata=k8s.ObjectMeta(name=name),
        spec=compositiontest.Spec(
            assertResources=SERVICE_ACCOUNT_RESOURCES + assert_resources,
            compositionPath=COMPOSITION,
            xr=XR_WITH_NETWORK_REF,
            xrdPath=XRD,
            timeoutSeconds=60,
            validate=True,
            # extraResources feed the function's required resources.
            extraResources=[network(published)],
            observedResources=OBSERVED[:1],
        ),
    )


tests = [
    compositiontest.CompositionTest(
        metadata=k8s.ObjectMeta(name="test-gke-cluster"),
        spec=compositiontest.Spec(
            assertResources=[
                {
                    "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                    "kind": "GKE",
                    "metadata": {"name": ID, "namespace": NAMESPACE},
                    "spec": {
                        "parameters": {
                            "id": ID,
                            "project": PROJECT,
                            "region": "us-west2",
                            "version": "1.34",
                            "nodes": {"count": 1, "instanceType": "e2-standard-2"},
                            "managementPolicies": ["*"],
                            "providerConfigName": "default",
                        },
                    },
                    "status": {
                        "gke": {
                            "clusterName": ID,
                            "project": PROJECT,
                            "workloadPool": f"{PROJECT}.svc.id.goog",
                            "kubeconfigSecretRef": {
                                "name": f"{ID}-connection",
                                "namespace": NAMESPACE,
                                "key": "kubeconfig",
                            },
                            "nodePool": {"instanceType": "n2-standard-4"},
                        },
                    },
                },
                {
                    "apiVersion": CLOUDPLATFORM,
                    "kind": "ServiceAccount",
                    "metadata": {
                        "labels": COMPOSITE_LABELS,
                        "annotations": {"crossplane.io/external-name": ID},
                    },
                    "spec": {
                        "managementPolicies": ["*"],
                        "forProvider": {"displayName": ID},
                        "providerConfigRef": PROVIDER_CONFIG_REF,
                    },
                },
                {
                    "apiVersion": CLOUDPLATFORM,
                    "kind": "ServiceAccountKey",
                    "metadata": {"labels": COMPOSITE_LABELS},
                    "spec": {
                        "managementPolicies": ["*"],
                        "forProvider": {"serviceAccountIdSelector": {"matchControllerRef": True}},
                        "providerConfigRef": PROVIDER_CONFIG_REF,
                        "writeConnectionSecretToRef": {"name": f"{ID}-sakey"},
                    },
                },
                {
                    "apiVersion": CLOUDPLATFORM,
                    "kind": "ProjectIAMMember",
                    "metadata": {
                        "labels": COMPOSITE_LABELS,
                        "annotations": {"crossplane.io/external-name": ID},
                    },
                    "spec": {
                        "managementPolicies": ["*"],
                        "forProvider": {
                            "role": "roles/container.admin",
                            "member": f"serviceAccount:{SA_EMAIL}",
                            "project": PROJECT,
                        },
                        "providerConfigRef": PROVIDER_CONFIG_REF,
                    },
                },
                {
                    "apiVersion": CONTAINER,
                    "kind": "Cluster",
                    "metadata": {"labels": COMPOSITE_LABELS},
                    "spec": {
                        "managementPolicies": ["*"],
                        "forProvider": {
                            "location": "us-west2",
                            "deletionProtection": False,
                            "enableIntranodeVisibility": True,
                            "initialNodeCount": 1,
                            "ipAllocationPolicy": {
                                "clusterSecondaryRangeName": "pods",
                                "servicesSecondaryRangeName": "services",
                            },
                            "loggingService": "logging.googleapis.com/kubernetes",
                            "monitoringService": "monitoring.googleapis.com/kubernetes",
                            "networkSelector": NETWORK_SELECTOR,
                            "subnetworkSelector": NETWORK_SELECTOR,
                            "nodeVersion": "1.34",
                            "minMasterVersion": "1.34",
                            "nodeConfig": {"serviceAccount": SA_EMAIL},
                            "workloadIdentityConfig": {"workloadPool": f"{PROJECT}.svc.id.goog"},
                        },
                        "providerConfigRef": PROVIDER_CONFIG_REF,
                        "writeConnectionSecretToRef": {"name": f"{ID}-gkecluster"},
                    },
                },
                {
                    "apiVersion": CONTAINER,
                    "kind": "NodePool",
                    "metadata": {"labels": COMPOSITE_LABELS},
                    "spec": {
                        "managementPolicies": ["*"],
                        "forProvider": {
                            "location": "us-west2",
                            "autoscaling": {"maxNodeCount": 1, "minNodeCount": 1},
                            "clusterSelector": {"matchControllerRef": True},
                            "initialNodeCount": 1,
                            "management": {"autoRepair": True, "autoUpgrade": True},
                            "maxPodsPerNode": 55,
                            "nodeConfig": {
                                "diskSizeGb": 20,
                                "imageType": "COS_CONTAINERD",
                                "machineType": "e2-standard-2",
                                "metadata": {"disable-legacy-endpoints": "true"},
                                "oauthScopes": ["https://www.googleapis.com/auth/cloud-platform"],
                                "preemptible": True,
                                "shieldedInstanceConfig": {
                                    "enableIntegrityMonitoring": True,
                                    "enableSecureBoot": True,
                                },
                                "serviceAccount": SA_EMAIL,
                                "workloadMetadataConfig": {"mode": "GKE_METADATA"},
                            },
                        },
                        "providerConfigRef": PROVIDER_CONFIG_REF,
                    },
                },
                {
                    "apiVersion": "helm.m.crossplane.io/v1beta1",
                    "kind": "ProviderConfig",
                    "metadata": {
                        "labels": COMPOSITE_LABELS,
                        "name": ID,
                        "namespace": NAMESPACE,
                        "annotations": {
                            "crossplane.io/composition-resource-name": "providerConfigHelm"
                        },
                    },
                    "spec": CLUSTER_PROVIDER_CONFIG_SPEC,
                },
                {
                    "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
                    "kind": "ProviderConfig",
                    "metadata": {
                        "labels": COMPOSITE_LABELS,
                        "name": ID,
                        "namespace": NAMESPACE,
                        "annotations": {
                            "crossplane.io/composition-resource-name": "providerConfigKubernetes"
                        },
                    },
                    "spec": CLUSTER_PROVIDER_CONFIG_SPEC,
                },
                {
                    "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
                    "kind": "Object",
                    "metadata": {
                        "labels": COMPOSITE_LABELS,
                        "name": f"{ID}-workload-identity-settings",
                        "namespace": NAMESPACE,
                    },
                    "spec": {
                        "managementPolicies": ["Create", "Observe", "Update", "LateInitialize"],
                        "providerConfigRef": {"kind": "ProviderConfig", "name": ID},
                        "forProvider": {
                            "manifest": {
                                "apiVersion": "v1",
                                "kind": "ConfigMap",
                                "metadata": {
                                    "name": f"{ID}-workload-identity-settings",
                                    "namespace": "default",
                                },
                                "data": {"gkeProject": PROJECT},
                            },
                        },
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": f"{ID}-connection",
                        "namespace": NAMESPACE,
                        "annotations": {
                            "crossplane.io/composition-resource-name": "connection-secret"
                        },
                        "labels": COMPOSITE_LABELS,
                    },
                    "type": "connection.crossplane.io/v1alpha1",
                },
            ],
            compositionPath=COMPOSITION,
            xrPath=XR,
            xrdPath=XRD,
            timeoutSeconds=60,
            validate=True,
            observedResources=OBSERVED,
        ),
    ),
    # Before the Network publishes its selfLinks only the service account resources are composed;
    # that nothing else is, is asserted in the function's unit tests.
    network_ref_test(
        "test-gke-network-ref-waiting",
        published=False,
        assert_resources=[
            {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "GKE",
                "metadata": {"name": ID, "namespace": NAMESPACE},
                "status": {"gke": {"clusterName": ID, "project": PROJECT}},
            },
        ],
    ),
    # Once it has, the cluster is wired from the Network's status rather than by label.
    network_ref_test(
        "test-gke-network-ref-published",
        published=True,
        assert_resources=[
            {
                "apiVersion": CONTAINER,
                "kind": "Cluster",
                "metadata": {"labels": COMPOSITE_LABELS},
                "spec": {
                    "forProvider": {
                        "location": "us-west2",
                        "network": NETWORK_SELF_LINK,
                        "subnetwork": SUBNETWORK_SELF_LINK,
                        "ipAllocationPolicy": {
                            "clusterSecondaryRangeName": "pods",
                            "servicesSecondaryRangeName": "services",
                        },
                        "nodeConfig": {"serviceAccount": SA_EMAIL},
                    },
                    "writeConnectionSecretToRef": {"name": f"{ID}-gkecluster"},
                },
            },
            {
                "apiVersion": CONTAINER,
                "kind": "NodePool",
                "spec": {"forProvider": {"clusterSelector": {"matchControllerRef": True}}},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": f"{ID}-connection", "namespace": NAMESPACE},
                "type": "connection.crossplane.io/v1alpha1",
            },
        ],
    ),
]

# The test runner expects an "items" array, one entry per test.
output = {"items": [t.model_dump(by_alias=True, exclude_none=True) for t in tests]}
print(yaml.dump(output))
