#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ASSET_ID = 86116556258301
RELEASES_FILE = os.path.join(os.path.dirname(__file__), "..", "releases.json")
MAX_RETRIES = 3

def normalize_tag(version: str) -> str:
    """Normalize version string (e.g. 'v1.0974' -> 'v10974')."""
    v = version.strip().lower()
    clean = re.sub(r"[^\d]", "", v)
    return f"v{clean}" if clean else v

def fetch_asset_details(asset_id: int) -> tuple[dict | None, dict | None]:
    """
    Fetch asset details with retry logic.
    Returns (data_dict, fatal_error_dict).
    """
    url = f"https://economy.roblox.com/v2/assets/{asset_id}/details"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            # Fatal asset status codes (moderated, deleted, or unauthorized)
            if e.code in (403, 404, 410):
                return None, {
                    "code": e.code,
                    "reason": e.reason,
                    "message": f"Roblox returned HTTP {e.code} ({e.reason}). The asset may be moderated, removed, or made private.",
                }
            # Transient server/rate limit errors: retry
            print(f"[Attempt {attempt}/{MAX_RETRIES}] HTTP {e.code} {e.reason}. Retrying...", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[Attempt {attempt}/{MAX_RETRIES}] Network error: {e}. Retrying...", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"[Attempt {attempt}/{MAX_RETRIES}] Unexpected error: {e}. Retrying...", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    print(f"Warning: Failed to reach Roblox API after {MAX_RETRIES} attempts. Skipping check.", file=sys.stderr)
    return None, None

def extract_version(description: str) -> str:
    match = re.search(r"\b(v[0-9]+(?:\.[0-9]+)*)\b", description, re.IGNORECASE)
    if match:
        return match.group(1)
    return "unknown"

def set_github_output(key: str, value: str):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and os.path.exists(output_path):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")

def main():
    update_file = "--update" in sys.argv

    if not os.path.exists(RELEASES_FILE):
        releases_data = {
            "assetId": ASSET_ID,
            "name": "Eclipse Animator",
            "version": "",
            "tag": "",
            "lastUpdated": "",
            "history": [],
        }
    else:
        with open(RELEASES_FILE, "r", encoding="utf-8") as f:
            releases_data = json.load(f)

    print(f"Checking Creator Store asset {ASSET_ID}...")
    details, fatal_error = fetch_asset_details(ASSET_ID)

    if fatal_error:
        print(f"\n[CRITICAL] Asset API Error: {fatal_error['message']}", file=sys.stderr)
        set_github_output("api_alert", "true")
        set_github_output("api_error_code", str(fatal_error["code"]))
        set_github_output("api_error_message", fatal_error["message"])
        set_github_output("has_update", "false")
        set_github_output("is_new_version", "false")
        set_github_output("is_metadata_update", "false")
        sys.exit(0)

    set_github_output("api_alert", "false")

    if not details:
        # Transient network failure; exit cleanly without failing the GitHub Action run
        print("\n[=] Check skipped due to temporary network/API unavailability.")
        set_github_output("has_update", "false")
        set_github_output("is_new_version", "false")
        set_github_output("is_metadata_update", "false")
        sys.exit(0)

    current_updated = details.get("Updated", "")
    description = details.get("Description", "")
    name = details.get("Name", "Eclipse Animator")
    current_version = extract_version(description)
    current_tag = normalize_tag(current_version)

    last_updated = releases_data.get("lastUpdated", "")
    last_version = releases_data.get("version", "")
    last_tag = releases_data.get("tag", normalize_tag(last_version) if last_version else "")

    print(f"Upstream Updated: {current_updated}")
    print(f"Recorded Updated: {last_updated}")
    print(f"Upstream Version: {current_version} (Tag: {current_tag})")
    print(f"Recorded Version: {last_version} (Tag: {last_tag})")

    # Determine what changed
    is_new_version = (current_tag != last_tag and bool(current_tag) and current_tag != "unknown")
    is_metadata_update = (current_updated != last_updated and not is_new_version and bool(current_updated))
    has_update = is_new_version or is_metadata_update

    set_github_output("has_update", "true" if has_update else "false")
    set_github_output("is_new_version", "true" if is_new_version else "false")
    set_github_output("is_metadata_update", "true" if is_metadata_update else "false")
    set_github_output("version", current_version)
    set_github_output("tag", current_tag)
    set_github_output("updated", current_updated)
    set_github_output("name", name)

    if is_new_version:
        print(f"\n[!] New upstream release version detected: {current_version} ({current_tag}) at {current_updated}")
    elif is_metadata_update:
        print(f"\n[i] Creator Store metadata/listing update detected (version {current_tag} unchanged) at {current_updated}")
    else:
        print("\n[=] Upstream is up to date.")

    if has_update and update_file:
        releases_data["lastUpdated"] = current_updated
        releases_data["version"] = current_version
        releases_data["tag"] = current_tag
        releases_data["name"] = name
        now_iso = datetime.now(timezone.utc).isoformat()
        releases_data.setdefault("history", []).append({
            "version": current_version,
            "tag": current_tag,
            "updated": current_updated,
            "type": "release" if is_new_version else "revision",
            "recordedAt": now_iso,
        })
        with open(RELEASES_FILE, "w", encoding="utf-8") as f:
            json.dump(releases_data, f, indent=2)
            f.write("\n")
        print(f"Updated {RELEASES_FILE}")

if __name__ == "__main__":
    main()
