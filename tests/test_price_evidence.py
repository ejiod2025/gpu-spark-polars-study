"""The price and quota evidence for docs/PAPER3_INFRA.md must stay verifiable: hashes match, every file is listed, and the
official Google numbers re-derive from the archived pages."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "paper3-evidence" / "prices-20261111"
PRICES = EVIDENCE / "gcp-official-prices.json"
PAGES = ["gcp-official/general-purpose.html.gz", "gcp-official/disks-image-pricing.html.gz"]


def sums():
    entries = {}
    for line in (EVIDENCE / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        entries[name] = digest
    return entries


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_file_is_hashed_and_the_hashes_match():
    entries = sums()
    on_disk = {str(p.relative_to(EVIDENCE)) for p in EVIDENCE.rglob("*") if p.is_file()} - {"SHA256SUMS", "MANIFEST.md"}
    assert on_disk == set(entries)
    assert {name: sha256(EVIDENCE / name) for name in entries} == entries


def test_the_manifest_names_every_file():
    manifest = (EVIDENCE / "MANIFEST.md").read_text()
    for name in sums():
        assert name in manifest, name


def test_redacted_pages_carry_no_google_api_keys():
    import gzip
    import re
    for name in sums():
        if name.endswith(".gz"):
            assert not re.search(rb"AIza[0-9A-Za-z_\-]{35}", gzip.open(EVIDENCE / name, "rb").read()), name


def test_extractor_reproduces_the_committed_json():
    out = subprocess.run([sys.executable, "-I", str(EVIDENCE / "extract_gcp_official_prices.py"), *[str(EVIDENCE / p) for p in PAGES]],
                         capture_output=True, text=True, check=True, cwd=str(EVIDENCE.parent)).stdout
    fresh, kept = json.loads(out), json.loads(PRICES.read_text())
    for document in (fresh, kept):  # the input paths differ with the working directory; the hashes must not
        for item in document["inputs"]:
            item["file"] = Path(item["file"]).name
    assert fresh == kept


@pytest.fixture(scope="module")
def prices():
    return json.loads(PRICES.read_text())


def vm(prices, region, name):
    return next(r for r in prices["vm"] if r["region"] == region and r["machine_type"] == name)


@pytest.mark.parametrize("region", ["us-east4", "us-central1"])
@pytest.mark.parametrize("name,vcpus,memory,hourly", [
    ("c3-highmem-192-metal", 192, 1536, 12.701568),
    ("c3-standard-192-metal", 192, 768, 9.677184),
    ("c3-highcpu-192-metal", 192, 512, 8.669056),
    ("c3-highmem-44", 44, 352, 2.910776),
])
def test_official_c3_prices(prices, region, name, vcpus, memory, hourly):
    row = vm(prices, region, name)
    assert (row["vcpus"], row["memory_gib"], row["usd_per_hour"]) == (vcpus, memory, hourly)
    assert row["usd_per_month"] == pytest.approx(hourly * 730, abs=0.01)


def test_metal_is_priced_like_vms(prices):
    derived = prices["derived"]
    assert (derived["c3_usd_per_vcpu_hour"], derived["c3_usd_per_gib_hour"]) == (0.03465, 0.003938)
    assert derived["c3_rows_checked"] == 42 and derived["max_abs_error_usd_per_hour"] < 1e-9
    assert derived["months_are_730_hours"] and derived["regions_identical"]
    for row in prices["vm"]:
        if row["machine_type"].startswith("c3-"):
            assert row["vcpus"] * 0.03465 + row["memory_gib"] * 0.003938 == pytest.approx(row["usd_per_hour"], abs=1e-9)


def test_smallest_c3_vm_for_26_vcpu_and_200_gib(prices):
    fits = prices["derived"]["c3_vms_with_26plus_vcpu_and_200plus_gib_by_price"]
    assert fits[0]["machine_type"] == "c3-highmem-44"  # c3-standard-44 has 176 GiB, c3-highmem-22 has 22 vCPU
    assert all(f["vcpus"] >= 26 and f["memory_gib"] >= 200 for f in fits)
    assert [f["usd_per_hour"] for f in fits] == sorted(f["usd_per_hour"] for f in fits)


@pytest.mark.parametrize("region", ["us-east4", "us-central1"])
def test_hyperdisk_balanced_rates(prices, region):
    rows = {r["item"]: r for r in prices["hyperdisk_balanced"] if r["region"] == region}
    assert rows["Hyperdisk Balanced provisioned space"]["usd_per_month"] == 0.08
    assert rows["Hyperdisk Balanced provisioned IOPS"]["usd_per_month"] == 0.005
    assert rows["Hyperdisk Balanced provisioned throughput"]["usd_per_month"] == 0.04
    for row in rows.values():
        assert row["usd_per_hour"] * 730 == pytest.approx(row["usd_per_month"], rel=2e-3)


def test_c3_vcpu_quota_fits_one_metal_host_in_the_primary_region():
    quotas = json.loads((EVIDENCE / "gcp-region-quotas-us-east.json").read_text())
    c3 = {q["region"]: next(m for m in q["quotas"] if m["metric"] == "C3_CPUS")["limit"] for q in quotas}
    assert c3["us-east4"] >= 192 and c3["us-east1"] >= 192
    assert c3["us-east5"] < 192  # no 192-vCPU metal host there without a quota increase
