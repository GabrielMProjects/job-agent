"""Laden von Konfigurationsdateien (YAML) und .env.

Nutzt PyYAML, wenn installiert. Falls PyYAML fehlt, greift ein kleiner
eingebauter Parser für das benötigte YAML-Subset (Mappings, verschachtelte
Mappings, Listen von Skalaren). So läuft das Projekt auch ohne externe
Abhängigkeiten.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:  # bevorzugt echtes PyYAML
    import yaml  # type: ignore

    _HAVE_YAML = True
except Exception:  # pragma: no cover - Fallback-Pfad
    _HAVE_YAML = False

from models import Profile


# ---------------------------------------------------------------------------
# Minimaler YAML-Fallback-Parser
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    """Entfernt Kommentare (# ...), respektiert einfache Anführungszeichen."""
    in_single = in_double = False
    out = []
    for i, c in enumerate(line):
        if c == '"' and not in_single:
            in_double = not in_double
        elif c == "'" and not in_double:
            in_single = not in_single
        elif c == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                break
        out.append(c)
    return "".join(out)


def _parse_scalar(text: str) -> Any:
    s = text.strip()
    if not s:
        return None
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_inline_list(text: str) -> list:
    inner = text.strip()[1:-1].strip()
    if not inner:
        return []
    return [_parse_scalar(part) for part in inner.split(",")]


def _minimal_yaml_load(content: str) -> Dict[str, Any]:
    """Parst ein einfaches YAML-Subset über die Einrückung."""
    lines = []
    for raw in content.splitlines():
        line = _strip_comment(raw).rstrip()
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip()))

    pos = [0]

    def parse_block(min_indent: int):
        result: Any = None
        while pos[0] < len(lines):
            indent, content_line = lines[pos[0]]
            if indent < min_indent:
                break
            if content_line.startswith("- "):
                if result is None:
                    result = []
                pos[0] += 1
                result.append(_parse_scalar(content_line[2:]))
                continue
            # key: value
            if result is None:
                result = {}
            key, _, val = content_line.partition(":")
            key = key.strip()
            val = val.strip()
            pos[0] += 1
            if val == "":
                if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                    result[key] = parse_block(indent + 1)
                else:
                    result[key] = None
            elif val.startswith("["):
                result[key] = _parse_inline_list(val)
            else:
                result[key] = _parse_scalar(val)
        return result if result is not None else {}

    return parse_block(0)


def load_yaml(path: str | os.PathLike) -> Dict[str, Any]:
    """Lädt eine YAML-Datei als dict (UTF-8)."""
    text = Path(path).read_text(encoding="utf-8")
    if _HAVE_YAML:
        return yaml.safe_load(text) or {}
    return _minimal_yaml_load(text)


# ---------------------------------------------------------------------------
# Profil & Suchkonfiguration
# ---------------------------------------------------------------------------
def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Mischt `overlay` über `base` (overlay gewinnt; dicts werden rekursiv gemischt)."""
    result = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_profile(path: str | os.PathLike) -> Profile:
    """Lädt profile.yaml. Existiert daneben eine (ignorierte) `profile.local.yaml`,
    überschreiben deren Werte die Platzhalter aus profile.yaml. So bleiben echte
    Daten (Name, E-Mail) lokal und landen nie im öffentlichen Repo."""
    data = load_yaml(path) or {}
    local = Path(path).with_name("profile.local.yaml")
    if local.exists():
        data = _deep_merge(data, load_yaml(local) or {})
    return Profile.from_dict(data)


def load_search_config(path: str | os.PathLike) -> Dict[str, Any]:
    if not Path(path).exists():
        return {}
    return load_yaml(path) or {}


# ---------------------------------------------------------------------------
# Freitext-Kontext: Lebenslauf (CV) und Zeugnisse
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_text(path: str | os.PathLike) -> str:
    """Liest eine Textdatei (UTF-8). Fehlt sie, kommt ein leerer String zurück."""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def load_cv(path: str | os.PathLike | None = None) -> str:
    """Lädt den Lebenslauf als reinen Text (Default: config/cv.md)."""
    return load_text(path or (_CONFIG_DIR / "cv.md"))


def load_zeugnis(path: str | os.PathLike | None = None) -> str:
    """Lädt die Zeugnisse als reinen Text (Default: config/zeugnis.md)."""
    return load_text(path or (_CONFIG_DIR / "zeugnis.md"))


def load_reference(path: str | os.PathLike | None = None) -> str:
    """Lädt das Referenz-Anschreiben als Stil-Vorlage (Default:
    config/anschreiben_referenz.md)."""
    return load_text(path or (_CONFIG_DIR / "anschreiben_referenz.md"))


# ---------------------------------------------------------------------------
# .env laden (ohne python-dotenv)
# ---------------------------------------------------------------------------
def load_env(path: str | os.PathLike) -> Dict[str, str]:
    """Liest eine .env-Datei und schreibt Werte in os.environ (überschreibt nicht)."""
    env: Dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = value
        os.environ.setdefault(key, value)
    return env
