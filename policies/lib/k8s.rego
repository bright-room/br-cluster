package lib.k8s

import rego.v1

# `--combine` で渡される入力 (`[{path, contents}, ...]`) から resource 本体だけ
# 取り出す。`contents` が null のファイル (空 yaml 等) は除外。
resources contains r if {
	some entry in input
	r := entry.contents
	r.kind
}

# kind でフィルタした resource 一覧
of_kind(kind) := [r |
	some r in resources
	r.kind == kind
]

# resource 識別子 (deny メッセージ用)
ref(r) := sprintf("%s/%s/%s", [r.kind, _ns(r), r.metadata.name])

_ns(r) := r.metadata.namespace

_ns(r) := "_" if not r.metadata.namespace
