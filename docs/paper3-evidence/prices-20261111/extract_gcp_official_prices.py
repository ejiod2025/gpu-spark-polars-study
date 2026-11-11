#!/usr/bin/env python3
"""Re-derive gcp-official-prices.json from the archived Google Cloud pricing pages.

Run with the interpreter in isolated mode, from anywhere:

    python3 -I extract_gcp_official_prices.py gcp-official/general-purpose.html.gz \
        gcp-official/disks-image-pricing.html.gz > gcp-official-prices.json

Both pages render their tables from a JSON-like data block embedded in the HTML (one table per region and per
Hourly/Monthly toggle); the visible <table> only shows the default region. This script reads the embedded block.
Standard library only; the pages are untrusted data and are only parsed, never executed.
"""
import gzip
import hashlib
import json
import re
import sys

REGIONS = ("us-east4", "us-central1")
VM_NAME = re.compile(r"^(c3-(standard|highmem|highcpu)-\d+(-metal)?|c4-(standard|highmem)-288-metal)$")
DISK_NAME = re.compile(r"^Hyperdisk Balanced provisioned (space|IOPS|throughput)$")
HOURLY, MONTHLY = 3, 2  # the page's own usage-type codes (aria-label Hourly / Monthly)
# Tables are located by the first header cell; `Machine type` on the VM page, `Type` on the disk page.
HEADER_CELL = '[1,1,null,[null,[null,"\\u003cp\\u003e%s\\u003c/p\\u003e"]]'


def scan_array(text, start):
    """End index (exclusive) of the JSON array that opens at text[start]."""
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def cell_text(cell):
    try:
        return re.sub(r"<[^>]+>", "", cell[3][1][1])
    except (IndexError, TypeError):
        return None


def cells_in(node):
    """Rows (lists of cells) found anywhere under `node`."""
    rows = []

    def walk(item):
        if isinstance(item, list) and item and isinstance(item[0], list) and len(item[0]) >= 4 and item[0][:2] == [1, 1]:
            rows.append(item)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(node)
    return rows


def tables(page, header):
    """Yield (region_id, usage_type, header_texts, body_rows_texts) for every table whose first header cell is `header`."""
    marker = HEADER_CELL % header
    position = 0
    while True:
        position = page.find(marker, position)
        if position < 0:
            return
        brackets = 0
        while page[position - brackets - 1] == "[":
            brackets += 1
        for back in range(brackets + 1):  # the table array opens some brackets before the first header cell
            end = scan_array(page, position - back)
            if end is None:
                continue
            try:
                table = json.loads(page[position - back:end])
            except ValueError:
                continue
            if isinstance(table, list) and len(table) >= 4 and isinstance(table[2], str):
                region = re.search(r"\(([a-z0-9-]+)\)$", table[2])
                if region:
                    head = [cell_text(c) for c in cells_in(table[0])[0]]
                    body = [[cell_text(c) for c in row] for row in cells_in(table[1])]
                    yield region.group(1), table[3][0], head, body
                    break
        position += 1


def price(text):
    """'$12.701568 / 1 hour' -> (12.701568, '1 hour')"""
    match = re.match(r"^\$([0-9.]+) / (.+)$", text or "")
    return (float(match.group(1)), match.group(2)) if match else (None, None)


def vm_rows(page):
    found = {}
    for region, usage, head, body in tables(page, "Machine type"):
        if region not in REGIONS or usage not in (HOURLY, MONTHLY):
            continue
        for row in body:
            if len(row) != len(head) or not VM_NAME.match(row[0] or ""):
                continue
            entry = found.setdefault((region, row[0]), {"region": region, "machine_type": row[0], "vcpus": int(row[1]),
                                                       "memory_gib": float(row[2].split()[0])})
            amount, unit = price(row[3])
            key = "usd_per_hour" if usage == HOURLY else "usd_per_month"
            if key in entry and entry[key] != amount:
                raise SystemExit("conflicting default prices for %s %s" % (region, row[0]))
            entry[key] = amount
            if usage == HOURLY and len(row) == 8:  # the table variant with the discount columns
                for column, name in zip(row[4:], ("flex_cud_1y", "flex_cud_3y", "resource_cud_1y", "resource_cud_3y")):
                    entry["usd_per_hour_" + name] = price(column)[0]
    return sorted(found.values(), key=lambda e: (e["region"], e["machine_type"].split("-")[0], e["vcpus"], e["machine_type"]))


def disk_rows(page):
    found = {}
    for region, usage, head, body in tables(page, "Type"):
        if region not in REGIONS or usage not in (HOURLY, MONTHLY):
            continue
        for row in body:
            if len(row) != 2 or not DISK_NAME.match(row[0] or ""):
                continue
            amount, unit = price(row[1])
            if amount is None:  # tiered rows
                continue
            entry = found.setdefault((region, row[0]), {"region": region, "item": row[0]})
            key = "usd_per_hour" if usage == HOURLY else "usd_per_month"
            if key in entry and entry[key] != amount:
                raise SystemExit("conflicting prices for %s %s" % (region, row[0]))
            entry[key], entry["unit_" + ("hourly" if usage == HOURLY else "monthly")] = amount, unit
    return sorted(found.values(), key=lambda e: (e["region"], e["item"]))


def read_gz(path):
    with gzip.open(path, "rb") as handle:
        return handle.read()


def derive(vm):
    """C3 is billed per vCPU-hour and per GiB-hour. Solve both rates from the two metal shapes that differ only in
    memory, then check that every C3 row (VMs and metal, both regions) is reproduced to 1e-9 USD/hour."""
    metal = {r["machine_type"]: r for r in vm if r["region"] == REGIONS[0]}
    standard, highcpu = metal["c3-standard-192-metal"], metal["c3-highcpu-192-metal"]
    per_gib = round((standard["usd_per_hour"] - highcpu["usd_per_hour"]) / (standard["memory_gib"] - highcpu["memory_gib"]), 9)
    per_vcpu = round((standard["usd_per_hour"] - standard["memory_gib"] * per_gib) / standard["vcpus"], 9)
    c3 = [r for r in vm if r["machine_type"].startswith("c3-")]
    worst = max(abs(r["vcpus"] * per_vcpu + r["memory_gib"] * per_gib - r["usd_per_hour"]) for r in c3)
    fits = sorted((r["usd_per_hour"], r["machine_type"], r["vcpus"], r["memory_gib"]) for r in c3
                  if r["region"] == REGIONS[0] and not r["machine_type"].endswith("-metal") and r["vcpus"] >= 26 and r["memory_gib"] >= 200)
    return {"c3_usd_per_vcpu_hour": per_vcpu, "c3_usd_per_gib_hour": per_gib, "c3_rows_checked": len(c3),
            "max_abs_error_usd_per_hour": worst,
            "months_are_730_hours": all(abs(r["usd_per_hour"] * 730 - r["usd_per_month"]) < 0.01 for r in vm),
            "regions_identical": all(len({e["usd_per_hour"] for e in vm if e["machine_type"] == name}) == 1
                                     for name in {e["machine_type"] for e in vm}),
            "c3_vms_with_26plus_vcpu_and_200plus_gib_by_price": [dict(zip(("usd_per_hour", "machine_type", "vcpus", "memory_gib"), f)) for f in fits]}


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    vm_page, disk_page = read_gz(argv[1]), read_gz(argv[2])
    vm = vm_rows(vm_page.decode("utf-8", errors="replace"))
    json.dump({"schema": "gcp_official_prices/1", "regions": list(REGIONS), "usage_types": {"hourly": HOURLY, "monthly": MONTHLY},
               "inputs": [{"file": path, "sha256_of_decompressed": hashlib.sha256(data).hexdigest()}
                          for path, data in ((argv[1], vm_page), (argv[2], disk_page))],
               "vm": vm, "derived": derive(vm), "hyperdisk_balanced": disk_rows(disk_page.decode("utf-8", errors="replace"))},
              sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv)
