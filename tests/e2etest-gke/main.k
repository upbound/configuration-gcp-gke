
import models.io.upbound.dev.meta.v1alpha1 as metav1alpha1
import models.io.upbound.platform.gcp.v1alpha1 as platformgcpv1alpha1
import models.io.upbound.gcpm.v1beta1 as gcpv1beta1

# Network XR needed for GKE cluster
_network_xr = platformgcpv1alpha1.Network{
    metadata = {
        name = "e2e-gke-cluster-network"
        namespace = "default"
    }
    spec = {
        parameters = {
            id = "e2e-gke-cluster"
            region = "us-west2"
            managementPolicies = ["*"]
            providerConfigName = "default"
        }
    }
}

# GKE XR that depends on the network
_gke_xr = platformgcpv1alpha1.GKE{
    metadata = {
        name = "e2e-gke-cluster"
        namespace = "default"
    }
    spec = {
        parameters = {
            id = "e2e-gke-cluster"
            region = "us-west2"
            version = "latest"
            nodes = {
                count = 1
                instanceType = "e2-medium"
            }
            managementPolicies = ["*"]
            providerConfigName = "default"
        }
    }
}

_items = [
    metav1alpha1.E2ETest{
        metadata.name: "e2etest-gke-cluster"
        spec = {
            crossplane = {
                version = "2.0.2-up.5"
                autoUpgrade.channel = "Rapid"
            }
            defaultConditions: ["Ready", "Synced"]
            manifests: [
                _network_xr,
                _gke_xr
            ]
            extraResources: [
                gcpv1beta1.ProviderConfig{
                    metadata = {
                        name = "default"
                        namespace = "default"
                    }
                    spec: {
                        projectID: "crossplane-playground"
                        credentials: {
                            source: "Upbound"
                            upbound: {
                                federation: {
                                    providerID: "projects/283222062215/locations/global/workloadIdentityPools/solutions-upbound-oidc-pool/providers/solutions-u5d-oidc-pool"
                                    serviceAccount: "solutions-u5d-service-account@crossplane-playground.iam.gserviceaccount.com"
                                }
                            }
                        }
                    }
                }
            ]
            skipDelete: False
            timeoutSeconds: 4500
            cleanupTimeoutSeconds: 600
        }
    }
]
items = _items
