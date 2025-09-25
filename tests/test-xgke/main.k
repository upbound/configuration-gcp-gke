
import models.io.upbound.dev.meta.v1alpha1 as metav1alpha1
import models.io.upbound.gcp.cloudplatform.v1beta1 as cloudplatformv1beta1
import models.io.upbound.gcp.container.v1beta1 as containerv1beta1
import models.io.upbound.platform.gcp.v1alpha1 as platformgcpv1alpha1
import models.io.crossplane.helm.v1beta1 as helmv1beta1
import models.io.crossplane.kubernetes.v1alpha1 as kubernetesv1alpha1
import models.io.crossplane.kubernetes.v1alpha2 as kubernetesv1alpha2

_items = [
    metav1alpha1.CompositionTest {
        metadata.name: "test-gke-cluster"
        spec = {
            assertResources: [
                platformgcpv1alpha1.XGKE{
                    metadata = {
                        name = "configuration-gcp-gke"
                    }
                    spec = {
                        parameters = {
                            id = "configuration-gcp-gke"
                            region = "us-west2"
                            version = "latest"
                            nodes = {
                                count = 3
                                instanceType = "n1-standard-4"
                            }
                            deletionPolicy = "Delete"
                            providerConfigName = "default"
                        }
                    }
                }
                cloudplatformv1beta1.ServiceAccount{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                        annotations = {
                            "crossplane.io/external-name" = "configuration-gcp-gke"
                        }
                    }
                    spec = {
                        deletionPolicy = "Delete"
                        forProvider = {
                            displayName = "configuration-gcp-gke"
                        }
                        providerConfigRef = {
                            name = "default"
                        }
                    }
                }
                cloudplatformv1beta1.ServiceAccountKey{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                    }
                    spec = {
                        deletionPolicy = "Delete"
                        forProvider = {
                            serviceAccountIdSelector = {
                                matchControllerRef = True
                            }
                        }
                        providerConfigRef = {
                            name = "default"
                        }
                        writeConnectionSecretToRef = {
                            name = "configuration-gcp-gke-sakey"
                            namespace = "upbound-system"
                        }
                    }
                }
                cloudplatformv1beta1.ProjectIAMMember{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                        annotations = {
                            "crossplane.io/external-name" = "configuration-gcp-gke"
                        }
                    }
                    spec = {
                        deletionPolicy = "Delete"
                        forProvider = {
                            role = "roles/container.admin"
                            member = "serviceAccount:test-sa@test-project-123.iam.gserviceaccount.com"
                            project = "test-project-123"
                        }
                        providerConfigRef = {
                            name = "default"
                        }
                    }
                }
                containerv1beta1.Cluster{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                    }
                    spec = {
                        deletionPolicy = "Delete"
                        forProvider = {
                            location = "us-west2"
                            deletionProtection = False
                            enableIntranodeVisibility = True
                            initialNodeCount = 1
                            ipAllocationPolicy = [{
                                clusterSecondaryRangeName = "pods"
                                servicesSecondaryRangeName = "services"
                            }]
                            loggingService = "logging.googleapis.com/kubernetes"
                            monitoringService = "monitoring.googleapis.com/kubernetes"
                            networkSelector = {
                                matchLabels = {
                                    "networks.gcp.platform.upbound.io/network-id" = "configuration-gcp-gke"
                                }
                            }
                            subnetworkSelector = {
                                matchLabels = {
                                    "networks.gcp.platform.upbound.io/network-id" = "configuration-gcp-gke"
                                }
                            }
                            nodeConfig = [{
                                serviceAccount = "test-sa@test-project-123.iam.gserviceaccount.com"
                            }]
                            workloadIdentityConfig = [{
                                workloadPool = "test-project-123.svc.id.goog"
                            }]
                        }
                        providerConfigRef = {
                            name = "default"
                        }
                        writeConnectionSecretToRef = {
                            name = "configuration-gcp-gke-gkecluster"
                            namespace = "upbound-system"
                        }
                    }
                }
                containerv1beta1.NodePool{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                    }
                    spec = {
                        deletionPolicy = "Delete"
                        forProvider = {
                            location = "us-west2"
                            autoscaling = [{
                                maxNodeCount = 3
                                minNodeCount = 3
                            }]
                            clusterSelector = {
                                matchControllerRef = True
                            }
                            initialNodeCount = 3
                            management = [{
                                autoRepair = True
                                autoUpgrade = True
                            }]
                            maxPodsPerNode = 55
                            nodeConfig = [{
                                diskSizeGb = 10
                                imageType = "COS_CONTAINERD"
                                machineType = "n1-standard-4"
                                metadata = {
                                    "disable-legacy-endpoints" = "true"
                                }
                                oauthScopes = [
                                    "https://www.googleapis.com/auth/cloud-platform"
                                ]
                                preemptible = True
                                shieldedInstanceConfig = [{
                                    enableIntegrityMonitoring = True
                                    enableSecureBoot = True
                                }]
                                serviceAccount = "test-sa@test-project-123.iam.gserviceaccount.com"
                            }]
                        }
                        providerConfigRef = {
                            name = "default"
                        }
                    }
                }
                helmv1beta1.ProviderConfig{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                        name = "configuration-gcp-gke"
                        annotations = {
                            "crossplane.io/composition-resource-name" = "providerConfigHelm"
                        }
                    }
                    spec = {
                        credentials = {
                            secretRef = {
                                key = "kubeconfig"
                                name = "configuration-gcp-gke-gkecluster"
                                namespace = "upbound-system"
                            }
                            source = "Secret"
                        }
                        identity = {
                            secretRef = {
                                key = "private_key"
                                name = "configuration-gcp-gke-sakey"
                                namespace = "upbound-system"
                            }
                            source = "Secret"
                            type = "GoogleApplicationCredentials"
                        }
                    }
                }
                kubernetesv1alpha1.ProviderConfig{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                        name = "configuration-gcp-gke"
                        annotations = {
                            "crossplane.io/composition-resource-name" = "providerConfigKubernetes"
                        }
                    }
                    spec = {
                        credentials = {
                            secretRef = {
                                key = "kubeconfig"
                                name = "configuration-gcp-gke-gkecluster"
                                namespace = "upbound-system"
                            }
                            source = "Secret"
                        }
                        identity = {
                            secretRef = {
                                key = "private_key"
                                name = "configuration-gcp-gke-sakey"
                                namespace = "upbound-system"
                            }
                            source = "Secret"
                            type = "GoogleApplicationCredentials"
                        }
                    }
                }
                kubernetesv1alpha2.Object{
                    metadata = {
                        labels = {
                            "crossplane.io/composite" = "configuration-gcp-gke"
                        }
                        name = "configuration-gcp-gke-workload-identity-settings"
                    }
                    spec = {
                        deletionPolicy = "Orphan"
                        providerConfigRef = {
                            name = "configuration-gcp-gke"
                        }
                        forProvider = {
                            manifest = {
                                apiVersion = "v1"
                                kind = "ConfigMap"
                                metadata = {
                                    name = "configuration-gcp-gke-workload-identity-settings"
                                    namespace = "default"
                                }
                                data = {
                                    gkeProject = "test-project-123"
                                }
                            }
                        }
                    }
                }
            ]
            compositionPath: "apis/composition.yaml"
            xrPath: "examples/gke-xr.yaml"
            xrdPath: "apis/definition.yaml"
            timeoutSeconds: 60
            validate: False
            observedResources: [
                cloudplatformv1beta1.ServiceAccount{
                    metadata.annotations = {
                        "crossplane.io/composition-resource-name": "serviceaccount"
                    }
                    spec = {
                        forProvider = {
                            displayName: "test-service-account"
                        }
                        providerConfigRef = {
                            name: "default"
                        }
                    }
                    status = {
                        atProvider = {
                            id: "projects/test-project-123/serviceAccounts/test-sa@test-project-123.iam.gserviceaccount.com"
                            email: "test-sa@test-project-123.iam.gserviceaccount.com"
                        }
                    }
                }
                containerv1beta1.Cluster{
                    metadata.annotations = {
                        "crossplane.io/composition-resource-name": "gkecluster"
                    }
                    spec = {
                        forProvider = {
                            location: "us-west2"
                        }
                        providerConfigRef = {
                            name: "default"
                        }
                    }
                    status = {
                        atProvider = {
                            project: "test-project-123"
                        }
                    }
                }
            ]
        }
    }
]
items = _items