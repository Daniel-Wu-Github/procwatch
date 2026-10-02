import pytest

from procwatch.grouping import Rule, RulesError, parse_rules


def test_parses_a_rule_with_one_condition():
    rules = parse_rules('[[rule]]\nlabel = "Games"\nexe_contains = "CrossOver"\n')
    assert rules == [Rule(label="Games", exe_contains="CrossOver")]


def test_parses_several_rules_in_file_order_with_anded_conditions():
    text = (
        '[[rule]]\nlabel = "A"\nname = "node"\n'
        '[[rule]]\nlabel = "B"\ncmdline_contains = "x"\ncwd_prefix = "/tmp"\n'
    )
    rules = parse_rules(text)
    assert [r.label for r in rules] == ["A", "B"]
    assert rules[1].cmdline_contains == "x" and rules[1].cwd_prefix == "/tmp"


def test_empty_text_is_no_rules():
    assert parse_rules("") == []


@pytest.mark.parametrize("text", [
    '[[rule]]\nexe_contains = "x"\n',                          # no label
    '[[rule]]\nlabel = "A"\n',                                  # no condition
    '[[rule]]\nlabel = "A"\nexe_contians = "x"\n',              # typo'd key must not be ignored
    '[[rule]]\nlabel = ""\nname = "x"\n',                       # empty label
    '[[rule]]\nlabel = "A"\nname = 5\n',                        # wrong type
    'this is not toml = = =',
])
def test_invalid_rules_fail_loudly(text):
    with pytest.raises(RulesError):
        parse_rules(text)
