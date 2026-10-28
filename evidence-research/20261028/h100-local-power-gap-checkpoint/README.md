# Local power gap during isolated collection

The macOS power log records Low Power Sleep at 02:13:38 PDT on October 28, 2026
(09:13:38 UTC), using battery at 1%, followed by wake on AC attachment at
07:11:23 PDT (14:11:23 UTC). The original local controller and owned caffeinate
process survived. The current battery observation is AC/charging. Idle-sleep
prevention does not keep an exhausted battery running.

The local watcher last observed progress at 09:13:25 UTC and next observed the
existing remote supervisor at 14:11:52 UTC. Remote block 06's final session
succeeded at 11:15:26.689677 UTC; its backup control was created at
11:15:30.821446 UTC. The existing continuation controller verified that original
raw backup and acknowledged it at 14:12:51.939753 UTC. The supervisor then
continued its original schedule into block 07, with no restart or replacement
attempt. This is an operational handoff delay between measured blocks; no
action, process or parent timestamps are rewritten or corrected. Exact verified
blocks and final inference have their separate audits.

`POWER_EVENTS_SELECTED.txt` contains only the two verbatim relevant records
selected from `pmset -g log`; `WATCHER_EVENTS_SELECTED.jsonl` contains verbatim
bounded original event lines. They are selected observations, not claims to
preserve complete system logs. Original backup controls/receipts and the final
block 06 session metadata are copied unchanged. The profile log is a separately
captured 15-record observation; receipt 15 is block-07-before. The original full
journal remains in the immutable block 06 raw backup and its independent
checkpoint.

This record supplies operational pacing and host-history disclosure. It adds
no sessions, changes no scientific settings and establishes neither a
performance effect nor full-study/publication/retirement readiness.
