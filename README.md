# GCP GKE Configuration

This repository contains an Upbound project, tailored for users establishing their initial control plane with [Upbound](https://cloud.upbound.io). This configuration deploys fully managed [Google Kubernetes Engine (GKE)](https://cloud.google.com/kubernetes-engine) clusters with secure networking and workload identity.

## Overview

The core components of a custom API in [Upbound Project](https://docs.upbound.io/learn/control-plane-project/) include:

- **CompositeResourceDefinition (XRD):** Defines the API's structure.
- **Composition(s):** Configures the Functions Pipeline
- **Embedded Function(s):** Encapsulates the Composition logic and implementation within a self-contained, reusable unit

In this specific configuration, the API contains:

- **a [GCP GKE](/apis/definition.yaml) custom resource type.**
- **Composition:** Configured in [/apis/composition.yaml](/apis/composition.yaml)
- **Embedded Function:** The Composition logic is encapsulated within [embedded function](/functions/xgke/main.k)

## Architecture

This configuration creates the following GCP resources in a managed sequence:

### Phase 1 - Base Resources
- **GCP Service Account:** Custom service account for GKE node pools
- **Service Account Key:** JSON key for service account authentication

### Phase 2 - GKE Resources (created after service account is ready)
- **Project IAM Member:** Grants `roles/container.admin` to the service account
- **GKE Cluster:** Managed Kubernetes cluster with VPC-native networking
- **Node Pool:** Managed node pool with custom service account and security settings
- **Provider Configurations:** Helm and Kubernetes provider configs for cluster access
- **Workload Identity ConfigMap:** Settings for GKE workload identity integration

## Key Features

- **VPC-Native Networking:** Integrates with [configuration-gcp-network](https://marketplace.upbound.io/configurations/upbound/configuration-gcp-network) for secure networking
- **Workload Identity:** Enables secure pod-to-GCP service authentication with custom service accounts
- **Connection Secret Forwarding:** Automatically propagates kubeconfig to consuming applications
- **Managed Node Pools:** Pre-configured with security hardening (shielded instances, integrity monitoring)
- **Auto-scaling Support:** Configurable node pool scaling with management policies

## Implementation Patterns

This configuration follows modern Upbound DevEx best practices:

- **Conditional Resource Creation:** GKE cluster is only created after service account is ready, preventing immutable field update conflicts
- **Embedded KCL Functions:** Replaced complex patch-and-transform pipelines with maintainable KCL code
- **Status Reference Handling:** Uses the pattern `_ocds?.resourceName?.Resource?.status?.atProvider?.field` for cross-resource dependencies
- **Comprehensive Testing:** Includes both composition tests and end-to-end deployment tests

## Dependencies

This configuration requires:

- **configuration-gcp-network:** Provides VPC and subnet resources (matched via `network-id` labels)
- **GCP Provider:** Multiple GCP providers (compute, container, cloudplatform)
- **Crossplane Functions:** Embedded KCL function support

## Deployment

- Execute `up project run`
- Alternatively, install the Configuration from the [Upbound Marketplace](https://marketplace.upbound.io/configurations/upbound/configuration-gcp-gke)
- Check [examples](/examples/) for example XR (Composite Resource)

### Prerequisites

Ensure your GCP service account has the following IAM roles:
- `roles/container.admin` - For GKE cluster management
- `roles/compute.networkAdmin` - For network resource management
- `roles/iam.serviceAccountAdmin` - For service account creation
- `roles/iam.serviceAccountKeyAdmin` - For service account key management

## Usage Example

```yaml
apiVersion: gcp.platform.upbound.io/v1alpha1
kind: XGKE
metadata:
  name: my-gke-cluster
spec:
  parameters:
    id: my-gke-cluster
    region: us-west2
    version: "1.28"
    nodes:
      count: 3
      instanceType: e2-standard-4
    deletionPolicy: Delete
    providerConfigName: default
  writeConnectionSecretToRef:
    name: my-cluster-kubeconfig
    namespace: upbound-system
```

## Testing

The configuration can be tested using:

- `up composition render --xrd=apis/definition.yaml apis/composition.yaml examples/gke-xr.yaml` to render the composition
- `up test run tests/*` to run composition tests in `tests/test-xgke/`
- `up test run tests/* --e2e` to run end-to-end tests in `tests/e2etest-xgke/`

### Test Coverage

- **Composition Tests:** Validate resource generation, status reference handling, and conditional resource creation logic
- **E2E Tests:** Full deployment including network dependencies and provider configuration

## Migration from Legacy Configurations

This configuration has been migrated from legacy patch-and-transform patterns to modern Upbound DevEx architecture, providing improved maintainability and reliability.

## Next Steps

This repository serves as a foundational step. To enhance your configuration, consider:

1. Creating new API definitions in this same repo
2. Editing the existing API definition to your needs
3. Adding additional GCP services (databases, monitoring, etc.)
4. Customizing node pool configurations for different workloads

To learn more about how to build APIs for your managed control planes in Upbound, read the guide on [Upbound's docs](https://docs.upbound.io/).