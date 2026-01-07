import tempfile
import unittest
from pathlib import Path

from resolve_mod_deps import compute_missing, scan_mod_infos
from rp_mod_installer import ini_read, ini_set


class SmokeTests(unittest.TestCase):
    def test_ini_parse_and_dependency_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ini_path = base / "pzserver.ini"
            ini_path.write_text(
                "# Sample PZ server ini\nWorkshopItems=123\nMods=Core\nMap=Muldraugh, KY\n",
                encoding="utf-8",
            )

            lines = ini_read(ini_path)
            updated = ini_set(lines, "WorkshopItems", "123;456")
            updated = ini_set(updated, "Mods", "Core;Extra")

            updated_text = "\n".join(updated)
            self.assertIn("WorkshopItems=123;456", updated_text)
            self.assertIn("Mods=Core;Extra", updated_text)

            workshop_dir = base / "steamapps" / "workshop" / "content" / "108600"
            mod_a_dir = workshop_dir / "111"
            mod_b_dir = workshop_dir / "222"
            mod_a_dir.mkdir(parents=True)
            mod_b_dir.mkdir(parents=True)

            (mod_a_dir / "mod.info").write_text("id=ModA\nrequire=ModB\n", encoding="utf-8")
            (mod_b_dir / "mod.info").write_text("id=ModB\n", encoding="utf-8")

            mod_to_item, requires = scan_mod_infos(workshop_dir)
            missing = compute_missing(mod_to_item, requires)

            self.assertEqual(mod_to_item["ModA"], "111")
            self.assertEqual(mod_to_item["ModB"], "222")
            self.assertEqual(missing, set())


if __name__ == "__main__":
    unittest.main()
