"""Regresiones públicas de web_domain nullable y herencia de dominios ISPConfig."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from postfix_settings import MonitorError, Websites, domain_belongs


def sql_rows(rows):
    return "\n".join("\t".join([str(ident), str(parent)] + [
        "NULL" if value is None else value.encode("utf-8").hex()
        for value in (domain, user, kind, subdomain)
    ]) for ident, parent, domain, user, kind, subdomain in rows)


class WebsiteTests(unittest.TestCase):
    def sites(self, rows):
        return Websites({"ispconfig_server_id": 1}, lambda query: sql_rows(rows))

    def test_first_server_real_id_relationships_with_null_alias_users(self):
        sites = self.sites([(32, 0, "primary-a.example", "web900", "vhost", "none"),
                            (33, 32, "alias-a.example", None, "alias", "www"),
                            (5, 0, "primary-b.example", "web501", "vhost", "none"),
                            (44, 5, "alias-b.example", None, "alias", "none")])
        self.assertEqual(sites.domains("web900"), ["alias-a.example", "primary-a.example", "www.alias-a.example"])
        self.assertEqual(sites.domains("web501"), ["alias-b.example", "primary-b.example"])
        self.assertIsNone(sites.domains("web32"))  # El ID de registro no es el usuario.

    def test_second_server_real_id_relationship_with_null_alias_user(self):
        sites = self.sites([(20, 6, "alias-c.example", None, "alias", "none"),
                            (6, 0, "primary-c.example", "web700", "vhost", "none")])
        self.assertEqual(sites.domains("web700"), ["alias-c.example", "primary-c.example"])
        self.assertIsNone(sites.domains("web6"))

    def test_null_and_empty_descendants_inherit_but_separate_pool_does_not(self):
        sites = self.sites([(1, 0, "primary.example", "web900", "vhost", "none"),
                            (2, 1, "alias.example", None, "alias", "none"),
                            (3, 2, "nested.example", "", "subdomain", "none"),
                            (4, 1, "pool.example", "web901", "vhostsubdomain", "none"),
                            (5, 4, "pool-alias.example", None, "alias", "none")])
        self.assertEqual(sites.domains("web900"), ["alias.example", "nested.example", "primary.example"])
        self.assertEqual(sites.domains("web901"), ["pool-alias.example", "pool.example"])

    def test_orphan_or_userless_root_does_not_grant_domains(self):
        sites = self.sites([(33, 32, "orphan.example", None, "alias", "none"),
                            (4, 0, "unowned.example", None, "vhost", "none")])
        self.assertIsNone(sites.domains("web900"))

    def test_hexadecimal_literal_null_username_is_not_sql_null(self):
        sites = self.sites([(1, 0, "primary.example", "web900", "vhost", "none"),
                            (2, 1, "other.example", "NULL", "alias", "none")])
        self.assertEqual(sites.domains("web900"), ["primary.example"])
        self.assertEqual(sites.load()[2]["user"], "NULL")

    def test_foreign_uses_inherited_alias_not_another_customer(self):
        sites = self.sites([(6, 0, "primary.example", "web900", "vhost", "none"),
                            (20, 6, "alias.example", None, "alias", "www"),
                            (9, 0, "other.example", "web901", "vhost", "none")])
        domains = sites.domains("web900")
        self.assertTrue(domain_belongs("alias.example", domains))
        self.assertTrue(domain_belongs("www.alias.example", domains))
        self.assertFalse(domain_belongs("other.example", domains))

    def test_invalid_text_reports_row_id_and_field_without_echoing_data(self):
        valid = sql_rows([(20, 6, "alias.example", None, "alias", "none")]).split("\t")
        for index, field, value in ((2, "domain", "NULL"), (3, "system_user", "not-hex-secret"),
                                    (4, "type", "NULL"), (5, "subdomain", "NULL"),
                                    (2, "domain", "FF"), (3, "system_user", "FF"),
                                    (4, "type", "FF")):
            with self.subTest(field=field, value=value):
                fields = valid[:]
                fields[index] = value
                sites = Websites({"ispconfig_server_id": 1}, lambda query: "\t".join(fields))
                with self.assertRaises(MonitorError) as caught:
                    sites.load()
                self.assertIn("fila=1 domain_id=20 campo=" + field, str(caught.exception))
                self.assertNotIn("not-hex-secret", str(caught.exception))
                self.assertIsNone(sites.rows)

    def test_invalid_identifiers_and_columns_have_context(self):
        text = sql_rows([(20, 6, "alias.example", None, "alias", "none")])
        for broken, field in ((text + "\tunexpected", "columnas"),
                              (text.replace("20\t6", "20\t-1"), "parent_domain_id"),
                              (text.replace("20\t6", "0\t6"), "domain_id"),
                              (text + "\n" + text, "domain_id")):
            with self.subTest(field=field):
                sites = Websites({"ispconfig_server_id": 1}, lambda query: broken)
                with self.assertRaisesRegex(MonitorError, "campo=" + field):
                    sites.load()

    def test_idna_normalization_errors_identify_domain(self):
        sites = self.sites([(20, 6, "a" * 64 + ".example", None, "alias", "none")])
        with self.assertRaisesRegex(MonitorError, "domain_id=20 campo=domain"):
            sites.load()

    def test_query_is_scoped_cached_and_failed_load_is_retryable(self):
        responses = iter(("malformed", sql_rows([(6, 0, "primary.example", "web900", "vhost", "none")])))
        calls = []
        def query(sql):
            calls.append(sql)
            return next(responses)
        sites = Websites({"ispconfig_server_id": 7}, query)
        with self.assertRaises(MonitorError):
            sites.load()
        self.assertEqual(sites.domains("web900"), ["primary.example"])
        sites.domains("web900")
        self.assertEqual(len(calls), 2)
        self.assertIn("server_id=7;", calls[1])


if __name__ == "__main__":
    unittest.main()
