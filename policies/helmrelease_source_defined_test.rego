package main

import rego.v1

test_existing_helmrepo_passes if {
	count(deny) == 0 with input as _wrap([
		_repo("HelmRepository", "grafana", "default"),
		_hr_with_source("dash", "default", "HelmRepository", "grafana", ""),
	])
}

test_existing_ocirepo_passes if {
	count(deny) == 0 with input as _wrap([
		_repo("OCIRepository", "cilium", "kube-system"),
		_hr_with_source("cilium", "kube-system", "OCIRepository", "cilium", ""),
	])
}

test_missing_source_fails if {
	count(deny) == 1 with input as _wrap([
		_hr_with_source("dash", "default", "HelmRepository", "ghost", ""),
	])
}

test_typo_in_source_name_fails if {
	count(deny) == 1 with input as _wrap([
		_repo("HelmRepository", "grafana", "default"),
		_hr_with_source("dash", "default", "HelmRepository", "grafanaa", ""),
	])
}

test_namespace_resolution if {
	# sourceRef.namespace 明示指定が一致すれば OK
	count(deny) == 0 with input as _wrap([
		_repo("HelmRepository", "shared", "flux-system"),
		_hr_with_source("dash", "default", "HelmRepository", "shared", "flux-system"),
	])
}

test_namespace_mismatch_fails if {
	count(deny) == 1 with input as _wrap([
		_repo("HelmRepository", "shared", "flux-system"),
		_hr_with_source("dash", "default", "HelmRepository", "shared", "other-ns"),
	])
}

test_other_kind_source_ignored if {
	# GitRepository 等 (HelmRepository/OCIRepository 以外) は対象外
	count(deny) == 0 with input as _wrap([
		_hr_with_source("dash", "default", "GitRepository", "anything", ""),
	])
}

test_oci_chartref_with_existing_source_passes if {
	count(deny) == 0 with input as _wrap([
		_oci_repo_pinned("cilium", "kube-system"),
		_mk_hr_chartref("cilium", "kube-system", "cilium", ""),
	])
}

_oci_repo_pinned(name, ns) := {
	"apiVersion": "source.toolkit.fluxcd.io/v1",
	"kind": "OCIRepository",
	"metadata": {"name": name, "namespace": ns},
	"spec": {"url": "oci://example.invalid/x", "ref": {"tag": "1.0.0"}},
}

test_oci_chartref_missing_source_fails if {
	count(deny) == 1 with input as _wrap([
		_mk_hr_chartref("cilium", "kube-system", "ghost", ""),
	])
}

_mk_hr_chartref(name, ns, ref_name, ref_ns) := {
	"apiVersion": "helm.toolkit.fluxcd.io/v2",
	"kind": "HelmRelease",
	"metadata": {"name": name, "namespace": ns},
	"spec": {"chartRef": _mk_ref(ref_name, ref_ns)},
}

_mk_ref(name, "") := {"kind": "OCIRepository", "name": name}

_mk_ref(name, ns) := {"kind": "OCIRepository", "name": name, "namespace": ns} if ns != ""

test_exception_skips_check if {
	count(deny) == 0 with input as _wrap([
		_hr_with_source("dash", "default", "HelmRepository", "ghost", ""),
	])
		with data.exceptions.helmrelease_source_defined as {"HelmRelease/default/dash"}
}

_repo(kind, name, ns) := {
	"apiVersion": "source.toolkit.fluxcd.io/v1",
	"kind": kind,
	"metadata": {"name": name, "namespace": ns},
	"spec": {"url": "https://example.invalid"},
}

_hr_with_source(name, ns, src_kind, src_name, src_ns) := {
	"apiVersion": "helm.toolkit.fluxcd.io/v2",
	"kind": "HelmRelease",
	"metadata": {"name": name, "namespace": ns},
	"spec": {"chart": {"spec": {
		"chart": name,
		"version": "1.0.0",
		"sourceRef": _src(src_kind, src_name, src_ns),
	}}},
}

_src(kind, name, "") := {"kind": kind, "name": name}

_src(kind, name, ns) := {"kind": kind, "name": name, "namespace": ns} if ns != ""

_wrap(items) := [{"path": "test.yaml", "contents": item} | some item in items]
