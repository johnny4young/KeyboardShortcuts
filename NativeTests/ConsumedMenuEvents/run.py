#!/usr/bin/env python3
"""Run the native consumed-event regression in a disposable UUID application."""
from pathlib import Path
import json
import os
import plistlib
import re
import subprocess
import tempfile
import uuid

repo = Path(__file__).resolve().parents[2]
sources = Path(__file__).resolve().parent
output = Path(tempfile.mkdtemp(prefix="gancho-shortcut-menu.")).resolve()
print(f"Native menu evidence: {output}", flush=True)
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
version = subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"], cwd=repo, text=True).strip().removeprefix("v")
assert re.fullmatch(r"\d+\.\d+\.\d+", version)
identifier = "com.sindresorhus.KeyboardShortcuts.tests.shortcut-menu." + uuid.uuid4().hex
settings = {
    "SWIFT_VERSION": "6.0", "SWIFT_APPROACHABLE_CONCURRENCY": "YES",
    "SWIFT_DEFAULT_ACTOR_ISOLATION": "MainActor", "SWIFT_STRICT_CONCURRENCY": "complete",
    "SWIFT_TREAT_WARNINGS_AS_ERRORS": "YES", "CODE_SIGN_STYLE": "Manual",
    "CODE_SIGN_IDENTITY": "-", "CODE_SIGN_INJECT_BASE_ENTITLEMENTS": "NO",
    "ENABLE_HARDENED_RUNTIME": "YES", "ENABLE_DEBUG_DYLIB": "NO",
}
project = {
    "name": "ShortcutMenuFixture", "settings": {"base": settings},
    "packages": {"KeyboardShortcuts": {"path": str(repo)}},
    "targets": {
        "ShortcutMenuFixture": {
            "type": "application", "platform": "macOS", "deploymentTarget": "15.4",
            "sources": [{"path": str(sources / name)} for name in ["FixtureApp.swift", "ShortcutName.swift"]],
            "info": {"path": "FixtureInfo.plist", "properties": {
                "NSPrincipalClass": "MenuFixtureApplication",
                "FixtureDependencyVersion": version, "FixtureDependencyRevision": revision,
            }},
            "settings": {"base": {"PRODUCT_BUNDLE_IDENTIFIER": identifier}},
            "dependencies": [{"package": "KeyboardShortcuts"}],
        },
        "ShortcutMenuUITests": {
            "type": "bundle.ui-testing", "platform": "macOS", "deploymentTarget": "15.4",
            "sources": [{"path": str(sources / "ShortcutMenuUITests.swift")}],
            "settings": {"base": {
                "PRODUCT_BUNDLE_IDENTIFIER": identifier + ".runner",
                "GENERATE_INFOPLIST_FILE": "YES", "SWIFT_DEFAULT_ACTOR_ISOLATION": "nonisolated",
                "ENABLE_HARDENED_RUNTIME": "NO",
            }},
            "dependencies": [{"target": "ShortcutMenuFixture"}],
        },
    },
    "schemes": {"ShortcutMenuFixture": {
        "build": {"targets": {"ShortcutMenuFixture": "all"}},
        "test": {"targets": ["ShortcutMenuUITests"], "gatherCoverageData": False},
    }},
}
(output / "project.json").write_text(json.dumps(project, indent=2) + "\n")
manifest = {
    "version": version, "revision": revision, "bundleIdentifier": identifier,
    "snapshotPath": str(output / "snapshot.json"),
}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
subprocess.run(["xcodegen", "generate", "--spec", str(output / "project.json"), "--project", str(output)], check=True)
with (output / "build.log").open("w") as log:
    subprocess.run([
        "xcodebuild", "build-for-testing", "-project", str(output / "ShortcutMenuFixture.xcodeproj"),
        "-scheme", "ShortcutMenuFixture", "-destination", "platform=macOS,arch=arm64",
        "-derivedDataPath", str(output / "DerivedData"), "-disableAutomaticPackageResolution",
    ], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
products = output / "DerivedData/Build/Products"
runs = list(products.glob("ShortcutMenuFixture_*.xctestrun"))
assert len(runs) == 1
with runs[0].open("rb") as handle:
    configuration = plistlib.load(handle)
runner = configuration["ShortcutMenuUITests"]
runner["SystemAttachmentLifetime"] = "keepNever"
runner["UserAttachmentLifetime"] = "keepAlways"
variables = {
    "SHORTCUT_MENU_VERSION": version, "SHORTCUT_MENU_REVISION": revision,
    "SHORTCUT_MENU_BUNDLE_ID": identifier, "SHORTCUT_MENU_SNAPSHOT": manifest["snapshotPath"],
}
runner["EnvironmentVariables"].update(variables)
bounded = products / "native-menu.xctestrun"
with bounded.open("wb") as handle:
    plistlib.dump(configuration, handle)
environment = dict(os.environ)
environment.update({"TEST_RUNNER_" + key: value for key, value in variables.items()})
result = output / "native-menu.xcresult"
with (output / "native-menu.log").open("w") as log:
    completed = subprocess.run([
        "xcodebuild", "test-without-building", "-xctestrun", str(bounded),
        "-destination", "platform=macOS,arch=arm64", "-parallel-testing-enabled", "NO",
        "-only-testing:ShortcutMenuUITests/ShortcutMenuUITests/testConsumedMenuShortcutIsDeliveredOnceWithoutRequeue",
        "-test-timeouts-enabled", "YES", "-default-test-execution-time-allowance", "45",
        "-maximum-test-execution-time-allowance", "60", "-collect-test-diagnostics", "never",
        "-resultBundlePath", str(result),
    ], env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=120)
summary = json.loads(subprocess.check_output([
    "xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(result),
], text=True))
(output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
for application in ["ShortcutMenuFixture.app", "ShortcutMenuUITests-Runner.app"]:
    subprocess.run([
        "/System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister",
        "-u", str(products / "Debug" / application),
    ], capture_output=True, check=False, timeout=10)
for domain in [identifier, identifier + ".runner", identifier + ".runner.xctrunner"]:
    subprocess.run(["defaults", "delete", domain], capture_output=True, check=False, timeout=5)
assert completed.returncode == 0, f"Native test failed; see {output}"
assert summary.get("result") == "Passed"
assert summary.get("totalTestCount") == summary.get("passedTests") == 1
assert summary.get("failedTests") == summary.get("skippedTests") == summary.get("expectedFailures") == 0
print("PASS: one native menu test; zero failures, skips or expected failures", flush=True)
