"""Tests for parse_sqlconf and render_sqlconf in efs_editor module."""

import pytest
from credential_rotation.efs_editor import parse_sqlconf, render_sqlconf

SAMPLE_SQLCONF = """\
<?php
$host   = 'db.example.com';
$port   = '3306';
$login  = 'openemr_user';
$pass   = 's3cret!';
$dbase  = 'openemr';
"""


class TestParseSqlconf:
    def test_parses_all_fields(self):
        result = parse_sqlconf(SAMPLE_SQLCONF)
        assert result["host"] == "db.example.com"
        assert result["port"] == "3306"
        assert result["username"] == "openemr_user"
        assert result["password"] == "s3cret!"
        assert result["dbname"] == "openemr"

    def test_missing_fields_omitted(self):
        partial = "$host = 'only-host';\n"
        result = parse_sqlconf(partial)
        assert result == {"host": "only-host"}

    def test_empty_string_returns_empty(self):
        assert parse_sqlconf("") == {}

    def test_double_quotes_supported(self):
        content = '$host = "my-host";\n$port = "3306";\n$login = "user";\n$pass = "pw";\n$dbase = "db";'
        result = parse_sqlconf(content)
        assert result["host"] == "my-host"
        assert result["password"] == "pw"

    def test_whitespace_variations(self):
        content = "$host='no-space';\n$port = '3306';\n$login  =  'user';\n$pass='p';\n$dbase='d';"
        result = parse_sqlconf(content)
        assert result["host"] == "no-space"
        assert result["username"] == "user"


class TestRenderSqlconf:
    def test_replaces_all_fields(self):
        slot = {
            "host": "new-host.rds.amazonaws.com",
            "port": "3306",
            "username": "new_user",
            "password": "new_pass",
            "dbname": "openemr_v2",
        }
        result = render_sqlconf(SAMPLE_SQLCONF, slot)
        assert "'new-host.rds.amazonaws.com'" in result
        assert "'new_user'" in result
        assert "'new_pass'" in result
        assert "'openemr_v2'" in result

    def test_preserves_non_db_lines(self):
        content = "<?php\n// comment\n$host = 'h';\n$port = '3306';\n$login = 'u';\n$pass = 'p';\n$dbase = 'd';\n$extra = true;\n"
        slot = {"host": "h2", "port": "3307", "username": "u2", "password": "p2", "dbname": "d2"}
        result = render_sqlconf(content, slot)
        assert "// comment" in result
        assert "$extra = true;" in result

    def test_raises_on_missing_variable(self):
        incomplete = "$host = 'h';\n"
        slot = {"host": "h2", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        with pytest.raises(ValueError, match="Unable to locate"):
            render_sqlconf(incomplete, slot)

    def test_default_port(self):
        slot = {"host": "h", "username": "u", "password": "p", "dbname": "d"}
        result = render_sqlconf(SAMPLE_SQLCONF, slot)
        assert "'3306'" in result

    def test_idempotent_render(self):
        slot = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        first = render_sqlconf(SAMPLE_SQLCONF, slot)
        second = render_sqlconf(first, slot)
        assert first == second
