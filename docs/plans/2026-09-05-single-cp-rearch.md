# シングル control-plane 化とノード役割再編 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** k3s を control-plane 1 台 + worker 2 台に縮小し、余った 4 台を PostgreSQL / オブジェクトストレージ / オブザーバビリティ / 雑務用の単独ホストとして再定義できる状態まで、リポジトリのコード・設定・マニフェストを書き換える。

**Architecture:** `servers.yaml` を SoT として、Python CLI (`cluster-forge`) が Ansible inventory を生成し、Ansible がノードを構築し、Flux が k3s 内のマニフェストを同期する。この 3 層それぞれに変更が入る。CLI 層は `ServerType` に汎用 `standalone` 型を導入し、`services` リストから Ansible グループを生成する形に変える。Ansible 層は `roles/external` を `garage` / `caddy` / `certbot` / `postgresql` に分解し、gateway の DNS を 2 ゾーン構成に作り替える。マニフェスト層はオブザーバビリティ一式・Longhorn・CNPG・kube-vip・kured・external-dns-coredns・Argo Events を撤去する。

**Tech Stack:** Python 3 (pydantic / click / PyYAML / pytest), Ansible, Jinja2, Kustomize, Flux, Helm, Conftest (Rego), k3s, Cilium

**Spec:** [`docs/proposals/2026-09-05-single-cp-rearch.md`](../proposals/2026-09-05-single-cp-rearch.md)

## Global Constraints

- クラスタ LAN は `172.22.52.0/24`。ホスト IP は gateway1=`.1` / db1=`.10` / storage1=`.20` / observability1=`.30` / ai1=`.70` / cluster1-3=`.100`-`.102`。DHCP 動的レンジ `.150`-`.190`。LB プール `172.22.52.192/26`、cluster-gateway `172.22.52.200`。
- ホストドメインは `{{ cluster_env }}.br-cluster.bright-room.net`、サービスドメインは `{{ cluster_env }}.internal-service.bright-room.net`。prod の実値は `prod.br-cluster.bright-room.net` / `prod.internal-service.bright-room.net`。
- 旧ゾーン `cluster-internal.bright-room.net` は全廃。
- `b8m.app` (外部公開) は一切変更しない。
- ノード名: `br-gateway1` / `br-db1` / `br-storage1` / `br-observability1` / `br-ai1` / `br-cluster1` / `br-cluster2` / `br-cluster3`。
- `K8sRole` enum の値 (`primary` / `secondary` / `worker`) は変更しない。`secondary` は未使用値として残す。
- k3s の datastore は SQLite。`cluster-init` と `etcd-expose-metrics` を削除する。
- control-plane の taint `node-role.kubernetes.io/control-plane:NoSchedule` は維持する。
- k3s の `disable:` リストは現状維持 (`local-storage` を無効のままにする)。PVC 利用者はゼロになる。
- PostgreSQL の認証情報は 1Password の `postgresql` アイテム 1 つが唯一の出所。フィールド名は `zitadel_password` / `argo_workflows_password`。Ansible の role も k3s の ExternalSecret も同じアイテムを読む。
- 変更したすべての行は spec の記述に直接たどれること。無関係なリファクタリング・フォーマット修正はしない。
- コミットメッセージは Conventional Commits 風 (`feat(scope): ...` / `fix(scope): ...` / `refactor(scope): ...` / `docs(scope): ...`)。日本語可。
- 各コミットの末尾に以下を付ける:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```

## 作業の分割 (PR 単位)

repo の CLAUDE.md は「PR 単位で 1 トピック」を求めている。本計画は 3 つの PR に分ける。各 PR は単独で lint / test を通過する。

| PR | 範囲 | タスク | 検証コマンド |
|---|---|---|---|
| 1 | CLI とサーバー定義 | Task 1–6 | `make lint` / `make test` |
| 2 | Ansible (inventory / roles / k3s) | Task 7–17 | `make ansible/lint` / `make yaml/lint` |
| 3 | マニフェストとドキュメント | Task 18–28 | `make manifests/build` / `make policy/test` / `make manifests/flux-local` |

PR 1 → 2 → 3 の順に進める。PR 2 は PR 1 の `services` フィールドに依存し、PR 3 は PR 2 のドメイン名に依存する。

**注意:** PR 3 をマージした時点で現行クラスタの Flux は壊れる (存在しないコンポーネントを prune しようとする)。spec の [移行手順](../proposals/2026-09-05-single-cp-rearch.md#移行手順) の通り、**3 つの PR をすべてマージしてから旧クラスタをシャットダウンし、全台リフラッシュする**。稼働中クラスタに PR 3 だけ先に当ててはいけない。

---

# PR 1: CLI とサーバー定義

## Task 1: `ServerType.STANDALONE` と `services` フィールド

**Files:**
- Modify: `cli/cluster_forge/models.py`
- Modify: `cli/tests/conftest.py`
- Test: `cli/tests/test_inventory.py`

**Interfaces:**
- Produces: `ServerType.STANDALONE` (値 `"standalone"`)、`ServerDefinition.services: list[str]` (デフォルト `[]`)。`ServerType.EXTERNAL` は削除される。

- [ ] **Step 1: 失敗するテストを書く**

`cli/tests/test_inventory.py` の末尾に追記する。

```python
class TestStandaloneServerType:
    def test_standalone_type_exists(self) -> None:
        from cluster_forge.models import ServerType

        assert ServerType.STANDALONE == "standalone"

    def test_external_type_removed(self) -> None:
        from cluster_forge.models import ServerType

        assert not hasattr(ServerType, "EXTERNAL")

    def test_services_defaults_to_empty_list(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(name="br-ai1", type=ServerType.STANDALONE)
        assert server.services == []

    def test_services_accepts_list(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(
            name="br-storage1",
            type=ServerType.STANDALONE,
            services=["garage", "caddy", "certbot"],
        )
        assert server.services == ["garage", "caddy", "certbot"]

    def test_standalone_does_not_need_network_config(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(name="br-db1", type=ServerType.STANDALONE)
        assert server.needs_network_config is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest cli/tests/test_inventory.py::TestStandaloneServerType -v`
Expected: FAIL — `AttributeError: STANDALONE`

- [ ] **Step 3: 最小の実装**

`cli/cluster_forge/models.py` の `ServerType` と `ServerDefinition` を書き換える。

```python
class ServerType(StrEnum):
    GATEWAY = "gateway"
    NODE = "node"
    STANDALONE = "standalone"


class K8sRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    WORKER = "worker"


class ServerDefinition(BaseModel):
    name: str
    type: ServerType
    k8s_role: K8sRole | None = None
    services: list[str] = []

    @property
    def needs_network_config(self) -> bool:
        return self.type == ServerType.GATEWAY
```

`services: list[str] = []` は pydantic v2 では安全 (mutable default はモデルごとにコピーされる)。

- [ ] **Step 4: `conftest.py` のフィクスチャを新ノードマップに更新**

`ServerType.EXTERNAL` を消すと `cli/tests/conftest.py` が構築時に落ち、pytest が**収集の段階で失敗する**。Task 2 以降の「テストを走らせて失敗を見る」が成立しなくなるので、フィクスチャの更新はここで済ませる。

`cli/tests/conftest.py` の `gateway_server` から `full_inventory` までを次で置き換える。`external_server` フィクスチャは削除し、`standalone_server` に置き換える。

```python
@pytest.fixture
def gateway_server() -> ServerDefinition:
    return ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY)


@pytest.fixture
def node_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
    )


@pytest.fixture
def standalone_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-storage1",
        type=ServerType.STANDALONE,
        services=["garage", "caddy", "certbot"],
    )


@pytest.fixture
def worker_node_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-cluster2", type=ServerType.NODE, k8s_role=K8sRole.WORKER
    )


@pytest.fixture
def sample_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(
                name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-storage1",
                type=ServerType.STANDALONE,
                services=["garage", "caddy", "certbot"],
            ),
        ],
    )


@pytest.fixture
def full_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(
                name="br-db1",
                type=ServerType.STANDALONE,
                services=["postgresql", "certbot"],
            ),
            ServerDefinition(
                name="br-storage1",
                type=ServerType.STANDALONE,
                services=["garage", "caddy", "certbot"],
            ),
            ServerDefinition(name="br-observability1", type=ServerType.STANDALONE),
            ServerDefinition(name="br-ai1", type=ServerType.STANDALONE),
            ServerDefinition(
                name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-cluster2", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
            ServerDefinition(
                name="br-cluster3", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
        ],
    )
```

`external_server` フィクスチャを参照しているテストが他にあれば `standalone_server` に読み替える。

Run: `grep -rn 'external_server' cli/tests/`
Expected: 出力なし

- [ ] **Step 5: 新しいテストが通ることを確認**

Run: `uv run pytest cli/tests/test_inventory.py::TestStandaloneServerType -v`
Expected: PASS

- [ ] **Step 6: 残りの失敗箇所を確認**

Run: `uv run pytest -v 2>&1 | tail -40`
Expected: 収集は成功する。`ServerType.EXTERNAL` を参照する `cli/cluster_forge/inventory_generator.py` と、旧ホスト名を期待する `cli/tests/test_inventory_generator.py` / `cli/tests/test_bootstrap.py` が FAIL する。これは Task 2–4 で直す。**この時点ではコミットしない。**

## Task 2: `_build_domains()` からホスト種別の分岐を削除

**Files:**
- Modify: `cli/cluster_forge/inventory_generator.py:16-27`
- Test: `cli/tests/test_inventory_generator.py`

**Interfaces:**
- Consumes: Task 1 の `ServerType.STANDALONE`
- Produces: `_build_domains(server: ServerDefinition, host_domain_ref: str) -> dict` — 返すのは `{"server": "<short>.<host_domain_ref>"}` の 1 キーのみ。

- [ ] **Step 1: 失敗するテストを書く**

`cli/tests/test_inventory_generator.py` の `TestGenerateClusterHosts` クラス内で、`test_gateway_has_dns_ntp_domains` と `test_external_has_object_storage_domain` の 2 メソッドを次の 3 メソッドに置き換える。

```python
    def test_every_host_has_only_server_domain(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        for entry in result:
            assert list(entry["domains"].keys()) == ["server"]

    def test_server_domain_uses_host_domain_ref(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert gw["domains"]["server"] == "gateway1.{{ host_domain }}"

    def test_service_domains_are_not_generated(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        for entry in result:
            assert "dns" not in entry["domains"]
            assert "ntp" not in entry["domains"]
            assert "object_storage" not in entry["domains"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py::TestGenerateClusterHosts -v`
Expected: FAIL — `domains` に `dns` / `ntp` が含まれる、`cluster_domain` 参照になっている

- [ ] **Step 3: 実装**

`cli/cluster_forge/inventory_generator.py` の `_build_domains()` を置き換える。

```python
def _build_domains(server: ServerDefinition, host_domain_ref: str) -> dict:
    """Build the host domain mapping. One record per physical server.

    Service records (dns / ntp / object-storage / rdbms / k8s-api) live in
    `service_records` in group_vars/all/network.yaml, not here — a service
    name does not always match a role name, and a service can move between
    hosts without the host record changing.
    """
    short = server.name.replace("br-", "")
    return {"server": f"{short}.{host_domain_ref}"}
```

同ファイルの `generate_cluster_hosts()` 内、Jinja 参照を差し替える。

```python
        # Use Jinja2 reference for host_domain so Ansible resolves it
        domain_ref = "{{ host_domain }}"
```

`ServerType` の import は Task 3 でも使うのでそのまま残す。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py::TestGenerateClusterHosts -v`
Expected: PASS

## Task 3: `services` から Ansible グループを生成

**Files:**
- Modify: `cli/cluster_forge/inventory_generator.py:38-76`
- Test: `cli/tests/test_inventory_generator.py`

**Interfaces:**
- Consumes: Task 1 の `ServerDefinition.services` と Step 4 で更新済みの `cli/tests/conftest.py`
- Produces: `generate_hosts_yaml()` が `all.children` に `br_cluster` / `gateway` / `clusters` / `standalone` と、`services` に現れた各サービス名のグループを持つ dict を返す。`external` グループは無くなる。

- [ ] **Step 1: フィクスチャが更新済みであることを確認**

`cli/tests/conftest.py` のフィクスチャは Task 1 Step 4 で新ノードマップに更新済み。

Run: `grep -n 'br-db1\|br-storage1\|br-cluster1' cli/tests/conftest.py`
Expected: `full_inventory` に 8 台すべてが並んでいる。まだ旧ホスト名なら Task 1 Step 4 を先に済ませる。

- [ ] **Step 2: 失敗するテストを書く**

`cli/tests/test_inventory_generator.py` の `TestGenerateHostsYaml` を置き換える。`test_external_group` を削除し、`test_secondary_group` の期待値を「空」に、残りを新ホスト名にする。

```python
class TestGenerateHostsYaml:
    def test_contains_all_groups(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        children = result["all"]["children"]
        assert "br_cluster" in children
        assert "gateway" in children
        assert "clusters" in children
        assert "standalone" in children

    def test_external_group_removed(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        assert "external" not in result["all"]["children"]

    def test_gateway_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        gw_hosts = result["all"]["children"]["gateway"]["hosts"]
        assert "br-gateway1" in gw_hosts

    def test_primary_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        primary = result["all"]["children"]["clusters"]["children"]["master"]
        assert "br-cluster1" in primary["children"]["primary"]["hosts"]

    def test_secondary_group_is_empty(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        master = result["all"]["children"]["clusters"]["children"]["master"]
        assert master["children"]["secondary"]["hosts"] == {}

    def test_worker_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        workers = result["all"]["children"]["clusters"]["children"]["worker"]
        hosts = workers["hosts"]
        assert "br-cluster2" in hosts
        assert "br-cluster3" in hosts

    def test_standalone_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        hosts = result["all"]["children"]["standalone"]["hosts"]
        assert "br-db1" in hosts
        assert "br-storage1" in hosts
        assert "br-observability1" in hosts
        assert "br-ai1" in hosts

    def test_service_groups_generated(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert children["garage"]["hosts"] == {"br-storage1": None}
        assert children["caddy"]["hosts"] == {"br-storage1": None}
        assert children["postgresql"]["hosts"] == {"br-db1": None}

    def test_service_group_can_span_hosts(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert children["certbot"]["hosts"] == {
            "br-storage1": None,
            "br-db1": None,
        }

    def test_no_group_for_unused_service(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert "cloudflared" not in children

    def test_br_cluster_contains_every_server(
        self, full_inventory: Inventory
    ) -> None:
        result = generate_hosts_yaml(full_inventory)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-gateway1" in cluster
        assert "br-db1" in cluster
        assert "br-ai1" in cluster
        assert "br-cluster1" in cluster
        assert "br-cluster3" in cluster

    def test_node_without_k8s_role_excluded_from_clusters(self) -> None:
        inv = Inventory(
            environments=["dev"],
            servers=[
                ServerDefinition(name="br-orphan", type=ServerType.NODE, k8s_role=None),
            ],
        )
        result = generate_hosts_yaml(inv)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-orphan" not in cluster
```

`test_service_group_can_span_hosts` の期待値は挿入順に依存する。`full_inventory` の並びは `br-db1` が先、`br-storage1` が後なので、実装は `inventory.servers` の順で走査する。上のテストは dict の等価比較 (順序非依存) なので順序に影響されない。

- [ ] **Step 3: テストが失敗することを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py::TestGenerateHostsYaml -v`
Expected: FAIL — `KeyError: 'standalone'`

- [ ] **Step 4: 実装**

`cli/cluster_forge/inventory_generator.py` の `generate_hosts_yaml()` を置き換える。

```python
def generate_hosts_yaml(inventory: Inventory) -> dict:
    """Generate Ansible hosts.yaml structure from servers.yaml."""
    gateways = [s for s in inventory.servers if s.type == ServerType.GATEWAY]
    standalones = [s for s in inventory.servers if s.type == ServerType.STANDALONE]
    nodes_with_k8s = [
        s for s in inventory.servers if s.type == ServerType.NODE and s.k8s_role
    ]
    primaries = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.PRIMARY]
    secondaries = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.SECONDARY]
    workers = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.WORKER]

    cluster_members = [*gateways, *standalones, *nodes_with_k8s]

    def hosts_dict(servers: list[ServerDefinition]) -> dict:
        return {s.name: None for s in servers}

    # One group per service name, in first-seen order. A service can span
    # hosts (certbot runs on both storage1 and db1).
    service_groups: dict[str, dict] = {}
    for server in inventory.servers:
        for service in server.services:
            service_groups.setdefault(service, {"hosts": {}})["hosts"][
                server.name
            ] = None

    structure: dict = {
        "all": {
            "children": {
                "br_cluster": {"hosts": hosts_dict(cluster_members)},
                "gateway": {"hosts": hosts_dict(gateways)},
                "clusters": {
                    "children": {
                        "master": {
                            "children": {
                                "primary": {"hosts": hosts_dict(primaries)},
                                "secondary": {"hosts": hosts_dict(secondaries)},
                            },
                        },
                        "worker": {"hosts": hosts_dict(workers)},
                    },
                },
                "standalone": {"hosts": hosts_dict(standalones)},
                **service_groups,
            },
        },
    }
    return structure
```

- [ ] **Step 5: テストが通ることを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py -v`
Expected: PASS (`TestGenerateClusterHosts` の `test_returns_entry_per_server` は 8 台のままなので通る)

## Task 4: `MockSecretProvider` を新ノードマップに更新

**Files:**
- Modify: `cli/cluster_forge/secrets.py:110-145`
- Test: `cli/tests/test_inventory_generator.py`

**Interfaces:**
- Produces: `MockSecretProvider.MOCK_SERVERS` のキーが新ホスト名になる。IP は RFC 5737 の TEST-NET-1 (`192.0.2.0/24`) のまま。

- [ ] **Step 1: 失敗するテストを書く**

`cli/tests/test_inventory_generator.py` の `TestGenerateClusterHosts` の `test_uses_secrets_for_ip_and_mac` の下に追記する。

```python
    def test_mock_provider_covers_every_server(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        # 192.0.2.99 is the MockSecretProvider fallback for unknown hosts.
        for entry in result:
            assert entry["ip"] != "192.0.2.99", entry["name"]

    def test_cluster1_mock_ip(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        node = next(h for h in result if h["name"] == "br-cluster1")
        assert node["ip"] == "192.0.2.100"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py::TestGenerateClusterHosts::test_mock_provider_covers_every_server -v`
Expected: FAIL — `br-db1` などが fallback の `192.0.2.99` になる

- [ ] **Step 3: 実装**

`cli/cluster_forge/secrets.py` の `MOCK_SERVERS` を置き換える。IP の下位オクテットは本番の割り当て (`.1` / `.10` / `.20` / `.30` / `.70` / `.100`-`.102`) と同じ数字にして対応を追いやすくする。

```python
    MOCK_SERVERS: dict[str, InventorySecrets] = {
        "br-gateway1": InventorySecrets(
            ip_address="192.0.2.1",
            mac_address="00:00:5e:00:53:01",
            wan_ip="198.51.100.50",
        ),
        "br-db1": InventorySecrets(
            ip_address="192.0.2.10",
            mac_address="00:00:5e:00:53:10",
        ),
        "br-storage1": InventorySecrets(
            ip_address="192.0.2.20",
            mac_address="00:00:5e:00:53:20",
        ),
        "br-observability1": InventorySecrets(
            ip_address="192.0.2.30",
            mac_address="00:00:5e:00:53:30",
        ),
        "br-ai1": InventorySecrets(
            ip_address="192.0.2.70",
            mac_address="00:00:5e:00:53:70",
        ),
        "br-cluster1": InventorySecrets(
            ip_address="192.0.2.100",
            mac_address="00:00:5e:00:53:64",
        ),
        "br-cluster2": InventorySecrets(
            ip_address="192.0.2.101",
            mac_address="00:00:5e:00:53:65",
        ),
        "br-cluster3": InventorySecrets(
            ip_address="192.0.2.102",
            mac_address="00:00:5e:00:53:66",
        ),
    }
```

MAC の最終オクテットは RFC 7042 のドキュメント用範囲 `00:00:5E:00:53:00`–`FF` に収める必要がある。`.100`–`.102` は 16 進で `64`–`66`。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest cli/tests/test_inventory_generator.py -v`
Expected: PASS

- [ ] **Step 5: CLI テスト全体を確認**

Run: `uv run pytest -v 2>&1 | tail -30`
Expected: `test_bootstrap.py` が `ServerType.EXTERNAL` や旧ホスト名を参照していれば FAIL する。FAIL したテストの `br-node1` → `br-cluster1`、`br-external1` → `br-storage1`、`ServerType.EXTERNAL` → `ServerType.STANDALONE` に置き換える。`bootstrap.py` 本体はロジック上 `ServerType.GATEWAY` しか見ていないので変更不要。

## Task 5: `servers.yaml` と playbook / Makefile のリネーム

**Files:**
- Modify: `servers.yaml`
- Modify: `cli/cluster_forge/provisioner.py:5-16`
- Modify: `Makefile:135` (`PLAYBOOKS` 変数の行)
- Test: `cli/tests/test_provisioner.py`

**Interfaces:**
- Consumes: Task 1 の `services`
- Produces: `PLAYBOOK_COMMANDS` のキー `setup-standalone` → `playbooks/setup_standalone.yaml`。`setup-external` は消える。

- [ ] **Step 1: 失敗するテストを書く**

`cli/tests/test_provisioner.py` の末尾に追記する。

```python
class TestPlaybookRename:
    def test_setup_standalone_registered(self) -> None:
        from cluster_forge.provisioner import PLAYBOOK_COMMANDS

        assert PLAYBOOK_COMMANDS["setup-standalone"] == (
            "playbooks/setup_standalone.yaml"
        )

    def test_setup_external_removed(self) -> None:
        from cluster_forge.provisioner import PLAYBOOK_COMMANDS

        assert "setup-external" not in PLAYBOOK_COMMANDS
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest cli/tests/test_provisioner.py::TestPlaybookRename -v`
Expected: FAIL — `KeyError: 'setup-standalone'`

- [ ] **Step 3: `provisioner.py` を実装**

`cli/cluster_forge/provisioner.py` の `PLAYBOOK_COMMANDS` の 2 行目を置き換える。

```python
    "setup-standalone": "playbooks/setup_standalone.yaml",
```

- [ ] **Step 4: `servers.yaml` を書き換える**

ファイル全体を次で置き換える。

```yaml
---
environments:
  - dev
  - prod

servers:
  - name: br-gateway1
    type: gateway
  - name: br-db1
    type: standalone
    services: [postgresql, certbot]
  - name: br-storage1
    type: standalone
    services: [garage, caddy, certbot]
  - name: br-observability1
    type: standalone
    services: []
  - name: br-ai1
    type: standalone
    services: []
  - name: br-cluster1
    type: node
    k8s_role: primary
  - name: br-cluster2
    type: node
    k8s_role: worker
  - name: br-cluster3
    type: node
    k8s_role: worker
```

- [ ] **Step 5: `Makefile` の `PLAYBOOKS` を書き換える**

`setup-external` を `setup-standalone` に置き換える。

```makefile
PLAYBOOKS := setup-node setup-gateway setup-standalone setup-monitoring-agent bootstrap-cluster k3s-start k3s-stop k3s-reset setup-k3s-leader-restart shutdown-cluster
```

- [ ] **Step 6: テストが通ることを確認**

Run: `uv run pytest -v`
Expected: PASS (全件)

- [ ] **Step 7: `make prod/provision/setup-standalone` ターゲットが生えたことを確認**

Run: `make -n prod/provision/setup-standalone`
Expected: `uv run cluster-forge provision run --env prod setup-standalone` が出力される

Run: `make -n prod/provision/setup-external`
Expected: `make: *** No rule to make target` のエラー

## Task 6: PR 1 の仕上げとコミット

**Files:**
- 変更なし (検証とコミットのみ)

- [ ] **Step 1: lint を通す**

Run: `make format && make lint`
Expected: エラーなし

- [ ] **Step 2: テストを通す**

Run: `make test`
Expected: 全件 PASS

- [ ] **Step 3: yamllint を通す**

Run: `make yaml/lint`
Expected: エラーなし (`servers.yaml` の inline list `[postgresql, certbot]` が `.yamllint.yaml` の設定に引っかかる場合は block sequence に展開する)

- [ ] **Step 4: コミット**

```bash
git add servers.yaml Makefile cli/
git commit -m "$(cat <<'EOF'
refactor(cli): standalone 型 + services リストでサーバーを定義する

非 k3s ホストが 1 台から 4 台に増えるため、ServerType.EXTERNAL を汎用の
STANDALONE に置き換え、ServerDefinition に services リストを追加する。
services の値ごとに Ansible グループを生成することで、ホストにサービスを
足すときに servers.yaml の 1 行と role だけで済むようにする。

_build_domains() からホスト種別の分岐 (gateway なら dns/ntp、external なら
object_storage) を削除。サービスの DNS 名は role 名と一致しないため、
group_vars/all/network.yaml の service_records で明示的に持つ。

setup-external playbook は setup-standalone にリネーム。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PR 2: Ansible (inventory / roles / k3s)

## Task 7: `network.yaml` — サブネット・ドメイン・サービスレコード

**Files:**
- Modify: `provisioner/inventories/base/group_vars/all/network.yaml`

**Interfaces:**
- Produces: `cluster_env`, `host_domain`, `service_domain`, `service_records` の 4 変数。`cluster_domain` と `cluster_vips` は削除される。

- [ ] **Step 1: ファイルを書き換える**

`provisioner/inventories/base/group_vars/all/network.yaml` を次で置き換える。`cluster_vips` ブロックが既存ファイルにあれば削除する。

```yaml
---
##################################
# Cluster configuration
##################################
# 環境名。ドメインに 1 階層挟むことで dev / prod をゾーンごと分離する。
cluster_env: prod

# 物理サーバーのホスト名が属するゾーン。
host_domain: "{{ cluster_env }}.br-cluster.bright-room.net"

# サーバー上で動くアプリケーションが属するゾーン。
# 実体は service_records で host に紐づく A レコード。
service_domain: "{{ cluster_env }}.internal-service.bright-room.net"

##################################
# Network configuration
##################################
cluster_network:
  cidr: "172.22.52.0/24"
  subnet: "172.22.52.0"
  netmask: "255.255.255.0"
  prefix_length: 24

##################################
# DHCP configuration
##################################
# ホストは MAC reservation で固定するため、動的レンジは予備。
# cluster1-3 が .100-.102 を使うので .150 以降に置く。
dhcp:
  range_begin: "172.22.52.150"
  range_end: "172.22.52.190"
  default_lease_time: 86400
  max_lease_time: 604800

##################################
# DNS configuration
##################################
dns:
  forwarders:
    - "8.8.8.8"
    - "8.8.4.4"
  fallback_servers:
    - "8.8.8.8"
    - "8.8.4.4"

# サービス名 → それが乗っているホスト。CoreDNS がホストの IP を返す
# A レコードとして配信する。サービスを引っ越すときは host を書き換える。
service_records:
  - name: dns
    host: br-gateway1
  - name: ntp
    host: br-gateway1
  - name: object-storage
    host: br-storage1
  - name: rdbms
    host: br-db1
  - name: k8s-api
    host: br-cluster1

# Domains resolved to gateway1's wan_ip from WAN side.
# Traffic is DNAT-forwarded to the LAN target by nftables.
wan_exposed_domains:
  - "k8s-api.{{ service_domain }}"
```

- [ ] **Step 2: 旧変数の参照が残っていないか確認**

Run: `grep -rn 'cluster_domain\|cluster_vips' provisioner/ cli/`
Expected: `provisioner/roles/gateway/templates/Corefile.j2`、`provisioner/roles/k3s/` 配下、`provisioner/roles/external/templates/Caddyfile.j2` に残る。これらは Task 8 / 10 / 13 で消す。それ以外に出たら、その箇所も同じ方針で `host_domain` / `service_domain` に置き換える。

- [ ] **Step 3: yamllint を通す**

Run: `make yaml/lint`
Expected: エラーなし

## Task 8: `Corefile.j2` — 2 ゾーン構成と etcd 撤去

**Files:**
- Modify: `provisioner/roles/gateway/templates/Corefile.j2`

**Interfaces:**
- Consumes: Task 7 の `host_domain` / `service_domain` / `service_records`

- [ ] **Step 1: ファイルを書き換える**

`provisioner/roles/gateway/templates/Corefile.j2` を次で置き換える。

```
(common) {
    cache
    errors
    loop
    health :1053
}

{% set gw = cluster_hosts | selectattr('name', 'equalto', 'br-gateway1') | first %}
{{ host_domain }}:53 {
    bind {{ gw.ip }}
    hosts {
{% for host in cluster_hosts %}
        {{ host.ip }}     {{ host.domains.server }}
{% endfor %}
        fallthrough
    }
    import common
}

{{ service_domain }}:53 {
    bind {{ gw.ip }}
    hosts {
{% for record in service_records %}
        {{ (cluster_hosts | selectattr('name', 'equalto', record.host) | first).ip }}     {{ record.name }}.{{ service_domain }}
{% endfor %}
        fallthrough
    }
    import common
}

{{ service_domain }}:53 {
    bind {{ wan_ip }}
    hosts {
{% for domain in wan_exposed_domains %}
        {{ wan_ip }}     {{ domain }}
{% endfor %}
        fallthrough
    }
    import common
}

.:53 {
    bind {{ gw.ip }} {{ wan_ip }}
    forward . {% for fwd in dns.forwarders %}{{ fwd }}:53 {% endfor %}

    import common
}
```

変更点は 4 つ。

1. 権威ゾーンが `host_domain` と `service_domain` の 2 ブロックになった
2. ホストゾーンは `host.domains.values()` のループをやめ `host.domains.server` 1 つを直接展開する (Task 2 でキーが 1 つになったため)
3. `cluster_vips` のループを `service_records` に置き換え、ホスト名から IP を引く
4. `etcd` プラグインブロックを削除

WAN 側の bind ブロックは `wan_exposed_domains` が `k8s-api.{{ service_domain }}` だけなので `service_domain` ゾーンに置く。

- [ ] **Step 2: `resolv.conf` の `search` を修正**

`provisioner/roles/gateway/tasks/dns.yaml` の最終タスク `Write static resolv.conf pointing to local CoreDNS` の `content` を書き換える。

```yaml
    content: |
      # Managed by br-cluster-provisioner (gateway role)
      nameserver {{ (cluster_hosts | selectattr('name', 'equalto', 'br-gateway1') | first).ip }}
      search {{ host_domain }} {{ service_domain }}
```

- [ ] **Step 3: etcd のインストールタスクを削除**

`provisioner/roles/gateway/tasks/dns.yaml` から、`# --- etcd ---` コメントから `Enable and start etcd` タスクまで (`Create etcd data directory` / `Download etcd archive` / `Extract etcd` / `Copy etcd binaries` / `Create etcd systemd service file` / 直後の `Reload systemd daemon` / `Enable and start etcd` の 7 タスク) を削除する。`# --- CoreDNS ---` 以降はそのまま残す。

- [ ] **Step 4: etcd のテンプレートとバージョン定義を削除**

```bash
rm provisioner/roles/gateway/templates/etcd.service.j2
```

`provisioner/inventories/base/group_vars/all/versions.yaml` から `etcd` のエントリを、直上の `# renovate:` コメント行とセットで削除する。同ファイルの `node_exporter` と `alloy` もオブザーバビリティ撤去で不要になるので、同じくコメント行ごと削除する。`certbot` / `coredns` / `garage` / `k3s` / `kubectl` / `helm` は残す。

Run: `grep -n 'etcd\|node_exporter\|alloy\|certbot' provisioner/inventories/base/group_vars/all/versions.yaml`
Expected: `certbot` の 2 行のみ

- [ ] **Step 5: etcd の参照が残っていないか確認**

Run: `grep -rn 'etcd' provisioner/roles/gateway/ provisioner/inventories/`
Expected: 出力なし

- [ ] **Step 6: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし

## Task 9: `br-gateway1` の nftables と cloudflared 移設

**Files:**
- Modify: `provisioner/inventories/base/host_vars/br-gateway1.yaml`
- Create: `provisioner/roles/gateway/tasks/cloudflared.yaml`
- Create: `provisioner/roles/gateway/templates/cloudflared-config.yaml.j2`
- Modify: `provisioner/roles/gateway/tasks/main.yaml`
- Modify: `provisioner/roles/gateway/handlers/main.yaml`
- Modify: `provisioner/playbooks/setup_gateway.yaml`

**Interfaces:**
- Consumes: Task 7 の `cluster_network.cidr`

- [ ] **Step 1: cloudflared のタスクとテンプレートを gateway role に移す**

```bash
git mv provisioner/roles/external/tasks/cloudflared.yaml provisioner/roles/gateway/tasks/cloudflared.yaml
git mv provisioner/roles/external/templates/cloudflared-config.yaml.j2 provisioner/roles/gateway/templates/cloudflared-config.yaml.j2
```

移した `cloudflared.yaml` の中身は変更しない (テンプレートは tunnel_id しか参照していないためホストに依存しない)。移動後に `grep -n 'br-external1\|external' provisioner/roles/gateway/tasks/cloudflared.yaml` を実行し、ヒットしたら該当箇所を `br-gateway1` に読み替える。

- [ ] **Step 2: `roles/gateway/tasks/main.yaml` に登録し、ハンドラを移す**

`provisioner/roles/gateway/tasks/main.yaml` の末尾に追記する。

```yaml
- name: Cloudflared (br-infra Tunnel) configuration
  include_tasks: cloudflared.yaml
  tags: [cloudflared]
```

`cloudflared.yaml` は `Restart cloudflared` ハンドラを notify する。ハンドラは今 `roles/external/handlers/main.yaml` にあるので、同じタスクの中で `roles/gateway/handlers/main.yaml` に移さないと、gateway role が存在しないハンドラを notify する状態になり Step 5 の ansible-lint が落ちる。

`provisioner/roles/gateway/handlers/main.yaml` に追記する (ファイルが無ければ `---` から作る)。

```yaml
- name: Restart cloudflared
  service:
    name: cloudflared
    state: restarted
```

- [ ] **Step 3: `setup_gateway.yaml` の `required_secrets` に `cloudflared_br_infra` を追加**

`provisioner/playbooks/setup_gateway.yaml` の `secrets` role の `required_secrets` リストに `- cloudflared_br_infra` を追加する。

- [ ] **Step 4: nftables のルールを更新**

`provisioner/inventories/base/host_vars/br-gateway1.yaml` を 4 箇所変更する。IP は `cluster_network` から組み立てられているので、サブネット変更に伴う書き換えは不要 (`lan_network` は自動で追従する)。

**(a) FORWARD の k8s API 宛先** — `250 k8s api from wan (home lan only):` の行から `cluster_vips` の参照を消す。

```yaml
  250 k8s api from wan (home lan only):
    - iifname $wan_interface ip saddr $home_lan_network oifname $lan_interface ip daddr {{ (cluster_hosts | selectattr('name', 'equalto', 'br-cluster1') | first).ip }} tcp dport 6443 ct state new accept
```

**(b) NAT prerouting の DNAT 先** — `020 dnat k8s api:` も同様。

```yaml
  020 dnat k8s api:
    - iifname $wan_interface tcp dport 6443 dnat ip to {{ (cluster_hosts | selectattr('name', 'equalto', 'br-cluster1') | first).ip }}:6443
```

**(c) LAN からの INPUT 許可ポート** — `nft_define` の `input tcp accepted from lan` を置き換える。node-exporter (9100 / 9101) と Alloy (12345) はオブザーバビリティ撤去で、2379 は etcd 撤去で不要になる。

```yaml
  input tcp accepted from lan:
    name: in_tcp_accept
    value: "{ ssh, domain }"
```

**(d) Pod CIDR からの INPUT 許可ポート** — `input tcp accepted from k8s pods` から 2379 を削除する。CoreDNS への 53 だけ残す。

```yaml
  input tcp accepted from k8s pods:
    name: in_pod_tcp_accept
    value: "{ domain }"
```

Run: `grep -n 'cluster_vips\|2379\|9100\|9101\|12345' provisioner/inventories/base/host_vars/br-gateway1.yaml`
Expected: 出力なし

- [ ] **Step 5: ansible-lint / yamllint を通す**

Run: `make ansible/lint && make yaml/lint`
Expected: エラーなし

## Task 10: `roles/external` を `garage` / `caddy` / `certbot` に分解

**Files:**
- Create: `provisioner/roles/garage/` (tasks / templates / handlers / defaults)
- Create: `provisioner/roles/caddy/` (tasks / templates / handlers)
- Create: `provisioner/roles/certbot/` (tasks / defaults)
- Delete: `provisioner/roles/external/`

**Interfaces:**
- Consumes: Task 7 の `service_domain`
- Produces: `garage` / `caddy` / `certbot` の 3 role。それぞれ `roles/<name>/tasks/main.yaml` を入口に持つ。

- [ ] **Step 1: `certbot` role を切り出す**

現状、certbot の**インストール**は `roles/external/tasks/certbot.yaml` にあるが、**証明書の発行** (`certbot certonly`) は `roles/external/tasks/garage.yaml` の中にあり、対象ドメインが `external_host.domains.object_storage` にハードコードされている。db1 でも証明書が必要になるので、発行を certbot role 側に移して `certbot_domains` で駆動する形にする。

```bash
mkdir -p provisioner/roles/certbot/{tasks,defaults}
git mv provisioner/roles/external/tasks/certbot.yaml provisioner/roles/certbot/tasks/main.yaml
```

`provisioner/roles/certbot/defaults/main.yaml` を新規作成する。

```yaml
---
certbot_packages:
  - python3-pip
  - python3-venv

# 証明書を発行するドメイン。host_vars で上書きする。
certbot_domains: []
```

`provisioner/roles/certbot/tasks/main.yaml` の先頭に、`roles/external/tasks/pre_configuration.yaml` にあったパッケージインストールを移す (`external_packages` → `certbot_packages`)。

```yaml
---
- name: Install certbot prerequisite packages
  apt:
    name: "{{ certbot_packages }}"
    state: present
```

同ファイルの末尾 (`Add SSL certificate update cron` の後) に、`garage.yaml` から移した発行タスクを `certbot_domains` のループとして追加する。

```yaml
- name: Create SSL certificate
  command: |
    {{ lets_encrypt.virtual_environment }}/bin/certbot certonly \
      --dns-cloudflare \
      --dns-cloudflare-credentials {{ lets_encrypt.virtual_environment }}/.secrets/cloudflare_credentials.ini \
      --dns-cloudflare-propagation-seconds 60 \
      --server https://acme-v02.api.letsencrypt.org/directory \
      --agree-tos \
      --non-interactive \
      --rsa-key-size 4096 \
      --email {{ secrets_admin_email }} \
      --domain {{ item }}
  loop: "{{ certbot_domains }}"
  register: certbot_create
  changed_when:
    - certbot_create.rc == 0
    - '"Certificate not yet due for renewal; no action taken." not in certbot_create.stdout'
```

`roles/external/tasks/pre_configuration.yaml` は他に内容が無いので、Step 4 の `roles/external` 削除で一緒に消える。

- [ ] **Step 2: `garage` role を切り出す**

```bash
mkdir -p provisioner/roles/garage/{tasks,templates,handlers,defaults}
git mv provisioner/roles/external/tasks/garage.yaml provisioner/roles/garage/tasks/install.yaml
git mv provisioner/roles/external/tasks/garage_keys.yaml provisioner/roles/garage/tasks/keys.yaml
git mv provisioner/roles/external/tasks/garage_buckets.yaml provisioner/roles/garage/tasks/buckets.yaml
git mv provisioner/roles/external/templates/garage.service.j2 provisioner/roles/garage/templates/
git mv provisioner/roles/external/templates/garage.toml.j2 provisioner/roles/garage/templates/
git mv provisioner/roles/external/templates/garage-ssl-update.sh.j2 provisioner/roles/garage/templates/
```

`provisioner/roles/garage/tasks/main.yaml` を新規作成する。

```yaml
---
- name: Install Garage
  include_tasks: install.yaml
  tags: [garage]

- name: Create Garage buckets
  include_tasks: buckets.yaml
  tags: [garage]

- name: Create Garage access keys
  include_tasks: keys.yaml
  tags: [garage]
```

`provisioner/roles/garage/tasks/install.yaml` (旧 `garage.yaml`) から `Create SSL certificate` タスクを削除する (Step 1 で certbot role に移した)。残る証明書関連の 2 タスクは、`external_host.domains.object_storage` という消えたキーを参照しているので、`service_domain` から組み立てる形に書き換える。`defaults` に変数を置く。

`provisioner/roles/garage/defaults/main.yaml`:

```yaml
---
garage_tls_domain: "object-storage.{{ service_domain }}"
```

`install.yaml` の 2 タスクを次に置き換える。

```yaml
- name: Copy SSL certificates for Garage
  copy:
    src: "{{ lets_encrypt.certificate_dir }}/{{ garage_tls_domain }}/{{ item.src }}"
    dest: "{{ garage.ssl_dir }}/{{ item.dest }}"
    group: "{{ garage.operator.group }}"
    owner: "{{ garage.operator.user }}"
    mode: "0640"
    remote_src: true
  loop:
    - { src: "fullchain.pem", dest: "public.crt" }
    - { src: "privkey.pem", dest: "private.key" }

- name: Add certificate update script
  template:
    src: garage-ssl-update.sh.j2
    dest: "{{ lets_encrypt.deploy_hook_dir }}/garage-ssl-update.sh"
    owner: "root"
    group: "root"
    mode: "0700"
    backup: true
```

`provisioner/roles/garage/templates/garage-ssl-update.sh.j2` の `ext.domains.object_storage` を `garage_tls_domain` に置き換える。

```bash
cp {{ lets_encrypt.certificate_dir }}/{{ garage_tls_domain }}/fullchain.pem {{ garage.ssl_dir }}/public.crt
cp {{ lets_encrypt.certificate_dir }}/{{ garage_tls_domain }}/privkey.pem {{ garage.ssl_dir }}/private.key
```

ハンドラを振り分ける。`provisioner/roles/garage/handlers/main.yaml`:

```yaml
---
- name: Restart garage
  service:
    name: garage
    state: restarted
```

`provisioner/roles/caddy/handlers/main.yaml`:

```yaml
---
# Caddyfile で admin API を無効化 (admin off) しているため systemctl reload が
# admin API 経由の CLI 呼び出しで stuck する。restart で置き換える。
- name: Reload caddy
  service:
    name: caddy
    state: restarted

- name: Restart caddy
  service:
    name: caddy
    state: restarted
```

`Restart cloudflared` ハンドラは Task 9 Step 2 で `roles/gateway/handlers/main.yaml` に移してある。ここでは扱わない。

- [ ] **Step 3: `caddy` role を切り出す**

```bash
mkdir -p provisioner/roles/caddy/{tasks,templates,handlers}
git mv provisioner/roles/external/tasks/caddy.yaml provisioner/roles/caddy/tasks/main.yaml
git mv provisioner/roles/external/templates/Caddyfile.j2 provisioner/roles/caddy/templates/
git mv provisioner/roles/external/templates/caddy-override.conf.j2 provisioner/roles/caddy/templates/
```

`provisioner/roles/caddy/templates/Caddyfile.j2` を次で置き換える。`br-external1` の参照と `ext.domains.object_storage` (Task 2 で消えたキー) を、`service_domain` から組み立てる形にする。

```
{% set host = cluster_hosts | selectattr('name', 'equalto', 'br-storage1') | first %}
{% set object_storage_domain = 'object-storage.' ~ service_domain %}
{
    auto_https off
    admin off
}

{{ object_storage_domain }}:{{ garage.server.s3_port }} {
    bind {{ host.ip }}
    tls {{ garage.ssl_dir }}/public.crt {{ garage.ssl_dir }}/private.key
    reverse_proxy 127.0.0.1:{{ garage.server.s3_port }}
}

{{ object_storage_domain }}:443 {
    bind {{ host.ip }}
    tls {{ garage.ssl_dir }}/public.crt {{ garage.ssl_dir }}/private.key
    reverse_proxy 127.0.0.1:{{ garage.server.web_port }}
}
```

- [ ] **Step 4: `roles/external` を削除**

```bash
git rm -r provisioner/roles/external
```

この時点で `roles/external` に残っているのは `defaults/main.yaml` (`external_packages`)、`handlers/main.yaml`、`tasks/main.yaml`、`tasks/pre_configuration.yaml` の 4 つで、中身はすべて Step 1–3 で移し終えている。

- [ ] **Step 5: 残った参照を確認**

Run: `grep -rn 'roles/external\|role: external\|external_packages\|br-external1' provisioner/ cli/ Makefile`
Expected: 出力なし

- [ ] **Step 6: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし (Task 12 で playbook を作るまで role は誰からも呼ばれないが、lint は playbook しか見ないので通る)

## Task 11: `postgresql` role の新規作成

**Files:**
- Create: `provisioner/roles/postgresql/tasks/main.yaml`
- Create: `provisioner/roles/postgresql/defaults/main.yaml`
- Create: `provisioner/roles/postgresql/templates/pg_hba.conf.j2`, `provisioner/roles/postgresql/templates/postgresql-ssl-reload.sh.j2`
- Create: `provisioner/roles/postgresql/handlers/main.yaml`
- Create: `provisioner/roles/secrets/tasks/fetch_postgresql.yaml`
- Create: `provisioner/inventories/base/group_vars/postgresql.yaml`
- Modify: `provisioner/roles/secrets/tasks/main.yaml`

**Interfaces:**
- Consumes: Task 7 の `service_domain` / `cluster_network.cidr`、Task 10 の `certbot` role が発行した証明書
- Produces: `br-db1` 上で PostgreSQL 16 が `172.22.52.0/24` からの `hostssl` 接続を受け、`zitadel` / `argo_workflows` の DB とロールが存在する状態

- [ ] **Step 1: `defaults/main.yaml` を作る**

```yaml
---
postgresql_version: 16
postgresql_data_dir: /var/lib/postgresql/{{ postgresql_version }}/main
postgresql_conf_dir: /etc/postgresql/{{ postgresql_version }}/main
postgresql_port: 5432

# TLS 証明書は certbot role が発行したものを使う。
postgresql_tls_domain: "rdbms.{{ service_domain }}"

# 接続を許可する CIDR。Cilium が Pod の外向き通信をノード IP に masquerade
# するため、k3s から見た接続元は Pod CIDR ではなくノード IP になる。
postgresql_allowed_cidr: "{{ cluster_network.cidr }}"

postgresql_databases:
  - name: zitadel
    owner: zitadel
  - name: argo_workflows
    owner: argo_workflows
```

- [ ] **Step 2: `templates/pg_hba.conf.j2` を作る**

```
# Managed by br-cluster-provisioner (postgresql role). Do not edit manually.
# TYPE   DATABASE  USER  ADDRESS                             METHOD
local    all       all                                       peer
host     all       all   127.0.0.1/32                        scram-sha-256
host     all       all   ::1/128                             scram-sha-256
hostssl  all       all   {{ postgresql_allowed_cidr }}       scram-sha-256
```

- [ ] **Step 3: `handlers/main.yaml` を作る**

```yaml
---
- name: Restart postgresql
  systemd:
    name: "postgresql@{{ postgresql_version }}-main"
    state: restarted
```

- [ ] **Step 4: `tasks/main.yaml` を作る**

```yaml
---
- name: Install PostgreSQL
  apt:
    name:
      - "postgresql-{{ postgresql_version }}"
      - python3-psycopg2
    state: present
    update_cache: true

- name: Configure listen_addresses
  lineinfile:
    path: "{{ postgresql_conf_dir }}/postgresql.conf"
    regexp: '^#?listen_addresses'
    line: "listen_addresses = '*'"
    owner: postgres
    group: postgres
    mode: "0644"
  notify: Restart postgresql

- name: Configure TLS certificate paths
  lineinfile:
    path: "{{ postgresql_conf_dir }}/postgresql.conf"
    regexp: "^#?\\s*{{ item.key }}\\s*="
    line: "{{ item.key }} = '{{ item.value }}'"
    owner: postgres
    group: postgres
    mode: "0644"
  loop:
    - key: ssl
      value: "on"
    - key: ssl_cert_file
      value: "{{ lets_encrypt.certificate_dir }}/{{ postgresql_tls_domain }}/fullchain.pem"
    - key: ssl_key_file
      value: "{{ lets_encrypt.certificate_dir }}/{{ postgresql_tls_domain }}/privkey.pem"
  notify: Restart postgresql

- name: Configure pg_hba.conf
  template:
    src: pg_hba.conf.j2
    dest: "{{ postgresql_conf_dir }}/pg_hba.conf"
    owner: postgres
    group: postgres
    mode: "0640"
  notify: Restart postgresql

- name: Flush handlers so the server is up with TLS before creating roles
  meta: flush_handlers

- name: Ensure PostgreSQL is running
  systemd:
    name: "postgresql@{{ postgresql_version }}-main"
    state: started
    enabled: true
  when: not ansible_check_mode

- name: Create database roles
  become_user: postgres
  community.postgresql.postgresql_user:
    name: "{{ item.owner }}"
    password: "{{ secrets_postgresql[item.owner ~ '_password'] }}"
    state: present
  loop: "{{ postgresql_databases }}"
  no_log: true
  when: not ansible_check_mode

- name: Create databases
  become_user: postgres
  community.postgresql.postgresql_db:
    name: "{{ item.name }}"
    owner: "{{ item.owner }}"
    encoding: UTF-8
    state: present
  loop: "{{ postgresql_databases }}"
  when: not ansible_check_mode
```

`secrets_postgresql` は次の Step で `secrets` role に追加する fetch タスクが用意する dict。

- [ ] **Step 5: `secrets` role に `postgresql` の取得タスクを追加**

`required_secrets` の各エントリは `roles/secrets/tasks/fetch_<name>.yaml` に 1 対 1 で対応する。`postgresql` を足すには dispatch 行と fetch タスクの両方が要る。

`provisioner/roles/secrets/tasks/main.yaml` の末尾に追記する。

```yaml
- name: Fetch PostgreSQL role passwords
  include_tasks: fetch_postgresql.yaml
  when: "'postgresql' in required_secrets"
```

`provisioner/roles/secrets/tasks/fetch_postgresql.yaml` を新規作成する。`fetch_garage_operators.yaml` と同じ形 (1 アイテムの複数フィールドを dict にまとめる) にする。

```yaml
---
- name: Fetch PostgreSQL role passwords
  run_once: true
  when:
    - secrets_postgresql is not defined
    - postgresql_databases is defined
  become: false
  block:
    - name: Get PostgreSQL role passwords from 1Password
      # field label は <role>_password 形式。external-secrets の
      # onepassword-connect provider が label のフラット検索しか
      # サポートしないため、section は使わず item 全体で unique にする。
      onepassword.connect.field_info:
        vault: "{{ vault_id }}"
        item: "postgresql"
        field: "{{ item.owner }}_password"
      register: _secrets_postgresql_raw
      loop: "{{ postgresql_databases }}"
      no_log: true
      delegate_to: localhost

    - name: Cache PostgreSQL role passwords
      set_fact:
        secrets_postgresql: >-
          {%- set ns = namespace(d={}) -%}
          {%- for db in postgresql_databases -%}
            {%- set ns.d = ns.d | combine({
              db.owner ~ '_password':
                _secrets_postgresql_raw.results[loop.index0].field.value
            }) -%}
          {%- endfor -%}
          {{ ns.d }}
        cacheable: true
      no_log: true
```

`postgresql_databases` は `roles/postgresql/defaults/main.yaml` の変数なので、`secrets` role より先に評価される必要がある。Task 12 の playbook では `secrets` role を `postgresql` role より前に置いているが、`defaults` は play の開始時点で読まれないため、`postgresql_databases` を `provisioner/inventories/base/group_vars/postgresql.yaml` (Task 3 で生成される `postgresql` グループの group_vars) に移す。

`provisioner/inventories/base/group_vars/postgresql.yaml` を新規作成する。

```yaml
---
postgresql_databases:
  - name: zitadel
    owner: zitadel
  - name: argo_workflows
    owner: argo_workflows
```

`roles/postgresql/defaults/main.yaml` からは `postgresql_databases` のブロックを削除する。

- [ ] **Step 6: 証明書を PostgreSQL が読める場所にコピーし、更新 hook を追加**

**certbot の `/etc/letsencrypt/live` と `/etc/letsencrypt/archive` は root 所有の 0700 で、`postgres` ユーザーはディレクトリを辿ることすらできない。** PostgreSQL に `/etc/letsencrypt/live/...` を直接指させると、起動時に `could not load private key file ... Permission denied` で落ちる。Garage と同じく、コピーして所有者を移す。

`provisioner/roles/postgresql/defaults/main.yaml` に追記する。

```yaml
postgresql_ssl_dir: /etc/postgresql/ssl
```

`tasks/main.yaml` の TLS 設定タスクより **前** に、コピーを置く。

```yaml
- name: Create PostgreSQL SSL directory
  file:
    path: "{{ postgresql_ssl_dir }}"
    owner: postgres
    group: postgres
    mode: "0700"
    state: directory

- name: Copy SSL certificates for PostgreSQL
  copy:
    src: "{{ lets_encrypt.certificate_dir }}/{{ postgresql_tls_domain }}/{{ item.src }}"
    dest: "{{ postgresql_ssl_dir }}/{{ item.dest }}"
    owner: postgres
    group: postgres
    mode: "0600"
    remote_src: true
  loop:
    - { src: "fullchain.pem", dest: "server.crt" }
    - { src: "privkey.pem", dest: "server.key" }
  notify: Restart postgresql
```

Step 4 の `Configure TLS certificate paths` の `ssl_cert_file` / `ssl_key_file` は、この配置先を指す。

```yaml
    - key: ssl_cert_file
      value: "{{ postgresql_ssl_dir }}/server.crt"
    - key: ssl_key_file
      value: "{{ postgresql_ssl_dir }}/server.key"
```

`provisioner/roles/postgresql/templates/postgresql-ssl-reload.sh.j2` を作る。certbot が更新したら再コピーしてから reload する。

```bash
#!/bin/sh
# Managed by br-cluster-provisioner (postgresql role). Do not edit manually.
set -eu
install -o postgres -g postgres -m 0600 \
  {{ lets_encrypt.certificate_dir }}/{{ postgresql_tls_domain }}/fullchain.pem \
  {{ postgresql_ssl_dir }}/server.crt
install -o postgres -g postgres -m 0600 \
  {{ lets_encrypt.certificate_dir }}/{{ postgresql_tls_domain }}/privkey.pem \
  {{ postgresql_ssl_dir }}/server.key
systemctl reload postgresql@{{ postgresql_version }}-main
```

`tasks/main.yaml` の末尾に追記する。

```yaml
- name: Add certificate renewal reload hook
  template:
    src: postgresql-ssl-reload.sh.j2
    dest: "{{ lets_encrypt.deploy_hook_dir }}/postgresql-ssl-reload.sh"
    owner: root
    group: root
    mode: "0700"
```

- [ ] **Step 7: `community.postgresql` コレクションを requirements に追加**

`provisioner/requirements.yaml` の `collections:` に追記する。

```yaml
  - name: community.postgresql
```

- [ ] **Step 8: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし

## Task 12: `setup_standalone.yaml` playbook

**Files:**
- Create: `provisioner/playbooks/setup_standalone.yaml`
- Delete: `provisioner/playbooks/setup_external.yaml`

**Interfaces:**
- Consumes: Task 3 の `standalone` グループと service グループ、Task 10 / 11 の各 role

- [ ] **Step 1: playbook を作る**

`provisioner/playbooks/setup_standalone.yaml` を新規作成する。1 つの play で全 standalone ホストに共通処理を流し、サービスごとの play で該当グループにだけ role を当てる。

```yaml
---
- name: Configure standalone servers (common)
  hosts: standalone
  gather_facts: true
  become: true

  collections:
    - onepassword.connect

  environment:
    OP_CONNECT_TOKEN: "{{ lookup('env', 'OP_CONNECT_TOKEN') }}"
    OP_CONNECT_HOST: "{{ lookup('env', 'OP_CONNECT_HOST') }}"

  pre_tasks:
    # init_disk で root を os_partition_size まで拡張してから apt upgrade を走らせないと
    # dist-upgrade 中にディスクフルで失敗する (3.4G では入らない)
    - name: Setup initialize disk
      include_tasks: ../tasks/init_disk.yaml
      tags: [system]

    - name: Package update
      include_tasks: ../tasks/update.yaml
      tags: [always]

  roles:
    - name: Fetch required secrets
      role: secrets
      vars:
        required_secrets:
          - discord_webhook
      tags: [always]

    - name: Common system configuration
      role: common
      tags: [system]

- name: Configure certbot hosts
  hosts: certbot
  gather_facts: true
  become: true

  collections:
    - onepassword.connect

  environment:
    OP_CONNECT_TOKEN: "{{ lookup('env', 'OP_CONNECT_TOKEN') }}"
    OP_CONNECT_HOST: "{{ lookup('env', 'OP_CONNECT_HOST') }}"

  roles:
    - name: Fetch required secrets
      role: secrets
      vars:
        required_secrets:
          - cloudflare_token
          - admin_email
      tags: [always]

    - name: Certbot (Let's Encrypt DNS-01)
      role: certbot
      tags: [certbot]

- name: Configure PostgreSQL hosts
  hosts: postgresql
  gather_facts: true
  become: true

  collections:
    - onepassword.connect

  environment:
    OP_CONNECT_TOKEN: "{{ lookup('env', 'OP_CONNECT_TOKEN') }}"
    OP_CONNECT_HOST: "{{ lookup('env', 'OP_CONNECT_HOST') }}"

  roles:
    - name: Fetch required secrets
      role: secrets
      vars:
        required_secrets:
          - postgresql
      tags: [always]

    - name: PostgreSQL server
      role: postgresql
      tags: [postgresql]

- name: Configure Garage hosts
  hosts: garage
  gather_facts: true
  become: true

  collections:
    - onepassword.connect

  environment:
    OP_CONNECT_TOKEN: "{{ lookup('env', 'OP_CONNECT_TOKEN') }}"
    OP_CONNECT_HOST: "{{ lookup('env', 'OP_CONNECT_HOST') }}"

  roles:
    - name: Fetch required secrets
      role: secrets
      vars:
        required_secrets:
          - garage_rpc_secret
          - garage_operators
      tags: [always]

    - name: Garage S3
      role: garage
      tags: [garage]

- name: Configure Caddy hosts
  hosts: caddy
  gather_facts: true
  become: true

  roles:
    - name: Caddy reverse proxy
      role: caddy
      tags: [caddy]
```

certbot が Garage / Caddy より先に走るのは、Caddy が証明書ファイルを参照するため。`hosts: caddy` の play が最後なのは同じ理由。

- [ ] **Step 2: 旧 playbook を削除**

```bash
git rm provisioner/playbooks/setup_external.yaml
```

- [ ] **Step 3: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし

## Task 13: k3s を SQLite シングル control-plane に

**Files:**
- Modify: `provisioner/roles/k3s/templates/config.yaml.master.j2`
- Modify: `provisioner/roles/k3s/tasks/configure.yaml:2-8`

**Interfaces:**
- Consumes: Task 7 の `service_domain`
- Produces: `primary_control_node_ip` を `primary` グループから導出する

- [ ] **Step 1: `configure.yaml` のハードコードを直す**

`provisioner/roles/k3s/tasks/configure.yaml` の冒頭 2 タスクを置き換える。`br-node1` の文字列固定をやめ、`primary` グループの唯一のホストから引く。

```yaml
---
- name: Determine primary control node
  set_fact:
    is_primary_control_node: "{{ 'primary' in group_names }}"

- name: Set primary control node IP
  set_fact:
    primary_control_node_ip: "{{ (cluster_hosts | selectattr('name', 'equalto', groups['primary'] | first) | first).ip }}"
```

`groups['primary']` は inventory 全体の辞書なので、`hosts.yaml` で `clusters.children.master.children.primary` という入れ子になっていても Ansible が平坦化してくれる。worker 側でもこの fact が引ける (`server:` の生成に必要)。`groups['master']` に書き換えないこと — `master` には secondary も含まれる。

- [ ] **Step 2: `config.yaml.master.j2` を書き換える**

ファイル全体を次で置き換える。`cluster-init` (embedded etcd) と `etcd-expose-metrics` を削除し、`tls-san` から VIP を外す。

```
{% if not is_primary_control_node | bool %}
server: https://{{ primary_control_node_ip }}:{{ k3s.cluster.port }}
{% endif %}
token-file: {{ k3s.config_dir }}/cluster-token
node-name: {{ inventory_hostname }}

flannel-backend: none
disable-network-policy: true
disable-helm-controller: true
disable-kube-proxy: true
disable:
  - coredns
  - local-storage
  - servicelb
  - traefik
  - metrics-server
kubelet-arg:
  - config={{ k3s.config_dir }}/kubelet.config
kube-controller-manager-arg:
  - bind-address=0.0.0.0
  - terminated-pod-gc-threshold=10
kube-scheduler-arg:
  - bind-address=0.0.0.0
node-taint:
  - node-role.kubernetes.io/control-plane:NoSchedule
tls-san:
  - k8s-api.{{ service_domain }}
  - {{ primary_control_node_ip }}
write-kubeconfig-mode: 644
embedded-registry: true
```

`cluster-init: true` を消すと k3s は datastore として SQLite を使う。`{% if %}` を反転させて、primary では何も出力せず secondary (現状ゼロ台) にだけ `server:` を出す形にした。

- [ ] **Step 3: `k3s_api_vip_address` / `k3s_api_vip_domain` の参照を消す**

Run: `grep -rn 'k3s_api_vip' provisioner/`
Expected: `group_vars` のどこかに定義があるはず。定義と、Step 2 以外の参照箇所をすべて削除する。

Run: `grep -rn 'k3s_api_vip' provisioner/`
Expected: 削除後は出力なし

- [ ] **Step 4: `post_setup.yaml` の kubeconfig を確認**

`provisioner/roles/k3s/tasks/post_setup.yaml` の `Build kubeconfig with primary + wan fallback contexts` が VIP を参照していれば `primary_control_node_ip` に置き換える。

Run: `grep -n 'vip\|172.22.10' provisioner/roles/k3s/tasks/post_setup.yaml`
Expected: 出力なし

- [ ] **Step 5: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし

## Task 14: `setup_node.yaml` から kube-vip のブートストラップを外す

**Files:**
- Modify: `provisioner/playbooks/setup_node.yaml:38-70`

- [ ] **Step 1: kube-vip の先入れを削除**

`provisioner/playbooks/setup_node.yaml` の Play 2 (`hosts: primary`、`Bootstrap CNI, CoreDNS, and kube-vip on primary master`) から kube-vip に関するタスクとファイルコピーの要素を削除する。Play 名も実態に合わせる。

```yaml
# Play 2: Bootstrap CNI and CoreDNS on the control-plane node
- name: Bootstrap CNI and CoreDNS on primary master
  hosts: primary
```

`Copy bootstrap manifests to primary master` のタスクで kube-vip のマニフェスト / values をコピーしている行、および `helm install kube-vip` 相当のタスクを削除する。Cilium と CoreDNS の 2 つだけを残す。

Run: `grep -n 'kube-vip\|kube_vip' provisioner/playbooks/setup_node.yaml`
Expected: 出力なし

- [ ] **Step 2: 他に kube-vip の参照が無いか確認**

Run: `grep -rn 'kube-vip\|kube_vip' provisioner/`
Expected: 出力なし

- [ ] **Step 3: ansible-lint を通す**

Run: `make ansible/lint`
Expected: エラーなし

## Task 15: `host_vars` の付け替え

**Files:**
- Delete: `provisioner/inventories/base/host_vars/br-external1.yaml`, `br-node4.yaml`, `br-node5.yaml`, `br-node6.yaml`
- Create: `provisioner/inventories/base/host_vars/br-db1.yaml`, `br-storage1.yaml`, `br-observability1.yaml`, `br-ai1.yaml`
- Modify: `provisioner/inventories/base/group_vars/all/main.yaml`
- Delete: `provisioner/playbooks/setup_monitoring_agent.yaml`, `provisioner/playbooks/setup_k3s_leader_restart.yaml`, `provisioner/roles/k3s_leader_restart/`

**Interfaces:**
- Consumes: Task 7 の `service_domain`

- [ ] **Step 1: `br-storage1.yaml` を作る**

旧 `br-external1.yaml` の Garage 設定を引き継ぐ。バケットは `argo-workflows` だけにする (loki / tempo は撤去)。

```yaml
---
##################################
# Storage configuration
# OS ディスクの残り領域を Garage (S3) のデータ置き場として使う
##################################
storage_mode: ext4
mount_point: /storage

##################################
# Let's Encrypt configuration
##################################
lets_encrypt:
  virtual_environment: /lets_encrypt
  certificate_dir: /etc/letsencrypt/live
  deploy_hook_dir: /etc/letsencrypt/renewal-hooks/deploy

certbot_domains:
  - "object-storage.{{ service_domain }}"

##################################
# Garage S3 configuration
##################################
garage:
  data_dir: /storage/garage/data
  meta_dir: /storage/garage/meta
  config_dir: /etc/garage
  ssl_dir: /etc/garage/ssl
  capacity: 1T
  server:
    s3_port: 3900
    rpc_port: 3901
    web_port: 3902
    admin_port: 3903
  operator:
    user: garage
    group: garage
  provisioning:
    region: ap-northeast-1
    buckets:
      - name: argo-workflows
    users:
      # 1Password garage item の field label は <name>_access_key_id 形式。
      # S3 bucket は伝統的に hyphen で命名するので user.name とは分離する。
      - name: argo_workflows
        bucket_acl:
          bucket_name: argo-workflows
```

- [ ] **Step 2: `br-db1.yaml` を作る**

```yaml
---
##################################
# Storage configuration
# OS ディスクの残り領域を PostgreSQL のデータ置き場として使う
##################################
storage_mode: ext4
mount_point: /var/lib/postgresql

##################################
# Let's Encrypt configuration
##################################
lets_encrypt:
  virtual_environment: /lets_encrypt
  certificate_dir: /etc/letsencrypt/live
  deploy_hook_dir: /etc/letsencrypt/renewal-hooks/deploy

certbot_domains:
  - "rdbms.{{ service_domain }}"
```

- [ ] **Step 3: `br-observability1.yaml` と `br-ai1.yaml` を作る**

両方とも同じ内容。当面用途は無いが、パーティションは後から切り直せないので今のうちに切る。

```yaml
---
##################################
# Storage configuration
# 現時点で用途は未定。パーティションの切り直しには再フラッシュが必要なため、
# 先に ext4 データ領域だけ確保しておく。
##################################
storage_mode: ext4
mount_point: /storage
```

- [ ] **Step 4: 旧 host_vars を削除**

```bash
git rm provisioner/inventories/base/host_vars/br-external1.yaml \
       provisioner/inventories/base/host_vars/br-node4.yaml \
       provisioner/inventories/base/host_vars/br-node5.yaml \
       provisioner/inventories/base/host_vars/br-node6.yaml
```

`br-gateway1.yaml` は Task 9 で更新済みなので残す。`br-node1` / `br-node2` / `br-node3` の host_vars は元から存在しない (`storage_mode: none` がデフォルト)。`br-cluster1-3` も `none` なので host_vars を作らない。

- [ ] **Step 5: `group_vars/all/main.yaml` を更新**

`storage_mode` のコメントと `alloy_journal_enabled` を書き換える。

```yaml
##################################
# Storage configuration
# storage_mode:
#   none - OS ディスク全体を root に使う (gateway1, cluster1-3)
#   ext4 - OS 用 + データ用パーティションに分割 (db1, storage1, observability1, ai1)
##################################
storage_mode: none
os_partition_size_mib: 65536  # 64 GiB
mount_opts: "defaults,noatime,discard,nofail"
```

`alloy_journal_enabled: true` とその上のコメントブロックを削除する (オブザーバビリティ全撤去のため journald の送り先が無くなる)。

- [ ] **Step 6: 不要になった playbook を削除**

オブザーバビリティ全撤去で監視エージェントが、control-plane 1 台化で leader-restart が、それぞれ不要になる。`setup_k3s_leader_restart.yaml` は `hosts: master` で leader 選出のある etcd 構成を前提にしたタイマーで、CP が 1 台なら「リーダーを避けて再起動する」対象が存在しない。

```bash
git rm provisioner/playbooks/setup_monitoring_agent.yaml \
       provisioner/playbooks/setup_k3s_leader_restart.yaml
git rm -r provisioner/roles/k3s_leader_restart
```

`cli/cluster_forge/provisioner.py` の `PLAYBOOK_COMMANDS` から `"setup-monitoring-agent"` と `"setup-k3s-leader-restart"` の行を削除し、`Makefile` の `PLAYBOOKS` からも両方を削除する。Alloy / node_exporter をインストールしている role があれば併せて削除する。

`docs/incidents/2026-04-29-leader-restart-silent-failure.md` は過去の記録なので残す。

Run: `grep -rn 'alloy\|node_exporter\|monitoring.agent\|monitoring_agent\|leader_restart\|leader-restart' provisioner/ cli/ Makefile`
Expected: 出力なし

- [ ] **Step 7: CLI テストを更新**

Run: `uv run pytest cli/tests/test_provisioner.py -v`
Expected: `setup-monitoring-agent` / `setup-k3s-leader-restart` を参照するテストがあれば FAIL する。該当アサーションを削除する。

## Task 16: `group_vars/external.yaml` を `standalone.yaml` に

**Files:**
- Delete: `provisioner/inventories/base/group_vars/external.yaml`
- Create: `provisioner/inventories/base/group_vars/standalone.yaml`

- [ ] **Step 1: 置き換える**

```bash
git mv provisioner/inventories/base/group_vars/external.yaml \
       provisioner/inventories/base/group_vars/standalone.yaml
```

中身を次で置き換える。

```yaml
---
# standalone ホスト専用の override。k3s の外にいて再起動を調整する仕組みが
# 無いため、unattended-upgrades 自身で reboot させる。
# 04:00 JST に再起動 (gateway (03:00) と被らせない)。
unattended_upgrades_automatic_reboot: true
unattended_upgrades_automatic_reboot_time: "04:00"
```

k3s ノード (`clusters` グループ) は `roles/common` のデフォルト `unattended_upgrades_automatic_reboot: false` のままにする。kured が無くなるため、再起動待ちのノードは手動で順に再起動する運用になる。

- [ ] **Step 2: ansible-lint / yamllint を通す**

Run: `make ansible/lint && make yaml/lint`
Expected: エラーなし

## Task 17: PR 2 の仕上げとコミット

- [ ] **Step 1: 旧ホスト名・旧サブネット・旧ゾーンの残骸を確認**

Run: `grep -rn 'br-node[1-6]\|br-external1\|172\.22\.10\.\|cluster-internal\|cluster_domain\|cluster_vips' provisioner/ cli/ servers.yaml Makefile`
Expected: 出力なし

- [ ] **Step 2: lint 一式を通す**

Run: `make ansible/lint && make yaml/lint && make lint && make test`
Expected: すべてエラーなし

- [ ] **Step 3: コミット**

```bash
git add provisioner/ cli/ Makefile
git commit -m "$(cat <<'EOF'
feat(provisioner): ノード役割再編とシングル CP 化に合わせて Ansible を再構成

- サブネットを 172.22.52.0/24 に変更、ホスト IP を役割別に振り直し
- 内部ドメインを host_domain (br-cluster) / service_domain (internal-service)
  の 2 ゾーンに分割。環境名を 1 階層挟んで dev/prod を分離
- CoreDNS から etcd プラグインとバイナリを撤去 (external-dns-coredns 廃止)
- roles/external を garage / caddy / certbot に分解し、cloudflared を
  gateway に移設。setup_standalone playbook を新設
- postgresql role を新規追加 (br-db1、TLS は certbot 発行の証明書)
- k3s の datastore を SQLite に (cluster-init / etcd-expose-metrics を削除)、
  kube-vip のブートストラップと API VIP を撤去
- オブザーバビリティ全撤去に伴い setup_monitoring_agent を削除

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PR 3: マニフェストとドキュメント

## Task 18: オブザーバビリティ一式の撤去

**Files:**
- Delete: `manifests/platform/{alloy,alloy-cp,alloy-events,opentelemetry-collector,hubble-flow-exporter,kube-prometheus-stack,grafana,loki,tempo}/`
- Delete: `manifests/clusters/prod/platform/{alloy,alloy-cp,alloy-events,opentelemetry-collector,hubble-flow-exporter,kube-prometheus-stack,grafana,loki,tempo}-app.yaml`
- Modify: `manifests/clusters/prod/platform/kustomization.yaml`

- [ ] **Step 1: コンポーネントを削除**

```bash
git rm -r manifests/platform/alloy \
          manifests/platform/alloy-cp \
          manifests/platform/alloy-events \
          manifests/platform/opentelemetry-collector \
          manifests/platform/hubble-flow-exporter \
          manifests/platform/kube-prometheus-stack \
          manifests/platform/grafana \
          manifests/platform/loki \
          manifests/platform/tempo
git rm manifests/clusters/prod/platform/alloy-app.yaml \
       manifests/clusters/prod/platform/alloy-cp-app.yaml \
       manifests/clusters/prod/platform/alloy-events-app.yaml \
       manifests/clusters/prod/platform/opentelemetry-collector-app.yaml \
       manifests/clusters/prod/platform/hubble-flow-exporter-app.yaml \
       manifests/clusters/prod/platform/kube-prometheus-stack-app.yaml \
       manifests/clusters/prod/platform/grafana-app.yaml \
       manifests/clusters/prod/platform/loki-app.yaml \
       manifests/clusters/prod/platform/tempo-app.yaml
```

- [ ] **Step 2: `kustomization.yaml` から削除した app を外す**

`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` から上記 9 ファイルの行を削除する。

- [ ] **Step 3: 他コンポーネントに残った monitoring 参照を削除**

まず対象を列挙する。**grep の結果を `xargs dirname` に流して一括削除しないこと** — `cilium/app/components/hubble/` のように、monitoring 以外の役割を持つディレクトリまで巻き込む。

Run: `ls -d manifests/platform/*/monitoring 2>/dev/null`
Expected: `monitoring/` サブディレクトリを持つコンポーネントの一覧

列挙されたディレクトリだけを明示的に削除する。

```bash
git rm -r manifests/platform/<component>/monitoring   # 列挙されたぶんだけ繰り返す
```

次に、`monitoring/` 以外の場所に残った Prometheus 系リソースを探す。

Run: `grep -rln 'kind: ServiceMonitor\|kind: PodMonitor\|kind: PrometheusRule\|kind: GrafanaDashboard' manifests/platform/`
Expected: 出力なし。出た場合は**ファイル単位で**削除し、そのファイルを含む `kustomization.yaml` の `resources:` からも外す。ディレクトリごと消さない。

削除したディレクトリを `path:` で指している `manifests/clusters/prod/platform/*-app.yaml` があれば、その Kustomization ファイルごと削除し、`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` からも外す。

- [ ] **Step 4: ビルドが通ることを確認**

Run: `make manifests/build`
Expected: エラーなし

- [ ] **Step 5: コミット**

```bash
git add manifests/
git commit -m "$(cat <<'EOF'
refactor(manifests): オブザーバビリティ一式をクラスタから撤去

Loki / Tempo / Prometheus / Grafana と収集側 (Alloy 3 種 / OpenTelemetry
Collector / Hubble Flow Exporter) を撤去する。各コンポーネントの
monitoring/ サブディレクトリ (ServiceMonitor / PrometheusRule) も同時に削除。

オブザーバビリティ基盤は br-observability1 上で一から作り直す方針のため、
収集側だけ残して外部に送る構成は採らない。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 19: Longhorn / csi-external-snapshotter / cloudnative-pg の撤去

**Files:**
- Delete: `manifests/platform/{longhorn,csi-external-snapshotter,cloudnative-pg}/`
- Delete: `manifests/clusters/prod/platform/{longhorn,csi-external-snapshotter,cloudnative-pg,platform-pg-cluster}-app.yaml`
- Modify: `manifests/clusters/prod/platform/kustomization.yaml`
- Modify: `manifests/platform/zitadel/app/base/referencegrant.yaml`

- [ ] **Step 1: コンポーネントを削除**

```bash
git rm -r manifests/platform/longhorn \
          manifests/platform/csi-external-snapshotter \
          manifests/platform/cloudnative-pg
git rm manifests/clusters/prod/platform/longhorn-app.yaml \
       manifests/clusters/prod/platform/csi-external-snapshotter-app.yaml \
       manifests/clusters/prod/platform/cloudnative-pg-app.yaml \
       manifests/clusters/prod/platform/platform-pg-cluster-app.yaml
```

- [ ] **Step 2: `kustomization.yaml` から外す**

`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` から 4 行を削除する。

- [ ] **Step 3: `referencegrant.yaml` から longhorn を外す**

`manifests/platform/zitadel/app/base/referencegrant.yaml` の `spec.from` から `longhorn` namespace のエントリを削除する。他に `from` が残らない場合はファイルごと削除し、`manifests/platform/zitadel/app/base/kustomization.yaml` の `resources:` からも外す。

- [ ] **Step 4: 残った longhorn 参照を確認**

Run: `grep -rn 'longhorn\|platform-pg\|cloudnative-pg\|volumesnapshot' manifests/ --include='*.yaml' -i`
Expected: `manifests/platform/argo-workflows/app/base/eventbus.yaml` (Task 21 で削除)、`manifests/platform/zitadel/app/base/values.yaml` (Task 22 で修正) のみ

- [ ] **Step 5: コミット**

```bash
git add manifests/
git commit -m "$(cat <<'EOF'
refactor(manifests): Longhorn / CNPG をクラスタから撤去

PVC 利用者が Argo Events の EventBus だけになり、その Argo Events も
撤去するため、分散ブロックストレージを維持する理由が無くなる。
csi-external-snapshotter は Longhorn 専用だったので同時に撤去。

PostgreSQL は br-db1 に移設するため cloudnative-pg と platform-pg
クラスタも撤去する。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 20: kube-vip / kured / external-dns-coredns / internal-gateway の撤去

**Files:**
- Delete: `manifests/platform/{kube-vip,kured,external-dns-coredns}/`
- Delete: `manifests/clusters/prod/platform/{kube-vip,kured,external-dns-coredns}-app.yaml`
- Delete: `manifests/platform/envoy-gateway/config/base/{internal-gateway.yaml,internal-gateway-class.yaml,internal-envoy-proxy.yaml}`
- Modify: `manifests/platform/envoy-gateway/config/base/kustomization.yaml`
- Modify: `manifests/clusters/prod/platform/kustomization.yaml`

- [ ] **Step 1: コンポーネントを削除**

```bash
git rm -r manifests/platform/kube-vip \
          manifests/platform/kured \
          manifests/platform/external-dns-coredns
git rm manifests/clusters/prod/platform/kube-vip-app.yaml \
       manifests/clusters/prod/platform/kured-app.yaml \
       manifests/clusters/prod/platform/external-dns-coredns-app.yaml
```

- [ ] **Step 2: internal-gateway を削除**

```bash
git rm manifests/platform/envoy-gateway/config/base/internal-gateway.yaml \
       manifests/platform/envoy-gateway/config/base/internal-gateway-class.yaml \
       manifests/platform/envoy-gateway/config/base/internal-envoy-proxy.yaml
```

`manifests/platform/envoy-gateway/config/base/kustomization.yaml` の `resources:` から `internal-gateway-class.yaml` と `internal-gateway.yaml` の行を削除する。`internal-envoy-proxy.yaml` が別の形 (patches 等) で参照されていればそこも外す。

- [ ] **Step 3: `kustomization.yaml` から外す**

`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` から 3 行を削除する。

- [ ] **Step 4: 残った参照を確認**

Run: `grep -rn 'kube-vip\|kured\|internal-gateway\|INTERNAL_CLUSTER_GATEWAY\|external-dns-coredns' manifests/`
Expected: `manifests/clusters/prod/config/cluster-settings.yaml` の `INTERNAL_CLUSTER_GATEWAY_IP` のみ (Task 23 で削除)

- [ ] **Step 5: コミット**

```bash
git add manifests/
git commit -m "$(cat <<'EOF'
refactor(manifests): kube-vip / kured / external-dns-coredns を撤去

control-plane が 1 台になり API VIP が不要になるため kube-vip を撤去する。
Service LB の ARP 広告は CiliumL2AnnouncementPolicy が担う。

LAN 内向けの DNS 自動登録 (external-dns-coredns) は、アクセス制限を
Cloudflare Access 側に置いているため不要。唯一の internal-gateway 利用者
だった Loki の HTTPRoute も撤去済みなので、internal-gateway ごと削除する。

kured は control-plane が 1 台では自動再起動がクラスタ全停止になるため撤去。
再起動待ちのノードは手動で順に再起動する。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 21: Argo Events の撤去と Argo Workflows の再配線

**Files:**
- Delete: `manifests/platform/argo-workflows/notify/`, `manifests/platform/argo-workflows/samples/base/{eventsource-webhook.yaml,sensor-webhook.yaml,sensor-rbac.yaml}`, `manifests/platform/argo-workflows/app/base/eventbus.yaml`
- Modify: `manifests/platform/argo-workflows/app/base/{kustomization.yaml,helm.yaml,values-workflows.yaml}`
- Modify: `manifests/platform/argo-workflows/app/base/externalsecret-archive.yaml`
- Modify: `manifests/clusters/prod/platform/argo-workflows-app.yaml`

**Interfaces:**
- Consumes: Task 7 の `service_domain` (`rdbms.prod.internal-service.bright-room.net` / `object-storage.prod.internal-service.bright-room.net`)

- [ ] **Step 1: `notify-discord` の WorkflowTemplate を退避してから Argo Events を削除**

`workflowtemplate-discord.yaml` は残す。Sensor / EventSource / EventBus だけ削除する。

```bash
git mv manifests/platform/argo-workflows/notify/base/workflowtemplate-discord.yaml \
       manifests/platform/argo-workflows/app/base/workflowtemplate-discord.yaml
git rm -r manifests/platform/argo-workflows/notify
git rm manifests/platform/argo-workflows/app/base/eventbus.yaml \
       manifests/platform/argo-workflows/samples/base/eventsource-webhook.yaml \
       manifests/platform/argo-workflows/samples/base/sensor-webhook.yaml \
       manifests/platform/argo-workflows/samples/base/sensor-rbac.yaml
```

`workflowtemplate-discord.yaml` の `metadata.labels` にある `notify.b8m.app/skip: "true"` と `spec.workflowMetadata` の同ラベルは、Sensor の再帰トリガ防止用だった。Sensor が無くなるので両方削除する。`manifests/platform/argo-workflows/app/base/kustomization.yaml` の `resources:` に `workflowtemplate-discord.yaml` を追加し、`eventbus.yaml` の行を削除する。

- [ ] **Step 2: argo-events の HelmRelease を削除**

`manifests/platform/argo-workflows/app/base/helm.yaml` から argo-events の `HelmRelease` を削除する。`manifests/platform/argo-workflows/app/base/values-events.yaml` と `helm-patch-events.yaml` も削除する。

```bash
git rm manifests/platform/argo-workflows/app/base/values-events.yaml \
       manifests/platform/argo-workflows/app/base/helm-patch-events.yaml
```

`kustomization.yaml` の `configMapGenerator` / `patches` から `values-events` / `helm-patch-events` の参照を削除する。`manifests/clusters/prod/platform/argo-workflows-app.yaml` の `path:` が `notify` を指す Kustomization を含んでいればそれも削除する。

- [ ] **Step 3: `values-workflows.yaml` の PostgreSQL 接続先と S3 エンドポイントを変更**

`manifests/platform/argo-workflows/app/base/values-workflows.yaml` の `persistence.postgresql.host` を変更する。

```yaml
  persistence:
    archive: true
    archiveTTL: 30d
    postgresql:
      host: rdbms.prod.internal-service.bright-room.net
      port: 5432
      database: argo_workflows
      tableName: argo_workflows
      userNameSecret:
        name: argo-workflows-archive-db
        key: username
      passwordSecret:
        name: argo-workflows-archive-db
        key: password
```

`artifactRepository.s3.endpoint` を変更する。

```yaml
artifactRepository:
  archiveLogs: true
  s3:
    bucket: argo-workflows
    endpoint: object-storage.prod.internal-service.bright-room.net:3900
    region: ap-northeast-1
    insecure: false
```

- [ ] **Step 4: Discord 通知を `workflowDefaults` の exit hook に移す**

まず WorkflowTemplate のインターフェースを確認する。

Run: `grep -n 'entrypoint:\|^  arguments:\|^      - name:' manifests/platform/argo-workflows/app/base/workflowtemplate-discord.yaml`
Expected: `entrypoint: post` と、`workflow-name` / `workflow-namespace` / `phase` / `started-at` / `finished-at` / `progress` / `message` の 7 パラメータ

Sensor はこの 7 つを EventSource のペイロードから埋めていた。exit hook から呼ぶ場合は Argo の workflow 変数で埋める。`values-workflows.yaml` の `controller` セクション (`persistence` と同じ階層) に追加する。

```yaml
  # Argo Events の Sensor を撤去したため、全 Workflow への Discord 通知は
  # workflowDefaults の exit hook で一括適用する。
  # spec.onExit は同一 Workflow 内の template 名しか取れず WorkflowTemplate を
  # 参照できないため、LifecycleHook (spec.hooks) の templateRef を使う。
  # Sensor が EventSource のペイロードから埋めていた 7 パラメータは、
  # ここでは Argo の workflow 変数から埋める。
  workflowDefaults:
    spec:
      hooks:
        exit:
          expression: "true"
          templateRef:
            name: notify-discord
            template: post
          arguments:
            parameters:
              - name: workflow-name
                value: "{{workflow.name}}"
              - name: workflow-namespace
                value: "{{workflow.namespace}}"
              - name: phase
                value: "{{workflow.status}}"
              - name: started-at
                value: "{{workflow.creationTimestamp}}"
              - name: finished-at
                value: "{{workflow.duration}}"
              - name: progress
                value: "{{workflow.parameters}}"
              - name: message
                value: "{{workflow.failures}}"
```

`{{workflow.*}}` は Argo が展開する変数なので、Helm values 内では二重波括弧のまま書く (Flux の `substituteFrom` は `${VAR}` 形式しか置換しないので衝突しない)。

`templateRef.template` の `post` は上の grep で確認した `entrypoint` の値。異なっていたら実際の値に合わせる。

`finished-at` / `progress` / `message` に当てた変数は Sensor 時代と意味がずれる。Discord に出る文言が不自然なら、`workflowtemplate-discord.yaml` の該当パラメータを削って必要なものだけにする方が素直。**Task 28 Step 1 の `make check` は通っても文言までは検証できないので、spec の移行手順 #11 で実物を見て調整する。**

**バージョン確認**: `spec.hooks` (LifecycleHook) は Argo Workflows v3.3 以降の機能。`manifests/platform/argo-workflows/app/base/helm.yaml` の argo-workflows chart は 1.0.14 で、これは v3.6 系以降を配布しているため要件を満たす。念のため `make manifests/flux-local` のレンダリング結果で controller の image tag を確認する。

- [ ] **Step 5: `externalsecret-archive.yaml` の参照先を確認**

`manifests/platform/argo-workflows/app/base/externalsecret-archive.yaml` は 1Password から `argo-workflows-archive-db` Secret を作る。CNPG が生成していた Secret ではなく 1Password 直参照であればそのまま。CNPG の Secret を参照していた場合は 1Password 参照に書き換える。

Run: `grep -n 'platform-pg\|cnpg\|remoteRef' -A3 manifests/platform/argo-workflows/app/base/externalsecret-archive.yaml`
Expected: `remoteRef.key` が 1Password の `postgresql` アイテム、`property` が `argo_workflows_password` になっていること。CNPG 生成の `platform-pg-argo-workflows` を参照していたら書き換える (Task 22 Step 2 で Zitadel 側と同じアイテムに揃える)。

- [ ] **Step 6: ビルドとポリシー検査**

Run: `make manifests/build && make policy/test`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add manifests/
git commit -m "$(cat <<'EOF'
refactor(argo-workflows): Argo Events を撤去し DB / S3 の接続先を更新

常駐コストの大半を占めていた Argo Events (NATS JetStream x3 + コントローラ群)
を撤去する。実利用は Discord 通知の Sensor と未使用の webhook サンプルだけで、
前者は workflowDefaults の exit hook (LifecycleHook の templateRef) で
代替できる。DAG / 並列 / cron / リトライは Workflows 本体の機能なので失わない。

これにより EventBus の PVC 依存も消え、Longhorn 撤去後に代替の StorageClass
を用意する必要が無くなる。

workflow archive の PostgreSQL は br-db1 に、artifact の S3 エンドポイントは
新しいサービスドメインに向け直す。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 22: Zitadel の DB 接続先変更

**Files:**
- Modify: `manifests/platform/zitadel/app/base/values.yaml:14,35`

- [ ] **Step 1: `Host` を書き換える**

`manifests/platform/zitadel/app/base/values.yaml` の 35 行目付近を変更する。

```yaml
        Host: rdbms.prod.internal-service.bright-room.net
```

14 行目付近のコメント `#   - platform-pg CNPG cluster already created the zitadel database and role` を次に置き換える。

```yaml
#   - br-db1 の PostgreSQL が zitadel データベースとロールを作成済み
#     (provisioner/roles/postgresql)
```

- [ ] **Step 2: Zitadel の DB 認証情報を 1Password 直参照にする**

CNPG が `platform-pg-zitadel` Secret を生成していた経路が消えるので、Ansible の `postgresql` role と同じ 1Password アイテムを ExternalSecret から読む形にする。

Run: `grep -rn 'platform-pg' manifests/platform/zitadel/`
Expected: `values.yaml` か ExternalSecret が `platform-pg-zitadel` を参照している

`manifests/platform/zitadel/app/base/externalsecret-db.yaml` を新規作成する。`remoteRef.key` は Ansible が読むのと同じ `postgresql` アイテム、`property` は同じフィールド名にする。

```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: zitadel-db
  namespace: zitadel
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: zitadel-db
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: postgresql
        property: zitadel_password
```

`secretStoreRef` の `kind` / `name` と `apiVersion` は `manifests/platform/argo-workflows/app/base/externalsecret-archive.yaml` の実物に合わせる (バージョンが違うと Conftest ではなく Flux の適用時に落ちる)。

Run: `grep -n 'apiVersion\|secretStoreRef' -A2 manifests/platform/argo-workflows/app/base/externalsecret-archive.yaml`

`manifests/platform/zitadel/app/base/kustomization.yaml` の `resources:` に `externalsecret-db.yaml` を追加し、`values.yaml` の DB パスワード参照を `zitadel-db` Secret に向ける。

`externalsecret-archive.yaml` (Argo) も同じ `postgresql` アイテムの `argo_workflows_password` を読むように揃える。CNPG 生成の Secret (`platform-pg-argo-workflows`) を参照していたら書き換える。

- [ ] **Step 3: ビルドとポリシー検査**

Run: `make manifests/build && make policy/test`
Expected: エラーなし。Conftest の Secret ルール (直書き禁止) に引っかかる場合は ExternalSecret 経由になっているか確認する。

## Task 23: `cluster-settings.yaml` と SUC の Plan

**Files:**
- Modify: `manifests/clusters/prod/config/cluster-settings.yaml`
- Modify: `manifests/platform/system-upgrade-controller/app/base/server-plan.yaml`
- Modify: `manifests/platform/system-upgrade-controller/app/base/agent-plan.yaml`
- Modify: `manifests/platform/envoy-gateway/config/base/envoy-proxy.yaml`
- Modify: `manifests/platform/cilium/config/base/ip-pool-lb.yaml`

- [ ] **Step 1: `cluster-settings.yaml` を更新**

- `CLUSTER_GATEWAY_IP` を `172.22.52.200` に変更
- `INTERNAL_CLUSTER_GATEWAY_IP` の行を削除
- `KUBE_VIP_ADDRESS` があれば削除
- `COREDNS_ETCD_URL` があれば削除
- オブザーバビリティ / Argo Events 関連の変数があれば削除

Run: `grep -n 'IP\|URL' manifests/clusters/prod/config/cluster-settings.yaml`
Expected: 残るのは `CLUSTER_GATEWAY_IP: "172.22.52.200"` と、削除対象でない変数のみ

- [ ] **Step 2: LB-IPAM のプールを更新**

`manifests/platform/cilium/config/base/ip-pool-lb.yaml` の CIDR を `172.22.52.192/26` に変更する。

- [ ] **Step 3: SUC の Plan を新ノード名に**

`manifests/platform/system-upgrade-controller/app/base/agent-plan.yaml` の nodeSelector を変更する。

```yaml
      - { key: kubernetes.io/hostname, operator: In, values: ["br-cluster2"] }
```

コメントの `Phase 1a: テスト worker 1 台 (br-node5) にのみ適用。` を `Phase 1a: テスト worker 1 台 (br-cluster2) にのみ適用。` に更新する。

`manifests/platform/system-upgrade-controller/app/base/server-plan.yaml` の nodeSelector を変更し、コメントを実態に合わせる。

```yaml
  # control-plane は br-cluster1 の 1 台のみ。この Plan を実行すると
  # クラスタが全停止するため、Phase 1a のテスト運用では対象にしない。
  # 本番展開は agent-plan で挙動を確認してから。
    nodeSelectorTerms:
      - matchExpressions:
        - { key: kubernetes.io/hostname, operator: In, values: ["br-cluster1"] }
```

- [ ] **Step 4: Envoy の LB IP annotation を確認**

Run: `grep -rn 'lb-ipam' manifests/platform/envoy-gateway/`
Expected: `${CLUSTER_GATEWAY_IP}` を参照している。変数の値は Step 1 で更新済みなのでファイル自体の変更は不要。`${INTERNAL_CLUSTER_GATEWAY_IP}` を参照している行が残っていたら Task 20 の削除漏れなので、そのファイルを削除する。

- [ ] **Step 5: `substituteFrom` の整合性を確認**

Run: `make manifests/substitute-check`
Expected: エラーなし (削除した変数を参照している Kustomization が残っていれば検出される)

- [ ] **Step 6: ビルドとポリシー検査**

Run: `make manifests/build && make policy/test && make manifests/flux-local`
Expected: すべてエラーなし

- [ ] **Step 7: コミット**

```bash
git add manifests/
git commit -m "$(cat <<'EOF'
feat(manifests): 新サブネット / 新ノード名に合わせて設定値を更新

- LB-IPAM プールを 172.22.52.192/26、cluster-gateway を .200 に
- INTERNAL_CLUSTER_GATEWAY_IP / KUBE_VIP_ADDRESS / COREDNS_ETCD_URL を削除
- SUC の Plan の nodeSelector を br-cluster1 / br-cluster2 に
- Zitadel の DB 接続先を br-db1 の PostgreSQL に

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 24: k3s 内 CoreDNS の設定確認

**Files:**
- Modify: `manifests/platform/coredns/app/base/values.yaml:29`

- [ ] **Step 1: `auth.b8m.app` の rewrite を確認**

`manifests/platform/coredns/app/base/values.yaml` の 29 行目は `${CLUSTER_GATEWAY_IP} auth.b8m.app` という hosts エントリ。変数の値は Task 23 で `172.22.52.200` に更新済みなのでファイル自体の変更は不要。

Run: `grep -n '172.22.10\|cluster-internal' manifests/platform/coredns/`
Expected: 出力なし。出た場合は該当箇所を新しい値に置き換える。

- [ ] **Step 2: 全マニフェストに旧値が残っていないか確認**

Run: `grep -rn '172\.22\.10\.\|cluster-internal\|platform-pg\|longhorn' manifests/ --include='*.yaml' -i`
Expected: 出力なし

## Task 25: `docs/platform/` の更新

**Files:**
- Delete: `docs/platform/observability.md`, `docs/platform/storage.md`
- Modify: `docs/platform/networking.md`, `docs/platform/workflows.md`, `docs/platform/identity.md`
- Modify: `docs/README.md`

- [ ] **Step 1: 失効したドキュメントを削除**

```bash
git rm docs/platform/observability.md docs/platform/storage.md
```

`docs/README.md` のインデックスから 2 ファイルへのリンクを削除する。

- [ ] **Step 2: `networking.md` を更新**

| 箇所 | 変更 |
|---|---|
| 「このグループが解決する課題」 | `kube-vip` の行と `external-dns-coredns` の行を削除 |
| 「グループ全体の設計判断」の表 | ARP 広告の行を「Cilium L2 Announcement 単独」に変更。旧構成の欄に「Cilium L2 + kube-vip svc_enable の二重 (kube-vip 撤去により単独化)」と理由を書く |
| 「Gateway 本数」の行 | internal-gateway 撤去に合わせて「cluster-gateway 1 本」に |
| 「LB IP 払い出し」節 | IP を `172.22.52.200` / プール `172.22.52.192/26` に。internal-gateway の記述を削除 |
| 「ARP 広告 (二重で有効)」節 | 節ごと書き換え。2026-04-25 の 502 の経緯は残しつつ、kube-vip 撤去後は Cilium L2 単独であること、構築時に `arping` で検証する手順があることを書く |
| `kube-vip` 節 | 節ごと削除 |
| `external-dns-coredns` の記述 | 削除。`external-dns-cloudflare` の記述は残す |
| 内部 DNS の記述 | `cluster-internal.bright-room.net` を新 2 ゾーンに |

- [ ] **Step 3: `workflows.md` を更新**

Argo Events (EventBus / EventSource / Sensor) の記述を削除し、Discord 通知が `workflowDefaults` の exit hook で実装されていることに書き換える。mermaid 図の `subgraph Events` ブロックを削除し、`PG` ノードのラベルを `br-db1 PostgreSQL` に、`S3` を `br-storage1 Garage` に変更する。

「このグループが解決する課題」から「外部トリガ (HTTP webhook で起動)」の行を削除する。

- [ ] **Step 4: `identity.md` を更新**

Zitadel の DB が `br-db1` の PostgreSQL であることに書き換える。`auth.b8m.app` の CoreDNS rewrite の記述はそのまま (IP だけ `172.22.52.200`)。

- [ ] **Step 5: マニフェストとの整合を確認**

Run: `grep -rn 'longhorn\|kube-vip\|kured\|loki\|tempo\|grafana\|prometheus\|alloy\|argo-events\|external-dns-coredns\|internal-gateway' docs/platform/ -i`
Expected: 撤去の経緯として意図的に残した記述のみ

## Task 26: `docs/` のトップレベルを更新

**Files:**
- Modify: `docs/architecture.md`, `docs/hardware.md`, `docs/network.md`, `docs/kubernetes.md`, `docs/provisioning.md`, `docs/cli.md`, `docs/operations.md`, `README.md`, `CLAUDE.md`
- Modify: `docs/runbooks/k3s-upgrade.md`

- [ ] **Step 1: `hardware.md` を更新**

ノード一覧の表を新ノードマップ (spec の [ノードマップ](../proposals/2026-09-05-single-cp-rearch.md#ノードマップ)) に置き換える。「役割別の詳細」の `br-external1 (External)` 節を `br-db1` / `br-storage1` / `br-observability1` / `br-ai1` の 4 節に分ける。ディスクレイアウトの表の `storage_mode` 対象ホストを更新する。RTL9210 UAS quirk の節は変更しない。

- [ ] **Step 2: `network.md` を更新**

サブネット表 / ホスト IP 表 / DNS ゾーンの節を新設計に置き換える。API VIP の行を削除。nftables の INPUT / FORWARD / NAT の表を Task 9 の変更に合わせる。`external-dns-coredns` による動的レコードの記述を削除し、`service_records` による静的 A レコードの説明に置き換える。

- [ ] **Step 3: `kubernetes.md` を更新**

「ノード別 k3s 役割」の表を 3 ノード構成に。「ブートストラップ順序」から kube-vip を削除。datastore が SQLite であること、クラスタ状態のバックアップを取らない方針を追記する。

- [ ] **Step 4: `provisioning.md` / `cli.md` を更新**

`make {env}/provision/setup-external` を `setup-standalone` に。`setup-monitoring-agent` の行を削除。`servers.yaml` の `services` フィールドと `standalone` 型の説明を追加する。

- [ ] **Step 5: `operations.md` を更新**

kured による自動再起動の記述を、手動での順次再起動に置き換える。Longhorn / オブザーバビリティ関連の運用手順を削除する。

- [ ] **Step 6: `docs/runbooks/k3s-upgrade.md` を更新**

Phase 1a の対象を `br-node5` → `br-cluster2` に。etcd snapshot 連携の記述を削除し、SQLite では snapshot 機構が使えないこと、control-plane の upgrade はクラスタ全停止を伴うことを書く。

- [ ] **Step 7: `README.md` を更新**

冒頭の「8 ノード (gateway1 / external1 / k3s 6 台)」を「8 ノード (gateway1 / standalone 4 台 / k3s 3 台)」に。「よく使うコマンド」の `setup-external` を `setup-standalone` に、`setup-monitoring-agent` の行を削除する。

- [ ] **Step 8: `CLAUDE.md` を更新**

| 箇所 | 変更 |
|---|---|
| 「触らないもの」の `garage/setup.sh (br-external1 上の...)` | `br-storage1` に |
| 「環境前提」の Longhorn バックアップの記述 | Longhorn 撤去に合わせて書き換え。「PVC 利用者はゼロ、クラスタ状態は Flux から再構築する」に |
| 「chicken-and-egg な依存」の表の kube-vip の行 | 削除 |
| 「非自明な設計判断」の表 | ARP 広告 / Pod ログ収集 / Loki・Tempo / Longhorn の 4 行を削除。k3s upgrade の行を SQLite 前提に更新。「datastore は SQLite、クラスタ状態のバックアップは取らない」の行を追加 |

- [ ] **Step 9: 図の更新をタスク化**

`docs/assets/` の `hardware-topology.svg` / `networking-external.svg` / `networking-internal.svg` / `networking-dependency.svg` は draw.io 形式のため、このタスクでは更新しない。各図を参照している doc の該当箇所に次のコメントを入れる。

```markdown
<!-- TODO(figure): 2026-09-05 のノード再編を未反映。draw.io で更新が必要 -->
```

図の更新は手作業が要るので別 issue として切り出す。

- [ ] **Step 10: 全体の整合を確認**

Run: `grep -rn 'br-node[1-6]\|br-external1\|172\.22\.10\.\|cluster-internal' docs/ README.md CLAUDE.md`
Expected: `docs/incidents/` と `docs/proposals-done/` の過去記録、および `docs/proposals/2026-09-05-single-cp-rearch.md` の経緯説明のみ (履歴なので書き換えない)

## Task 27: 失効した proposal の整理

**Files:**
- Modify or Delete: `docs/proposals/observability-plan-remaining.md`

- [ ] **Step 1: 内容を確認**

Run: `head -40 docs/proposals/observability-plan-remaining.md`

- [ ] **Step 2: 削除する**

オブザーバビリティを全撤去して一から作り直す方針のため、この proposal の残作業は全て失効する。

```bash
git rm docs/proposals/observability-plan-remaining.md
```

CLAUDE.md の規約に従い、削除理由はコミットメッセージに残す。`docs/proposals/` を参照しているドキュメント (`docs/README.md` など) にリンクがあれば削除する。

## Task 28: PR 3 の仕上げとコミット

- [ ] **Step 1: CI 等価チェックを通す**

Run: `make check`
Expected: すべてエラーなし。`manifests/flux-local` が撤去したコンポーネントを参照して落ちる場合、`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` に残骸がある。

- [ ] **Step 2: spec の記述と実装の差分を確認**

spec の [プラットフォーム構成](../proposals/2026-09-05-single-cp-rearch.md#プラットフォーム構成) の「存続 (13)」の一覧と、`manifests/clusters/prod/platform/kustomization.yaml` の `resources:` を突き合わせる。

Run: `ls manifests/platform/`
Expected: `argo-workflows` / `cert-manager` / `cilium` / `cloudflared` / `coredns` / `envoy-gateway` / `external-dns-cloudflare` / `external-secrets` / `flux-operator` / `metrics-server` / `onepassword-connect` / `system-upgrade-controller` / `zitadel` の 13 個のみ

- [ ] **Step 3: ドキュメントをコミット**

```bash
git add docs/ README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: ノード再編とプラットフォーム削減に合わせて全 doc を更新

コードを正としてノード一覧 / IP 設計 / DNS ゾーン / k3s 構成 / 運用手順を
書き換える。撤去したコンポーネント (observability / storage) の doc は削除。

docs/proposals/observability-plan-remaining.md を削除。オブザーバビリティを
全撤去して br-observability1 上で一から作り直す方針になり、残作業が全て
失効したため。

docs/assets/ の drawio 図は未更新。該当箇所に TODO コメントを入れた。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 完了条件

- [ ] `make check` が通る
- [ ] `grep -rn 'br-node[1-6]\|br-external1\|172\.22\.10\.\|cluster-internal\|cluster_domain\|cluster_vips\|kube-vip\|longhorn\|platform-pg' provisioner/ cli/ manifests/ servers.yaml Makefile` の出力が空
- [ ] `ls manifests/platform/` が 13 コンポーネントのみ
- [ ] `make -n prod/provision/setup-standalone` が成功し、`make -n prod/provision/setup-external` が失敗する
- [ ] 3 つの PR がすべてマージ済み

## この計画の範囲外

| 項目 | 扱い |
|---|---|
| 1Password のアイテム作成 / 更新 | spec の前提条件 #1。リポジトリ外の手作業 |
| `br-cloudflare-terraform` の Tunnel Route 変更 | spec の前提条件 #2。別リポジトリ |
| `br-cluster-zitadel-terraform` の state 作り直し | spec の前提条件 #3。別リポジトリ |
| 全 8 台の `free -h` / MAC 記録 | spec の前提条件 #4。物理作業。**ディスクを消す前にしかできない** |
| 実際のリフラッシュと構築 | spec の [移行手順](../proposals/2026-09-05-single-cp-rearch.md#移行手順)。PR マージ後の運用作業 |
| `docs/assets/` の drawio 図の更新 | Task 26 Step 9 で TODO コメントを入れるのみ。別 issue |
| `br-observability1` / `br-ai1` に載せるもの | spec の [後続のサブプロジェクト](../proposals/2026-09-05-single-cp-rearch.md#後続のサブプロジェクト) B / C |
