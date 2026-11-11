#!/usr/bin/env python3
"""Qualify the frozen Cabal lane in a fresh, disposable directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile

from inputs import (fetch_sources, normalized_plan, plan_digest, read_json,
                    sha256, verify_manifests, verify_plan)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def tree_inputs(root, excluded=()):
    """Bind file contents and membership, including previously absent overrides."""
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current = Path(current)
        for name in dirs + files:
            path = current / name
            if path.is_symlink():
                raise ValueError(f"symlink is outside the qualified input snapshot: {path}")
        if current == root:
            dirs[:] = [name for name in dirs if name not in excluded]
        for name in files:
            path = current / name
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f"non-file input: {path}")
            result[str(path.relative_to(root))] = {
                "sha256": sha256(path.read_bytes()), "executable": bool(mode & 0o111)}
    return result


class Lane:
    def __init__(self, work):
        self.work = work.resolve()
        self.source = self.work / "source"
        self.logs = self.work / "logs"
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.work / "home"),
                    "CABAL_DIR": str(self.work / "cabal"), "LANG": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.pins = read_json(HERE / "inputs.json")
        self.projects = {"hal": self.source, "examples": self.source,
                         "hpack": self.work / "hpack",
                         "header-fixed": self.work / "header-fixed"}
        self.args = {name: ["--builddir=" + self.dist(name)] for name in self.projects}
        self.args["examples"] += ["--project-file=cabal.examples.project"]
        self.accepted_inputs = None

    def input_snapshot(self):
        roots = set(self.projects.values())
        result = {}
        for root in roots:
            excluded = [self.dist(name) for name, path in self.projects.items() if path == root]
            result[str(root.relative_to(self.work))] = tree_inputs(root, excluded)
        result["home"] = tree_inputs(self.work / "home")
        config = self.work / "cabal/config"
        if config.is_symlink():
            raise ValueError("Cabal configuration must be a regular file")
        result["cabal/config"] = sha256(config.read_bytes())
        return result

    def check_inputs(self):
        if self.accepted_inputs is not None and self.input_snapshot() != self.accepted_inputs:
            raise ValueError("local/project/configuration inputs changed since preparation")

    def dist(self, name):
        return "dist-" + name

    def plan(self, name):
        return self.projects[name] / self.dist(name) / "cache/plan.json"

    def command(self, name, args, cwd=None):
        self.check_inputs()
        log = self.logs / (name + ".log")
        if log.exists():
            raise ValueError(f"refusing to overwrite command evidence: {log}")
        write_json(self.logs / (name + ".command.json"), args)
        with log.open("w") as out:
            result = subprocess.run(args, cwd=cwd or self.source, env=self.env,
                                    stdout=out, stderr=subprocess.STDOUT, timeout=2400)
        (self.logs / (name + ".exit")).write_text(str(result.returncode) + "\n")
        print(name, "exit", result.returncode, flush=True)
        if result.returncode:
            print(log.read_text()[-8000:])
            raise RuntimeError(f"command failed; full evidence: {log}")
        self.check_inputs()
        return log.read_text()

    def identity(self):
        spec = importlib.util.spec_from_file_location(
            "installer", ROOT / ".github/actions/install_cabal/install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        release = installer.release_for_host()
        exe = Path(shutil.which("cabal", path=self.env["PATH"])).resolve()
        if sha256(exe.read_bytes()) != release.binary_sha256:
            raise ValueError("executing Cabal does not match the verified release binary")
        version = subprocess.check_output([str(exe), "--version"], env=self.env, text=True)
        ghc = subprocess.check_output(["ghc", "--numeric-version"], env=self.env, text=True).strip()
        if "compiled using version 3.18.2.0" not in version or ghc != "9.10.3":
            raise ValueError("unexpected executing toolchain")
        return {"cabal_executable": str(exe), "executable_sha256": release.binary_sha256,
                "archive_sha256": release.archive_sha256, "version": version,
                "ghc": ghc, "platform": platform.platform(),
                "global_Cabal": subprocess.check_output(
                    ["ghc-pkg", "field", "Cabal", "version"], env=self.env, text=True)}

    def update_index(self):
        # Index updates also write a project cache; keep it in the declared build root.
        self.command("index", ["cabal", "update"] + self.args["hal"])

    def prepare(self, package_cache, offline):
        self.work.mkdir()  # Never take over an existing workspace or setup cache.
        self.logs.mkdir()
        (self.work / "home").mkdir()
        (self.work / "cabal").mkdir()
        cache = package_cache.resolve() if package_cache else self.work / "packages"
        cache.mkdir(parents=True, exist_ok=True)
        (self.work / "cabal/config").write_text(
            "repository hackage.haskell.org\n  url: https://hackage.haskell.org/\n"
            "  secure: True\nremote-repo-cache: " + str(cache) + "\nstore-dir: "
            + str(self.work / "cabal/store") + "\nlogs-dir: "
            + str(self.work / "cabal/logs") + "\n")
        self.source.mkdir()
        for name in ("src", "test", "examples", "docs", ".github"):
            shutil.copytree(ROOT / name, self.source / name,
                            ignore=shutil.ignore_patterns("dist", "dist-*", ".stack-work", "__pycache__"))
        for name in ("package.yaml", "hal.cabal", "cabal.project", "cabal.project.freeze",
                     "README.md", "CHANGELOG.md", "LICENSE"):
            shutil.copyfile(ROOT / name, self.source / name)
        for name in ("hpack", "header-fixed"):
            shutil.copytree(HERE / name, self.projects[name])
        project = (self.source / "cabal.project").read_text()
        if project.count("packages: .\n") != 1:
            raise ValueError("unexpected root project package declaration")
        (self.source / "cabal.examples.project").write_text(
            project.replace("packages: .\n", "packages: . examples\n"))
        shutil.copyfile(self.source / "cabal.project.freeze",
                        self.source / "cabal.examples.project.freeze")
        self.accepted_inputs = self.input_snapshot()
        write_json(self.work / "toolchain.json", self.identity())
        if not offline:
            self.update_index()
        cache = cache / "hackage.haskell.org"
        verify_manifests(cache, self.pins, self.work / "manifests")
        fetch_sources(cache, self.pins, download=not offline)
        for name in self.projects:
            self.command(name + "-resolve", ["cabal", "build", "all", "--dry-run", "-v2"]
                         + self.args[name], self.projects[name])
            plan = read_json(self.plan(name))
            verify_plan(plan, self.pins, name)
            write_json(self.work / (name + "-normalized.json"), normalized_plan(plan))
        write_json(self.work / "prepared.json", {"package_cache": str(cache),
                    "inputs_sha256": sha256((HERE / "inputs.json").read_bytes()),
                    "local_inputs": self.accepted_inputs})

    def build_project(self, name):
        self.check_inputs()
        verify_plan(read_json(self.plan(name)), self.pins, name)
        unit_logs = self.logs / (name + "-units")
        unit_logs.mkdir()
        self.command(name + "-build", ["cabal", "build", "all", "-j3", "-v3",
                     "--build-log=" + str(unit_logs / "$libname.log")] + self.args[name],
                     self.projects[name])

    def test_hal(self, name, root, args):
        text = self.command(name + "-test", ["cabal", "test", "all", "--test-show-details=direct",
                            "--test-options=+RTS -N2 -RTS"] + args, root)
        if "102 examples, 0 failures" not in text:
            raise ValueError("unexpected HAL suite result; update qualification deliberately")
        self.command(name + "-check", ["cabal", "check"], root)

    def routes(self):
        rows = []
        for name in ("hal", "hpack", "examples"):
            plan = read_json(self.plan(name))
            units = {u["id"]: u for u in plan["install-plan"]}
            pending = [u["id"] for u in units.values() if u.get("style") == "local"]
            reachable = set()
            while pending:
                uid = pending.pop()
                if uid in reachable:
                    continue
                reachable.add(uid)
                unit = units[uid]
                pending += unit.get("depends", []) + unit.get("exe-depends", [])
                for component in unit.get("components", {}).values():
                    pending += component.get("depends", []) + component.get("exe-depends", [])
            for uid, unit in units.items():
                if unit["type"] == "pre-existing":
                    rows.append({"plan": name, "unit": uid, "route": "compiler package; not rebuilt"})
                    continue
                logs = list(self.logs.glob("*-units/" + uid + ".log"))
                if unit.get("style") == "local":
                    logs = [self.logs / (name + "-build.log")]
                if not logs:
                    if uid in reachable:
                        raise ValueError(f"missing execution-route evidence: {uid}")
                    rows.append({"plan": name, "unit": uid, "route": "unrequested solver component"})
                    continue
                text = "\n".join(path.read_text() for path in logs)
                if ("Using in-library setup method with build-type " not in text
                        or "Using Cabal-3.18.2.0 compiled by ghc-9.10" not in text
                        or "Using external setup method" in text):
                    raise ValueError(f"unexpected executing setup route: {uid}")
                rows.append({"plan": name, "unit": uid, "route": "embedded Cabal 3.18.2.0",
                             "logs": [str(path.relative_to(self.work)) for path in logs]})
        write_json(self.work / "execution-routes.json", rows)

    def build(self):
        receipt = read_json(self.work / "prepared.json")
        if receipt["inputs_sha256"] != sha256((HERE / "inputs.json").read_bytes()):
            raise ValueError("input lock changed since preparation")
        self.accepted_inputs = receipt["local_inputs"]
        if not isinstance(self.accepted_inputs, dict) or not self.accepted_inputs:
            raise ValueError("missing prepared input snapshot")
        self.check_inputs()
        self.identity()
        fetch_sources(Path(receipt["package_cache"]), self.pins)
        store = self.work / "cabal/store"
        if list(store.glob("**/*.conf")) or (self.work / "build-start.json").exists():
            raise ValueError("build qualification requires a cold package/setup store")
        write_json(self.work / "build-start.json", {"cold_store": True})
        for name in self.projects:
            self.command(name + "-revalidate", ["cabal", "build", "all", "--dry-run", "-v2"]
                         + self.args[name], self.projects[name])
            verify_plan(read_json(self.plan(name)), self.pins, name)
        self.build_project("hal")
        self.test_hal("hal", self.source, self.args["hal"])
        self.build_project("hpack")
        plan = read_json(self.plan("hpack"))
        units = [u for u in plan["install-plan"] if u["pkg-name"] == "hpack"
                 and u.get("component-name") == "exe:hpack"]
        if len(units) != 1:
            raise ValueError("missing hpack executable in bootstrap plan")
        hpack = Path(units[0]["bin-file"]).resolve()
        if store.resolve() not in hpack.parents:
            raise ValueError("hpack executable is outside the isolated store")
        self.command("hpack-version", [str(hpack), "--version"])
        for name, root, manifest in (("hal", self.source, "hal.cabal"),
                                     ("examples", self.source / "examples", "examples.cabal")):
            before = (root / manifest).read_bytes()
            self.command(name + "-hpack", [str(hpack), "--force"], root)
            if before != (root / manifest).read_bytes():
                raise ValueError(f"generated {manifest} differs from committed description")
        self.build_project("examples")
        binaries = [u for u in read_json(self.plan("examples"))["install-plan"]
                    if u["pkg-name"] == "examples"]
        if len(binaries) != 5 or not all(Path(u["bin-file"]).is_file() for u in binaries):
            raise ValueError("all five example executables must build")
        self.command("sdist", ["cabal", "sdist"] + self.args["hal"])
        archives = list((self.source / self.dist("hal") / "sdist").glob("*.tar.gz"))
        if len(archives) != 1:
            raise ValueError("expected one HAL source distribution")
        unpack = self.work / "sdist"
        unpack.mkdir()
        with tarfile.open(archives[0]) as archive:
            for member in archive:
                if (not (member.isfile() or member.isdir()) or member.name.startswith("/")
                        or ".." in Path(member.name).parts):
                    raise ValueError("unsafe source distribution member")
            archive.extractall(unpack)
        root = unpack / "hal-1.1"
        self.command("sdist-resolve", ["cabal", "build", "all", "--dry-run", "-v2"], root)
        verify_plan(read_json(root / "dist-newstyle/cache/plan.json"), self.pins, "hal")
        self.command("sdist-build", ["cabal", "build", "all", "-j3", "-v3"], root)
        self.test_hal("sdist", root, [])
        from headers import qualify_headers
        # Deliberate Custom/v1 deletion controls run only in disposable fixtures.
        qualify_headers(self)
        self.routes()
        write_json(self.work / "results.json", {"HAL_examples": 102, "sdist_examples": 102,
                    "example_executables": 5, "sdist_sha256": sha256(archives[0].read_bytes()),
                    "hpack_sha256": sha256(hpack.read_bytes()),
                    "scope": "Cabal lane only; raw advisory matches are not suppressed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "build"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--package-cache", type=Path)
    parser.add_argument("--offline-preparation", action="store_true")
    args = parser.parse_args()
    lane = Lane(args.work)
    if args.phase == "prepare":
        lane.prepare(args.package_cache, args.offline_preparation)
    else:
        lane.build()


if __name__ == "__main__":
    main()
