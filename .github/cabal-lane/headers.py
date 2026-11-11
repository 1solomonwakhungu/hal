"""Disposable duplicate-header regression and affected setup-selection controls."""
from pathlib import Path
import json
from inputs import read_json, sha256

SOURCE = b"#define PROBE 1\n"
GENERATED = b"#define PROBE 2\n"


def qualify_headers(lane):
    lane.check_inputs()
    results = []
    root = lane.projects["header-fixed"]
    plan = read_json(lane.plan("header-fixed"))
    unit = next(u for u in plan["install-plan"] if u["pkg-name"] == "header-probe")
    by_id = {u["id"]: u for u in plan["install-plan"]}
    setup = unit["components"]["setup"]["depends"]
    libraries = [(by_id[i]["pkg-name"], by_id[i]["pkg-version"]) for i in setup]
    if ("Cabal", "3.18.2.0") not in libraries:
        raise ValueError("fixed fixture must select the fixed library, not just a new client")
    generated = Path(unit["dist-dir"]) / "build/include/probe.h"
    for path, data in ((root / "include/probe.h", SOURCE), (generated, GENERATED)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # This fixture intentionally adds one synthetic source header after planning.
    # Production inputs have already passed their unchanged-snapshot checks.
    lane.accepted_inputs = lane.input_snapshot()
    lane.build_project("header-fixed")
    text = (lane.logs / "header-fixed-build.log").read_text()
    if ("Duplicate header found in" not in text or "use is unspecified." not in text
            or "Using Cabal-3.18.2.0" not in text
            or (root / "include/probe.h").read_bytes() != SOURCE
            or generated.read_bytes() != GENERATED):
        raise ValueError("fixed duplicate-header routine was not exercised successfully")
    results.append({"case": "fixed-library", "Cabal": "3.18.2.0", "warning": True,
                    "source_preserved": True, "source_sha256": sha256(SOURCE),
                    "generated_sha256": sha256(GENERATED)})
    for name, custom, command in (
            ("bundled-312", False, ["runghc", "Setup.hs", "configure", "--builddir=dist", "-v2"]),
            ("client-v1-312", False, ["cabal", "v1-configure", "--builddir=dist", "-v2"]),
            ("client-custom-312", True, ["cabal", "build", "--builddir=dist", "-v3"])):
        root = lane.work / name
        root.mkdir()
        (root / "include").mkdir()
        (root / "include/probe.h").write_bytes(SOURCE)
        manifest = "cabal-version: 2.2\nname: header-probe\nversion: 0.1\nbuild-type: "
        manifest += "Custom\ncustom-setup\n  setup-depends: base, Cabal ==3.12.1.0\n" if custom else "Simple\n"
        manifest += ("library\n  exposed-modules: Probe\n  build-depends: base >=4.20 && <4.21\n"
                     "  default-language: Haskell2010\n  include-dirs: include\n  includes: probe.h\n")
        (root / "probe.cabal").write_text(manifest)
        (root / "cabal.project").write_text("packages: .\nactive-repositories: :none\n")
        (root / "Probe.hs").write_text("module Probe where\nvalue :: Int\nvalue = 1\n")
        (root / "Setup.hs").write_text("import Distribution.Simple\nmain = defaultMain\n")
        generated = root / "dist/build/include/probe.h"
        if custom:
            lane.command(name + "-resolve", ["cabal", "build", "--dry-run", "--builddir=dist", "-v2"], root)
            plan = read_json(root / "dist/cache/plan.json")
            unit = next(u for u in plan["install-plan"] if u["pkg-name"] == "header-probe")
            generated = Path(unit["dist-dir"]) / "build/include/probe.h"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(GENERATED)
        text = lane.command(name, command, root)
        if ((root / "include/probe.h").exists() or generated.read_bytes() != GENERATED
                or "Duplicate header" not in text or "Using Cabal-3.12.1.0" not in text):
            raise ValueError("affected deletion control did not reproduce expected behavior")
        results.append({"case": name, "Cabal": "3.12.1.0", "warning": True,
                        "source_preserved": False, "generated_sha256": sha256(GENERATED)})
    (lane.work / "header-results.json").write_text(json.dumps(results, indent=2) + "\n")
