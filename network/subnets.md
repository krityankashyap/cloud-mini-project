# Task 5 — Subnetting `10.20.0.0/16`

## Method

Allocate **largest requirement first**, contiguously from `10.20.0.0`, using the
smallest prefix that satisfies each host count. The usable-host rule is
`usable = 2^(32 - prefix) - 2` (subtract network + broadcast).

| Requirement | Hosts needed | Smallest block that fits | Chosen prefix | Addresses (`2^(32-p)`) | Usable (`-2`) |
|-------------|--------------|--------------------------|---------------|------------------------|----------------|
| app         | 1,000        | need ≥ 1,002 → 1,024     | **/22**       | 1,024                  | 1,022          |
| public      | 250          | need ≥ 252 → 256         | **/24**       | 256                    | 254            |
| data        | 100          | need ≥ 102 → 128         | **/25**       | 128                    | 126            |
| management  | 20           | need ≥ 22 → 32           | **/27**       | 32                     | 30             |

Placement is contiguous: each subnet begins at the address immediately after the
previous subnet's broadcast address.

- app `/22`  : `10.20.0.0` – `10.20.3.255`  → next free = `10.20.4.0`
- public `/24`: `10.20.4.0` – `10.20.4.255`  → next free = `10.20.5.0`
- data `/25` : `10.20.5.0` – `10.20.5.127`  → next free = `10.20.5.128`
- mgmt `/27` : `10.20.5.128` – `10.20.5.159`

## Allocation table

| Subnet     | Network address | Prefix | Usable host range             | Broadcast address | Usable hosts |
|------------|-----------------|--------|-------------------------------|-------------------|--------------|
| app        | `10.20.0.0`     | /22    | `10.20.0.1` – `10.20.3.254`   | `10.20.3.255`     | 1,022        |
| public     | `10.20.4.0`     | /24    | `10.20.4.1` – `10.20.4.254`   | `10.20.4.255`     | 254          |
| data       | `10.20.5.0`     | /25    | `10.20.5.1` – `10.20.5.126`   | `10.20.5.127`     | 126          |
| management | `10.20.5.128`   | /27    | `10.20.5.129` – `10.20.5.158` | `10.20.5.159`     | 30           |

Total consumed: `10.20.0.0` – `10.20.5.159`, well inside the `/16`. The remaining
space (`10.20.5.160` onward) is free for future growth.

## NACL for the public subnet — `network/nacl.json`

The load balancer lives in the **public** subnet. The NACL is **stateless**, so
every packet — including replies — is evaluated on its own against the rule list
for its direction, lowest rule number first, first match wins, default **deny**.

Key CIDRs used in the rules:

- app subnet        = `10.20.0.0/22`
- management subnet = `10.20.5.128/27`
- abusive range     = `198.51.100.0/24`
- clients anywhere  = `0.0.0.0/0`, using ephemeral source ports `1024`–`65535`

### Inbound rules (match the packet's **source** address)

| Rule | Protocol | Port range  | Source CIDR       | Action | Purpose                                              |
|------|----------|-------------|-------------------|--------|------------------------------------------------------|
| 100  | all      | (all)       | `198.51.100.0/24` | deny   | Block abusive range first — even on web ports        |
| 110  | tcp      | 80          | `0.0.0.0/0`       | allow  | HTTP from anywhere                                    |
| 120  | tcp      | 443         | `0.0.0.0/0`       | allow  | HTTPS from anywhere                                   |
| 130  | tcp      | 22          | `10.20.5.128/27`  | allow  | SSH only from the management subnet                  |
| 140  | tcp      | 1024–65535  | `10.20.0.0/22`    | allow  | Replies coming back from the app subnet              |

### Outbound rules (match the packet's **destination** address)

| Rule | Protocol | Port range | Dest CIDR      | Action | Purpose                                      |
|------|----------|------------|----------------|--------|----------------------------------------------|
| 100  | tcp      | 8000       | `10.20.0.0/22` | allow  | Forward requests to the app service on :8000 |
| 110  | tcp      | 1024–65535 | `0.0.0.0/0`    | allow  | Replies going back to clients anywhere       |

Anything not matched is denied by the implicit default-deny.

## Explanation (≤ ½ page)

**Why a stateless NACL needs its own rules for reply traffic.** A stateful
firewall remembers that it allowed an outbound request, so it automatically lets
the matching reply back in. A NACL keeps no such connection table: it judges each
packet independently with no memory of what came before. So when the public
load balancer forwards a request to the app subnet on TCP 8000 (outbound rule
100), the app's response arrives as a *brand-new inbound packet* whose source is
the app subnet and whose destination port is the ephemeral port the load
balancer used. Nothing in the "allow 80/443/22" inbound rules covers that, so
without an explicit rule (inbound 140) the reply would hit the default-deny and
the connection would hang. The same logic applies outbound: a client's request
comes in on 80/443, but our *reply* to the client leaves on the client's
ephemeral port (1024–65535) to an arbitrary address, which is why outbound rule
110 exists. Statelessness forces us to open **both directions** of every flow.

**Why rule order matters for `198.51.100.0/24`.** Evaluation is lowest-number-
first and first-match-wins. The abusive range must be blocked *even on ports 80
and 443*, but those same ports are allowed from `0.0.0.0/0` (which includes
`198.51.100.0/24`). If the allow-443 rule had the lower number, a packet from
`198.51.100.9` to port 443 would match it first and be allowed — the deny would
never be reached. Placing the deny at rule **100**, below the allow at **120**,
guarantees the abusive source is rejected before any allow rule can match it. A
deny only overrides an allow when its rule number is strictly lower.
