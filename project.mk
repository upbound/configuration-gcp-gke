PROJECT_NAME := configuration-gcp-gke
UPTEST_INPUT_MANIFESTS := ./examples/network-xr.yaml,./examples/gke-xr.yaml
UPTEST_SKIP_UPDATE := true
UPTEST_SKIP_IMPORT := true
UPTEST_DEFAULT_TIMEOUT = 3600s
