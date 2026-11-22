# Paper 3 campaign controller: an always-on machine for the Lambda drivers

Status: written 2026-11-22. **Nothing in this document has been created, launched or changed in any cloud.** Everything it says about the
cloud comes from read-only calls (`gcloud ... describe|list|get-iam-policy`, `testIamPermissions`, local `--help`), from the providers' public
documentation, or from tests and container runs on this machine; each statement says which, and what is **unverified**. It is an operating
document like `docs/PAPER3_RUNBOOK.md` (which it extends and does not replace): it changes no value of the locked design. The tools it names
are in `tools/paper3_campaign/` and are tested in `tests/test_paper3_controller.py`.

Contents: 1 Short version - 2 The problem - 3 What the project looks like (read-only findings) - 4 The options - 5 The recommendation -
6 The controller - 7 Step list - 8 Operating it - 9 When something fails - 10 Teardown - 11 What changed in the tools, and how it was checked -
12 What the author must decide - 13 The controller in a separate project.

## 1. Short version

* **Recommendation: option B (split), plus two additions.** One small VM runs **every Lambda launch** (price snapshot, launch, driver, teardown)
  and a stateless lease reaper. It has **no GCP identity at all** (no service account, no scopes, no IAM binding), so it is technically unable to
  touch any resource of the shared project `siriusmedai`, and no IAM policy is changed. The **GCP launches (C-GCP, C-VM) stay on the Mac**, but
  are launched so that a Mac outage costs a bounded amount and loses no data: the provider's own `--max-run-duration` now **stops** the host
  instead of deleting it, so the disk with the round data stays.
* **Why not everything on the controller (option A).** It is feasible for the author (they are project Owner), but the name-based IAM conditions can
  only guard instance and disk *mutations*; `list`, operations, machine types and the subnet cannot be conditioned on a name, the one decisive
  condition (on `compute.instances.create`) is unverified and can only be tested by creating something, and the conditional bindings live in a
  project policy that other administrators edit. B gets the same protection for the Lambda hosts (the only unbounded exposure) with none of that. A
  is specified in section 4.1 in case the author wants the GCP drivers always on as well.
* **Cost:** about **$0.85 per day** ($4.3 for a 5-day use; $11.9 if it runs its full 14-day provider-side limit; $26 per month if forgotten, which the limit prevents):
  `e2-small` $0.0168/h, 100 GB `pd-balanced`, one ephemeral IPv4 address.
* **Name:** `p3-controller-owner-ejiod-expires-20261206t2100z` (the brief's `<YYYYMMDD>` form does not satisfy the lease convention that
  `probe_power.lease_status` checks; the full `<YYYYMMDD>t<HHMM>z` does). Labels `project=paper3-energy,owner=ejiod,purpose=controller,expires=...`.
* **Access:** IAP tunnel + OS Login only; a dedicated VPC whose only ingress rule is Google's IAP range on tcp:22; no public listener.
* **Keep-alive:** systemd system units (not tmux): `p3-driver@<lease>.service` per launch (restarted on a crash, not on the driver's deliberate
  exit codes, comes back after a reboot) and `p3-reaper.timer` (every 5 minutes and after every boot). Verified under a real systemd 255 in an
  Ubuntu 24.04 container, including a container restart as the "reboot" (section 11).
* **Three things found on the way that matter for the GCP hosts too** (section 3): the default compute service account of the project holds the
  **Editor** role; the project's **shared `ssh-keys` metadata** holds three keys and the author's is one of them; and Google's documentation says
  `--max-run-duration` can be changed after creation only **after stopping** the VM, which the runbook's "Extending a lease" (GCP) does not say. The first
  two are **fixed in the planner by default** (5.3): every C-GCP and C-VM host is created with `--no-service-account --no-scopes` and
  `block-project-ssh-keys=TRUE`; the bring-up pulls nothing from GCS or any Google API, so no service account is needed at all.
* **An alternative that keeps the Lambda key away from `siriusmedai`'s other administrators:** the same controller in a **new GCP project** (section 13).
  The author can link it to the billing account they already use; creating it is a decision for the author.

## 2. The problem

The drivers run on the author's Mac. The Mac slept on 2026-11-16 (a B200 probe result was lost and the instance billed about 3 h) and rebooted
the same day (every watcher process was killed for 6 days). **Lambda has no provider-side time limit, no stop state and no spend limit**
(docs/PAPER3_INFRA.md section 5): a Lambda instance bills until something terminates it, and everything that terminates it (the driver, the per-launch
watchdog) is a process on the operator's machine. The campaign is about three days; Lambda hosts are 4 of the 6 host classes (H100, A100, GH200,
B200) and 8 of the 11 launches (B200 is admitted by design amendment A2; 6 of 9 without it). GCP is different: `--max-run-duration` is enforced by Google, so a dead operator machine costs at most the lease
(21 h of the metal host, $267) and the design already accepts that exposure.

What a controller has to give: (1) drivers that keep running when the Mac is off; (2) a termination backstop for Lambda that survives its own reboot;
(3) a way to watch and collect from the Mac; (4) no new way to hurt the shared GCP project; (5) the secrets (Lambda API key, the SSH key for the
hosts) in as few places as possible.

## 3. What the project looks like (read-only findings, 2026-11-22)

Every line below is from a read-only call as the author's account; no identity, key or token is reproduced. `siriusmedai` runs unrelated production workloads.

| Finding | Evidence | Consequence |
|---|---|---|
| The author's account is **project Owner** | `roles/owner` bound directly; `testIamPermissions` granted every project-level permission I probed (about 40), among them `resourcemanager.projects.setIamPolicy`, `iam.serviceAccounts.create`, `compute.networks.create`, `compute.instances.create`, `iap.tunnelInstances.accessViaIAP` and `compute.instances.osAdminLogin` | Both options are feasible without anyone else's help. B needs no IAM change at all. |
| **No organisation or folder**: the project's ancestry is the project alone | `gcloud projects get-ancestors` | No organisation policy, no inherited IAM; every IAM statement is project-level, and so is anyone else's access to the controller. |
| Project IAM policy is **version 1** and has 71 role bindings, most of them service agents and application service accounts: Owner x2 users, Editor x1 user + 3 service accounts, Compute Admin x3 users, Project IAM Admin x1 user | `get-iam-policy` (roles and member types only) | A conditional binding needs a version 3 policy; other administrators edit this policy (section 4.1). Those principals can also get a shell on the controller: **the Lambda key on the controller is only as private as the project's administrator set**. |
| The **default compute service account holds `roles/editor`** | its member is in the Editor binding | Any VM created without `--no-service-account` runs as project Editor with the default scopes (which include read access to Cloud Storage). The runbook's first C-GCP create command did not pass it. **Now the planner always passes `--no-service-account --no-scopes`** to a campaign host. What the bring-up pulls (read from `setup_h100.sh`, the generators and the bring-up script): apt, PyPI, Maven Central (the RAPIDS jar), the DuckDB extension repository, public GitHub; no `gs://`, no `gcloud`, no metadata-server token. The controller is created the same way. |
| **Project-wide `ssh-keys` metadata: 3 entries, none expiring; the author's `google_compute_engine` key is one of them.** OS Login is not enforced project-wide | `compute project-info describe` (key names and lengths only; the public-key comparison printed only a boolean) | A VM created with default metadata trusts all three keys. **Now every campaign host gets `block-project-ssh-keys=TRUE`** (access is the key `gcloud compute ssh` puts into the instance's own metadata, or OS Login with `P3_GCP_OS_LOGIN=1`), and the controller gets `enable-oslogin=TRUE` as well. The author has an OS Login profile already (1 POSIX account, 1 key). |
| The `default` network has ingress rules for **0.0.0.0/0 on tcp:22, tcp:2222, tcp:3389 and icmp, without target tags** (so they apply to every VM in it), and `default-allow-internal` (10.128.0.0/9, all ports) | `firewall-rules list` | A controller in `default` would have SSH open to the Internet and be reachable from every production VM. It gets its own VPC (section 6). |
| APIs enabled: compute, iam, iamcredentials, **iap, oslogin**, cloudresourcemanager, storage, serviceusage; **cloudbilling is not** | `services list --enabled` | IAP SSH and OS Login work as is; billing budgets still need the author's consent (PAPER3_INFRA 5). |
| Quotas: `NETWORKS` 4/30, `FIREWALLS` 33/500, `INSTANCES` 5/6000 and `CPUS` 6/3000 in us-central1, `C3_CPUS` 0/300 in us-central1 and us-east4 | `compute project-info describe`, `regions describe` | The controller fits without a quota request and does not touch the C3 quota the metal hosts need. |
| **Nothing named `p3-` exists** now: no instance, no disk | `instances list --filter='name~^p3-'`, `disks list` | A clean start; the teardown check (section 10) is "this is empty again". |
| The archive bucket `gs://siriusmedai-gpu-benchmark-archive` (US-CENTRAL1, public access prevention enforced, uniform access, soft delete 7 days) has one prefix, `gpu-spark-polars-study/` | `storage ls`, `buckets describe` | Paper 1's results are there. There is no Paper 3 prefix yet; the Mac can write one (section 8.4). |
| Google: `--max-run-duration` "starts when the VM reaches RUNNING", restarts from zero after a stop and start, can be changed after creation only "first stop your VM, then update"; the action is `STOP` or `DELETE`; "automatic termination might take up to 30 seconds longer" | docs.cloud.google.com `limit-vm-runtime` | STOP is a documented action. The page says nothing about bare metal, positively or negatively: **STOP on `c3-*-metal` is unverified** (the create call validates it synchronously, so a rejection costs nothing). Extending the limit of a running host is not possible. |
| Google: a stopped VM "continue[s] to incur charges for any disks... that remain attached" | docs `stop-start-instance` | A stopped metal host costs its disk only (section 4.2). |
| Lambda API: requests "generally limited to one request per second", launch "one request per 12 seconds"; API keys can be created per identity with an optional `expires_in`, and revoked; the key is shown once | docs.lambda.com/api/cloud (the SSH-key and launch sections were not in the part I could read) | The controller gets its **own, revocable, expiring key**. `ssh_key_names` takes exactly one key (from memory, **unverified**), so the controller needs its own registered SSH key too. |

## 4. The options

| | A. everything on the controller | **B. split (recommended)** | C. other |
|---|---|---|---|
| Lambda drivers | controller | controller | |
| GCP drivers | controller, as a conditioned service account | **Mac**, with provider-side STOP | |
| Controller's GCP identity | a service account with 3 conditional bindings + subnet bindings | **none** | |
| IAM changes in the shared project | project policy to version 3, 1 service account, 2 custom roles, 3+ bindings | **none** | |
| Can the controller modify a non-p3 resource? | not if every condition works as intended (unverified for create); can **read** every instance's metadata (list cannot be conditioned) | **no, by construction** | |
| Mac outage during a GCP launch | the campaign continues | the host runs on until the lease, then **stops**; disk and data kept; cost capped at the lease | |
| Mac outage during a Lambda launch | nothing | nothing | |
| New moving parts | many | 4 small scripts, 3 unit files | |

### 4.1 Option A in detail (not recommended; here so the author can choose it)

What can and cannot be conditioned on a name (Google's IAM Conditions pages, read 2026-11-22): Compute Engine **Instance, Disk, Image, Snapshot,
Firewall, InstanceTemplate** (and load-balancer pieces) accept `resource.type`/`resource.name` conditions; the name is
`projects/<project-id>/zones/<zone>/instances/<name>`; `startsWith()`, `endsWith()` and `extract()` exist, wildcards do not. **Subnetwork, Network,
Address, MachineType, Zone and Operation are not on the list.** List methods are checked against the parent, not a name. What the drivers call:

| Call (tools) | Permission | Name-conditionable? |
|---|---|---|
| `instances create` | `compute.instances.create`, `compute.disks.create` (boot disk named like the instance), `compute.instances.setLabels`, `setMetadata` | yes if IAM evaluates the new resource's name (**unverified**; needs a canary) |
| `instances describe/delete/stop/start/set-scheduling`, `compute ssh` | `compute.instances.get/delete/stop/start/setScheduling`, `compute.instances.osAdminLogin` | yes (existing resource) |
| `instances list --filter` (`gcp_instance_listed`, both watchdogs) | `compute.instances.list` | **no**: project-wide, and `list` returns whole resources including their metadata (startup scripts, environment) |
| waiting for an operation, machine type, zone, region, project | `compute.zoneOperations.get`, `machineTypes.get`, `zones.get`, `regions.get`, `projects.get` | no (read-only, harmless) |
| the NIC | `compute.subnetworks.use`, `useExternalIp` | no by name; a binding **on one subnet** works, and the campaign hosts would need their own VPC for it to mean anything |

The bindings would be (on `siriusmedai`, all with `request.time < timestamp("<campaign end>")` so that they lapse by themselves):
(1) a custom role of the instance and disk mutations above, condition
`(resource.type == "compute.googleapis.com/Instance" && resource.name.extract("/instances/{n}").startsWith("p3-")) || (resource.type == "compute.googleapis.com/Disk" && resource.name.extract("/disks/{n}").startsWith("p3-"))`;
(2) a custom role of the read-only calls (including `compute.instances.list`) with only the time condition; (3) `roles/compute.networkUser` on the one subnet.
```text
service account   p3-campaign-driver@siriusmedai   (attached to the controller VM; no key file: the metadata server issues its tokens)
custom role       p3CampaignInstances = compute.instances.{create,delete,get,setLabels,setMetadata,setScheduling,start,stop,osAdminLogin}
                                        compute.disks.{create,delete,get,setLabels,use}
custom role       p3CampaignShared    = compute.instances.list, compute.disks.list, compute.zoneOperations.{get,list}, compute.globalOperations.get,
                                        compute.machineTypes.get, compute.zones.get, compute.regions.get, compute.projects.get,
                                        compute.networks.get, compute.subnetworks.get, compute.images.getFromFamily
binding 1         serviceAccount:p3-campaign-driver  ->  p3CampaignInstances   when  <the name condition above> && request.time < timestamp("<end>")
binding 2         serviceAccount:p3-campaign-driver  ->  p3CampaignShared      when  request.time < timestamp("<end>")
binding 3         serviceAccount:p3-campaign-driver  ->  roles/compute.networkUser  on the subnet of a dedicated campaign VPC (resource-level policy)
```
The permission sets are the ones the tools' calls need (read off `roles/compute.instanceAdmin.v1`); the exact minimum is what the canary's 403 messages would show.
Two things stay open that the author should weigh: **the controller could read the metadata of every instance in the project**, and a version 3
policy with conditional bindings is read-modify-written by every other administrator and by the project's automation: Google's documentation says that a version 1 write without the etag "deletes all of the conditions", which for these bindings turns a
guarded grant into an unguarded one. If A is chosen, the canary is mandatory: create `p3-canary-...` (must succeed) and `zz-canary-not-ours` with
`--max-run-duration=5m --instance-termination-action=DELETE` (must be refused with 403) as the service account, before any campaign host.

### 4.2 Option B in detail: what STOP buys and costs

`P3_GCP_TERMINATION_ACTION=STOP` makes the planner's create command carry `--instance-termination-action=STOP`; `p3_launch.sh` arms the Mac
watchdog in **stop mode** (it must never delete what the provider keeps) and records `termination_action` in the launch record. At the lease the
host stops, compute stops billing, the boot disk (which holds the data, `--boot-disk-size=800GB`) stays.

| Item | Cost (list prices in `docs/paper3-evidence/prices-20261111/`) |
|---|---:|
| Stopped host, disk as provisioned (800 GiB Hyperdisk Balanced, 16,000 IOPS, 1,000 MBps) | $163.40/month = **$5.37/day** (space $64.00 + IOPS $65.00 + throughput $34.40) |
| Same disk after `gcloud compute disks update NAME --provisioned-iops=3000 --provisioned-throughput=140` (the baseline, which is free) | $64.00/month = **$2.10/day** |
| Compute of a stopped host | $0 |
| Worst case for a Mac outage at hour h of C-GCP launch 1 | $12.70 x (21 - h) until the stop, then the disk per day; with DELETE the same compute cost and the data gone |

Recovering the data of a stopped host: start it again if the zone has capacity (the clock restarts: another full lease), or snapshot the disk
(`gcloud compute disks snapshot`), make a disk from it and mount it on a small VM that takes Hyperdisk Balanced (an E2 does not; a `c3-standard-4` or
`n4-standard-2` does). Both are for the lead to do by hand; neither has been tried. **Unverified:** that a `c3-*-metal` accepts STOP at all
(the P3 probe verified DELETE; the create call rejects an unsupported value synchronously, in which case unset the variable and accept DELETE, with
the data copied by the Mac while it is up), and that the provider's stop is enforced at expiry on metal (neither STOP nor DELETE has been observed
firing; the Mac watchdog in stop mode is the second layer).

### 4.3 Option C: what else came out

1. **A stateless reaper** (`tools/paper3_campaign/p3_reaper.sh`): the per-launch Lambda watchdog is a `nohup` process, and a reboot of whatever
   machine runs it kills it. The reaper needs nothing but the Lambda API: it reads the lease from the **name** of every `p3-` instance
   (`...-expires-<YYYYMMDDTHHMMZ>`), and terminates what is more than 30 minutes past it (or past `expires_utc` of its launch record, if a person
   extended the lease and that is later). A systemd timer runs it every 5 minutes and 2 minutes after boot.
2. **A separate GCP project for the controller** (author's decision, section 12): the Lambda key on the controller is readable by everyone who can
   administer `siriusmedai` (section 3). Section 13 says what it takes, with the read-only checks already made.
3. **A Lambda CPU instance as the controller** is possible in principle (no GCP involvement at all) but Lambda opens port 22 to the world by default,
   has no IAP or OS Login, and puts the controller in the account it protects: not pursued.
4. **Hardening the campaign hosts** (not the controller): `--no-service-account --no-scopes` (no Editor token on a box that runs `pip` and
   `apt` from the Internet) and `block-project-ssh-keys=TRUE`. They are the planner's default now (5.3), so the runbook's generated block shows them.

## 5. The recommendation

### 5.1 Architecture

```
 Mac (operator)                              GCP project siriusmedai                         Lambda
 ------------------------------              ------------------------------------         --------------------
 p3_ctl.sh status|logs|pull|start|stop  -->  IAP tunnel, OS Login  -->  p3-controller-...  --> 1x H100, A100, GH200, B200 (each its own
 GCP launches: p3_launch.sh c-gcp|c-vm       (VPC p3-controller-net,    no service account    lease name, no provider limit)
   P3_GCP_TERMINATION_ACTION=STOP            ingress: IAP range only)   systemd:
 p3_driver.sh for them (caffeinate)                                       p3-driver@<lease>  per launch
 gcloud compute ssh -> campaign hosts                                     p3-reaper.timer    every 5 min
 round data and ledger: pulled/verified                                   ~p3ops/: key, ledger, repo @ CAMPAIGN
```

### 5.2 Who does what

| | Mac | Controller |
|---|---|---|
| Lambda: price snapshot, `p3_launch.sh`, driver, teardown | no | **yes** (`p3ops`) |
| Lambda: watchdog at the lease | no | yes (as before, `nohup`) + the reaper timer |
| GCP: launch, driver, watchdog, teardown | **yes**, as in the runbook | no (no GCP identity) |
| Lambda API key | the Mac keeps its own (hourly read-only audit; emergency terminate) | its own, revocable key |
| SSH key to Lambda hosts | `~/.ssh/lambda_key22` stays here | a **new** key pair generated on the controller (its private half never leaves it) |
| Round data | pulled from the controller and verified | lands here first (`~p3ops/study/rerun/paper3/<host>/launch<N>/`) |

### 5.3 The GCP launches from the Mac

Unchanged from the runbook except for one environment variable of `p3_launch.sh` (a dry run prints the exact `gcloud` command):

```bash
export P3_GCP_TERMINATION_ACTION=STOP
```

What every C-GCP and C-VM create command now carries, with no variable (the generated block of the runbook's section 4 shows it): **`--no-service-account --no-scopes`**
and **`--metadata=block-project-ssh-keys=TRUE`**. The launch record gains `access` (`service_account: null`, `block_project_ssh_keys: true`, `os_login`) so the
host's identity settings are evidence. Access to the host is then the key `gcloud compute ssh` writes into the instance's own metadata (the guest agent applies it within
a minute; the driver's ssh-ready loop waits up to 15 minutes); `P3_GCP_OS_LOGIN=1` adds `enable-oslogin=TRUE` instead (**untested on a `c3-highmem-192-metal`**; the metadata-key path is
the one the probe used, minus the project-wide keys). `P3_GCP_EXTRA_FLAGS` (long options only) may not repeat or undo a flag the planner sets: `--zone`, `--machine-type`,
`--max-run-duration`, `--instance-termination-action`, `--labels`, `--project`, the disk flags, `--service-account`, `--scopes`, `--no-service-account`, `--no-scopes`, `--metadata`,
`--metadata-from-file`. The knobs are read by the command line only, never by `p3_plan.py docs`, so the generated blocks do not depend on the environment.

## 6. The controller

| Property | Value |
|---|---|
| Name | `p3-controller-owner-ejiod-expires-20261206t2100z` (now + 14 days; GCP lower case; 48 characters; satisfies `probe_power.lease_status`) |
| Labels | `project=paper3-energy`, `owner=ejiod`, `purpose=controller`, `expires=<the same stamp>` |
| Type, zone | `e2-small` (2 shared vCPUs, 2 GiB), `us-central1-a` (the Iowa column of the archived E2 price table, the first one; the us-east4 metal quota is not involved). Scale to `e2-medium` ($0.0335/h) if five drivers plus a large pack ever swap |
| Image, disk | `ubuntu-2404-lts-amd64`, 100 GB `pd-balanced`, **`--no-boot-disk-auto-delete`** (a deleted VM must not take the ledger and the round data with it; the teardown deletes the disk by name) |
| Cost | e2-small $0.016752855/h; disk $0.10/GB-month (archived disk page); ephemeral IPv4 $0.005/h (**from general knowledge, not in the archived pages**). 5 days: $2.01 + $1.64 + $0.60 = **$4.25**; 14 days $5.63 + $4.60 + $1.68 = $11.9; monthly $12.23 + $10.00 + $3.65 = $25.9. Egress to the Mac: a few GB at about $0.12/GB (unverified; round sizes are not known until the pathfinder's round 1 is copied) |
| Provider-side limit | `--max-run-duration=336h --instance-termination-action=STOP`: a leak guard, not a schedule (if the campaign slips past it, the controller stops: raise it before, by stopping and `set-scheduling`) |
| Network | custom VPC `p3-controller-net`, subnet `p3-controller-subnet` (10.250.0.0/28, us-central1, private Google access), firewall `p3-controller-allow-iap-ssh` (ingress tcp:22 from 35.235.240.0/20 to tag `p3-controller`). **No other ingress rule**, so Google's implied deny covers the rest; the ephemeral external address is for egress only (Lambda API, GitHub, apt). 4 of the 30 networks are used today |
| Identity | **`--no-service-account --no-scopes`**: the metadata server hands out no token, so nothing on the machine can call a Google API, and no IAM binding names it |
| Access | IAP TCP forwarding + OS Login: `gcloud compute ssh --tunnel-through-iap` as the author (Owner has `iap.tunnelInstances.accessViaIAP` and `compute.instances.osAdminLogin`); `enable-oslogin=TRUE`, `block-project-ssh-keys=TRUE` so the three shared project keys grant nothing; Shielded VM on |
| Users and files | root: the OS Login admin via `sudo`; `p3ops`: no password, no sudo, home mode 0700, owns `~/.config/lambda/api_key` (0600), `~/.ssh/lambda_key22` (0600), `~/p3ledger/` (records and `ledger.jsonl`, 0700), `~/study/` (detached checkout of the campaign commit) |
| Software | git, jq, curl, perl + libdigest-sha-perl (`shasum`), python3 (the planner is standard-library only; it ran under Python 3.12.3), openssh-client, tmux. No `caffeinate` (the tools skip it when absent) |
| Repository | `p3_controller_setup.sh` clones `https://github.com/ejiod2025/gpu-spark-polars-study.git` and checks out exactly the commit in the instance metadata attribute `p3-commit` (the **campaign commit**, pushed). It refuses to move a checkout with local changes or under a running driver |
| Keep-alive | `p3-driver@<lease>.service`: `Restart=on-failure`, `RestartSec=60`, at most 5 starts in 15 minutes, `RestartPreventExitStatus=2 10 11 20 21 30` (the driver's own exits: usage, allow-list review, stopped after bring-up, held, host gone, stopped by rule). Those leave the unit **failed** (visible in `p3_ctl.sh status`) and are never restarted; a crash, an OOM kill or a reboot is. `p3-reaper.timer`: 2 min after boot, then every 5 min; the service idles until the API key file exists |
| Why not tmux | tmux does not survive a reboot and has no restart policy; it is installed for interactive work only (`sudo -u p3ops -H tmux new -As p3`) |
| Data | the driver copies and verifies every round from the host to the controller (as it did to the Mac); `p3_ctl.sh pull HOST N` brings a finished launch to the Mac and checks it against `LAUNCH-SHA256SUMS` |

## 7. Step list

Every command that creates, changes or deletes something is for **the lead to run after the author approves**; the read-only ones are marked. Every
`gcloud` command names its project (`--project=$CTL_PROJECT`, so the ambient default cannot redirect it) and none names a resource that is not `p3-`.

```bash
CTL_PROJECT=siriusmedai                       # the project the controller lives in (section 13: a project of its own)
export P3_CONTROLLER_PROJECT=$CTL_PROJECT      # read by tools/paper3_campaign/p3_ctl.sh; the campaign tools keep P3_GCP_PROJECT
CAMPAIGN=<the 40-hex campaign commit, pushed to origin, containing tools/paper3_campaign/controller/ and p3_controller_setup.sh>
STAMP=$(date -u -v+14d +%Y%m%dt%H%Mz)          # macOS; on Linux: date -u -d '+14 days' +%Y%m%dt%H%Mz
CONTROLLER=p3-controller-owner-ejiod-expires-$STAMP
ZONE=us-central1-a
REGION=us-central1
NET=p3-controller-net
SUBNET=p3-controller-subnet
FW=p3-controller-allow-iap-ssh
```

**7.1 Before anything (read-only).** Confirm the names are free and the commit is public:
```bash
gcloud compute instances list --project=$CTL_PROJECT --filter='name~^p3-' --format='value(name)'      # empty
gcloud compute networks list --project=$CTL_PROJECT --filter='name~^p3-' --format='value(name)'       # empty
git ls-remote origin "$CAMPAIGN"                                               # the commit is on origin
```

**7.2 The network** (3 resources, all `p3-controller-*`, none shared with another workload):
```bash
gcloud compute networks create $NET --project=$CTL_PROJECT --subnet-mode=custom --bgp-routing-mode=regional
gcloud compute networks subnets create $SUBNET --project=$CTL_PROJECT --network=$NET --region=$REGION --range=10.250.0.0/28 --enable-private-ip-google-access
gcloud compute firewall-rules create $FW --project=$CTL_PROJECT --network=$NET --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=p3-controller
```

**7.3 The VM** (the bootstrap script is its startup script; it holds no secret):
```bash
gcloud compute instances create "$CONTROLLER" --project=$CTL_PROJECT --zone=$ZONE --machine-type=e2-small \
    --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size=100GB --boot-disk-type=pd-balanced --no-boot-disk-auto-delete \
    --network=$NET --subnet=$SUBNET --tags=p3-controller \
    --no-service-account --no-scopes \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --maintenance-policy=MIGRATE --max-run-duration=336h --instance-termination-action=STOP \
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE,p3-commit=$CAMPAIGN \
    --metadata-from-file=startup-script=tools/paper3_campaign/p3_controller_setup.sh \
    --labels=project=paper3-energy,owner=ejiod,purpose=controller,expires=$STAMP
```
Wait about two minutes, then (read-only):
```bash
export P3_CONTROLLER=$CONTROLLER P3_CONTROLLER_ZONE=$ZONE
tools/paper3_campaign/p3_ctl.sh ssh 'sudo journalctl -u google-startup-scripts.service -n 12 --no-pager | cut -c1-160'      # ends with "p3_controller_setup: done"
```
The log lines `api_key: MISSING` and `lambda_key22: MISSING` are expected until 7.4.

**7.4 The two secrets** (the author does the dashboard parts; nothing is typed on a command line or printed):

* *Lambda API key.* In the Lambda dashboard create a **new** API key named `p3-controller` (set an expiry if the dashboard offers one: the API has `expires_in`); this key is revoked at the end, the Mac's key is untouched. Then, with the key in the clipboard:
  ```bash
  pbpaste | tr -d '\n' | gcloud compute ssh "$CONTROLLER" --project=$CTL_PROJECT --zone=$ZONE --tunnel-through-iap --quiet \
      --command 'sudo -u p3ops -H sh -c "umask 077 && cat > \$HOME/.config/lambda/api_key"'
  ```
  (The key travels on stdin only. To reuse the Mac's key instead, replace `pbpaste` with `cat ~/.config/lambda/api_key`; the pipe keeps it off the terminal.)
* *SSH key to the Lambda hosts.* Generate it on the controller so the private half never moves, and register the public half at Lambda as `p3-controller-key`:
  ```bash
  tools/paper3_campaign/p3_ctl.sh ssh "sudo -u p3ops -H ssh-keygen -q -t ed25519 -N '' -C p3-controller -f /home/p3ops/.ssh/lambda_key22 && sudo cat /home/p3ops/.ssh/lambda_key22.pub"
  tools/paper3_campaign/p3_ctl.sh ssh "echo 'P3_LAMBDA_SSH_KEY_NAME=p3-controller-key' | sudo tee -a /etc/p3/operator.env"
  ```
  (the line in `operator.env` is what `p3_launch.sh` reads through `P3_LAMBDA_SSH_KEY_NAME`; the file is created commented.)
* Run `sudo google_metadata_script_runner startup` (or reboot) once more: the report must say both files are present with mode 600.

**7.5 Acceptance, before any Lambda launch** (all but the reboot are read-only):
```bash
tools/paper3_campaign/p3_ctl.sh ssh 'sudo -u p3ops -H bash -c "cd ~/study && git rev-parse HEAD && python3 tools/paper3_campaign/p3_plan.py check-lock >/dev/null && echo lock-ok && tools/paper3_probes/lambda.sh list; tools/paper3_campaign/p3_reaper.sh"'
tools/paper3_campaign/p3_ctl.sh ssh 'systemctl is-enabled p3-reaper.timer; systemctl list-timers p3-reaper.timer --no-pager'
tools/paper3_campaign/p3_ctl.sh ssh 'sudo reboot'          # then wait 2 minutes and run the line above again: the timer is back and the checkout is at the campaign commit
```
The first line must print the campaign commit, `lock-ok`, an empty list of our Lambda instances and a reaper pass of 0 instances. The reaper and the
units have been run under a real systemd; **what has not been run is the controller against the real Lambda API** (the first `lambda.sh list` is that test, and it is read-only).

**7.6 A Lambda launch from the Mac, step by step** (the runbook's section 4 with the work on the controller):
```bash
tools/paper3_campaign/p3_ctl.sh ssh                                  # a shell as the OS Login user; then:  sudo -u p3ops -H -i   and   tmux new -As p3
#   on the controller, as p3ops, in ~/study:
tools/paper3_campaign/p3_price_snapshot.sh --out rerun/paper3/snapshots/$(date -u +%Y%m%dT%H%MZ)-h100-l1 --yes
tools/paper3_campaign/p3_launch.sh h100 1 --place us-south-2 --commit $CAMPAIGN --price-snapshot rerun/paper3/snapshots/<that directory>            # dry run
tools/paper3_campaign/p3_launch.sh h100 1 --place us-south-2 --commit $CAMPAIGN --price-snapshot rerun/paper3/snapshots/<that directory> --yes     # launches
#   back on the Mac:
tools/paper3_campaign/p3_ctl.sh start p3-campaign-h100-l1-owner-ejiod-expires-<stamp>                  # enables and starts the driver unit
tools/paper3_campaign/p3_ctl.sh start p3-campaign-h100-l1-owner-ejiod-expires-<stamp> --stop-after-bringup     # the H100 pathfinder's pause (driver exit 11); run `start LEASE` without options to continue
```
`--credit-funded` defaults to yes for Lambda. The driver's options live in `/etc/p3/driver-<lease>.env` (`start` writes them; an empty option list clears them).

**7.7 A GCP launch from the Mac** is the runbook's section 4 verbatim with the `export` of 5.3 and `caffeinate` as before; the identity and ssh hardening comes from the planner.

## 8. Operating it

**8.1 Watching (all read-only, from the Mac).**
```bash
tools/paper3_campaign/p3_ctl.sh status       # every open launch: lease, stages, the QA view (gate outcomes only); the p3-* units; a report-only reaper pass
tools/paper3_campaign/p3_ctl.sh logs 80      # the drivers' and the reaper's journal
tools/paper3_campaign/p3_reaper.sh           # from the Mac's own key: the same lease check, report only (exit 3 if something is overdue)
```
A unit that says `failed` with `Result=exit-code ExecMainStatus=20` is a driver that **held for a person**: read the driver's log line, as in the runbook's table of exit codes.
The Mac-side reaper pass is report-only on purpose: a lease extended on the controller lives in the controller's ledger, which the Mac cannot see.

**8.2 Blinding.** Nothing here reads a study value: `p3_ctl.sh status` runs the whitelisted QA view, `pull` and `ledger` move files without opening them, `p3_ctl.sh logs` shows only the driver's own lines, which never echo a stage's log line and show the bring-up only through `tools/paper3_campaign/p3_bringup_view.py` (step results, `[FAIL]` lines without times, the preflight verdict). The one exception is the log of a stage that failed and held its driver: `p3_ctl.sh open-log LEASE LOGNAME [LINES]` opens exactly that and records the opening (runbook section 8). The same rule as the runbook's section 8 applies to anyone who logs in to the controller.

**8.3 After each finished launch.**
```bash
tools/paper3_campaign/p3_ctl.sh pull h100 1            # copies rerun/paper3/h100/launch1 to this repository's rerun/ and checks LAUNCH-SHA256SUMS
tools/paper3_campaign/p3_ctl.sh ledger                 # the controller's launch records and ledger.jsonl into ~/p3ledger-controller (P-BILLED inputs; they hold hosts' addresses)
tools/paper3_campaign/p3_ctl.sh snapshots              # the price snapshots taken on the controller (design 2.6)
```
`pull` refuses a launch without its `LAUNCH-SHA256SUMS` (the driver writes it last), never trusts a manifest from an earlier copy, and does not delete anything on the controller.

**8.4 The second copy** (the runbook's section 3 item 6). From the Mac, with the author's own credentials (no key on the controller): `gcloud storage rsync --recursive rerun/paper3 gs://siriusmedai-gpu-benchmark-archive/gpu-spark-polars-study/paper3-campaign/` writes only under a new prefix of the existing private bucket
(Nearline, soft delete 7 days). This is **outward-facing**: the author approves it first, and the blinding rule applies (copy, do not open).

## 9. When something fails

| Event | What happens | What a person does |
|---|---|---|
| The Mac sleeps, reboots or is off | **Lambda launches: nothing changes.** A GCP launch: its driver and watchdog stop; the host runs until `--max-run-duration`, then stops with the disk kept | Wake the Mac, run the driver again (it attaches). After a long outage check the host with `gcloud compute instances list --filter='labels.project=paper3-energy'` |
| The controller reboots (maintenance, kernel update, `reboot`) | Driver units and the reaper timer come back at boot; each driver attaches to the stages still running on its host. The per-launch watchdog (`nohup`) is lost; the reaper (30 min grace) is the backstop | Nothing; `p3_ctl.sh status` shows the units `active` |
| A driver crashes | systemd restarts it after 60 s (5 times in 15 minutes at most) | If it keeps failing, `p3_ctl.sh logs` |
| A driver ends by its own code (10, 11, 20, 21, 30) | The unit is `failed` and stays so; the Lambda host keeps billing (exit 10, 11, 20) | The runbook's section 4 step 5 and section 9 |
| The controller VM is stopped or deleted | A Lambda host bills until terminated; the data on the disk is kept (`--no-boot-disk-auto-delete`) | From the Mac: the host's id is in `p3_ctl.sh ledger` (if the VM is up) or in the Lambda dashboard; `tools/paper3_probes/lambda.sh terminate ID` refuses anything not named `p3-` |
| Controller and Mac both down | Lambda: unbounded until someone looks. GCP: capped by the provider | The only residual risk of B; check the Lambda dashboard before leaving both machines idle |
| The reaper terminates something it should not | Only an instance named `p3-...-expires-T` more than 30 minutes past T and past its record's `expires_utc` | Extend a lease **in the record on the controller** (`record_set`, the runbook's "Extending a lease") **before** it lapses; the name of a Lambda host cannot be changed |

## 10. Teardown at the end of the campaign

1. Every launch has `terminate_confirmed_utc` (`p3_ctl.sh status` lists only open ones; it must list none) and `lambda.sh list` on the controller shows no `p3-` instance of ours.
2. Pull and verify everything (8.3), make the second copy (8.4), and only then go on.
3. **Revoke the controller's Lambda API key and delete its SSH key `p3-controller-key`** in the Lambda dashboard (the author). The Mac's key was never touched.
4. Delete the controller and its network, in this order:
   ```bash
   gcloud compute instances delete "$CONTROLLER" --project=$CTL_PROJECT --zone=$ZONE --quiet
   gcloud compute disks delete "$CONTROLLER" --project=$CTL_PROJECT --zone=$ZONE --quiet          # kept by --no-boot-disk-auto-delete
   gcloud compute firewall-rules delete $FW --project=$CTL_PROJECT --quiet
   gcloud compute networks subnets delete $SUBNET --project=$CTL_PROJECT --region=$REGION --quiet
   gcloud compute networks delete $NET --project=$CTL_PROJECT --quiet
   ```
   (If the controller has a project of its own, section 13: `gcloud projects delete $CTL_PROJECT` replaces all five; Google keeps a deleted project for 30 days.)
5. Confirm (read-only), each with `--project=$CTL_PROJECT`: `gcloud compute instances list --filter='name~^p3-'`, `gcloud compute disks list --filter='name~^p3-'`, `gcloud compute networks list --filter='name~^p3-'`, `gcloud compute firewall-rules list --filter='name~^p3-'` are all empty (and the same two instance and disk lists in `siriusmedai`, for the campaign hosts), and the stopped campaign hosts' disks (4.2) have been copied and deleted.

## 11. What changed in the tools, and how it was checked

All of it is opt-in or new; with nothing set, every existing command, every generated runbook block and every existing test is unchanged.

| File | Change |
|---|---|
| `tools/paper3_campaign/p3_plan.py` | **GCP hosts are created with `--no-service-account --no-scopes --metadata=block-project-ssh-keys=TRUE` always** (the generated block of `docs/PAPER3_RUNBOOK.md` was regenerated; nothing else in it changed). `launch_spec` also takes `termination_action` (DELETE default, STOP), `os_login`, `extra_flags` (checked against the planner's own flags, identity and metadata included) and `ssh_key_name`; the `launch-spec` command line reads `P3_GCP_TERMINATION_ACTION`, `P3_GCP_OS_LOGIN`, `P3_GCP_EXTRA_FLAGS`, `P3_LAMBDA_SSH_KEY_NAME`; `docs` never does |
| `docs/PAPER3_RUNBOOK.md` | only the generated `launch c-gcp 1` block (`p3_plan.py docs ... --write`): three more flags |
| `tools/paper3_campaign/p3_gcp_watchdog.sh` | `P3_GCP_WATCHDOG_ACTION=stop`: stops (never deletes) a running host, leaves a stopped one alone; default `delete` as before |
| `tools/paper3_campaign/p3_launch.sh` | passes the watchdog action from the request; the record gains `termination_action` and `access` (service account, project ssh keys, OS Login) |
| `tools/paper3_campaign/p3_reaper.sh` | new: the stateless lease reaper (report only, or `--terminate`) |
| `tools/paper3_campaign/p3_ctl.sh` | new, Mac side: `status`, `logs`, `ssh`, `pull`, `ledger`, `snapshots`, `open-log`, `start`, `stop`; it can name only a `p3-controller-*` instance, only through the IAP tunnel, in the project of `P3_CONTROLLER_PROJECT` (not `P3_GCP_PROJECT`) |
| `tools/paper3_campaign/p3_controller_setup.sh` | new: the controller's bootstrap and startup script (idempotent; `--dry-run`) |
| `tools/paper3_campaign/controller/p3-driver@.service`, `p3-reaper.service`, `p3-reaper.timer` | new: the systemd units |
| `tools/paper3_campaign/p3_bringup_view.py` | new: the operator-facing view of a bring-up log, by allow-list: step results, `[FAIL]` lines with times redacted, the preflight `VERDICT` (design 7.3) |
| `tools/paper3_campaign/p3_driver.sh` | forces `P3_QUIET_LOG=1` and shows the bring-up log only through `p3_bringup_view.py`; the full logs are copied and archived unread; a stage that fails and holds the driver lists its log in `.held-logs` |
| `tools/paper3_campaign/p3_open_log.sh` | new: the one sanctioned way to read an archived log, only a held (failed) stage's, each opening written to `log-openings.log` and the ledger (the documented design 7.3 exception) |
| `tools/paper3_probes/p3_stage_lib.sh` | opt-in `P3_QUIET_LOG`: the progress line says the last log line is withheld instead of echoing it; unset (the probe drivers) it behaves as before |
| `tests/test_paper3_runbook_blinding.py` | new: 39 tests (the view's allow-list and redactions, a `[PASS]` line with a time never reaching the operator, the stage library's default and quiet progress line, the driver end to end on the fake host with timing lines in the bring-up and round logs, checked by mutation; the held-log path: only a failed stage's log opens, every opening is recorded, path tricks are refused) |
| `tests/test_paper3_controller.py` | new: 55 tests (planner knobs and the guard rails, launcher pass-through, watchdog stop mode, the reaper including extended leases, failed listings and failed terminations, the Mac helper's refusals and its checksum check, the units against the driver's real exit codes, the bootstrap dry run, and this document's file names, knobs and `gcloud` targets) |

Checked: the 55 + 39 new tests pass, and so do the 127 existing runbook and ops tests (`tests/test_paper3_runbook.py`, `tests/test_paper3_runbook_ops.py`) and the 44 probe-driver and watchdog tests; the units pass `systemd-analyze verify`; in an
Ubuntu 24.04 container (arm64) the bootstrap ran twice with identical results, built the layout and modes of section 6, moved the checkout between two commits, and
refused a checkout with local changes; under a **real systemd 255** in that container the reaper timer was enabled by the script, the reaper service ran as `p3ops` and requested the
termination of a long-expired fake instance, a stub driver was restarted after a crash with its options from `/etc/p3/driver-<lease>.env`, was **not** restarted after
exit 20 (state `failed`), and after a container restart (the reboot) the driver unit and the timer were running again. **Not checked:** anything against a real cloud; STOP on a
metal host; OS Login on a metal host; the controller against the Lambda API; the recovery of a stopped host's disk; the IAM conditions of option A.

## 12. What the author must decide

1. **Option B, or A.** B is recommended; A adds the GCP drivers to the controller and the risks of 4.1.
2. **Where the controller lives.** In `siriusmedai` every project administrator can read the Lambda key off it; a separate project (section 13) avoids that, bills to the billing account the author already sees, and makes the teardown one command.
3. **STOP on the campaign hosts** (`P3_GCP_TERMINATION_ACTION=STOP`): accept the disk cost of 4.2 ($2 to $5 per stopped host-day until copied and deleted) for the data of a Mac outage.
4. **The hardening of the GCP campaign hosts** (`--no-service-account --no-scopes`, `block-project-ssh-keys=TRUE`) is now the planner's default and appears in the runbook's generated block; it changes how the host is created, not anything measured. `P3_GCP_OS_LOGIN=1` is optional and untested on metal.
5. **The runbook's "Extending a lease" for GCP** (section 9 there): Google's documentation says the limit of a running VM cannot be changed without stopping it. Until that is tried on a metal host, size the lease (the planner's `lease_h`) rather than count on extending it.
6. **The writes to `gs://siriusmedai-gpu-benchmark-archive`** (8.4) and the Lambda dashboard steps (7.4, 10.3).

## 13. The controller in a separate project

Why: the Lambda API key is account-wide, and on a VM in `siriusmedai` every principal that administers the project (section 3) can get a shell on it and read the
key. A project of its own has no such population: its IAM policy is the author as Owner plus Google's service agents, and nothing flows in from `siriusmedai`
(no organisation, so no inherited policy either). The controller itself, its network, its scripts and its cost are the same; only the project changes.

**Read-only checks made on 2026-11-22**

| Check | Result |
|---|---|
| Does the author's account belong to an organisation? | `gcloud organizations list` is empty: a new project has **no parent** (no organisation policy, no folder) |
| Which projects does the author see? | 3 in all; none starts with `p3-` |
| Is billing set up, and can the author attach a new project to it? | `siriusmedai` has billing enabled. The author sees **exactly one open billing account, the one `siriusmedai` uses**, and holds `billing.resourceAssociations.create` on it (`testIamPermissions`, REST; nothing changed). A new project would therefore bill to the same account and credits, which is also the answer to "is GCP credit-funded" that the runbook still needs (`--cost-credit-funded`) |
| May the author create a project without a parent? | **Not testable read-only** (the permission is checked when creating); the project-creation quota is not readable either |
| Quotas for one `e2-small` | It needs 2 CPUs in one region, 1 instance, 1 in-use address, 100 GB of disk, 1 network and 1 firewall rule. The default quotas of a billed project are far above that (**unverified numbers**: they cannot be read before the project exists); no increase is expected. The C3 quotas the metal hosts need stay in `siriusmedai`, where the campaign hosts stay |
| APIs | `compute`, `iap` and `oslogin` must be enabled in the new project (`siriusmedai` has all three); enabling `compute` creates the project's own `default` network with the usual permissive rules (22, 3389, icmp from anywhere). No VM uses it; the controller has its own VPC, and the empty `default` network can be deleted |

**What changes in the step list.** Only the project. Create it, link billing, enable the three APIs, then run sections 7.1 to 7.7 with `CTL_PROJECT` set to it
(every `gcloud` command of section 7 and 10 already carries `--project=$CTL_PROJECT`, and `P3_CONTROLLER_PROJECT` is what `p3_ctl.sh` reads). The campaign tools keep
`P3_GCP_PROJECT` (default `siriusmedai`), so the campaign hosts are launched where their quota is, and a controller in another project cannot redirect them.

```bash
CTL_PROJECT=p3-controller-a1b2c3
BILLING=<the open billing account: gcloud billing accounts list>
gcloud projects create $CTL_PROJECT --name='Paper 3 controller' --labels=project=paper3-energy,owner=ejiod
gcloud billing projects link $CTL_PROJECT --billing-account=$BILLING
gcloud services enable compute.googleapis.com iap.googleapis.com oslogin.googleapis.com --project=$CTL_PROJECT
```

Choose your own suffix: a project ID is global (6 to 30 lower-case characters, digits and hyphens) and **can never be reused, even after the project is deleted**. For
`siriusmedai`'s sake nothing else changes: no IAM binding, no service account, no firewall rule or network there. **Teardown** is one command, `gcloud projects delete $CTL_PROJECT`, after the pulls
and the Lambda-side revocations of section 10; Google keeps a deleted project for 30 days. **Approvals needed:** the project creation and the billing link are the author's; both are outside what this
document has been able to try.
