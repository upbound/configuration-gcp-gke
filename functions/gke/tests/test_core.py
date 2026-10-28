"""Unit tests for composed-resource construction, status and validation.

These cover what the offline CompositionTests cannot express, most importantly the absence of
resources: assertResources matches resources that exist and has no "this was not composed" form.
Nearly every assertion is about the manifest the provider ends up with, so build() serializes
exactly as fn.py does.
"""

import inspect

import pytest
from models.io.upbound.platform.gcp.gke import v1alpha1 as gkev1alpha1
from pydantic import BaseModel, ValidationError

from function import core
from function.serialize import to_dict

XR = "my-gke"
NS = "team-a"
ID = "my-cluster"
PROJECT = "test-project-123"
SA_EMAIL = f"my-cluster@{PROJECT}.iam.gserviceaccount.com"
NETWORK = "my-network"

MINIMAL = {
    "id": ID,
    "region": "us-west2",
    "managementPolicies": ["*"],
    "providerConfigName": "default",
    "nodes": {"count": 3, "instanceType": "e2-standard-2"},
}

NETWORK_SELF_LINK = f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/global/networks/net"
SUBNETWORK_SELF_LINK = (
    f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/regions/us-west2/subnetworks/sub"
)

PRECONDITION = (core.SERVICE_ACCOUNT, core.SERVICE_ACCOUNT_KEY)
EVERYTHING = (*PRECONDITION, core.PROJECT_IAM_MEMBER, *core.NETWORK_DEPENDENT)


def params(**overrides) -> gkev1alpha1.Parameters:
    return gkev1alpha1.Parameters(**{**MINIMAL, **overrides})


def service_account(ready=True, email=SA_EMAIL) -> dict:
    status = {"atProvider": {"id": f"projects/{PROJECT}/serviceAccounts/{email}"}}
    if email:
        status["atProvider"]["email"] = email
    if ready:
        status["conditions"] = [{"type": "Ready", "status": "True", "reason": "Available"}]
    return {"metadata": {"name": ID}, "status": status}


SA_READY = {core.SERVICE_ACCOUNT: service_account()}


def network(published=True, ranges=None) -> dict:
    """A Network XR with the status configuration-gcp-network publishes.

    name, region, ipCidrRange and the secondary range names are published from the first
    reconcile; id and selfLink only once the network and subnetwork have been observed in GCP.
    """
    net = {"name": "net"}
    sub = {
        "name": "net-subnetwork",
        "region": "us-west2",
        "ipCidrRange": "172.16.0.0/16",
        "secondaryRanges": ranges
        if ranges is not None
        else {"pods": "pods", "services": "services"},
    }
    if published:
        net |= {"id": f"projects/{PROJECT}/global/networks/net", "selfLink": NETWORK_SELF_LINK}
        sub |= {
            "id": "projects/p/regions/us-west2/subnetworks/sub",
            "selfLink": SUBNETWORK_SELF_LINK,
        }
    return {
        "apiVersion": core.NETWORK_API_VERSION,
        "kind": core.NETWORK_KIND,
        "metadata": {"name": NETWORK, "namespace": NS},
        "status": {"vpc": net, "subnetwork": sub},
    }


REF = {"networkRef": {"name": NETWORK}}


def models(p=None, **kwargs) -> dict[str, BaseModel]:
    return core.build(XR, NS, p or params(), **kwargs)


def build(p=None, **kwargs) -> dict[str, dict]:
    """Composed resources serialized exactly as fn.py sends them."""
    return {k: to_dict(v) for k, v in models(p, **kwargs).items()}


def for_provider(resources: dict, name: str) -> dict:
    return resources[name]["spec"]["forProvider"]


class TestServiceAccountGate:
    """Nothing runs as the service account until it's Ready and has an email.

    A cluster's node service account can't be changed after creation, so composing the cluster
    early would bake in a wrong (empty) one.
    """

    @pytest.mark.parametrize(
        "observed",
        [
            {},
            {core.SERVICE_ACCOUNT: service_account(ready=False)},
            {core.SERVICE_ACCOUNT: service_account(email="")},
        ],
        ids=["not-observed", "not-ready", "no-email"],
    )
    def test_only_the_service_account_and_key_before_it_is_ready(self, observed):
        assert list(build(observed=observed)) == list(PRECONDITION)

    def test_everything_once_it_is_ready(self):
        # Order is part of the contract: it keeps rendered output stable.
        assert list(build(observed=SA_READY)) == list(EVERYTHING)

    def test_the_gate_applies_with_a_network_ref_too(self):
        got = build(params(**REF), observed={}, network=network())
        assert list(got) == list(PRECONDITION)


class TestLabelWiring:
    """Without a networkRef the cluster selects the Network's managed resources by label."""

    def test_cluster_selects_network_and_subnetwork_by_network_id(self):
        fp = for_provider(build(observed=SA_READY), core.GKE_CLUSTER)
        selector = {"matchLabels": {core.NETWORK_ID_LABEL: ID}}
        assert fp["networkSelector"] == selector
        assert fp["subnetworkSelector"] == selector
        assert fp["ipAllocationPolicy"] == {
            "clusterSecondaryRangeName": "pods",
            "servicesSecondaryRangeName": "services",
        }

    def test_cluster_names_no_network_by_value(self):
        fp = for_provider(build(observed=SA_READY), core.GKE_CLUSTER)
        assert "network" not in fp
        assert "subnetwork" not in fp

    def test_a_supplied_network_is_ignored(self):
        # Only a networkRef opts in. A stray required resource must not change the wiring of a
        # cluster that has always selected by label.
        assert build(observed=SA_READY, network=network()) == build(observed=SA_READY)

    def test_never_waits_for_a_network(self):
        assert not core.waiting_for_network(params(), None)


class TestNetworkRefWaiting:
    """With a networkRef nothing needing the network is composed until the Network publishes."""

    @pytest.mark.parametrize(
        "net",
        [
            None,
            {"metadata": {"name": NETWORK}},
            network(published=False),
            # Both selfLinks are required: a network with no subnetwork yet isn't somewhere a
            # cluster can run.
            network() | {"status": {"vpc": {"selfLink": NETWORK_SELF_LINK}}},
            network() | {"status": {"subnetwork": {"selfLink": SUBNETWORK_SELF_LINK}}},
        ],
        ids=["absent", "no-status", "unpublished", "network-only", "subnetwork-only"],
    )
    def test_network_dependent_resources_are_not_composed(self, net):
        p = params(**REF)
        got = build(p, observed=SA_READY, network=net)
        assert core.waiting_for_network(p, net)
        for name in core.NETWORK_DEPENDENT:
            assert name not in got, name

    def test_resources_that_dont_need_the_network_are_still_composed(self):
        # The IAM binding only needs the service account, so it's composed ahead of the cluster.
        got = build(params(**REF), observed=SA_READY, network=None)
        assert list(got) == [*PRECONDITION, core.PROJECT_IAM_MEMBER]

    def test_existing_resources_are_kept_while_waiting(self):
        # A composed resource missing from the desired state is deleted. Losing sight of the
        # Network for a moment must not take the cluster with it.
        observed = SA_READY | {
            core.GKE_CLUSTER: {
                "spec": {
                    "forProvider": {
                        "network": NETWORK_SELF_LINK,
                        "subnetwork": SUBNETWORK_SELF_LINK,
                        "ipAllocationPolicy": {
                            "clusterSecondaryRangeName": "gke-pods",
                            "servicesSecondaryRangeName": "gke-services",
                        },
                    }
                }
            },
            core.NODE_POOL: {},
        }
        got = build(params(**REF), observed=observed, network=None)
        assert core.GKE_CLUSTER in got
        assert core.NODE_POOL in got
        # Nothing that doesn't exist yet is added.
        assert core.PROVIDER_CONFIG_HELM not in got
        assert core.CONNECTION_SECRET not in got

    @pytest.mark.parametrize("shape", [dict, lambda d: [d]], ids=["object", "list"])
    def test_a_kept_cluster_keeps_the_network_it_has(self, shape):
        # Changing a cluster's network replaces it, so the observed wiring is re-emitted as is.
        # upjet serializes ipAllocationPolicy as an object or a one-element list by version.
        observed = SA_READY | {
            core.GKE_CLUSTER: {
                "spec": {
                    "forProvider": {
                        "network": "net-a",
                        "subnetwork": "sub-a",
                        "ipAllocationPolicy": shape(
                            {
                                "clusterSecondaryRangeName": "p",
                                "servicesSecondaryRangeName": "s",
                            }
                        ),
                    }
                }
            },
        }
        fp = for_provider(build(params(**REF), observed=observed), core.GKE_CLUSTER)
        assert fp["network"] == "net-a"
        assert fp["subnetwork"] == "sub-a"
        assert fp["ipAllocationPolicy"] == {
            "clusterSecondaryRangeName": "p",
            "servicesSecondaryRangeName": "s",
        }
        assert "networkSelector" not in fp

    def test_a_kept_cluster_keeps_its_kubeconfig_secret(self):
        observed = SA_READY | {core.GKE_CLUSTER: {}, core.CONNECTION_SECRET: {}}
        cds = {core.GKE_CLUSTER: {"kubeconfig": b"kc"}}
        got = build(params(**REF), observed=observed, connection_details=cds)
        assert got[core.CONNECTION_SECRET]["data"] == {"kubeconfig": "a2M="}


class TestNetworkRefPublished:
    """Once the Network publishes, the cluster is wired by value from its status."""

    def test_everything_is_composed(self):
        p = params(**REF)
        assert not core.waiting_for_network(p, network())
        assert list(build(p, observed=SA_READY, network=network())) == list(EVERYTHING)

    def test_cluster_takes_the_self_links(self):
        fp = for_provider(build(params(**REF), observed=SA_READY, network=network()), "gkecluster")
        assert fp["network"] == NETWORK_SELF_LINK
        assert fp["subnetwork"] == SUBNETWORK_SELF_LINK

    def test_cluster_does_not_select_by_label(self):
        # Selecting the Network's managed resources is the coupling networkRef removes.
        fp = for_provider(build(params(**REF), observed=SA_READY, network=network()), "gkecluster")
        for field in ("networkSelector", "subnetworkSelector", "networkRef", "subnetworkRef"):
            assert field not in fp, field

    def test_secondary_ranges_come_from_the_network(self):
        net = network(ranges={"pods": "gke-pods", "services": "gke-services"})
        fp = for_provider(build(params(**REF), observed=SA_READY, network=net), "gkecluster")
        assert fp["ipAllocationPolicy"] == {
            "clusterSecondaryRangeName": "gke-pods",
            "servicesSecondaryRangeName": "gke-services",
        }

    def test_missing_secondary_ranges_fall_back_to_the_networks_defaults(self):
        net = network(ranges={})
        fp = for_provider(build(params(**REF), observed=SA_READY, network=net), "gkecluster")
        assert fp["ipAllocationPolicy"] == {
            "clusterSecondaryRangeName": "pods",
            "servicesSecondaryRangeName": "services",
        }

    def test_published_status_wins_over_the_observed_cluster(self):
        observed = SA_READY | {core.GKE_CLUSTER: {"spec": {"forProvider": {"network": "old"}}}}
        fp = for_provider(build(params(**REF), observed=observed, network=network()), "gkecluster")
        assert fp["network"] == NETWORK_SELF_LINK

    def test_everything_else_matches_the_label_path(self):
        # networkRef changes where the cluster runs and nothing else.
        by_ref = build(params(**REF), observed=SA_READY, network=network())
        by_label = build(observed=SA_READY)
        for name in EVERYTHING:
            if name == core.GKE_CLUSTER:
                continue
            assert by_ref[name] == by_label[name], name
        assert _without_network(for_provider(by_ref, "gkecluster")) == _without_network(
            for_provider(by_label, "gkecluster")
        )


def _without_network(fp: dict) -> dict:
    fields = (
        "network",
        "subnetwork",
        "networkSelector",
        "subnetworkSelector",
        "ipAllocationPolicy",
    )
    return {k: v for k, v in fp.items() if k not in fields}


class TestComposedResources:
    def test_management_policies_and_provider_config_reach_every_gcp_resource(self):
        p = params(managementPolicies=["Observe"], providerConfigName="team-a")
        for name, res in build(p, observed=SA_READY).items():
            if res["apiVersion"].endswith(".gcp.m.upbound.io/v1beta1"):
                assert res["spec"]["managementPolicies"] == ["Observe"], name
                assert res["spec"]["providerConfigRef"] == {
                    "kind": "ProviderConfig",
                    "name": "team-a",
                }, name

    def test_empty_management_policies_and_provider_config_fall_back(self):
        p = params(managementPolicies=[], providerConfigName="")
        sa = build(p)[core.SERVICE_ACCOUNT]["spec"]
        assert sa["managementPolicies"] == ["*"]
        assert sa["providerConfigRef"]["name"] == "default"

    def test_latest_version_pins_no_version(self):
        fp = for_provider(build(params(version="latest"), observed=SA_READY), "gkecluster")
        assert "nodeVersion" not in fp
        assert "minMasterVersion" not in fp

    def test_workload_identity_off_omits_the_pool_and_metadata_mode(self):
        got = build(params(workloadIdentity={"enabled": False}), observed=SA_READY)
        assert "workloadIdentityConfig" not in for_provider(got, core.GKE_CLUSTER)
        assert "workloadMetadataConfig" not in for_provider(got, core.NODE_POOL)["nodeConfig"]

    def test_provider_configs_are_named_after_the_cluster_id(self):
        got = build(observed=SA_READY)
        for name in core.READY_WHEN_COMPOSED:
            assert got[name]["metadata"] == {"name": ID, "namespace": NS}, name

    def test_connection_secret_is_empty_until_the_cluster_exists(self):
        assert build(observed=SA_READY)[core.CONNECTION_SECRET]["data"] == {}

    def test_kubernetes_object_keeps_its_schema_default(self):
        # exclude_none, not exclude_defaults: watch: false has always been sent.
        got = build(observed=SA_READY)[core.WORKLOAD_IDENTITY_SETTINGS]
        assert got["spec"]["watch"] is False


class TestStatus:
    def test_published_before_anything_is_observed(self):
        assert core.status(XR, NS, params(project=PROJECT)) == {
            "gke": {
                "clusterName": ID,
                "project": PROJECT,
                "workloadPool": f"{PROJECT}.svc.id.goog",
                "kubeconfigSecretRef": {
                    "name": f"{XR}-connection",
                    "namespace": NS,
                    "key": "kubeconfig",
                },
                "nodePool": {"instanceType": "e2-standard-2"},
            }
        }

    def test_project_falls_back_to_the_service_account_then_the_cluster(self):
        assert core.status(XR, NS, params(), observed=SA_READY)["gke"]["project"] == PROJECT
        cluster = {core.GKE_CLUSTER: {"status": {"atProvider": {"project": "other"}}}}
        assert core.status(XR, NS, params(), observed=cluster)["gke"]["project"] == "other"

    @pytest.mark.parametrize("shape", [dict, lambda d: [d]], ids=["object", "list"])
    def test_running_machine_type_is_read_from_the_node_pool(self, shape):
        pool = {"status": {"atProvider": {"nodeConfig": shape({"machineType": "n2-standard-4"})}}}
        got = core.status(XR, NS, params(), observed={core.NODE_POOL: pool})
        assert got["gke"]["nodePool"] == {"instanceType": "n2-standard-4"}

    def test_status_matches_the_xrd_schema(self):
        # status.gke is typed in the XRD now; what the function publishes must fit it.
        gkev1alpha1.Status(**core.status(XR, NS, params(), observed=SA_READY))


class TestValidate:
    def test_no_network_ref_is_fine(self):
        assert core.validate(params()) == []

    @pytest.mark.parametrize("name", ["net", "my-network", "a.b-c", "0"])
    def test_valid_names_pass(self, name):
        assert core.validate(params(networkRef={"name": name})) == []

    @pytest.mark.parametrize("name", ["Net", "net_1", "-net", "net-", "a b", "a" * 254])
    def test_a_name_no_network_could_have_is_fatal(self, name):
        errors = core.validate(params(networkRef={"name": name}))
        assert len(errors) == 1
        assert "spec.parameters.networkRef.name" in errors[0]

    @pytest.mark.parametrize("ref", [{}, {"name": ""}])
    def test_the_xr_model_rejects_a_missing_or_empty_name(self, ref):
        # The XRD requires a non-empty name; the generated model enforces it before validate().
        with pytest.raises(ValidationError):
            params(networkRef=ref)


class TestStructure:
    def test_build_returns_models(self):
        for name, res in models(observed=SA_READY).items():
            assert isinstance(res, BaseModel), name

    def test_core_does_not_import_the_crossplane_sdk(self):
        src = inspect.getsource(core)
        assert "crossplane.function" not in src
        assert "resource.update" not in src
