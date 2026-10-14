# GKE composition function

Composes a `GKE` from GCP managed resources: a service account and key first,
then, once the service account is Ready, the cluster, its node pool, an IAM
binding, Helm and Kubernetes `ProviderConfig`s, a Workload Identity settings
`Object` and the XR's connection `Secret`. It also publishes `status.gke` for
configurations that build on this API.

`compose()` in [function/fn.py](function/fn.py) holds the logic and has no gRPC
dependency. Don't rename the composition resource names: Crossplane would
replace the resources they identify. The function is built and tested with the
rest of the project:

```console
up project build
up test run tests/test-gke
```
