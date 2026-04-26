package main

import rego.v1

import data.exceptions
import data.lib.k8s

# HelmRelease の chart 版を必ず pin する。
#  - HelmRepository style: spec.chart.spec.version を pin
#  - OCIRepository style:  spec.chartRef が指す OCIRepository の spec.ref.tag/digest を pin
# floating ("*", "x.x", semver range "^/~/>/<") も Renovate を機能させない /
# 再現性が壊れるので禁止。
deny contains msg if {
	some hr in k8s.of_kind("HelmRelease")
	not k8s.ref(hr) in exceptions.helmrelease_version_pinned
	violation := _violation(hr)
	msg := sprintf("%s: %s", [k8s.ref(hr), violation])
}

# HelmRepository style: chart.spec.version
_violation(hr) := msg if {
	hr.spec.chart
	v := _chart_version(hr)
	_floating(v)
	msg := sprintf("chart.spec.version must be pinned (got %q)", [v])
}

# OCIRepository style: chartRef → OCIRepository.spec.ref.tag
_violation(hr) := msg if {
	hr.spec.chartRef
	hr.spec.chartRef.kind == "OCIRepository"
	ocr := _ocirepo(hr)
	v := _ocirepo_version(ocr)
	_floating(v)
	msg := sprintf(
		"OCIRepository %q ref.tag must be pinned (got %q)",
		[hr.spec.chartRef.name, v],
	)
}

_chart_version(hr) := v if {
	v := hr.spec.chart.spec.version
} else := ""

_ocirepo_version(ocr) := v if {
	v := ocr.spec.ref.tag
} else := v if {
	v := ocr.spec.ref.digest
} else := ""

_ocirepo(hr) := o if {
	some o in k8s.of_kind("OCIRepository")
	o.metadata.name == hr.spec.chartRef.name
	o.metadata.namespace == _ref_ns(hr, hr.spec.chartRef)
}

_ref_ns(_, ref) := ref.namespace

_ref_ns(hr, ref) := hr.metadata.namespace if not ref.namespace

# floating かどうか
_floating(v) if v == ""

_floating(v) if v == "*"

_floating(v) if contains(v, "x")

_floating(v) if startswith(v, ">")

_floating(v) if startswith(v, "<")

_floating(v) if startswith(v, "^")

_floating(v) if startswith(v, "~")
