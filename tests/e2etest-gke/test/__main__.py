"""Generate the GKE E2ETest and print it as YAML."""

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.e2etest import v1alpha1 as e2etest

NAMESPACE = "default"
CLUSTER_ID = "e2e-gke-cluster"

test = e2etest.E2ETest(
    metadata=k8s.ObjectMeta(name="e2etest-gke-cluster"),
    spec=e2etest.Spec(
        crossplane=e2etest.Crossplane(
            version="2.0.2-up.5",
            autoUpgrade=e2etest.AutoUpgrade(channel="Rapid"),
        ),
        defaultConditions=["Ready", "Synced"],
        manifests=[
            # The GKE cluster finds the network through its network-id labels.
            {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "Network",
                "metadata": {"name": f"{CLUSTER_ID}-network", "namespace": NAMESPACE},
                "spec": {
                    "parameters": {
                        "id": CLUSTER_ID,
                        "region": "us-west2",
                        "managementPolicies": ["*"],
                        "providerConfigName": "default",
                    },
                },
            },
            {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "GKE",
                "metadata": {"name": CLUSTER_ID, "namespace": NAMESPACE},
                "spec": {
                    "parameters": {
                        "id": CLUSTER_ID,
                        "project": "crossplane-playground",
                        "region": "us-west2",
                        "version": "1.34",
                        "nodes": {"count": 1, "instanceType": "e2-medium"},
                        "managementPolicies": ["*"],
                        "providerConfigName": "default",
                    },
                },
            },
        ],
        extraResources=[
            {
                "apiVersion": "gcp.m.upbound.io/v1beta1",
                "kind": "ProviderConfig",
                "metadata": {"name": "default", "namespace": NAMESPACE},
                "spec": {
                    "projectID": "crossplane-playground",
                    "credentials": {
                        "source": "Upbound",
                        "upbound": {
                            "federation": {
                                "providerID": "projects/283222062215/locations/global/workloadIdentityPools/solutions-upbound-oidc-pool/providers/solutions-u5d-oidc-pool",
                                "serviceAccount": "solutions-u5d-service-account@crossplane-playground.iam.gserviceaccount.com",
                            },
                        },
                    },
                },
            },
        ],
        skipDelete=False,
        timeoutSeconds=4500,
        cleanupTimeoutSeconds=600,
    ),
)

# The test runner expects an "items" array, one entry per test.
output = {"items": [test.model_dump(by_alias=True, exclude_none=True)]}
print(yaml.dump(output))
