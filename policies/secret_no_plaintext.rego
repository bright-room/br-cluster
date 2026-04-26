package main

import rego.v1

import data.exceptions
import data.lib.k8s

# 平文 Secret を repo に commit するのを禁じる。Secret は ExternalSecret /
# cert-manager / Flux bootstrap (Ansible 経路) が生成する想定。
# 例外 (ConfigMap-as-Secret 的な空 data 用途等) は exceptions.rego で許可。
deny contains msg if {
	some s in k8s.of_kind("Secret")
	not k8s.ref(s) in exceptions.secret_no_plaintext
	_has_payload(s)
	msg := sprintf(
		"%s: plaintext Secret committed; provision via ExternalSecret (1Password) or cert-manager",
		[k8s.ref(s)],
	)
}

_has_payload(s) if s.data

_has_payload(s) if s.stringData
