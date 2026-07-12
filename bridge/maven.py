"""
sym add --java  —  the Maven supply line.

Downloads a Maven artifact AND its transitive compile deps from Maven
Central into the local jar store, so `java.import org.apache.poi...`
(or plain-Python `sym.java(...)`) can use any Java-exclusive library.

Best-effort transitive resolution: walks POMs, follows compile-scope
non-optional deps, resolves ${property} versions through the parent
chain, consults dependencyManagement (including BOM imports) when a
dep omits its version. Not a full Maven — but enough for the real
libraries people want (verified against Apache POI's tree).

Popular names have aliases in bridge/registry.json under "_java_aliases",
so `sym add --java poi` just works.
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

CENTRAL = "https://repo1.maven.org/maven2"
JAR_DIR = os.environ.get(
    "SYM_JAR_DIR",
    os.path.join(os.path.expanduser("~"), ".sym", "jars"))

_POM_NS = "{http://maven.apache.org/POM/4.0.0}"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "symbridge"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _path(group, artifact, version, ext):
    g = group.replace(".", "/")
    return f"{g}/{artifact}/{version}/{artifact}-{version}.{ext}"


def _strip(tag):
    return tag.replace(_POM_NS, "")


def _pom_tree(group, artifact, version):
    raw = _fetch(f"{CENTRAL}/{_path(group, artifact, version, 'pom')}")
    return ET.fromstring(raw)


def _text(el, name):
    child = el.find(_POM_NS + name)
    return child.text.strip() if child is not None and child.text else None


class _Pom:
    """A POM plus its parent chain — enough context to resolve versions."""

    def __init__(self, group, artifact, version):
        self.gav = (group, artifact, version)
        self.root = _pom_tree(group, artifact, version)
        self.props = {"project.version": version, "project.groupId": group}
        self.dep_mgmt = {}
        self.parent = None
        p = self.root.find(_POM_NS + "parent")
        if p is not None:
            try:
                self.parent = _Pom(_text(p, "groupId"), _text(p, "artifactId"),
                                   _text(p, "version"))
            except Exception:
                self.parent = None
        props_el = self.root.find(_POM_NS + "properties")
        if props_el is not None:
            for child in props_el:
                if child.text:
                    self.props[_strip(child.tag)] = child.text.strip()
        dm = self.root.find(f"{_POM_NS}dependencyManagement/{_POM_NS}dependencies")
        if dm is not None:
            for d in dm.findall(_POM_NS + "dependency"):
                g, a = _text(d, "groupId"), _text(d, "artifactId")
                v = _text(d, "version")
                scope = _text(d, "scope")
                if scope == "import" and v:
                    try:  # BOM import — inherit its dependencyManagement
                        bom = _Pom(self._resolve(g), a, self._resolve(v))
                        self.dep_mgmt.update(bom.dep_mgmt)
                    except Exception:
                        pass
                elif g and a and v:
                    self.dep_mgmt[(self._resolve(g), a)] = self._resolve(v)

    def _resolve(self, value):
        if not value:
            return value
        for _ in range(5):
            m = re.match(r"^\$\{(.+?)\}$", value.strip())
            if not m:
                return value
            key = m.group(1)
            pom = self
            while pom is not None:
                if key in pom.props:
                    value = pom.props[key]
                    break
                pom = pom.parent
            else:
                return value
        return value

    def managed_version(self, group, artifact):
        pom = self
        while pom is not None:
            if (group, artifact) in pom.dep_mgmt:
                return pom.dep_mgmt[(group, artifact)]
            pom = pom.parent
        return None

    def dependencies(self):
        """compile-scope, non-optional, resolved versions."""
        deps = []
        deps_el = self.root.find(_POM_NS + "dependencies")
        if deps_el is None:
            return deps
        for d in deps_el.findall(_POM_NS + "dependency"):
            scope = _text(d, "scope") or "compile"
            if scope not in ("compile", "runtime"):
                continue
            if (_text(d, "optional") or "false") == "true":
                continue
            g = self._resolve(_text(d, "groupId"))
            a = self._resolve(_text(d, "artifactId"))
            v = self._resolve(_text(d, "version")) or self.managed_version(g, a)
            if g and a and v and not v.startswith("${"):
                deps.append((g, a, v))
        return deps


def add_java(coordinate: str, quiet=False, _seen=None) -> list:
    """coordinate: 'group:artifact:version' or a registry alias like 'poi'.
    Returns list of jar paths downloaded/present."""
    coordinate = _alias(coordinate)
    if coordinate.count(":") != 2:
        raise ValueError(
            f"'{coordinate}' — need group:artifact:version "
            f"(or a known alias; see bridge/registry.json _java_aliases)")
    group, artifact, version = coordinate.split(":")
    _seen = _seen if _seen is not None else set()
    if (group, artifact) in _seen:
        return []
    _seen.add((group, artifact))
    os.makedirs(JAR_DIR, exist_ok=True)

    jar_path = os.path.join(JAR_DIR, f"{artifact}-{version}.jar")
    jars = []
    if not os.path.exists(jar_path):
        if not quiet:
            print(f"  ↓ {group}:{artifact}:{version}")
        try:
            data = _fetch(f"{CENTRAL}/{_path(group, artifact, version, 'jar')}")
            with open(jar_path, "wb") as f:
                f.write(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # some artifacts are pom-only aggregators
                jar_path = None
            else:
                raise
    if jar_path:
        jars.append(jar_path)

    try:
        pom = _Pom(group, artifact, version)
        for g, a, v in pom.dependencies():
            jars += add_java(f"{g}:{a}:{v}", quiet=quiet, _seen=_seen)
    except Exception as e:
        if not quiet:
            print(f"  (transitive resolution stopped at {artifact}: {e})",
                  file=sys.stderr)
    return jars


def _alias(name: str) -> str:
    if ":" in name:
        return name
    reg_file = os.path.join(os.path.dirname(__file__), "registry.json")
    with open(reg_file) as f:
        aliases = json.load(f).get("_java_aliases", {})
    if name in aliases:
        return aliases[name]
    raise ValueError(
        f"unknown alias '{name}'. Use group:artifact:version, or add it to "
        f"_java_aliases in bridge/registry.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m bridge.maven <alias | group:artifact:version>")
        sys.exit(1)
    got = add_java(sys.argv[1])
    print(f"  ✅ {len(got)} jars in {JAR_DIR}")
