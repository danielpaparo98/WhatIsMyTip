#!/usr/bin/env python3
"""Download AFL team logos from Squiggle."""

import os
import urllib.request
import urllib.parse
from pathlib import Path

# Squiggle logo base URL
BASE_URL = "https://squiggle.com.au/wp-content/themes/squiggle/assets/images"

# AFL teams with their logo filenames (from Squiggle API)
TEAMS = {
    "Adelaide": "Adelaide.png",
    "Brisbane Lions": "Brisbane.png",
    "Carlton": "Carlton.png",
    "Collingwood": "Collingwood.png",
    "Essendon": "Essendon.png",
    "Fremantle": "Fremantle.png",
    "Geelong": "Geelong.png",
    "Gold Coast": "GoldCoast.png",
    "Greater Western Sydney": "Giants.png",
    "Hawthorn": "Hawthorn.png",
    "Melbourne": "Melbourne.png",
    "North Melbourne": "NorthMelbourne.png",
    "Port Adelaide": "PortAdelaide.png",
    "Richmond": "Richmond.png",
    "St Kilda": "StKilda.png",
    "Sydney": "Sydney.png",
    "West Coast": "WestCoast.png",
    "Western Bulldogs": "Bulldogs.png",
}

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "logos"


def download_logo(team_name: str, filename: str) -> bool:
    """Download a single team logo."""
    # URL encode the filename to handle spaces
    url = f"{BASE_URL}/{urllib.parse.quote(filename)}"
    output_path = OUTPUT_DIR / filename

    try:
        urllib.request.urlretrieve(url, output_path)
        print(f"[OK] Downloaded {team_name} logo")
        return True
    except Exception as e:
        print(f"[FAIL] Failed to download {team_name} logo: {e}")
        return False


def main():
    """Download all AFL team logos."""
    print(f"Downloading AFL team logos to {OUTPUT_DIR}")
    print("-" * 50)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    success_count = 0
    for team_name, filename in TEAMS.items():
        if download_logo(team_name, filename):
            success_count += 1

    print("-" * 50)
    print(f"Downloaded {success_count}/{len(TEAMS)} logos")


if __name__ == "__main__":
    main()
