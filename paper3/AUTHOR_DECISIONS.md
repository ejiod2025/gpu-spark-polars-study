# Paper 3: decisions for the author, and the e-Energy 2027 submission checklist

Prepared 2026-11-24 while the campaign runs. No results exist yet; nothing here depends on them.
Each item gives options, a recommendation and the trade-off. Sources are listed at the end.

## 1. Title

| | Title | Trade-off |
|---|---|---|
| **A (recommended)** | Energy and Cost per Query of CPU and GPU Dataframe Engines in the Cloud: A Pre-registered Measurement Protocol and Break-Even Scales | Says exactly what the paper delivers, whatever the results; long. |
| B | When Does a GPU Pay Off for Analytics? Pre-registered Energy and Cost per Query of Polars and Spark on CPU and GPU Cloud Hosts | Catchier for the e-Energy audience; a question title reads as a promise of an answer, which censored break-evens (">SF300") may not give. |
| C | Measure the Meter First: Validated, Scope-Tagged Energy and Cost per Query for CPU and GPU Dataframe Engines on Rented Hosts | Puts the protocol first; harder to find by search, and the engines are not named. |

Recommendation: **A**. It can be fixed now; B or C can still be chosen once the results are known.

## 2. How reviewers can verify the pre-registration lock without de-anonymizing

The lock's evidence is the annotated git tag `paper3-design-lock` (commit `bd42401`, 2026-11-16 15:37:31 UTC) and
the Zenodo deposit of the design file (DOI 10.5281/zenodo.24284712), which carries the author's name. The sha256 of
the locked design file, `docs/PAPER3_ENERGY_DESIGN.md` at the tag, is
`09f4e85fdf64c67bf07e5b205a342ef11c02fd05f461e5d3c572a10789a311b2` (md5 `8599bc1eaa81dca890128412bf704df8`).
The file names the author in two lines.

What the rules allow: the e-Energy 2027 CFP asks for "non-destructive anonymization", own work "in the third person",
and "If you cite anonymous work, you should also send the deanonymized reference(s) to the PC chairs in a separate
email." It says nothing about repositories, links or pre-registrations.

| | Option | Trade-off |
|---|---|---|
| **A (recommended)** | Cite the lock record anonymously (as the submission build does now), print the design file's sha256 in the paper, and send the Zenodo DOI to the PC chairs by separate email, as the CFP prescribes for anonymous citations. Add the anonymized mirror of option B for reading. | Uses the CFP's own procedure; the chairs can check the time stamp, reviewers can check the content through the mirror. The reviewers themselves cannot see the time stamp. |
| B | An anonymized mirror of the repository (anonymous.4open.science, "Anonymous Github") linked in the paper. | Reviewers can read the locked design and every amendment. The mirror replaces listed words with "XXX", so the mirrored design file's sha256 no longer matches, and git tags and commit dates are not shown: it shows content, not the time of the lock. The word list must cover every identifier, the mirror must not track visitors, and whether e-Energy allows such links is unknown until the HotCRP form opens: ask the PC chairs (see the checklist). |
| C | No verification during review; the Zenodo DOI appears only in the camera-ready version. | Simplest; reviewers take the lock on trust, which weakens the paper's main methodological claim during review. |

Before relying on the sha256: confirm on the Zenodo record that the deposited file's checksum equals the git file's
(Zenodo shows an md5; it should be `8599bc1e…df8`). The record page could not be fetched from here.

## 3. Code and raw-data DOIs: what is deposited, and when

Precedent (Paper 1): one raw archive per host on Zenodo with a `SHA256SUMS` (doi:10.5281/zenodo.24125306), and the
code as git tag `v1.0.0` with its own Zenodo DOI (doi:10.5281/zenodo.24139260). Zenodo can reserve a DOI before a
record is published, so the camera-ready text can carry DOIs that are minted earlier.

What would be deposited:
- **Raw campaign data**: every launch's directory as copied off the host, with its `LAUNCH-SHA256SUMS` and launch
  record, including the stopped C-VM and C-GCP launch 1s (A8) and their relaunches; the price snapshots taken at
  each launch's start and end.
- **Analysis outputs**: `stats.json`, the tables and `recovery.json`, produced by the code frozen at `ea67f12`.
- **Code**: a release tag at the commit the paper's numbers come from (the frozen analysis plus the campaign
  commits it read), with a Zenodo DOI.

| | Timing | Trade-off |
|---|---|---|
| **A (recommended)** | Raw data deposited as soon as the last launch ends, before the full analysis is run (DOI reserved, record published). Code release and analysis outputs at the submission freeze (January 2027). | The deposit date shows the data were fixed before they were analysed, which supports the blinding claim. The records are public during review; the CFP allows public pre-submission versions, but the anonymized paper must not link them, and they should not be broadcast. |
| B | Everything at submission (January 2027). | One step; the data's existence before the analysis rests on git and file times only. |
| C | Everything at camera-ready (April 2027). | Nothing public during review; reviewers have no code or data except an anonymized mirror. |

To decide with the deposit: whether to redact host identifiers. Paper 1 kept its archives unredacted (hostnames,
public IPs of terminated rentals, GPU UUIDs). Paper 3's launch records also hold lease names containing the author's
login (`owner-ejiod`).

## 4. e-Energy 2027 submission checklist

Dates (CFP, winter deadline; all 23:59 AoE unless shown otherwise):
- [ ] Submission site https://eenergy27winter.hotcrp.com opens three weeks before each deadline, so around
      2026-12-25; until then it shows only "Submissions are currently closed".
- [ ] Abstract registration: **2027-01-08**.
- [ ] Paper submission: **2027-01-15**.
- [ ] Notification 2027-03-05; revision due 2027-03-19 (the author response goes in an appendix of the revised paper);
      revision notification 2027-04-16; camera-ready due 2027-04-30.
- [ ] Accepted papers are presented at the conference: the 18th ACM e-Energy, Atlanta, June 5-11, 2027 (cite
      "18th" from the CFP; the HotCRP banner says "17th").

Format:
- [ ] PDF from `make pdf` (`main.pdf`: ACM `sigconf`, 9 pt, the `anonymous` option).
- [ ] At most 10 pages excluding references and appendices (`make pages`); the camera-ready may have one more page.
- [ ] Appendices only "for the purpose of justifying the technical correctness" of the body's claims. The current
      appendices are full contrast tables, the meters' pre-lock validation, the hosts not measured, the analysis
      readings (A7), the gates and the pre-registration record.
- [ ] Final mode: `make stats STATS=<analysis-out>/stats.json` from the real (not synthetic) analysis output, no
      watermark on any page, `make todo` leaves no TODO.

Anonymization (the CFP: non-destructive; no identification "explicitly or by implication"):
- [ ] `pdftotext main.pdf -` shows no author name except in the third-person citations of Papers 1 and 2 and Paper
      1's raw data; no email, affiliation, GitHub URL, Zenodo DOI of the author's own deposits or commit hash.
- [ ] The Disclosures section names no funding source (the CFP: "Remove acknowledgment of identifying names and
      funding sources").
- [ ] `pdfinfo main.pdf` and the XMP metadata show "Anonymous Author(s)" and no path or user name.
- [ ] Upload under a neutral file name (the CFP warns that file names are embedded in output).
- [ ] The lock record is cited anonymously; the deanonymized references go to the PC chairs by separate email.
- [ ] If an anonymized mirror is linked: its word list covers the author's name, `ejiod@bu.edu`, `ejiod`,
      `ejiod2025`, the university, the cloud project name, the lease names and any other email address or domain
      found by `grep -rE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+'` over the mirrored tree; the mirror is checked by reading
      it, file by file, before it is linked.
- [ ] Public preprints are allowed, but not broadcast (social media, press, large mailing lists).

Supplementary material:
- [ ] Unknown until the HotCRP form opens (around 2026-12-25): the 2027 CFP and the 2026 CFP say nothing about
      supplementary files, repositories, code or data, and no separate submission-instructions page exists.
      **Ask the PC chairs** before relying on a supplementary upload or a linked mirror.
- [ ] Any linked mirror must not track who opens it: ACM's peer-review FAQ says that if a repository tracks usage in
      ways that could identify reviewers, the publication may require the material to be provided in a way that
      does not reveal their identity. Whether Anonymous Github logs visitors is not documented in its README; check
      before linking it.

AI disclosure (ACM Policy on Authorship, "Use of Artificial Intelligence", updated 2026-05-14):
- ACM's rule for AI used in the research: "the specific use(s) of AI tools must be described in detail in the methods
  section of the Work", including code and data that the conclusions rely on. For writing help ACM "no longer
  requires the disclosure". AI tools cannot be authors; the named authors are accountable for all content. The
  e-Energy 2027 CFP says nothing about AI use.
- [ ] Methods: the paragraph "Use of AI tools" (end of Section 3), in both builds. It names the tools and what they
      did (harness, probes, gates, campaign automation, the design and amendments, the analysis code after the
      lock, running the probes and the campaign, writing help) and the author's role. It names no people.
- [ ] Submission build: the full AI statement also in the Disclosures section, with "The author(s)".
- [ ] Camera-ready: the full statement in the acknowledgments ("The author directed the study …"). The three
      texts must keep agreeing.

Camera-ready only:
- [ ] Restore the author block, acknowledgments, funding and links (`make camera`).
- [ ] The ACM rights block (`\setcopyright`, `\acmDOI`, `\acmISBN`) from the ACM rights form.
- [ ] The engine versions from each launch's `campaign.json`; the launch facts in `record.tex`.

## Sources (accessed 2026-11-23/24)
- e-Energy 2027 CFP, https://energy.acm.org/conferences/eenergy/2027/pages/cfp.php, and the HotCRP site
  https://eenergy27winter.hotcrp.com/ (read by venue-scout in a browser, 2026-11-23/24; quotes as relayed).
- ACM Policy on Authorship, https://www.acm.org/publications/policies/new-acm-policy-on-authorship (updated
  2026-05-14), and its FAQ, https://www.acm.org/publications/policies/frequently-asked-questions (venue-scout,
  2026-11-24).
- ACM Peer Review Policy FAQ, https://www.acm.org/publications/policies/peer-review-faq (venue-scout, 2026-11-24).
- Anonymous Github: https://anonymous.4open.science/ and its README,
  https://github.com/tdurieux/anonymous_github (README fetched).
- Zenodo DOI reservation: https://help.zenodo.org/docs/deposit/describe-records/reserve-doi/
- Paper 1 deposits: `REPRODUCE.md` (raw archives, doi:10.5281/zenodo.24125306; code `v1.0.0`,
  doi:10.5281/zenodo.24139260).
- Lock tag and file hash: `git show paper3-design-lock:docs/PAPER3_ENERGY_DESIGN.md | shasum -a 256`.
