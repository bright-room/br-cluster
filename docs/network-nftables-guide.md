# br-gateway1 nftables ルール解説

## 全体像

`br-gateway1` は、クラスタの **玄関の門番** です。外の世界とクラスタ内の間に立って、全ての通信をチェックします。

```mermaid
graph LR
    WAN["外の世界<br/>(WAN)"]
    GW["br-gateway1<br/>(門番)"]
    LAN["クラスタ内 (LAN)<br/>K8sノード<br/>br-external1 (Garage)<br/>その他"]

    WAN <-->|wlan0| GW
    GW <-->|eth0| LAN

    style WAN fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style LAN fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
```

門番は通信を **4つの場面** でチェックします。

---

## 1. Input = 「門番自身に話しかける通信」

ゲートウェイを通り抜けるのではなく、**ゲートウェイ本人宛** の通信です。

```mermaid
graph LR
    WAN["外の世界<br/>(WAN)"] -->|"① WAN → GW"| GW["br-gateway1<br/>(ここが宛先)"]
    LAN["クラスタ内<br/>(LAN)"] -->|"② LAN → GW"| GW

    style WAN fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style LAN fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
```

**基本方針: 全部拒否! 以下だけ通す**

### ① 外(WAN) → ゲートウェイ自身

| 許可するもの | 何に使う？ |
|---|---|
| SSH | 外からゲートウェイにログイン |

**これだけ。** 最低限に絞っています。

### ② クラスタ内(LAN) → ゲートウェイ自身

| 許可するもの | 何に使う？ |
|---|---|
| SSH | ノードからゲートウェイにログイン |
| DNS | 「google.com の IP 教えて」という名前解決 |
| etcd (2379) | K8s の設定データベース |
| node-exporter (9100) | 「CPU 使用率いくつ？」等の監視データ収集 |
| NTP | 時計合わせ |
| DHCP | 「IP アドレスちょうだい」 |

### 常に許可

| 許可するもの | 何に使う？ |
|---|---|
| ping (ICMP) | 「生きてる？」の確認 |

---

## 2. Output = 「門番自身が出す通信」

```mermaid
graph LR
    GW["br-gateway1<br/>(ここが送信元)"] -->|"制限なし"| ANY["どこへでも OK"]

    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style ANY fill:#f5f5f5,stroke:#999,color:#333
```

自分自身は信用しているので **全部許可** です。

---

## 3. Forward = 「門を通り抜ける通信」

ゲートウェイ自身には用がなく、**中継して通過する** 通信のルールです。これが一番重要。

**基本方針: 全部拒否! 以下だけ通す**

### クラスタ内 → 外 (LAN → WAN)

クラスタ内のマシンがインターネットに出る通信です。

```mermaid
graph LR
    Node["K8sノード"] --> GW["br-gateway1"]
    GW --> Internet["インターネット"]

    style Node fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Internet fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
```

| 許可するもの | 何に使う？ |
|---|---|
| HTTP / HTTPS | Web サイトへのアクセス、パッケージ取得、Flux の Git clone |
| DNS | 名前解決 |
| NTP | 時計合わせ |

**それ以外は拒否。** 例えば、ノードから外部への SSH は通せません。

### 外 → クラスタ内 (WAN → LAN)

外からクラスタ内のマシンにアクセスする通信です。

```mermaid
graph LR
    Internet["インターネット"] --> GW["br-gateway1"]
    GW -->|SSH| Nodes["LAN内の各ノード"]
    GW -->|"HTTP/HTTPS"| Web["Webサービス"]
    GW -->|":6443"| K8s["K8s API (VIP)"]
    GW -->|":443 (DNAT)"| Caddy["Caddy → Garage Web UI"]

    style Internet fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Nodes fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style Web fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style K8s fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style Caddy fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
```

| 許可するもの | 何に使う？ |
|---|---|
| SSH | 外から LAN 内のどのノードにもログイン |
| HTTP / HTTPS | 外から LAN 内の Web サービスにアクセス |
| 6443 | 外から K8s API にアクセス (kubectl) |
| 443 (DNAT) | 外から Garage Web UI にアクセス (Caddy 経由) |

---

## 4. NAT = 「住所の書き換え」

クラスタ内のマシンは `172.22.x.x` というプライベート IP を持っています。これは **クラスタ内だけで通じる住所** で、外の世界からは直接アクセスできません。

そこでゲートウェイが **郵便局** のように住所を書き換えます。

### 中から外へ出るとき (マスカレード)

```mermaid
sequenceDiagram
    participant Node as K8sノード<br/>172.22.10.10
    participant GW as br-gateway1<br/>(住所を書き換え)
    participant Ext as インターネット<br/>8.8.8.8

    Node->>GW: 差出人: 172.22.10.10
    Note over GW: 差出人を<br/>192.168.2.50 に書き換え
    GW->>Ext: 差出人: 192.168.2.50
    Ext->>GW: 返事: 宛先 192.168.2.50
    Note over GW: 「これは<br/>172.22.10.10 宛だった」
    GW->>Node: 返事: 宛先 172.22.10.10
```

### 外から中へ入るとき (DNAT = 転送)

外からはクラスタ内の `172.22.x.x` が見えません。代わりにゲートウェイの IP + ポート番号で受けて、中の正しいマシンに転送します。

```mermaid
graph LR
    User1["外の人<br/>:443"] --> GW["br-gateway1<br/>(宛先を書き換え)"]
    User2["外の人<br/>:6443"] --> GW

    GW -->|"443 → 172.22.10.50:443"| Caddy["Caddy<br/>→ Garage Web UI"]
    GW -->|"6443 → K8s VIP:6443"| K8s["K8s API"]

    style User1 fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style User2 fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style GW fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Caddy fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style K8s fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
```

| 外から見えるアドレス | 転送先 | 用途 |
|---|---|---|
| `192.168.2.50:443` | `172.22.10.50:443` (Caddy) | Garage Web UI (TLS) |
| `192.168.2.50:6443` | K8s API VIP:6443 | kubectl 操作 |

---

## Garage まわりの通信フロー

Garage には複数のポートがありますが、用途によってアクセス経路が異なります。

```mermaid
graph TB
    subgraph wan ["外からのアクセス"]
        ExtUser["外部ユーザー"]
    end

    subgraph gw ["br-gateway1"]
        DNAT["DNAT<br/>:443 → 172.22.10.50:443"]
    end

    subgraph external ["br-external1 (172.22.10.50)"]
        Caddy443["Caddy :443<br/>TLS終端"]
        Caddy3900["Caddy :3900<br/>TLS終端"]
        GarageWeb["Garage Web UI<br/>localhost:3902"]
        GarageS3["Garage S3 API<br/>localhost:3900"]
        GarageAdmin["Garage Admin API<br/>localhost:3903"]
    end

    subgraph lan ["クラスタ内"]
        K8s["K8sノード<br/>(バックアップ)"]
        Admin["管理者<br/>(SSH経由)"]
    end

    ExtUser -->|":443"| DNAT
    DNAT --> Caddy443
    Caddy443 --> GarageWeb

    K8s -->|":3900"| Caddy3900
    Caddy3900 --> GarageS3

    Admin -->|"SSH → garage CLI"| GarageAdmin

    style ExtUser fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style DNAT fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Caddy443 fill:#fce4ec,stroke:#e57373,color:#5c1a1a
    style Caddy3900 fill:#fce4ec,stroke:#e57373,color:#5c1a1a
    style GarageWeb fill:#f3e5f5,stroke:#ab47bc,color:#4a1a5c
    style GarageS3 fill:#f3e5f5,stroke:#ab47bc,color:#4a1a5c
    style GarageAdmin fill:#f3e5f5,stroke:#ab47bc,color:#4a1a5c
    style K8s fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style Admin fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
```

| 経路 | 用途 | TLS |
|---|---|---|
| 外 → GW(:443) → Caddy → Garage Web UI | Web UI を外から閲覧 | あり |
| K8sノード → Caddy(:3900) → Garage S3 API | バックアップ (Restic, Longhorn, Barman) | あり |
| SSH → garage CLI → Admin API (localhost:3903) | バケット作成・キー管理 | 不要 (localhost) |

---

## 全通信の一覧

```mermaid
graph TB
    subgraph wan ["外の世界 (WAN)"]
        ExtSSH["SSH"]
        ExtHTTPS["HTTPS :443"]
        ExtK8s[":6443"]
        ExtWeb["HTTP/HTTPS"]
    end

    subgraph gw ["br-gateway1"]
        Input["Input<br/>(GW自身宛)"]
        Forward["Forward<br/>(通過する通信)"]
    end

    subgraph lan ["クラスタ内 (LAN)"]
        Nodes["K8sノード"]
        WebSvc["Webサービス"]
        External["br-external1<br/>(Caddy + Garage)"]
        K8sAPI["K8s API (VIP)"]
    end

    subgraph outbound ["インターネット"]
        Internet["HTTP/HTTPS, DNS, NTP"]
    end

    %% Input
    ExtSSH -->|"WAN→GW: SSHのみ"| Input
    Nodes -->|"LAN→GW: SSH,DNS,etcd,監視,NTP,DHCP"| Input

    %% Forward WAN→LAN
    ExtSSH -->|"SSH"| Forward
    Forward --> Nodes
    ExtWeb -->|"HTTP/HTTPS"| Forward
    Forward --> WebSvc
    ExtK8s --> Forward
    Forward -->|"DNAT :6443"| K8sAPI
    ExtHTTPS --> Forward
    Forward -->|"DNAT :443"| External

    %% Forward LAN→WAN
    Nodes --> Forward
    Forward -->|"HTTP/HTTPS, DNS, NTP"| Internet

    style ExtSSH fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style ExtHTTPS fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style ExtK8s fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style ExtWeb fill:#e8f4fd,stroke:#4a90d9,color:#1a3a5c
    style Input fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Forward fill:#fff3e0,stroke:#e09040,color:#5c3a1a
    style Nodes fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style WebSvc fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style External fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style K8sAPI fill:#e8f5e9,stroke:#66bb6a,color:#1a5c2a
    style Internet fill:#f5f5f5,stroke:#999,color:#333
```
