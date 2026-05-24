"""Tests for the installer scripts' environment-value escaping.

Covers both:
  - systemd_escape_env_value() in install_service.sh (systemd unit files)
  - yaml_quote() in install_docker.sh (docker-compose YAML)

Each function is called via bash subprocess, then round-tripped through a
Python re-implementation of the target format's unquoting rules.

Dangerous characters by format:
  systemd: % (specifier expansion), " and \\ (unquoting), spaces (field split)
  YAML single-quoted: ' (only special char; doubled to escape)
"""

import re
import subprocess

SERVICE_SCRIPT = "scripts/setup/install_service.sh"
DOCKER_SCRIPT = "scripts/setup/install_docker.sh"

# ---------------------------------------------------------------------------
# Brutal test strings — shared across both formats
# ---------------------------------------------------------------------------

BRUTAL_STRINGS = [
    # Basic
    ("simple", "hello"),
    ("with_space", "hello world"),
    ("with_spaces", "  hello   world  "),
    # Dollar signs (the original bug report, issue #159)
    ("dollar_mid", "p@ss$word"),
    ("dollar_end", "password$"),
    ("double_dollar", "pa$$word"),
    ("dollar_brace", "pa${HOME}ss"),
    ("dollar_paren", "pa$(whoami)ss"),
    # Percent specifiers (systemd expansion)
    ("percent_n", "pass%nword"),
    ("percent_u", "pass%uword"),
    ("percent_H", "pass%Hword"),
    ("double_percent", "pass%%word"),
    ("percent_at_end", "password%"),
    # Backslashes
    ("single_backslash", r"pass\word"),
    ("double_backslash", "pass\\\\word"),
    ("trailing_backslash", "password\\"),
    ("backslash_n", "pass\\nword"),
    # Quotes
    ("double_quote", 'pass"word'),
    ("single_quote", "pass'word"),
    ("mixed_quotes", """pass"wo'rd"""),
    ("all_quotes", """he said "it's done" """),
    # Combined chaos
    ("kitchen_sink", r"""p@ss$w%ord"with\special'chars"""),
    ("systemd_nightmare", r"%n$HOME\"%u"),
    # Unicode and emoji
    ("emoji", "p@ss\U0001f512word"),
    ("unicode_accents", "p\u00e4ssw\u00f6rd"),
    ("cjk", "\u5bc6\u7801"),
    ("emoji_pile", "\U0001f680\U0001f525\U0001f4a5"),
    # Edge cases
    ("empty", ""),
    ("only_spaces", "   "),
    ("only_percent", "%"),
    ("only_backslash", "\\"),
    ("only_double_quote", '"'),
    ("only_single_quote", "'"),
    ("tab_embedded", "pass\tword"),
    ("very_long", "A" * 1000),
    ("glob_chars", "p*ss?[w]ord"),
    ("shell_pipe", "pass|word&bg"),
    ("semicolon", "pass;word"),
    ("backtick", "pass`whoami`word"),
    ("exclamation", "pass!word"),
    ("hash", "pass#word"),
    ("tilde", "~pass"),
    ("equals", "pass=word"),
    ("colon", "user:pass"),
    # Device paths (serial ports, by-id paths with colons)
    ("serial_simple", "/dev/ttyUSB0"),
    ("serial_acm", "/dev/ttyACM0"),
    ("serial_by_id", "/dev/serial/by-id/usb-Heltec_HT-n5262_F423934AA2AB2A5E-if00"),
    ("serial_colon_in_id", "/dev/serial/by-id/usb-vendor:product-0:0"),
    ("tcp_host", "192.168.1.100"),
    ("ble_address", "AA:BB:CC:DD:EE:FF"),
]


# ---------------------------------------------------------------------------
# systemd helpers
# ---------------------------------------------------------------------------


def _bash_systemd_escape(value: str) -> str:
    """Call systemd_escape_env_value() via bash."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
systemd_escape_env_value() {
    local v="$1"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    v="${v//%/%%}"
    printf '"%s"' "$v"
}
systemd_escape_env_value "$1"
""",
            "--",
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _systemd_unquote(raw: str) -> str:
    """Re-implement systemd.syntax(7) double-quote unquoting."""
    raw = raw.strip()
    if not raw.startswith('"') or not raw.endswith('"') or len(raw) < 2:
        return raw
    inner = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner) and inner[i + 1] in ('"', "\\"):
            out.append(inner[i + 1])
            i += 2
        else:
            out.append(inner[i])
            i += 1
    return "".join(out)


def _systemd_expand_specifiers(value: str) -> str:
    """Expand %% → % and detect leaked single-% specifiers."""
    stripped = value.replace("%%", "")
    if re.search(r"%[a-zA-Z]", stripped):
        return "SPECIFIER_LEAKED"
    return value.replace("%%", "%")


def _systemd_round_trip(value: str) -> str:
    return _systemd_expand_specifiers(_systemd_unquote(_bash_systemd_escape(value)))


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _bash_yaml_quote(value: str) -> str:
    """Call yaml_quote() via bash."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
yaml_quote() {
    local value="$1"
    value=${value//\'/\'\'}
    printf "'%s'" "$value"
}
yaml_quote "$1"
""",
            "--",
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _yaml_unquote_single(raw: str) -> str:
    """YAML single-quoted scalar unquoting: '' → ' inside single quotes."""
    raw = raw.strip()
    if not raw.startswith("'") or not raw.endswith("'") or len(raw) < 2:
        return raw
    return raw[1:-1].replace("''", "'")


def _yaml_round_trip(value: str) -> str:
    return _yaml_unquote_single(_bash_yaml_quote(value))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSystemdEscape:
    """All brutal strings survive systemd escape → unquote → specifier round trip."""

    def test_all_strings_round_trip(self):
        failures = []
        for name, value in BRUTAL_STRINGS:
            recovered = _systemd_round_trip(value)
            if recovered != value:
                escaped = _bash_systemd_escape(value)
                failures.append(
                    f"  {name}: input={value!r}  recovered={recovered!r}  escaped={escaped!r}"
                )
        assert not failures, "Systemd round-trip failures:\n" + "\n".join(failures)

    def test_no_specifier_leaks(self):
        failures = []
        for name, value in BRUTAL_STRINGS:
            escaped = _bash_systemd_escape(value)
            unquoted = _systemd_unquote(escaped)
            stripped = unquoted.replace("%%", "")
            leaked = re.findall(r"%[a-zA-Z]", stripped)
            if leaked:
                failures.append(f"  {name}: {leaked} in unquoted={unquoted!r}")
        assert not failures, "Specifier leaks:\n" + "\n".join(failures)

    def test_output_always_double_quoted(self):
        failures = []
        for name, value in BRUTAL_STRINGS:
            escaped = _bash_systemd_escape(value)
            if not (escaped.startswith('"') and escaped.endswith('"')):
                failures.append(f"  {name}: {escaped!r}")
        assert not failures, "Not double-quoted:\n" + "\n".join(failures)

    def test_function_present_in_installer(self):
        with open(SERVICE_SCRIPT) as f:
            content = f.read()
        assert "systemd_escape_env_value()" in content
        assert 'systemd_escape_env_value "$AUTH_USERNAME"' in content
        assert 'systemd_escape_env_value "$AUTH_PASSWORD"' in content


class TestYamlQuote:
    """All brutal strings survive YAML single-quote escape → unquote round trip."""

    def test_all_strings_round_trip(self):
        failures = []
        for name, value in BRUTAL_STRINGS:
            recovered = _yaml_round_trip(value)
            if recovered != value:
                escaped = _bash_yaml_quote(value)
                failures.append(
                    f"  {name}: input={value!r}  recovered={recovered!r}  escaped={escaped!r}"
                )
        assert not failures, "YAML round-trip failures:\n" + "\n".join(failures)

    def test_output_always_single_quoted(self):
        failures = []
        for name, value in BRUTAL_STRINGS:
            escaped = _bash_yaml_quote(value)
            if not (escaped.startswith("'") and escaped.endswith("'")):
                failures.append(f"  {name}: {escaped!r}")
        assert not failures, "Not single-quoted:\n" + "\n".join(failures)

    def test_function_present_in_installer(self):
        with open(DOCKER_SCRIPT) as f:
            content = f.read()
        assert "yaml_quote()" in content
        assert 'yaml_quote "$AUTH_USERNAME"' in content
        assert 'yaml_quote "$AUTH_PASSWORD"' in content
