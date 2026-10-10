from pathlib import Path

import hermes_minitoo
from hermes_minitoo.capabilities import CAPABILITIES, RESEARCH_URL
from hermes_minitoo.cli import parser


def test_capability_references_point_to_development_branch():
    for cap in CAPABILITIES.values():
        assert cap.reference.startswith(RESEARCH_URL), cap.reference
        assert cap.reference.split("#")[0].endswith(".md"), cap.reference


def test_common_options_work_before_and_after_subcommand():
    p = parser()
    assert p.parse_args(["--verbose", "status"]).verbose is True
    assert p.parse_args(["status", "--verbose"]).verbose is True
    assert p.parse_args(["--state-dir", "/a", "status"]).state_dir == Path("/a")
    assert p.parse_args(["status", "--state-dir", "/b"]).state_dir == Path("/b")
    assert p.parse_args(["status"]).verbose is False


def test_version_is_a_string():
    assert isinstance(hermes_minitoo.__version__, str)


def test_gadget_parser_keeps_sdk_commands_and_adds_minitoo():
    from hermes_minitoo.cli import gadget_parser

    p = gadget_parser()
    assert p.parse_args(["linux", "status"]).command == "linux"
    args = p.parse_args(["minitoo", "--state-dir", "/x", "button", "talk", "press"])
    got = (args.command, args.minitoo_command, args.button, args.state)
    assert got == ("minitoo", "button", "talk", "press")
    assert args.state_dir == Path("/x")
    assert callable(args.func)
    assert p.parse_args(["minitoo", "run", "--config", "c.json"]).config == Path("c.json")


def test_minitoo_subcommands_match_legacy_cli():
    from hermes_minitoo.cli import gadget_parser

    def commands(parser_):
        group = next(a for a in parser_._actions if a.__class__.__name__ == "_SubParsersAction")
        return group

    legacy = set(commands(parser()).choices)
    group = commands(gadget_parser()).choices["minitoo"]
    assert legacy == set(commands(group).choices)
