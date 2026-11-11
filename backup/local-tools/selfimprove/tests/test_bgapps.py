import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import bgapps
import si


class TestBgappsSweep(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        # Ensure SELFIMPROVE_TESTING is set so real inventory is never invoked
        self.env_patcher = patch.dict(os.environ, {'SELFIMPROVE_TESTING': '1'})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def test_run_inventory_safety_never_runs_real_inventory(self):
        """Under testing conditions, run_inventory never invokes powershell.exe."""
        with patch('subprocess.run') as mock_run:
            out = bgapps.run_inventory()
            self.assertEqual(out, "")
            mock_run.assert_not_called()

    def test_run_inventory_synthetic_env(self):
        """BGAPPS_SYNTHETIC_OUTPUT takes precedence and returns immediately without running powershell."""
        synthetic_sample = "== startup\nFakeApp | HKCU\\Run | Fake.exe"
        with patch.dict(os.environ, {'BGAPPS_SYNTHETIC_OUTPUT': synthetic_sample}), patch('subprocess.run') as mock_run:
            out = bgapps.run_inventory()
            self.assertEqual(out, synthetic_sample)
            mock_run.assert_not_called()

    def test_load_decisions_from_file(self):
        """load_decisions extracts tokens from decisions.md."""
        decisions_file = self.root / 'test_decisions.md'
        decisions_file.write_text(
            "# Decisions\n\n"
            "## Turned off\n"
            "- Autostart off: Discord, Brave updater, Edge auto-launch.\n"
            "- Background access off: Phone Link, Solitaire.\n\n"
            "## Kept, with reason\n"
            "- Tailscale: network access. Docker Desktop: autostart off.\n"
            "- NVIDIA, Intel graphics: drivers.\n",
            encoding='utf-8',
        )
        decisions = bgapps.load_decisions(decisions_file)
        self.assertIn('discord', decisions)
        self.assertIn('brave', decisions)
        self.assertIn('tailscale', decisions)
        self.assertIn('docker', decisions)
        self.assertIn('nvidia', decisions)
        self.assertIn('solitaire', decisions)

    def test_parse_inventory_output_all_sections(self):
        """parse_inventory_output parses all 6 sections correctly."""
        raw = (
            "== webview2 owners\n"
            "1001 <- Slack.exe | --webview-exe-name=slack.exe\n"
            "== msedge main\n"
            "\"C:\\Program Files\\Edge\\msedge.exe\" --no-startup-window\n"
            "== startup\n"
            "Steam | HKCU\\SOFTWARE\\Run | \"C:\\Steam\\steam.exe\"\n"
            "OneDrive | HKCU\\SOFTWARE\\Run | \"C:\\OneDrive\\onedrive.exe\"\n"
            "== startup approved (02/06=on, 03/07=off)\n"
            "HKCU Discord 3\n"
            "HKCU Steam 2\n"
            "HKLM HardwareAgent 2\n"
            "== background-allowed store apps\n"
            "Microsoft.BingWeather_8wekyb3d8bbwe disabled=1\n"
            "SpotifyAB.SpotifyMusic_zpdnekdrzrea0 disabled=0\n"
            "== services (auto, running, non-Microsoft)\n"
            "Tailscale | Auto | Tailscale IPN\n"
            "MinerService | Auto | Crypto Miner Daemon\n"
        )
        items = bgapps.parse_inventory_output(raw)
        self.assertEqual(len(items), 11)

        wv = [i for i in items if i['category'] == 'webview2']
        self.assertEqual(len(wv), 1)
        self.assertEqual(wv[0]['name'], 'Slack.exe')

        edge = [i for i in items if i['category'] == 'msedge']
        self.assertEqual(len(edge), 1)

        startups = [i for i in items if i['category'] == 'startup']
        self.assertEqual(len(startups), 2)
        self.assertEqual(startups[0]['name'], 'Steam')

        appr = [i for i in items if i['category'] == 'startup_approved']
        self.assertEqual(len(appr), 3)
        self.assertFalse(appr[0]['enabled'])  # byte 3 = disabled
        self.assertTrue(appr[1]['enabled'])   # byte 2 = enabled

        store = [i for i in items if i['category'] == 'store_app']
        self.assertEqual(len(store), 2)
        self.assertTrue(store[0]['disabled'])
        self.assertFalse(store[1]['disabled'])

        svcs = [i for i in items if i['category'] == 'service']
        self.assertEqual(len(svcs), 2)
        self.assertEqual(svcs[0]['name'], 'Tailscale')
        self.assertEqual(svcs[1]['name'], 'MinerService')

    def test_classify_uncovered_proposals_and_ask_owner(self):
        """Uncovered items are classified into 'would disable' (with command) or 'ask the owner' (risky)."""
        decisions_file = self.root / 'decisions.md'
        decisions_file.write_text(
            "# Decisions\n\n"
            "## Turned off\n- Autostart off: Discord.\n\n"
            "## Kept, with reason\n- Tailscale: network.\n- OneDrive: backup.\n",
            encoding='utf-8',
        )
        raw = (
            "== startup\n"
            "Discord | HKCU\\Run | \"C:\\Discord.exe\"\n"
            "Steam | HKCU\\Run | \"C:\\Steam.exe\"\n"
            "OpenVPN GUI | HKCU\\Run | \"C:\\VPN\\openvpn-gui.exe\"\n"
            "== startup approved (02/06=on, 03/07=off)\n"
            "HKLM DisplayDriverControl 2\n"
            "HKCU Discord 3\n"
            "== background-allowed store apps\n"
            "SpotifyAB.SpotifyMusic_zpdnekdrzrea0 disabled=0\n"
            "Microsoft.YourPhone_8wekyb3d8bbwe disabled=1\n"
            "== services (auto, running, non-Microsoft)\n"
            "Tailscale | Auto | Tailscale IPN\n"
            "UnknownSvc | Auto | Unknown Background Daemon\n"
        )
        decisions = bgapps.load_decisions(decisions_file)
        items = bgapps.parse_inventory_output(raw)
        would_disable, ask_owner = bgapps.classify_uncovered(items, decisions)

        # Discord (covered), Tailscale (covered), YourPhone (disabled=1) -> not in either list
        # Steam -> would disable
        steam_items = [i for i in would_disable if i['name'] == 'Steam']
        self.assertEqual(len(steam_items), 1)
        self.assertIn("New-ItemProperty", steam_items[0]['command'])
        self.assertIn("'Steam'", steam_items[0]['command'])

        # Spotify -> would disable
        spotify_items = [i for i in would_disable if 'Spotify' in i['name']]
        self.assertEqual(len(spotify_items), 1)
        self.assertIn("Set-ItemProperty", spotify_items[0]['command'])

        # OpenVPN -> risky keyword (vpn) -> ask the owner
        vpn_items = [i for i in ask_owner if i['name'] == 'OpenVPN GUI']
        self.assertEqual(len(vpn_items), 1)
        self.assertIn('vpn', vpn_items[0]['reason'].lower())

        # DisplayDriverControl (HKLM) -> risky (HKLM + driver) -> ask the owner
        driver_items = [i for i in ask_owner if i['name'] == 'DisplayDriverControl']
        self.assertEqual(len(driver_items), 1)

        # UnknownSvc (service) -> risky (service requires admin) -> ask the owner
        svc_items = [i for i in ask_owner if i['name'] == 'UnknownSvc']
        self.assertEqual(len(svc_items), 1)
        self.assertIn('Windows service', svc_items[0]['reason'])

    def test_deduplication(self):
        """Items appearing in both Win32_StartupCommand and StartupApproved are deduplicated."""
        raw = (
            "== startup\n"
            "Steam | HKCU\\Run | \"C:\\Steam.exe\"\n"
            "== startup approved (02/06=on, 03/07=off)\n"
            "HKCU Steam 2\n"
        )
        items = bgapps.parse_inventory_output(raw)
        would_disable, ask_owner = bgapps.classify_uncovered(items, set())
        steam_proposals = [i for i in would_disable if i['name'] == 'Steam']
        self.assertEqual(len(steam_proposals), 1)

    def test_bgapps_sweep_lines_all_covered(self):
        """When everything in inventory is covered, lines show 'None this week.'."""
        decisions_file = self.root / 'decisions.md'
        decisions_file.write_text("- Discord\n- Tailscale\n", encoding='utf-8')
        raw = (
            "== startup\nDiscord | HKCU\\Run | Discord.exe\n"
            "== services (auto, running, non-Microsoft)\nTailscale | Auto | Tailscale\n"
        )
        lines = bgapps.bgapps_sweep_lines(
            inventory_output=raw,
            decisions_path=decisions_file,
        )
        rendered = '\n'.join(lines)
        self.assertIn('## Background apps sweep', rendered)
        self.assertIn('### Would disable (user-level proposals)', rendered)
        self.assertIn('### Ask the owner (risky / admin)', rendered)
        self.assertEqual(rendered.count('None this week.'), 2)

    def test_bgapps_sweep_lines_with_findings(self):
        """Markdown output includes proposals and ask-owner entries."""
        decisions_file = self.root / 'decisions.md'
        decisions_file.write_text("- Tailscale\n", encoding='utf-8')
        raw = (
            "== startup\nSteam | HKCU\\Run | Steam.exe\n"
            "== services (auto, running, non-Microsoft)\nUnknownSvc | Auto | Unknown Daemon\n"
        )
        lines = bgapps.bgapps_sweep_lines(
            inventory_output=raw,
            decisions_path=decisions_file,
        )
        rendered = '\n'.join(lines)
        self.assertIn('## Background apps sweep', rendered)
        self.assertIn("- Steam (startup): `New-ItemProperty", rendered)
        self.assertIn("- UnknownSvc (service): Windows service", rendered)

    def test_si_review_includes_bgapps_sweep(self):
        """si.py review writes the background apps sweep section into the generated report."""
        reports_dir = self.root / 'reports'
        reports_dir.mkdir(parents=True, exist_ok=True)
        tunables_file = self.root / 'tunables.json'
        proposals_file = self.root / 'proposals.jsonl'
        records_file = self.root / 'records.jsonl'

        synth_inventory = (
            "== startup\n"
            "UnapprovedApp | HKCU\\Run | \"C:\\UnapprovedApp.exe\"\n"
            "== services (auto, running, non-Microsoft)\n"
            "RiskySvc | Auto | Risky Daemon\n"
        )

        with patch.object(si, 'REPORTS', reports_dir), \
             patch.object(si, 'TUNABLES', tunables_file), \
             patch.object(si, 'PROPOSALS', proposals_file), \
             patch.object(si, 'RECORDS', records_file), \
             patch.object(si.subprocess, 'run') as mock_run:
            si.main(['review', '--days', '7', '--bgapps-synthetic', synth_inventory])

        report_files = list(reports_dir.glob('*.md'))
        self.assertTrue(len(report_files) >= 1)
        report_text = report_files[0].read_text(encoding='utf-8')
        self.assertIn('## Background apps sweep', report_text)
        self.assertIn('### Would disable (user-level proposals)', report_text)
        self.assertIn('UnapprovedApp', report_text)
        self.assertIn('### Ask the owner (risky / admin)', report_text)
        self.assertIn('RiskySvc', report_text)

    def test_si_cli_bgapps_command(self):
        """si.py bgapps runs the sweep and outputs markdown to stdout."""
        synth_inventory = "== startup\nSteam | HKCU\\Run | Steam.exe\n"
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = si.main(['bgapps', '--synthetic', synth_inventory])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn('## Background apps sweep', out)
        self.assertIn('Steam', out)


if __name__ == '__main__':
    unittest.main()
