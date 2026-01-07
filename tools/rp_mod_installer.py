#!/usr/bin/env python3
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen

# Local dependency resolver (same folder)
try:
    from resolve_mod_deps import (
        ResolverConfig,
        guess_steam_install_dir,
        scan_mod_infos,
        compute_missing,
        resolve_all,
    )
except Exception as e:
    ResolverConfig = None  # type: ignore
    guess_steam_install_dir = None  # type: ignore
    scan_mod_infos = None  # type: ignore
    compute_missing = None  # type: ignore
    resolve_all = None  # type: ignore

STEAM_API_DETAILS = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
ID_RE = re.compile(r"(?:filedetails/\?id=|\b)(\d{8,})\b")

def read_list(path: Path) -> List[str]:
    ids: List[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ID_RE.search(line)
        if m:
            ids.append(m.group(1))
    seen=set(); out=[]
    for x in ids:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def http_post(url: str, body: bytes) -> bytes:
    req = Request(url, data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}, method="POST")
    with urlopen(req, timeout=30) as r:
        return r.read()

def steam_details(ids: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i+100]
        parts = [f"itemcount={len(chunk)}".encode()]
        for idx, fid in enumerate(chunk):
            parts.append(f"publishedfileids[{idx}]={fid}".encode())
        raw = http_post(STEAM_API_DETAILS, b"&".join(parts))
        j = json.loads(raw.decode("utf-8", errors="ignore"))
        for d in j.get("response", {}).get("publishedfiledetails", []):
            fid = str(d.get("publishedfileid") or "")
            if fid:
                out[fid]=d
    return out

def days_ago(ts: int) -> int:
    return int((int(time.time()) - ts)/86400)

def run(cmd: List[str], *, stream: bool=True) -> Tuple[int,str]:
    if not stream:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return p.returncode, p.stdout

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    out_lines: List[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            out_lines.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        raise
    code = proc.wait()
    return code, "".join(out_lines)

def find_mod_ids(item_dir: Path) -> List[str]:
    mids=[]
    for mi in item_dir.rglob("mod.info"):
        try:
            txt = mi.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"^\s*id\s*=\s*(.+?)\s*$", txt, re.M)
        if m:
            mid = m.group(1).strip()
            if mid and " " not in mid:
                mids.append(mid)
    seen=set(); out=[]
    for x in mids:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def find_map_folders(item_dir: Path) -> List[str]:
    # PZ map mods: media/maps/<Folder>/map.info
    folders: List[str] = []
    for mi in item_dir.rglob("media/maps/*/map.info"):
        try:
            folder = mi.parent.name
            if folder:
                folders.append(folder)
        except Exception:
            continue
    # unique preserve order
    seen=set(); out=[]
    for x in folders:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def ini_read(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"INI not found: {path}")
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()

def ini_set(lines: List[str], key: str, value: str) -> List[str]:
    # Be tolerant of whitespace variants like "Key = ...".
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    pref = key + "="
    found=False
    out=[]
    for line in lines:
        if pat.match(line):
            out.append(pref + value); found=True
        else:
            out.append(line)
    if not found:
        out.append(pref + value)
    return out

def load_rules(path: Optional[str]) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}

def check_categories(workshop_ids: List[str], rules: dict, strict: bool) -> None:
    cats = (rules or {}).get("categories", {})
    if not cats:
        return
    violated = []
    for name, cfg in cats.items():
        maxn = int(cfg.get("max", 999999))
        wids = set(str(x) for x in cfg.get("workshop_ids", []))
        cnt = sum(1 for wid in workshop_ids if wid in wids)
        if cnt > maxn:
            violated.append((name, cnt, maxn, cfg.get("note","")))
    if violated:
        print("[Conflicts] Category limits exceeded:")
        for name, cnt, maxn, note in violated:
            print(f" - {name}: {cnt} > {maxn} {('('+note+')') if note else ''}")
        if strict:
            raise SystemExit(3)

def cmd_verify(a):
    ids = read_list(Path(a.list))
    print(f"[Verify] Items: {len(ids)}")
    for fid in ids:
        item_dir = Path(a.workshop_dir)/fid
        mids = find_mod_ids(item_dir) if item_dir.exists() else []
        maps = find_map_folders(item_dir) if item_dir.exists() else []
        print(f"- {fid}  modIDs={mids if mids else 'N/A'}  maps={maps if maps else 'N/A'}")

def cmd_freshness(a):
    ids = read_list(Path(a.list))
    details = steam_details(ids) if ids else {}
    warn=int(a.stale_warn); fail=int(a.stale_fail)
    bad=0
    print(f"[Freshness] Items: {len(ids)}  warn>{warn}d  fail>{fail}d")
    for fid in ids:
        d = details.get(fid, {})
        updated=int(d.get("time_updated") or 0)
        title=(d.get("title") or "").strip()
        if updated<=0:
            print(f"- {fid}  [WARN] no time_updated  title='{title}'")
            continue
        age=days_ago(updated)
        tag=""
        if warn and age>warn: tag=" [WARN]"
        if fail and age>fail: tag=" [FAIL]"; bad+=1
        print(f"- {fid}{tag}  updated_age_days={age}  title='{title}'")
    if bad:
        print(f"[Freshness] FAIL count={bad}")
        sys.exit(2)

def _parse_optional_int(val: str, default: int) -> int:
    if val is None:
        return default
    text = str(val).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _resolver_cfg(**kwargs):
    if ResolverConfig is None:
        return None
    try:
        allowed = set(signature(ResolverConfig).parameters.keys())
        filtered = {k: v for k, v in kwargs.items() if k in allowed}
        return ResolverConfig(**filtered)
    except Exception:
        return None


def _default_dep_cache(rules_path: str) -> Path:
    if rules_path:
        p = Path(rules_path)
        if p.is_dir():
            return p / "modid_map.json"
        return p.parent / "modid_map.json"
    return Path("rules") / "modid_map.json"


def cmd_apply(a):
    ids = read_list(Path(a.list))
    if not ids:
        raise SystemExit("No workshop IDs parsed from list file.")

    rules = load_rules(a.rules)
    check_categories(ids, rules, a.strict_categories)

    workshop_dir = Path(a.workshop_dir)
    ini_path = Path(a.ini)
    sep_w = a.sep_workshop
    sep_m = a.sep_mods

    if not a.dry_run:
        workshop_dir.mkdir(parents=True, exist_ok=True)

    # IMPORTANT: steamcmd downloads Workshop items into <install_dir>/steamapps/workshop/... by default.
    # If your server is installed elsewhere, we MUST force the same install dir, otherwise downloads land
    # in the wrong place and the server won't see them.
    force_install_dir = None
    if ResolverConfig is not None:
        force_install_dir = guess_steam_install_dir(workshop_dir)
    if not force_install_dir:
        # fallback: best guess (strip .../steamapps/workshop/content/108600)
        parts = workshop_dir.parts
        if "steamapps" in parts:
            idx = parts.index("steamapps")
            force_install_dir = Path(*parts[:idx])

    downloaded: List[str] = []
    missing_modinfo: List[str] = []
    workshop_ids: Set[str] = set(ids)

    def dl_item(fid: str) -> None:
        item_dir = workshop_dir / fid
        # Backward compatible: older argparse builds may not define --force.
        if item_dir.exists() and not getattr(a, "force", False):
            return
        if a.dry_run:
            return
        print(f"\n[SteamCMD] Downloading workshop item {fid} ...")
        cmd = [
            a.steamcmd,
            "+force_install_dir", str(force_install_dir),
            "+login", "anonymous",
            "+workshop_download_item", a.pz_appid, fid,
            "validate",
            "+quit",
        ]
        try:
            code, out = run(cmd, stream=True)
        except KeyboardInterrupt:
            print("\n[SteamCMD] Interrupted by user (CTRL+C). No INI changes were written yet.")
            raise
        if code != 0:
            print(out)
            print(f"[SteamCMD][WARN] First attempt failed for {fid}. Retrying once...")
            code, out = run(cmd, stream=True)
            if code != 0:
                print(out)
                raise SystemExit(f"steamcmd download failed for {fid}")
        downloaded.append(fid)

    # 1) Download requested items
    for fid in ids:
        dl_item(fid)

    # 2) Scan mod.info + compute required deps
    if ResolverConfig is None:
        mod_to_item = {}
        requires = {}
    else:
        mod_to_item, requires = scan_mod_infos(workshop_dir)

    # Legacy fallback (should not happen): try old method
    if ResolverConfig is None:
        all_mod_ids: List[str] = []
        all_maps: List[str] = []
        for fid in ids:
            item_dir = workshop_dir / fid
            if not item_dir.exists():
                missing_modinfo.append(fid)
                continue
            mids = find_mod_ids(item_dir)
            if not mids:
                missing_modinfo.append(fid)
            else:
                for mid in mids:
                    if mid not in all_mod_ids:
                        all_mod_ids.append(mid)
            if a.auto_map:
                for m in find_map_folders(item_dir):
                    if m not in all_maps:
                        all_maps.append(m)
    else:
        # Dependency auto-resolve loop
        if a.resolve_deps and not a.dry_run:
            cache_path = Path(a.dep_cache) if a.dep_cache else _default_dep_cache(a.rules)
            search_pages = _parse_optional_int(
                a.dep_search_pages, int(os.environ.get("DEPS_SEARCH_PAGES", "3"))
            )
            candidates = _parse_optional_int(
                a.dep_candidates, int(os.environ.get("DEPS_SEARCH_CANDIDATES", "10"))
            )
            http_timeout = _parse_optional_int(
                a.dep_timeout, int(os.environ.get("DEPS_SEARCH_TIMEOUT", "8"))
            )
            cfg = _resolver_cfg(
                steamcmd=str(a.steamcmd),
                workshop_dir=workshop_dir,
                steam_install_dir=Path(force_install_dir),
                appid=int(a.pz_appid),
                search_pages=search_pages,
                candidates=candidates,
                http_timeout=http_timeout,
                timeout=float(http_timeout),
                user_agent=os.environ.get("DEPS_USER_AGENT", "Mozilla/5.0"),
                cache_path=cache_path,
                verbose=True,
            )
            if cfg is None:
                raise SystemExit("Dependency resolver unavailable or incompatible.")
            loops = 0
            while loops < 5:
                loops += 1
                missing = compute_missing(mod_to_item, requires)
                if not missing:
                    break
                print(f"\n[Deps] Missing required mod IDs: {', '.join(sorted(missing))}")
                resolved = resolve_all(missing, cfg)
                if not resolved:
                    break
                for mid, wid in resolved.items():
                    workshop_ids.add(str(wid))
                    dl_item(str(wid))
                # rescan
                mod_to_item, requires = scan_mod_infos(workshop_dir)

        # Build final mod+map lists
        all_mod_ids = sorted(mod_to_item.keys(), key=lambda s: s.lower())
        all_maps: List[str] = []
        if a.auto_map:
            for wid in sorted(workshop_ids, key=lambda x: int(x)):
                item_dir = workshop_dir / wid
                if not item_dir.exists():
                    missing_modinfo.append(wid)
                    continue
                for m in find_map_folders(item_dir):
                    if m not in all_maps:
                        all_maps.append(m)

    # Sanitize workshop IDs for INI (digits only)
    final_workshop_ids = [w for w in sorted(workshop_ids, key=lambda x: int(x)) if w.isdigit()]

    # 3) Write INI (with backup)
    backup = ini_path.with_suffix(ini_path.suffix + ".bak.dry-run")
    if not a.dry_run:
        ini_lines = ini_read(ini_path)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ini_path.with_suffix(ini_path.suffix + f".bak.{ts}")
        backup.write_text("\n".join(ini_lines) + "\n", encoding="utf-8")

        ini_lines = ini_set(ini_lines, "WorkshopItems", sep_w.join(final_workshop_ids))
        ini_lines = ini_set(ini_lines, "Mods", sep_m.join(all_mod_ids))

        if a.auto_map:
            final_maps = [m for m in all_maps if m != a.map_vanilla]
            if a.map_vanilla and a.map_vanilla not in final_maps:
                final_maps.append(a.map_vanilla)
            ini_lines = ini_set(ini_lines, "Map", ";".join(final_maps))

        ini_path.write_text("\n".join(ini_lines) + "\n", encoding="utf-8")

    state = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "ini": str(ini_path),
        "backup": str(backup),
        "workshop_ids": final_workshop_ids,
        "mod_ids": all_mod_ids,
        "auto_map": bool(a.auto_map),
        "map_vanilla": a.map_vanilla,
        "maps": all_maps,
        "missing_modinfo": missing_modinfo,
        "downloaded": downloaded,
        "forced_install_dir": str(force_install_dir),
    }
    if a.state_out and not a.dry_run:
        Path(a.state_out).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[Apply] OK")
    print(f"  INI: {ini_path}")
    print(f"  Backup: {backup}")
    print(f"  Forced install dir: {force_install_dir}")
    print(f"  WorkshopItems: {sep_w.join(final_workshop_ids)}")
    print(f"  Mods: {sep_m.join(all_mod_ids)}")
    if a.auto_map:
        print(f"  Map: {(';'.join([m for m in all_maps if m != a.map_vanilla] + [a.map_vanilla]))}")
    if missing_modinfo:
        print(f"[Apply][WARN] no mod.info parsed for: {missing_modinfo}")

def cmd_report(a):
    ids=read_list(Path(a.list))
    details=steam_details(ids) if ids else {}
    items=[]
    for fid in ids:
        d=details.get(fid,{})
        updated=int(d.get("time_updated") or 0)
        items.append({
            "workshop_id": fid,
            "title": d.get("title"),
            "time_updated": updated,
            "updated_age_days": days_ago(updated) if updated else None,
            "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={fid}",
        })
    out={"generated_utc": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Report] Written: {a.out}")

def cmd_checklog(a):
    log = Path(a.log)
    if not log.exists():
        print(f"[CheckLog][WARN] Log not found: {log}")
        sys.exit(0)

    tail_n = int(a.tail)
    lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
    chunk = "\n".join(lines[-tail_n:]) if len(lines) > tail_n else "\n".join(lines)

    pat = re.compile(a.patterns, re.I)
    m = pat.search(chunk)
    if m:
        print("[CheckLog][FAIL] Detected error patterns after start.")
        print(f"Pattern: {a.patterns}")
        print("---- tail ----")
        print(chunk[-8000:])  # avoid insane output
        sys.exit(2)

    print("[CheckLog] OK (no error patterns found)")

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd", required=True)

    v=sub.add_parser("verify")
    v.add_argument("--list", required=True)
    v.add_argument("--workshop-dir", required=True)
    v.set_defaults(fn=cmd_verify)

    f=sub.add_parser("freshness")
    f.add_argument("--list", required=True)
    f.add_argument("--stale-warn", required=True)
    f.add_argument("--stale-fail", required=True)
    f.set_defaults(fn=cmd_freshness)

    a=sub.add_parser("apply")
    a.add_argument("--list", required=True)
    a.add_argument("--steamcmd", required=True)
    a.add_argument("--force", action="store_true", help="Force re-download even if already present")
    a.add_argument("--pz-appid", default="108600")
    a.add_argument("--workshop-dir", required=True)
    a.add_argument("--ini", required=True)
    a.add_argument("--sep-workshop", default=";")
    a.add_argument("--sep-mods", default=";")
    a.add_argument("--auto-map", action="store_true")
    a.add_argument("--map-vanilla", default="Muldraugh, KY")
    a.add_argument("--rules", default="")
    a.add_argument("--strict-categories", action="store_true")
    a.add_argument("--resolve-deps", action="store_true", help="Auto-install missing required mod IDs (best-effort)")
    a.add_argument("--dep-cache", default="", help="Cache JSON path for modId -> workshopId resolver")
    a.add_argument("--dep-search-pages", default="", help="Override search pages for dependency resolver")
    a.add_argument("--dep-candidates", default="", help="Override max candidates per modId")
    a.add_argument("--dep-timeout", default="", help="Override HTTP timeout for dependency resolver")
    a.add_argument("--state-out", default="")
    a.add_argument("--dry-run", action="store_true", help="Skip downloads and INI writes")
    a.set_defaults(fn=cmd_apply)

    r=sub.add_parser("report")
    r.add_argument("--list", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_report)

    c=sub.add_parser("checklog")
    c.add_argument("--log", required=True)
    c.add_argument("--tail", default="500")
    c.add_argument("--patterns", required=True)
    c.set_defaults(fn=cmd_checklog)

    args=ap.parse_args()
    args.fn(args)

if __name__=="__main__":
    main()
