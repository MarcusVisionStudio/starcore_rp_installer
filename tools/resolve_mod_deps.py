#!/usr/bin/env python3
"""StarCore RP Mod Installer - dependency resolver

Best-effort automatic installation of missing required mod IDs.

How it works:
1) Scan installed Workshop items in STEAM_WORKSHOP_DIR.
2) Parse every mod.info -> collects modId + require= dependencies.
3) For each missing required modId:
   - Search Steam Workshop (HTML browse) for the modId.
   - Try candidate publishedfileids, validate by scanning the item content for a mod.info with id=<modId>.
   - If validated, download via steamcmd into the same server install dir.

This is intentionally conservative: we only "accept" a Workshop ID when we can confirm it really contains the modId.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

APPID_PZ = 108600


def eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


def parse_list(val: str) -> List[str]:
    val = (val or "").strip()
    if not val:
        return []
    parts = re.split(r"[;,\s]+", val)
    return [p for p in (x.strip() for x in parts) if p]


def only_digits(items: Iterable[str]) -> List[str]:
    out = []
    for it in items:
        if it and it.isdigit():
            out.append(it)
    return out


def guess_steam_install_dir(workshop_dir: Path) -> Path:
    """Given .../steamapps/workshop/content/108600, return install dir (parent before steamapps)."""
    parts = list(workshop_dir.resolve().parts)
    if "steamapps" in parts:
        idx = parts.index("steamapps")
        if idx > 0:
            return Path(*parts[:idx])
    # fallback: go up 4 levels (108600/content/workshop/steamapps)
    return workshop_dir.resolve().parents[4]


def scan_mod_infos(workshop_dir: Path) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """Returns:
    - mod_to_item: modId -> workshopItemId (string)
    - requires: modId -> set(requiredModIds)
    """
    mod_to_item: Dict[str, str] = {}
    requires: Dict[str, Set[str]] = {}

    if not workshop_dir.exists():
        return mod_to_item, requires

    for item_dir in sorted([p for p in workshop_dir.iterdir() if p.is_dir() and p.name.isdigit()]):
        item_id = item_dir.name
        try:
            # search mod.info files in this workshop item
            for modinfo in item_dir.rglob("mod.info"):
                try:
                    text = modinfo.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                mid = None
                reqs: Set[str] = set()

                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.lower().startswith("id="):
                        mid = line.split("=", 1)[1].strip()
                    elif line.lower().startswith("require="):
                        raw = line.split("=", 1)[1].strip()
                        for r in parse_list(raw):
                            if r:
                                reqs.add(r)

                if mid:
                    # first hit wins (usually fine)
                    mod_to_item.setdefault(mid, item_id)
                    requires.setdefault(mid, set()).update(reqs)
        except Exception:
            continue

    return mod_to_item, requires


def compute_missing(mod_to_item: Dict[str, str], requires: Dict[str, Set[str]]) -> Set[str]:
    present = set(mod_to_item.keys())
    missing: Set[str] = set()
    for mid, reqs in requires.items():
        for r in reqs:
            if r and r not in present:
                missing.add(r)
    return missing


@dataclass
class ResolverConfig:
    steamcmd: str
    workshop_dir: Path
    steam_install_dir: Path
    search_pages: int = 3
    candidates: int = 10
    http_timeout: int = 12
    sleep_ms: int = 250
    cache_path: Optional[Path] = None

    # --- Compatibility fields (accepted but not required by this resolver) ---
    # The higher-level rp_mod_installer may pass these fields.
    # Keeping them here prevents runtime TypeError when the tool versions get mixed.
    appid: int = 108600
    freshness_warn_days: int = 60
    freshness_fail_days: int = 0
    max_depth: int = 2
    timeout: float = 4.0
    user_agent: str = "StarCoreRPInstaller/1.0"
    verbose: bool = False


def load_cache(path: Optional[Path]) -> Dict[str, str]:
    if not path:
        return {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def save_cache(path: Optional[Path], cache: Dict[str, str]) -> None:
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def http_get_text(url: str, timeout_s: int) -> str:
    """Fetch text via stdlib urllib (keeps installer dependency-free)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) StarCoreResolver/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    # Steam pages are UTF-8; ignore errors rather than crash.
    return data.decode("utf-8", errors="ignore")


def workshop_search_candidates(modid: str, pages: int, timeout_s: int) -> List[str]:
    ids: List[str] = []
    seen: Set[str] = set()

    for page in range(1, max(1, pages) + 1):
        url = (
            "https://steamcommunity.com/workshop/browse/?appid=108600"
            f"&searchtext={urllib.parse.quote(modid)}"
            "&childpublishedfileid=0&browsesort=textsearch&section=readytouseitems"
            "&requiredtags%5B0%5D=Mod&actualsort=textsearch"
            f"&p={page}"
        )
        try:
            html = http_get_text(url, timeout_s)
        except Exception:
            continue

        # Most reliable: data-publishedfileid="123"
        for m in re.findall(r"data-publishedfileid=\"(\d{8,})\"", html):
            if m not in seen:
                seen.add(m)
                ids.append(m)

        # Fallback: ...id=123
        if len(ids) < 5:
            for m in re.findall(r"\b\?id=(\d{8,})\b", html):
                if m not in seen:
                    seen.add(m)
                    ids.append(m)

        if len(ids) >= 50:
            break

    return ids


def steamcmd_download(steamcmd: str, steam_install_dir: Path, item_id: str) -> bool:
    cmd = [
        steamcmd,
        "+force_install_dir",
        str(steam_install_dir),
        "+login",
        "anonymous",
        "+workshop_download_item",
        str(APPID_PZ),
        str(item_id),
        "validate",
        "+quit",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ok = proc.returncode == 0
        if not ok:
            eprint(f"[deps] steamcmd failed for {item_id}:\n{proc.stdout[-2000:]}")
        return ok
    except FileNotFoundError:
        eprint(f"[deps] steamcmd not found: {steamcmd}")
        return False
    except Exception as ex:
        eprint(f"[deps] steamcmd error for {item_id}: {ex}")
        return False


def item_contains_modid(workshop_dir: Path, item_id: str, modid: str) -> bool:
    item_dir = workshop_dir / str(item_id)
    if not item_dir.exists():
        return False

    for modinfo in item_dir.rglob("mod.info"):
        try:
            text = modinfo.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("id="):
                mid = line.split("=", 1)[1].strip()
                if mid == modid:
                    return True
    return False


def resolve_one(modid: str, cfg: ResolverConfig, cache: Dict[str, str]) -> Optional[str]:
    # cached mapping
    if modid in cache:
        return cache[modid]

    candidates = workshop_search_candidates(modid, cfg.search_pages, cfg.http_timeout)
    candidates = only_digits(candidates)[: cfg.candidates]
    if not candidates:
        return None

    for wid in candidates:
        # if not installed, download first
        if not (cfg.workshop_dir / wid).exists():
            ok = steamcmd_download(cfg.steamcmd, cfg.steam_install_dir, wid)
            if not ok:
                continue

        # validate content
        if item_contains_modid(cfg.workshop_dir, wid, modid):
            cache[modid] = wid
            return wid

        # be a bit gentle with steam
        time.sleep(max(0.0, cfg.sleep_ms / 1000.0))

    return None


def resolve_all(missing_modids: Iterable[str], cfg: ResolverConfig) -> Dict[str, str]:
    cache = load_cache(cfg.cache_path)
    resolved: Dict[str, str] = {}

    for modid in sorted(set(missing_modids)):
        wid = resolve_one(modid, cfg, cache)
        if wid:
            resolved[modid] = wid

    save_cache(cfg.cache_path, cache)
    return resolved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workshop-dir", required=True, help="Path to .../steamapps/workshop/content/108600")
    ap.add_argument("--steamcmd", default="steamcmd", help="Path to steamcmd")
    ap.add_argument("--search-pages", type=int, default=3)
    ap.add_argument("--candidates", type=int, default=10)
    ap.add_argument("--http-timeout", type=int, default=12)
    ap.add_argument("--cache", default="./rules/modid_map.json", help="Cache file for modId->workshopId")
    ap.add_argument("--print-missing", action="store_true", help="Only scan + print missing required mod IDs")
    args = ap.parse_args()

    workshop_dir = Path(args.workshop_dir)
    steam_install_dir = guess_steam_install_dir(workshop_dir)

    mod_to_item, requires = scan_mod_infos(workshop_dir)
    missing = compute_missing(mod_to_item, requires)

    if args.print_missing:
        for m in sorted(missing):
            print(m)
        return 0

    if not missing:
        print("[deps] OK: no missing required mod IDs")
        return 0

    cfg = ResolverConfig(
        steamcmd=args.steamcmd,
        workshop_dir=workshop_dir,
        steam_install_dir=steam_install_dir,
        search_pages=args.search_pages,
        candidates=args.candidates,
        http_timeout=args.http_timeout,
        cache_path=Path(args.cache) if args.cache else None,
    )

    resolved = resolve_all(missing, cfg)
    for modid, wid in sorted(resolved.items()):
        print(f"[deps] {modid} -> {wid}")

    unresolved = sorted(set(missing) - set(resolved.keys()))
    if unresolved:
        eprint("[deps] Unresolved required mod IDs:")
        for m in unresolved:
            eprint(f"  - {m}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
