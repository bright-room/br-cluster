package main

import rego.v1

test_pinned_version_passes if {
	count(deny) == 0 with input as _wrap([_hr("foo", "10.5.15")])
}

test_missing_version_fails if {
	hr := {
		"apiVersion": "helm.toolkit.fluxcd.io/v2",
		"kind": "HelmRelease",
		"metadata": {"name": "foo", "namespace": "bar"},
		"spec": {"chart": {"spec": {"chart": "foo"}}},
	}
	count(deny) == 1 with input as _wrap([hr])
}

test_floating_star_fails if {
	count(deny) == 1 with input as _wrap([_hr("foo", "*")])
}

test_floating_caret_fails if {
	count(deny) == 1 with input as _wrap([_hr("foo", "^1.2.3")])
}

test_floating_tilde_fails if {
	count(deny) == 1 with input as _wrap([_hr("foo", "~1.2.3")])
}

test_floating_x_pattern_fails if {
	count(deny) == 1 with input as _wrap([_hr("foo", "1.x")])
}

test_exception_skips_check if {
	hr := _hr("foo", "*")
	count(deny) == 0 with input as _wrap([hr])
		with data.exceptions.helmrelease_version_pinned as {"HelmRelease/bar/foo"}
}

test_oci_chartref_with_pinned_tag_passes if {
	count(deny) == 0 with input as _wrap([
		_mk_ocirepo("cilium", "kube-system", {"tag": "1.19.2"}),
		_mk_hr_chartref("cilium", "kube-system", "cilium", ""),
	])
}

test_oci_chartref_with_floating_tag_fails if {
	count(deny) == 1 with input as _wrap([
		_mk_ocirepo("cilium", "kube-system", {"tag": "*"}),
		_mk_hr_chartref("cilium", "kube-system", "cilium", ""),
	])
}

test_oci_chartref_with_digest_passes if {
	count(deny) == 0 with input as _wrap([
		_mk_ocirepo("cilium", "kube-system", {"digest": "sha256:abc"}),
		_mk_hr_chartref("cilium", "kube-system", "cilium", ""),
	])
}

_mk_hr_chartref(name, ns, ref_name, ref_ns) := {
	"apiVersion": "helm.toolkit.fluxcd.io/v2",
	"kind": "HelmRelease",
	"metadata": {"name": name, "namespace": ns},
	"spec": {"chartRef": _mk_ref(ref_name, ref_ns)},
}

_mk_ocirepo(name, ns, ref) := {
	"apiVersion": "source.toolkit.fluxcd.io/v1",
	"kind": "OCIRepository",
	"metadata": {"name": name, "namespace": ns},
	"spec": {"url": "oci://example.invalid/x", "ref": ref},
}

_mk_ref(name, "") := {"kind": "OCIRepository", "name": name}

_mk_ref(name, ns) := {"kind": "OCIRepository", "name": name, "namespace": ns} if ns != ""

_hr(name, version) := {
	"apiVersion": "helm.toolkit.fluxcd.io/v2",
	"kind": "HelmRelease",
	"metadata": {"name": name, "namespace": "bar"},
	"spec": {"chart": {"spec": {"chart": name, "version": version}}},
}

_wrap(items) := [{"path": "test.yaml", "contents": item} | some item in items]
