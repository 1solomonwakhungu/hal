"""Verify the resolved inputs of the qualified Cabal build lane.

These are build input pins, not advisory exceptions. Raw plans retain every
version, including the compiler's Cabal library used by hpack for parsing.
"""
import hashlib
import json
from pathlib import Path
import re
import tarfile
import urllib.request


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def unit_key(unit):
    return ":".join((unit["pkg-name"], unit["pkg-version"],
                     unit.get("component-name", "global")))


def normalized_plan(plan):
    if (plan["cabal-version"] != "3.18.2.0"
            or plan["cabal-lib-version"] != "3.18.2.0"
            or plan["compiler-id"] != "ghc-9.10.3"
            or plan["os"] != "linux"
            or plan["arch"] not in ("aarch64", "x86_64")):
        raise ValueError("unsupported client, compiler, or platform")
    units = plan["install-plan"]
    if not isinstance(units, list) or not units:
        raise ValueError("empty or malformed resolved plan")
    ids = {u["id"]: unit_key(u) for u in units}
    if len(ids) != len(units) or len(set(ids.values())) != len(units):
        raise ValueError("duplicate unit identity")
    rows = []
    for unit in units:
        kind = unit["type"]
        if kind not in ("pre-existing", "configured"):
            raise ValueError("unknown unit type")
        row = {"unit": unit_key(unit), "type": kind}
        if kind == "configured":
            row.update({key: unit[key] for key in ("style", "flags")})
            if unit["style"] == "global":
                if unit["pkg-src"] != {"type": "repo-tar", "repo": {
                        "type": "secure-repo", "uri": "https://hackage.haskell.org/"}}:
                    raise ValueError("unsupported package source")
                row.update({key: unit[key] for key in
                            ("pkg-revision", "pkg-cabal-sha256", "pkg-src-sha256")})
            elif unit["style"] != "local" or unit["pkg-src"]["type"] != "local":
                raise ValueError("unsupported local source")
        for key in ("depends", "exe-depends", "setup-depends"):
            deps = unit.get(key, [])
            if not isinstance(deps, list) or len(set(deps)) != len(deps):
                raise ValueError("malformed dependency edges")
            row[key] = sorted(ids[dep] for dep in deps)
        components = unit.get("components", {})
        row["components"] = {}
        for name, component in components.items():
            if set(component) != {"depends", "exe-depends"}:
                raise ValueError("unknown component dependency representation")
            row["components"][name] = {
                key: sorted(ids[dep] for dep in component[key])
                for key in ("depends", "exe-depends")}
        rows.append(row)
    return sorted(rows, key=lambda row: row["unit"])


def plan_digest(plan):
    return sha256(json.dumps(normalized_plan(plan), sort_keys=True,
                             separators=(",", ":")).encode())


def build_type(manifest):
    text = manifest.decode("utf-8")
    matches = re.findall(r"^build-type\s*:\s*(\w+)\s*$", text, re.M | re.I)
    if len(matches) != 1:
        raise ValueError("an explicit single build-type is required")
    return matches[0]


def verify_plan(plan, pins, label):
    if plan_digest(plan) != pins["plans"][label]:
        raise ValueError(f"{label}: resolved graph differs from qualified inputs")
    for unit in plan["install-plan"]:
        if unit["type"] != "configured":
            continue
        if label != "header-fixed" and (unit.get("setup-depends")
                or unit.get("component-name", "").startswith("setup")
                or "setup" in unit.get("components", {})):
            raise ValueError("external/custom setup is outside the supported lane")
        if unit["style"] == "local":
            root = Path(unit["pkg-src"]["path"])
            manifests = list(root.glob("*.cabal"))
            if len(manifests) != 1:
                raise ValueError("ambiguous local package manifest")
            expected = "Custom" if label == "header-fixed" else "Simple"
            if build_type(manifests[0].read_bytes()) != expected:
                raise ValueError("unsupported local setup route")
        else:
            key = f"{unit['pkg-name']}-{unit['pkg-version']}"
            pin = pins["sources"][key]
            for field, expected in (("pkg-cabal-sha256", "manifest_sha256"),
                                    ("pkg-src-sha256", "source_sha256"),
                                    ("pkg-revision", "revision")):
                if unit[field] != pin[expected]:
                    raise ValueError(f"changed source identity: {key}")
            if pin["build_type"] not in ("Simple", "Configure"):
                raise ValueError(f"unsupported dependency setup route: {key}")


def verify_manifests(cache, pins, output):
    wanted = {(v["name"], v["version"], v["manifest_sha256"]): v
              for v in pins["sources"].values()}
    pairs = {key[:2] for key in wanted}
    found = set()
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cache / "01-index.tar", "r:") as archive:
        for member in archive:
            parts = member.name.split("/")
            if (len(parts) != 3 or tuple(parts[:2]) not in pairs
                    or parts[2] != parts[0] + ".cabal"):
                continue
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                raise ValueError("invalid index manifest")
            data = archive.extractfile(member).read()
            key = (parts[0], parts[1], sha256(data))
            if key in wanted:
                if build_type(data) != wanted[key]["build_type"]:
                    raise ValueError("manifest build type differs from reviewed input")
                (output / f"{parts[0]}-{parts[1]}.cabal").write_bytes(data)
                found.add(key)
    if found != set(wanted):
        raise ValueError(f"missing pinned package revisions: {set(wanted) - found}")


def fetch_sources(cache, pins, download=False):
    for key, pin in pins["sources"].items():
        dest = cache / pin["name"] / pin["version"] / f"{key}.tar.gz"
        if not dest.exists():
            if not download:
                raise ValueError(f"missing source archive: {key}")
            url = f"https://hackage.haskell.org/package/{key}/{key}.tar.gz"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024 or sha256(data) != pin["source_sha256"]:
                raise ValueError(f"source archive checksum mismatch: {key}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if sha256(dest.read_bytes()) != pin["source_sha256"]:
            raise ValueError(f"source archive checksum mismatch: {key}")
