# GCP GKE Configuration

This repository contains an Upbound project, tailored for users establishing their initial control plane with [Upbound](https://cloud.upbound.io). This configuration deploys fully managed [Google Kubernetes Engine (GKE)](https://cloud.google.com/kubernetes-engine) clusters with secure networking and workload identity.

**Version:** Crossplane v2 (namespaced resources, embedded Python functions)

## Overview

The core components of a custom API in [Upbound Project](https://docs.upbound.io/learn/control-plane-project/) include:

- **CompositeResourceDefinition (XRD):** Defines the API's structure.
- **Composition(s):** Configures the Functions Pipeline
- **Embedded Function(s):** Encapsulates the Composition logic and implementation within a self-contained, reusable unit

In this specific configuration, the API contains:

- **a [GCP GKE](/apis/gke/definition.yaml) custom resource type.**
- **Composition:** Configured in [/apis/gke/composition.yaml](/apis/gke/composition.yaml)
- **Embedded Function:** The Composition logic is encapsulated within an [embedded function](/functions/gke/function/): [core.py](/functions/gke/function/core.py) builds the composed resources and status, [fn.py](/functions/gke/function/fn.py) adapts it to Crossplane

## Architecture

This configuration creates the following GCP resources in a managed sequence:

### Phase 1 - Base Resources
- **GCP Service Account:** Custom service account for GKE node pools
- **Service Account Key:** JSON key for service account authentication

### Phase 2 - GKE Resources (created after service account is ready)
- **Project IAM Member:** Grants `roles/container.admin` to the service account

With `networkRef` set, the resources below additionally wait for the referenced Network to publish
its status (see [Networking](#networking)).

- **GKE Cluster:** Managed Kubernetes cluster with VPC-native networking
- **Node Pool:** Managed node pool with custom service account and security settings
- **Provider Configurations:** Helm and Kubernetes provider configs for cluster access
- **Workload Identity ConfigMap:** Settings for GKE workload identity integration

## Key Features

- **VPC-Native Networking:** Runs in a Network from [configuration-gcp-network](https://marketplace.upbound.io/configurations/upbound/configuration-gcp-network), referenced by name through its published status (see [Networking](#networking))
- **Workload Identity:** Enables secure pod-to-GCP service authentication with custom service accounts
- **Connection Secret Forwarding:** Automatically propagates kubeconfig to consuming applications
- **Managed Node Pools:** Pre-configured with security hardening (shielded instances, integrity monitoring)
- **Auto-scaling Support:** Configurable node pool scaling with management policies

## Implementation Patterns

This configuration follows modern Upbound DevEx best practices:

- **Conditional Resource Creation:** GKE cluster is only created after service account is ready, preventing immutable field update conflicts
- **Embedded Python Functions:** Replaced complex patch-and-transform pipelines with a maintainable Python function built on typed models generated from the provider schemas
- **Status Reference Handling:** Reads cross-resource dependencies from observed composed resources, e.g. `_get(observed, SERVICE_ACCOUNT, "status", "atProvider", "email")`, keyed by composition resource name, and other domains (the Network) only through their published status
- **Comprehensive Testing:** Includes unit tests of the function, composition tests and end-to-end deployment tests

## Dependencies

This configuration requires (Crossplane v2 compatible):

- **configuration-gcp-network v3:** Provides the Network the cluster runs in, referenced by `networkRef` through its published status (or, without `networkRef`, matched via `network-id` labels)
- **GCP Providers (namespaced):** gcp-compute, gcp-container and gcp-cloudplatform `>=v3.0.5, <v4.0.0`
- **Additional Providers:** helm v1.0.0, kubernetes v1.0.0
- **Crossplane Functions:** function-auto-ready v0.4.0 for readiness management

## Upgrading to provider-gcp v3

This configuration requires provider-gcp `>=v3.0.5, <v4.0.0`, which removes the
deprecated `v1beta1` API versions. New control planes can install it directly.

On a control plane that already runs provider-gcp v1.x or v2.x, at any minor
version, the provider's [storage version migration](https://github.com/crossplane-contrib/provider-upjet-gcp/releases/tag/v3.0.0) is mandatory and
must finish before this configuration is upgraded. It applies to the
sub-providers this configuration installs: provider-gcp-compute, provider-gcp-container and provider-gcp-cloudplatform.

1. Upgrade those sub-providers to v2.6, by way of v2.0 if they're on v1.x.
   v2.6 is a required stop: it stores objects as `v1beta2` while `v1beta1` is
   still served.
2. Upgrade them to v3.0.5 or later with the provider's migration
   `DeploymentRuntimeConfig` referenced from each `Provider`'s
   `runtimeConfigRef`. Use one per sub-provider, since its images must match
   that sub-provider's v3 image, and apply the ClusterRole and
   ClusterRoleBinding that come with it, with the binding's namespace set to
   Crossplane's, so the migration can clear `status.storedVersions`. Don't
   apply it to `provider-family-gcp`. Once the init container logs show the
   migration finished, remove the `runtimeConfigRef`.
3. Then upgrade this configuration.

Don't upgrade this configuration first. With automatic dependency version
upgrades enabled, its dependency constraint moves the sub-providers straight to
v3 and skips the migration; without them, it can't resolve its dependencies.
Rebuilding the control plane is the alternative to migrating.

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
kind: Network
metadata:
  name: my-network
  namespace: default
spec:
  parameters:
    id: my-network
    region: us-west2
---
apiVersion: gcp.platform.upbound.io/v1alpha1
kind: GKE  # Note: X-prefix removed in v2
metadata:
  name: my-gke-cluster
  namespace: default  # Required in v2
spec:
  parameters:
    id: my-gke-cluster
    region: us-west2
    version: "1.34"
    networkRef:
      name: my-network  # a Network in this namespace
    nodes:
      count: 3
      instanceType: e2-standard-4
    managementPolicies: ["*"]  # Replaces deletionPolicy in v2
    providerConfigName: default
```

**Note:** In Crossplane v2, connection secrets are automatically created with the naming pattern `{xr-name}-connection` in the same namespace as the XR. The `writeConnectionSecretToRef` field is no longer used in XR specs.

## Networking

A GKE finds its network in one of two ways.

- **`spec.parameters.networkRef.name` (recommended):** the name of a `Network` XR in the GKE's
  namespace. The function requests that Network and reads its published status, the contract
  configuration-gcp-network offers:
  - `status.vpc.selfLink` and `status.subnetwork.selfLink` become the cluster's `network` and
    `subnetwork`. Their presence is the Network's signal that both exist in GCP, so until both are
    published the cluster and everything that depends on it (node pool, ProviderConfigs, Workload
    Identity settings, connection Secret) are not composed, and the GKE reports a Normal
    "Waiting for Network ..." event. Any of them that already exist are kept meanwhile.
  - `status.subnetwork.secondaryRanges.pods` and `.services` name the cluster's pod and service
    secondary ranges.

  The GKE never selects the Network's managed resources, so the Network's implementation can
  change without breaking the cluster, and the Network's `id` doesn't have to match the GKE's.
- **Unset (the default, unchanged):** the cluster selects the network and subnetwork managed
  resources labelled `networks.gcp.platform.upbound.io/network-id: <spec.parameters.id>`, so the
  Network's `spec.parameters.id` must equal the GKE's. This couples the GKE to how the Network is
  implemented; prefer `networkRef` for new clusters.

Changing a cluster's network replaces it in GCP: on an existing GKE, only set `networkRef` to the
Network the cluster already runs in. Doing that changes nothing on the cluster: the label selectors
resolve to the same network and subnetwork selfLinks that `networkRef` reads from the Network's
status.

## Status

`status.gke` publishes, for configurations that build on a GKE to read instead of reaching into
its composed resources:

| Field | Value |
| --- | --- |
| `clusterName` | Name of the composed Cluster; `spec.parameters.id` until it is observed |
| `project` | `spec.parameters.project`, else the project of the observed service account or cluster |
| `workloadPool` | The Workload Identity pool, `<project>.svc.id.goog` |
| `kubeconfigSecretRef` | `name`, `namespace` and `key` of the Secret holding the kubeconfig |
| `nodePool.instanceType` | The node pool's running machine type; `spec.parameters.nodes.instanceType` until observed |

The schema is typed but open: fields may be added, and unknown ones are preserved.

## Testing

The configuration can be tested using:

- `up composition render --xrd=apis/gke/definition.yaml apis/gke/composition.yaml examples/gke-xr.yaml` to render the composition
- `up test run tests/test-*` to run composition tests in `tests/test-gke/`
- `up test run tests/e2etest-* --e2e` to run end-to-end tests in `tests/e2etest-gke/`
- `python -m pytest tests -q` from `functions/gke/` to run the function's unit tests (after
  `up project build` has generated the models into `.up/python`)

### Test Coverage

- **Unit Tests:** What composition tests can't assert, chiefly that resources are *not* composed:
  before the service account is ready, while waiting for a referenced Network's status, and that
  no label selectors are used with `networkRef`; plus `validate()` errors
- **Composition Tests:** Validate resource generation, status reference handling, and the `networkRef` path before and after the Network publishes its status (the Network is supplied through `extraResources`)
- **E2E Tests:** Full deployment of a Network and a GKE that references it by `networkRef`

## Migration from Crossplane v1 to v2

This configuration has been migrated from Crossplane v1 to v2. Key changes include:

### Breaking Changes

- **API Kind:** `XGKE` → `GKE` (X-prefix removed in v2)
- **Namespace Requirement:** All XRs must include `namespace: default` (or other namespace)
- **managementPolicies:** Replaces `deletionPolicy` parameter
- **Connection Secrets:** Automatically created with pattern `{xr-name}-connection`, no `writeConnectionSecretToRef` in XR spec
- **Provider APIs:** Namespaced groups (`*.gcp.m.upbound.io`, `helm.m.crossplane.io`, `kubernetes.m.crossplane.io`) instead of cluster-scoped
- **File Structure:** Reorganized into `apis/gke/` subdirectory

### Migration Path

If upgrading from v1:

1. Update all XR manifests: change `kind: XGKE` to `kind: GKE` and add `namespace: default`
2. Replace `deletionPolicy` with `managementPolicies: ["*"]`
3. Remove `writeConnectionSecretToRef` from XR specs (secrets auto-generated)
4. Update any automation/scripts to use new file paths (`apis/gke/` instead of `apis/`)
5. Refer to `.agents/plans/CROSSPLANE_V2_MIGRATION.md` for detailed migration steps

## Next Steps

This repository serves as a foundational step. To enhance your configuration, consider:

1. Creating new API definitions in this same repo
2. Editing the existing API definition to your needs
3. Adding additional GCP services (databases, monitoring, etc.)
4. Customizing node pool configurations for different workloads

To learn more about how to build APIs for your managed control planes in Upbound, read the guide on [Upbound's docs](https://docs.upbound.io/).
