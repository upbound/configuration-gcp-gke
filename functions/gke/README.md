# GKE composition function

Composes a `GKE` from GCP managed resources: a service account and key first,
then, once the service account is Ready, the cluster, its node pool, an IAM
binding, Helm and Kubernetes `ProviderConfig`s, a Workload Identity settings
`Object` and the XR's connection `Secret`. It also publishes `status.gke` for
configurations that build on this API.

With `spec.parameters.networkRef`, it requests the named `Network` as a required
resource and wires the cluster from its published status; until the Network has
published `status.vpc.selfLink` and `status.subnetwork.selfLink`, nothing
that needs the network is composed (existing resources are kept).

- [function/core.py](function/core.py): `build()` returns the composed resources
  as typed models, `status()` the XR status and `validate()` fatal errors. No
  Crossplane SDK, so it's unit-testable directly.
- [function/fn.py](function/fn.py): the gRPC adapter. Parses the XR, requests
  the Network, calls core and serializes with
  [function/serialize.py](function/serialize.py).

Don't rename the composition resource names: Crossplane would replace the
resources they identify. The function is built and tested with the rest of the
project:

```console
up project build                 # also generates the models into .up/python
up test run tests/test-gke
cd functions/gke && python -m pytest tests -q
```
