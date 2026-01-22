
import models.io.upbound.gcpm.cloudplatform.v1beta1 as cloudplatformv1beta1
import models.io.upbound.gcpm.container.v1beta1 as containerv1beta1
import models.io.upbound.platform.gcp.v1alpha1 as platformgcpv1alpha1
import models.io.crossplane.helm.v1beta1 as helmv1beta1
import models.io.crossplane.kubernetes.v1alpha1 as kubernetesv1alpha1
import models.io.crossplane.kubernetes.v1alpha2 as kubernetesv1alpha2
import models.io.k8s.api.core.v1 as corev1
import base64

oxr = platformgcpv1alpha1.GKE{**option("params").oxr}
_ocds = option("params")?.ocds or {}
params = oxr.spec.parameters
providerConfigRefName = params.providerConfigName or "default"

# Get status information from observed composed resources using correct access pattern
_service_account_id = _ocds?.serviceaccount?.Resource?.status?.atProvider?.id or ""
_service_account_email = _ocds?.serviceaccount?.Resource?.status?.atProvider?.email or ""
_cluster_project_id = _ocds?.gkecluster?.Resource?.status?.atProvider?.project or ""

# Check if ServiceAccount is ready before using it in cluster configuration
_serviceAccountReadyCondition = [condition for condition in (_ocds?.serviceaccount?.Resource?.status?.conditions or []) if condition.type == "Ready" and condition.status == "True"]
_isServiceAccountReady = len(_serviceAccountReadyCondition) > 0

# Extract project ID from service account status
_project_id = ""
if _service_account_id:
    # Format: "projects/test-project-123/serviceAccounts/email"
    # Split by "/" and take the element at index 1
    parts = _service_account_id.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        _project_id = parts[1]

# Get project ID from GKE cluster status (fallback)
if not _project_id and _cluster_project_id:
    _project_id = _cluster_project_id

_metadata = lambda name: str -> any {
    {
        annotations = {"krm.kcl.dev/composition-resource-name" = name}
    }
}

_defaults = {
    managementPolicies = params.managementPolicies or ["*"]
    if providerConfigRefName:
        providerConfigRef = {
            kind = "ProviderConfig"
            name = providerConfigRefName
        }
}

_connection_secret_name = lambda suffix: str -> str {
    _uid = oxr.metadata.uid if oxr.metadata?.uid else oxr.metadata.name
    "{}-{}".format(_uid, suffix)
}

# Create service account and IAM resources first
_base_items = [
    cloudplatformv1beta1.ServiceAccount{
        metadata = _metadata("serviceaccount") | {
            annotations = {
                "crossplane.io/external-name" = params.id
                "krm.kcl.dev/composition-resource-name" = "serviceaccount"
            }
        }
        spec = _defaults | {
            forProvider = {
                displayName = params.id
            }
        }
    }
    cloudplatformv1beta1.ServiceAccountKey{
        metadata = _metadata("serviceaccountkey")
        spec = _defaults | {
            forProvider = {
                serviceAccountIdSelector = {
                    matchControllerRef = True
                }
            }
            writeConnectionSecretToRef = {
                name = _connection_secret_name("sakey")
            }
        }
    }
]

# Create GKE cluster and dependent resources only when service account is ready
_gke_items = [] if not (_service_account_email and _isServiceAccountReady) else [
    cloudplatformv1beta1.ProjectIAMMember{
        metadata = _metadata("projectiammember") | {
            annotations = {
                "crossplane.io/external-name" = params.id
                "krm.kcl.dev/composition-resource-name" = "projectiammember"
            }
        }
        spec = _defaults | {
            forProvider = {
                role = "roles/container.admin"
                member = "serviceAccount:{}".format(_service_account_email)
                project = _project_id
            }
        }
    }
    containerv1beta1.Cluster{
        metadata = _metadata("gkecluster")
        spec = _defaults | {
            forProvider = {
                location = params.region
                deletionProtection = False
                enableIntranodeVisibility = True
                initialNodeCount = 1
                ipAllocationPolicy = {
                    clusterSecondaryRangeName = "pods"
                    servicesSecondaryRangeName = "services"
                }
                loggingService = "logging.googleapis.com/kubernetes"
                monitoringService = "monitoring.googleapis.com/kubernetes"
                networkSelector = {
                    matchLabels = {
                        "networks.gcp.platform.upbound.io/network-id" = params.id
                    }
                }
                subnetworkSelector = {
                    matchLabels = {
                        "networks.gcp.platform.upbound.io/network-id" = params.id
                    }
                }
                **({nodeVersion = params.version} if params.version != "latest" else {})
                **({minMasterVersion = params.version} if params.version != "latest" else {})
                nodeConfig = {
                    serviceAccount = _service_account_email
                }
                workloadIdentityConfig = {
                    workloadPool = "{}.svc.id.goog".format(_project_id)
                }
            }
            writeConnectionSecretToRef = {
                name = _connection_secret_name("gkecluster")
            }
        }
    }
    containerv1beta1.NodePool{
        metadata = _metadata("node-pool")
        spec = _defaults | {
            forProvider = {
                location = params.region
                autoscaling = {
                    maxNodeCount = params.nodes.count
                    minNodeCount = params.nodes.count
                }
                clusterSelector = {
                    matchControllerRef = True
                }
                initialNodeCount = params.nodes.count
                management = {
                    autoRepair = True
                    autoUpgrade = True
                }
                maxPodsPerNode = 55
                nodeConfig = {
                    diskSizeGb = 10
                    imageType = "COS_CONTAINERD"
                    machineType = params.nodes.instanceType
                    metadata = {
                        "disable-legacy-endpoints" = "true"
                    }
                    oauthScopes = [
                        "https://www.googleapis.com/auth/cloud-platform"
                    ]
                    preemptible = True
                    shieldedInstanceConfig = {
                        enableIntegrityMonitoring = True
                        enableSecureBoot = True
                    }
                    serviceAccount = _service_account_email
                }
            }
        }
    }
    helmv1beta1.ProviderConfig{
        metadata = _metadata("providerConfigHelm") | {
            name = params.id
            annotations = {
                "krm.kcl.dev/composition-resource-name" = "providerConfigHelm"
                "krm.kcl.dev/ready" = "True"
            }
        }
        spec = {
            credentials = {
                secretRef = {
                    key = "kubeconfig"
                    name = _connection_secret_name("gkecluster")
                    namespace = "upbound-system"
                }
                source = "Secret"
            }
            identity = {
                secretRef = {
                    key = "private_key"
                    name = _connection_secret_name("sakey")
                    namespace = "upbound-system"
                }
                source = "Secret"
                type = "GoogleApplicationCredentials"
            }
        }
    }
    kubernetesv1alpha1.ProviderConfig{
        metadata = _metadata("providerConfigKubernetes") | {
            name = params.id
            annotations = {
                "krm.kcl.dev/composition-resource-name" = "providerConfigKubernetes"
                "krm.kcl.dev/ready" = "True"
            }
        }
        spec = {
            credentials = {
                secretRef = {
                    key = "kubeconfig"
                    name = _connection_secret_name("gkecluster")
                    namespace = "upbound-system"
                }
                source = "Secret"
            }
            identity = {
                secretRef = {
                    key = "private_key"
                    name = _connection_secret_name("sakey")
                    namespace = "upbound-system"
                }
                source = "Secret"
                type = "GoogleApplicationCredentials"
            }
        }
    }
    kubernetesv1alpha2.Object{
        metadata = _metadata("workloadIdentitySettings") | {
            name = "{}-workload-identity-settings".format(params.id)
        }
        spec = {
            managementPolicies = ["Create", "Observe", "Update", "LateInitialize"]
            providerConfigRef = {
                name = params.id
            }
            forProvider = {
                manifest = {
                    apiVersion = "v1"
                    kind = "ConfigMap"
                    metadata = {
                        name = "{}-workload-identity-settings".format(params.id)
                        namespace = "default"
                    }
                    data = {
                        **({gkeProject = _project_id} if _project_id else {})
                    }
                }
            }
        }
    }
    corev1.Secret{
        metadata = {
            name = "{}-connection".format(oxr.metadata.name)
            namespace = oxr.metadata.namespace
            annotations = {
                "krm.kcl.dev/composition-resource-name" = "connection-secret"
            }
            labels = {
                "crossplane.io/composite" = oxr.metadata.name
            }
        }
        type = "connection.crossplane.io/v1alpha1"
        if "gkecluster" in _ocds:
            data = {
                # Password comes pre-encoded from managed resource's connection secret
                kubeconfig = _ocds["gkecluster"].ConnectionDetails?.kubeconfig or ""
            }
        else:
            data = {}
    }
]

items = _base_items + _gke_items
