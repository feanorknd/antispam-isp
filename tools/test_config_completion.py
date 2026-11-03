"""Contrato permanente: upgrade completa el .conf sin cambiar sus valores."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import outbound_spam as m
import runtime


class ConfigCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="config-completion-", dir="/root" if os.geteuid() == 0 else "/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "monitor.conf"
        self.environment = patch.dict(os.environ, OUTBOUND_SPAM_DATA=str(self.root / "data"),
                                      OUTBOUND_SPAM_LOG=str(self.root / "audit.log"))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.original = '# Mis valores\nnotify=false\nreport=()\nmax_sent=713\nmonitor_local=false\n'
        self.config.write_text(self.original)
        self.config.chmod(0o600)

    def complete(self, base=ROOT, writer=m.atomic_write):
        return runtime.migrate_config(self.config, base, m.Config.from_bytes, writer)

    def test_every_serialized_setting_has_an_example_and_private_definition(self):
        _, serialized = runtime.validate_config(self.config.read_bytes(), ROOT, m.Config.from_bytes)
        profile = m.Config.from_bytes(serialized)
        declarations = runtime.example_declarations(ROOT)
        self.assertFalse(set(profile.v) - set(declarations))
        completed = self.complete()
        self.assertIn("block_local", completed["added"])
        self.assertNotIn("monitor_local", completed["added"])
        self.assertEqual(runtime.assigned_names(self.config.read_text()), set(declarations))
        self.assertTrue(self.config.read_text().endswith(self.original))

    def test_values_empty_arrays_and_associative_rules_are_preserved(self):
        text = self.original + 'smtp_password=' + shlex.quote("a $literal # quote'") + '\nignore=(user@example.org)\n' + \
               'declare -A custom_limits=(\n ["user@example.org"]="750:900"\n ["local:5009"]="20:300"\n)\n'
        self.config.write_text(text)
        before, _ = runtime.validate_config(self.config.read_bytes(), ROOT, m.Config.from_bytes)
        self.complete()
        after, _ = runtime.validate_config(self.config.read_bytes(), ROOT, m.Config.from_bytes)
        self.assertEqual(after.v, before.v)
        self.assertTrue(self.config.read_text().endswith(text))

    def test_complete_file_is_not_rewritten_on_second_upgrade(self):
        self.complete()
        before = self.config.stat()
        content = self.config.read_bytes()
        self.assertEqual(self.complete()["added"], [])
        after = self.config.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(self.config.read_bytes(), content)

    def test_template_growth_adds_future_variables_and_multiline_arrays(self):
        base = self.root / "candidate"
        shutil.copytree(ROOT / "lib", base / "lib")
        example = (ROOT / "outbound-spam.conf.example").read_text()
        (base / "outbound-spam.conf.example").write_text(example + '\nfuture_knob="hello world"\n' +
            'future_array=(\n "one two"\n "literal # \\$value"\n)\ndeclare -A future_map=(["one two"]="literal \\$data")\n')
        self.complete()
        result = self.complete(base)
        self.assertEqual(result["added"], ["future_array", "future_knob", "future_map"])
        proc = subprocess.run(["bash", "-c", 'source "$1"; declare -p future_knob future_array future_map', "test", str(self.config)],
                              capture_output=True, text=True, check=True)
        self.assertIn("hello world", proc.stdout)
        self.assertIn("one two", proc.stdout)
        self.assertIn("literal", proc.stdout)
        self.assertFalse(self.complete(base)["added"])

    def test_dynamic_assignments_after_prefix_keep_precedence(self):
        text = self.original + 'if true; then block_local=false; fi\nexport status_http_port=34567\n' + \
               'declare -- smtp_host="localhost"\nmax_sent=$((700 + 13))\n'
        self.config.write_text(text)
        self.complete()
        config, _ = runtime.validate_config(self.config.read_bytes(), ROOT, m.Config.from_bytes)
        self.assertFalse(config["block_local"])
        self.assertEqual(config["status_http_port"], 34567)
        self.assertEqual(config["max_sent"], 713)

    def test_missing_arrays_use_typed_example_defaults(self):
        self.complete()
        text = self.config.read_text()
        self.assertIn("declare -A custom_limits=", text)
        self.assertIn("declare -a local_domains=", text)
        self.assertIn('smtp_password=""', text)

    def test_invalid_config_never_replaced_by_defaults(self):
        for text in ('notify=false\nmax_sent=0\n', 'notify=false\nreport=(\n', ''):
            self.config.write_text(text)
            with self.assertRaises((ValueError, m.MonitorError)):
                self.complete()
            self.assertEqual(self.config.read_text(), text)

    def test_cache_and_private_backup_match_completed_file(self):
        result = self.complete()
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        backup = Path(result["backup"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(backup.read_text())["config"], self.original)
        saved = json.loads(runtime.config_cache(self.config).read_text())
        import hashlib
        self.assertEqual(saved["source_sha256"], hashlib.sha256(self.config.read_bytes()).hexdigest())

    def test_cache_failure_restores_original_file_and_previous_cache(self):
        cache = runtime.config_cache(self.config)
        m.atomic_write(cache, '{"fixture":"old cache"}')
        def fail_once(path, data, mode=0o600):
            if Path(path) == cache and "source_sha256" in data:
                raise OSError("simulated cache failure")
            m.atomic_write(path, data, mode)
        with self.assertRaises(OSError):
            self.complete(writer=fail_once)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(cache.read_text(), '{"fixture":"old cache"}')

    def test_concurrent_external_edit_is_preserved(self):
        real_read = runtime.stable_bytes
        calls = []
        def changing(path):
            calls.append(path)
            if len(calls) == 2:
                self.config.write_text(self.original + 'max_sent=999\n')
            return real_read(path)
        with patch.object(runtime, "stable_bytes", side_effect=changing), self.assertRaises(ValueError):
            self.complete()
        self.assertTrue(self.config.read_text().endswith('max_sent=999\n'))

    def test_rollback_accepts_only_untouched_generated_prefix(self):
        self.complete()
        updated = self.config.read_bytes()
        self.assertTrue(runtime.completed_config_matches(self.original.encode(), updated))
        self.assertFalse(runtime.completed_config_matches(self.original.encode(), updated.replace(b'block_local="true"', b'block_local="false"')))
        self.assertFalse(runtime.completed_config_matches(self.original.encode(), updated + b'max_sent=900\n'))
        runtime.rollback_config_completion(self.config, self.original.encode(), ROOT, m.Config.from_bytes, m.atomic_write)
        self.assertEqual(self.config.read_text(), self.original)


if __name__ == "__main__":
    unittest.main()
