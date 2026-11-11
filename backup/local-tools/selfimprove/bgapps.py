"""Weekly background apps sweep for self-improvement review.

Inventories background applications, autostarts, store apps, and non-Microsoft services
using ~/local-llm/guard/bgapps-inventory.ps1 and cross-references against decisions
in ~/local-llm/guard/bgapps-decisions.md.

Auto-disable is proposal-only:
- Clearly unneeded user-level items are listed under 'would disable' with their PowerShell command.
- Risky items (services, machine-wide HKLM autostarts, drivers, network/security) are listed under 'ask the owner'.
- Windows settings are never modified automatically.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_INVENTORY_SCRIPT = Path('/home/chopp/local-llm/guard/bgapps-inventory.ps1')
DEFAULT_DECISIONS_FILE = Path('/home/chopp/local-llm/guard/bgapps-decisions.md')
DEFAULT_POWERSHELL_EXE = Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')

# Canonical synonyms and package/service name aliases for items in decisions.md
KNOWN_APP_ALIASES: dict[str, list[str]] = {
    'discord': ['discord'],
    'brave updater': ['brave', 'bravesoftware update', 'braveupdate'],
    'edge auto-launch': ['microsoftedgeautolaunch', 'msedgeautolaunch', 'edge', 'msedge'],
    'phone link': ['phonelink', 'phoneexperiencehost', 'yourphone', 'microsoft.yourphone'],
    'cross-device': ['crossdevice', 'crossdeviceservice', 'crossdeviceresume', 'microsoftwindows.crossdevice'],
    'copilot': ['copilot', 'microsoft.copilot'],
    'teams': ['teams', 'microsoftteams'],
    'xbox overlays/identity': ['xbox', 'gamingapp', 'microsoft.xbox', 'microsoft.gamingapp'],
    'news': ['bingnews', 'microsoft.bingnews', 'news'],
    'weather': ['bingweather', 'microsoft.bingweather', 'weather'],
    'solitaire': ['solitaire', 'microsoftsolitaire', 'microsoft.microsoftsolitaire'],
    'people': ['people', 'microsoft.people'],
    'movies': ['zunevideo', 'zunemusic', 'movies', 'microsoft.zunevideo', 'microsoft.zunemusic'],
    'feedback hub': ['feedbackhub', 'windowsfeedbackhub', 'microsoft.windowsfeedbackhub'],
    'search box web highlights': ['searchsettings', 'dynamicsearchbox', 'searchhost'],
    'crossdeviceservice': ['crossdeviceservice'],
    'crossdeviceresume': ['crossdeviceresume'],
    'smartbytetelemetry': ['smartbytetelemetry'],
    'hidden edge instance': ['msedge', 'edge'],
    'smartbyte analytics': ['smartbyte analytics', 'smartbyte analytics service', 'smartbyteanalyticsservice'],
    'smartbyte network': ['smartbyte network', 'smartbyte network service', 'smartbytenetworkservice'],
    'dell digital delivery': ['dell digital delivery', 'dell digital delivery services', 'delldigitaldeliveryservices'],
    'dell supportassist remediation': ['dell supportassist remediation', 'supportassistremediation', 'supportassist'],
    'logitech download assistant': ['logi download assistant', 'logitech download assistant', 'logidownloadassistant'],
    'edge startup boost and background mode': ['startupboostenabled', 'backgroundmodeenabled', 'msedge', 'edge'],
    'google drive': ['google drive', 'googledrive', 'googledrivefs'],
    'onedrive': ['onedrive'],
    'tailscale': ['tailscale', 'tailscale ipn', 'tailscale-ipn'],
    'docker desktop': ['docker desktop', 'docker', 'com.docker.service'],
    'msrdc': ['msrdc'],
    'wsl': ['wsl', 'wslhost', 'wslservice'],
    'wslhost': ['wslhost'],
    'claude and codex sandbox services': ['claude', 'codex'],
    'nvidia': ['nvidia', 'nvcontainer', 'nvdisplay', 'nvtelemetry'],
    'intel graphics': ['intel', 'cui_dservice', 'igfx'],
    'realtek and waves audio': ['realtek', 'rtkaudioservice', 'waves', 'wavessyssvc', 'waves audio'],
    'dell client management': ['dell client management', 'dellclientmanagement'],
    'dell fusion': ['dell fusion', 'dell fusion service', 'dellfusionservice'],
    'rivet ap selector': ['rivet ap selector', 'rivetapselector', 'rivet'],
    'windows defender': ['windefend', 'securityhealth', 'defender', 'security tray', 'msmpeng'],
    'shell processes': ['explorer', 'shellexperiencehost', 'startmenuexperiencehost', 'searchhost'],
    'mcafee webadvisor': ['mcafee', 'webadvisor'],
    'mcafee': ['mcafee'],
}

RISKY_KEYWORDS = {
    'driver', 'audio', 'sound', 'graphics', 'display', 'video',
    'intel', 'nvidia', 'amd', 'realtek', 'waves',
    'network', 'ethernet', 'wifi', 'wlan', 'bluetooth', 'vpn', 'wireguard', 'ipn',
    'defender', 'security', 'antivirus', 'firewall',
    'backup', 'sync', 'onedrive', 'googledrive', 'dropbox',
    'virtualbox', 'vmware', 'hyper-v', 'wsl', 'docker',
    'system', 'windows', 'microsoft',
}


def to_windows_path(p: Path | str) -> str:
    """Convert WSL / Linux path to Windows UNC path if possible."""
    s = str(p)
    if s.startswith('/'):
        try:
            res = subprocess.run(['wslpath', '-w', s], capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except Exception:
            pass
    return s


def is_testing_environment() -> bool:
    """Return True if running under unittest, pytest, or testing environment."""
    if os.environ.get('SELFIMPROVE_TESTING') == '1':
        return True
    if 'unittest' in sys.modules or 'pytest' in sys.modules:
        return True
    return False


def run_inventory(
    script_path: Path | str | None = None,
    powershell_exe: Path | str | None = None,
) -> str:
    """Run bgapps-inventory.ps1 via powershell.exe -NoProfile -File.

    Safety:
    - Never runs during unit testing or when SELFIMPROVE_TESTING is set.
    - Honors BGAPPS_SYNTHETIC_OUTPUT environment variable for testing.
    - Fails safely and returns empty string if PowerShell or script is unavailable.
    """
    synthetic = os.environ.get('BGAPPS_SYNTHETIC_OUTPUT')
    if synthetic is not None:
        return synthetic

    if is_testing_environment():
        return ""

    script = Path(script_path or os.environ.get('BGAPPS_INVENTORY_SCRIPT') or DEFAULT_INVENTORY_SCRIPT)
    ps_bin = Path(powershell_exe or os.environ.get('POWERSHELL_EXE') or DEFAULT_POWERSHELL_EXE)

    if not script.exists() or not ps_bin.exists():
        return ""

    try:
        win_path = to_windows_path(script)
        cmd = [str(ps_bin), '-NoProfile', '-File', win_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if hasattr(res, 'stdout') and isinstance(res.stdout, str):
            return res.stdout
    except Exception:
        pass
    return ""


def clean_token(s: str) -> str:
    """Normalize string for fuzzy pattern matching: lowercase alphanumeric only."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def load_decisions(decisions_path: Path | str | None = None) -> set[str]:
    """Parse bgapps-decisions.md and return normalized covered patterns/tokens."""
    path = Path(decisions_path or os.environ.get('BGAPPS_DECISIONS_PATH') or DEFAULT_DECISIONS_FILE)
    if not path.exists():
        return set()

    covered_tokens: set[str] = set()

    # Pre-seed with all known aliases
    for key, aliases in KNOWN_APP_ALIASES.items():
        covered_tokens.add(clean_token(key))
        for a in aliases:
            covered_tokens.add(clean_token(a))

    content = path.read_text(encoding='utf-8')
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith('- '):
            continue
        text = line[2:].strip()
        # Split compound clauses (e.g., "Google Drive: captain's files. OneDrive: backs up Desktop and Documents.")
        clauses = re.split(r'\.\s+', text)
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            # Extract header/name before colon
            parts = clause.split(':', 1)
            raw_names = parts[1] if len(parts) > 1 else parts[0]
            # Names might be comma/semicolon separated (e.g. "Discord, Brave updater, Edge auto-launch")
            sub_names = re.split(r'[,;]', raw_names)
            for sub in sub_names:
                sub = sub.strip()
                # Remove common parenthetical remarks
                sub_clean = re.sub(r'\(.*?\)', '', sub).strip()
                token = clean_token(sub_clean)
                if token and len(token) >= 2:
                    covered_tokens.add(token)
                    # Check known aliases
                    for key, aliases in KNOWN_APP_ALIASES.items():
                        if token in clean_token(key) or clean_token(key) in token:
                            for a in aliases:
                                covered_tokens.add(clean_token(a))

    return covered_tokens


def parse_inventory_output(raw_text: str) -> list[dict[str, Any]]:
    """Parse the sections output by bgapps-inventory.ps1 into structured items."""
    items: list[dict[str, Any]] = []
    current_section = ''

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('== '):
            header = line[3:].strip().lower()
            if 'webview2' in header:
                current_section = 'webview2'
            elif 'msedge' in header:
                current_section = 'msedge'
            elif 'startup approved' in header:
                current_section = 'startup_approved'
            elif 'startup' in header:
                current_section = 'startup'
            elif 'background' in header:
                current_section = 'store_app'
            elif 'services' in header:
                current_section = 'service'
            else:
                current_section = header
            continue

        if current_section == 'webview2':
            # e.g.: 1234 <- Slack.exe | --webview-exe-name=slack.exe
            m = re.match(r'^(\d+)\s*<-\s*([^|]+)\s*\|\s*(.*)$', line)
            if m:
                items.append({
                    'category': 'webview2',
                    'pid': m.group(1).strip(),
                    'name': m.group(2).strip(),
                    'details': m.group(3).strip(),
                    'raw': line,
                })
        elif current_section == 'msedge':
            # Main msedge command line
            items.append({
                'category': 'msedge',
                'name': 'msedge',
                'command': line,
                'raw': line,
            })
        elif current_section == 'startup':
            # e.g.: Discord | HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run | "C:\Users\..."
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 3:
                items.append({
                    'category': 'startup',
                    'name': parts[0],
                    'location': parts[1],
                    'command': parts[2],
                    'raw': line,
                })
        elif current_section == 'startup_approved':
            # e.g.: HKCU Discord 2  (or HKLM Logi Download Assistant 3)
            parts = line.split(maxsplit=2)
            if len(parts) >= 3:
                hive = parts[0]
                name = parts[1]
                byte_val = parts[2]
                enabled = byte_val in ('2', '6')
                items.append({
                    'category': 'startup_approved',
                    'hive': hive,
                    'name': name,
                    'byte': byte_val,
                    'enabled': enabled,
                    'raw': line,
                })
        elif current_section == 'store_app':
            # e.g.: Microsoft.BingWeather_8wekyb3d8bbwe disabled=0
            m = re.match(r'^(\S+)\s+disabled=(\d+)$', line)
            if m:
                pkg = m.group(1).strip()
                disabled = m.group(2).strip() == '1'
                items.append({
                    'category': 'store_app',
                    'name': pkg,
                    'disabled': disabled,
                    'raw': line,
                })
        elif current_section == 'service':
            # e.g.: Tailscale | Auto | Tailscale IPN
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 3:
                items.append({
                    'category': 'service',
                    'name': parts[0],
                    'start_mode': parts[1],
                    'display_name': parts[2],
                    'raw': line,
                })

    return items


def is_covered(item: dict[str, Any], decisions: set[str]) -> bool:
    """Check if an inventory item is covered by bgapps-decisions."""
    name = item.get('name', '')
    display = item.get('display_name', '')
    cmd = item.get('command', '')
    details = item.get('details', '')

    targets = [clean_token(t) for t in (name, display, cmd, details) if t]

    for target in targets:
        if not target:
            continue
        for dec in decisions:
            if not dec:
                continue
            if dec in target or target in dec:
                return True

    # Special handling for msedge main command line
    if item.get('category') == 'msedge':
        # Default hidden edge instance or regular edge instances are covered by Edge decisions
        if 'msedge' in decisions or 'edge' in decisions:
            return True

    return False


def is_risky_item(item: dict[str, Any]) -> tuple[bool, str]:
    """Determine whether an uncovered item is risky (belongs in 'ask the owner')."""
    cat = item.get('category', '')

    # 1. Windows services require admin and affect system/network/hardware
    if cat == 'service':
        return True, "Windows service (requires admin / potential system or service impact)"

    # 2. HKLM startup items are machine-wide and require admin
    loc = item.get('location', '').lower()
    hive = item.get('hive', '').upper()
    if 'hklm' in loc or hive == 'HKLM':
        return True, "Machine-wide (HKLM) autostart (requires admin)"

    # 3. Check for risky keywords in name, display name, or command
    combined = f"{item.get('name', '')} {item.get('display_name', '')} {item.get('command', '')} {item.get('details', '')}".lower()
    for kw in RISKY_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', combined) or kw in combined:
            return True, f"Risky user-level component ({kw} related)"

    return False, ""


def make_disable_command(item: dict[str, Any]) -> str:
    """Produce the safe user-level PowerShell command to disable the item."""
    cat = item.get('category', '')
    name = item.get('name', '')

    if cat in ('startup', 'startup_approved'):
        return f"New-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run' -Name '{name}' -PropertyType Binary -Value ([byte[]](3,0,0,0,0,0,0,0,0,0,0,0)) -Force"
    elif cat == 'store_app':
        return f"Set-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\BackgroundAccessApplications\\{name}' -Name Disabled -Value 1 -Type DWord"
    elif cat == 'webview2':
        proc = re.sub(r'\.exe$', '', name, flags=re.IGNORECASE)
        return f"Stop-Process -Name '{proc}' -Force"
    elif cat == 'msedge':
        return "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -match '--no-startup-window' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

    return f"# User-level disable command for {name}"


def classify_uncovered(
    items: list[dict[str, Any]],
    decisions: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter uncovered items and split into 'would disable' and 'ask the owner'."""
    would_disable: list[dict[str, Any]] = []
    ask_owner: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for item in items:
        if is_covered(item, decisions):
            continue

        # If a startup item is already turned off in StartupApproved, it is already disabled
        if item.get('category') == 'startup_approved' and not item.get('enabled', True):
            continue
        # If a store app is already disabled, background access is already off
        if item.get('category') == 'store_app' and item.get('disabled', False):
            continue

        name = item.get('name', '')
        cat = item.get('category', '')
        # Deduplicate identical startup items (e.g. from Win32_StartupCommand and StartupApproved)
        dedup_cat = 'startup' if cat in ('startup', 'startup_approved') else cat
        dedup_key = f"{dedup_cat}:{name.lower()}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        risky, reason = is_risky_item(item)
        if risky:
            ask_owner.append({
                'name': name,
                'category': cat,
                'reason': reason,
                'item': item,
            })
        else:
            would_disable.append({
                'name': name,
                'category': cat,
                'command': make_disable_command(item),
                'item': item,
            })

    return would_disable, ask_owner


def bgapps_sweep_lines(
    inventory_output: str | None = None,
    decisions_path: Path | str | None = None,
    script_path: Path | str | None = None,
    powershell_exe: Path | str | None = None,
) -> list[str]:
    """Generate the markdown lines for the background app sweep section in si.py review."""
    lines = [
        '',
        '## Background apps sweep',
        '',
    ]

    raw_inventory = inventory_output
    if raw_inventory is None:
        raw_inventory = run_inventory(script_path=script_path, powershell_exe=powershell_exe)

    if not raw_inventory:
        lines.append('No inventory data collected (script unavailable or no background apps detected).')
        return lines

    decisions = load_decisions(decisions_path)
    items = parse_inventory_output(raw_inventory)
    would_disable, ask_owner = classify_uncovered(items, decisions)

    lines.append('### Would disable (user-level proposals)')
    if would_disable:
        for item in would_disable:
            lines.append(f"- {item['name']} ({item['category']}): `{item['command']}`")
    else:
        lines.append('None this week.')
    lines.append('')

    lines.append('### Ask the owner (risky / admin)')
    if ask_owner:
        for item in ask_owner:
            lines.append(f"- {item['name']} ({item['category']}): {item['reason']}")
    else:
        lines.append('None this week.')

    return lines
