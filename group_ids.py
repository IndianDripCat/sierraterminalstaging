"""
Centralized Roblox group lookup tables shared across cogs.

`GROUP_IDS` intentionally supports both:
- alias/name -> numeric group ID lookups (e.g. `"MAD" -> 1032907325`)
- numeric group ID -> display name lookups for legacy compatibility
"""

GROUP_NAMES = {
    34230328: "SCP Foundation",
    35798374: "Atlantis Testing",
    326076454: "Anomaly Actors",
    1059704936: "Audit Board",
    996060743: "Files & Recordkeeping",
    884774428: "Office of Admissions",
    693222577: "Office of the Administrator",
    34364967: "Internal Security Department",
    496209906: "Ethics Committee",
    647495903: "Engineering & Technical Services",
    454398709: "Department of External Relations",
    34365046: "Administrative Department",
    348257864: "Internal Tribunal Department",
    1032907325: "Manufacturing Department",
    727230617: "MD > Emergency Medical Unit",
    34364928: "Medical Department",
    982198426: "Community Moderation Team",
    1022611648: "MTF > Triton-1",
    854011952: "SD > Military Police",
    34230571: "Scientific Department",
    34688572: "Mobile Task Forces",
    34688574: "MTF > A1",
    121792765: "MTF > Z6",
    689805150: "MTF > T9",
    371785680: "SD > Security Response Unit",
    34230495: "Security Department",
}

GROUP_ALIASES = {
    "SCPF": 34230328,
    "AA": 326076454,
    "EAA": 1059704936,
    "MAD": 1032907325,
    "MD": 34364928,
    "SD": 34230495,
    "SCD": 34230571,
    "MTF": 34688572,
    "EC": 496209906,
    "ETS": 647495903,
    "DER": 454398709,
    "AD": 34365046,
    "ITD": 348257864,
    "CMT": 982198426,
    "OA": 884774428,
    "OOTA": 693222577,
}

GROUP_IDS = {
    **GROUP_ALIASES,
    **{name.upper(): group_id for group_id, name in GROUP_NAMES.items()},
    **GROUP_NAMES,
}

