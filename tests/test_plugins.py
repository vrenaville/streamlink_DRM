import pkgutil
import re
import tokenize
from inspect import Parameter, signature
from pathlib import Path

import pytest

import streamlink.plugins
import tests.plugins
from streamlink.plugin.plugin import Matcher, Plugin
from streamlink.utils.module import exec_module


plugins_path = streamlink.plugins.__path__[0]
plugintests_path = tests.plugins.__path__[0]

protocol_plugins = [
    "http",
    "hls",
    "dash",
]
plugintests_ignore = [
    "test_stream",
]

plugin_modules = [
    module_info
    for module_info in pkgutil.iter_modules([plugins_path])
    if not module_info.name.startswith("common_")
]  # fmt: skip
plugins = [module_info.name for module_info in plugin_modules]
plugins_no_protocols = [pname for pname in plugins if pname not in protocol_plugins]
plugintests = [
    re.sub(r"^test_", "", tname)
    for finder, tname, ispkg in pkgutil.iter_modules([plugintests_path])
    if tname.startswith("test_") and tname not in plugintests_ignore
]

PLUGIN_TYPES = "live", "vod", "live, vod"
PLUGIN_METADATA = "id", "author", "category", "title"

re_url = re.compile(r"^https?://")
re_metadata = re.compile(rf"^({'|'.join(re.escape(item) for item in PLUGIN_METADATA)})(\s.+)?$")


def unique(iterable):
    seen = set()
    for item in iterable:
        if item not in seen:
            seen.add(item)
            yield item


class TestPlugins:
    @pytest.fixture(scope="class", params=plugin_modules)
    def plugin(self, request):
        return exec_module(request.param.module_finder, f"streamlink.plugins.{request.param.name}")

    def test_exports_plugin(self, plugin):
        assert hasattr(plugin, "__plugin__"), "Plugin module exports __plugin__"
        assert issubclass(plugin.__plugin__, Plugin), "__plugin__ is an instance of the Plugin class"

    def test_classname(self, plugin):
        classname = plugin.__plugin__.__name__
        assert classname == classname[0].upper() + classname[1:], "__plugin__ class name starts with uppercase letter"
        assert "_" not in classname, "__plugin__ class name does not contain underscores"

    def test_constructor(self, plugin):
        assert (
            plugin.__plugin__.__init__ is Plugin.__init__
            or tuple(
                (param.name, param.kind)
                for param in signature(plugin.__plugin__.__init__).parameters.values()
            ) == (
                ("self", Parameter.POSITIONAL_OR_KEYWORD),
                ("args", Parameter.VAR_POSITIONAL),
                ("kwargs", Parameter.VAR_KEYWORD),
            )
        )  # fmt: skip

    def test_matchers(self, plugin):
        pluginclass = plugin.__plugin__
        assert isinstance(pluginclass.matchers, list), "Has at a matchers list"
        assert len(pluginclass.matchers) > 0, "Has at least one matcher"
        assert all(isinstance(matcher, Matcher) for matcher in pluginclass.matchers), "Only has valid matchers"

    def test_plugin_api(self, plugin):
        pluginclass = plugin.__plugin__
        assert not hasattr(pluginclass, "can_handle_url"), "Does not implement deprecated can_handle_url(url)"
        assert not hasattr(pluginclass, "priority"), "Does not implement deprecated priority(url)"
        assert callable(pluginclass._get_streams), "Implements _get_streams()"


class TestPluginTests:
    @pytest.mark.parametrize("plugin", plugins)
    def test_plugin_has_tests(self, plugin):
        assert plugin in plugintests, "Test module exists for plugin"

    @pytest.mark.parametrize("plugintest", plugintests)
    def test_test_has_plugin(self, plugintest):
        assert plugintest in plugins, "Plugin exists for test module"


class TestPluginMetadata:
    @pytest.fixture(scope="class")
    def metadata_keys_all(self):
        return (
            "description",
            "url",
            "type",
            "webbrowser",
            "metadata",
            "region",
            "account",
            "notes",
        )

    @pytest.fixture(scope="class")
    def metadata_keys_required(self):
        return (
            "description",
            "url",
            "type",
        )

    @pytest.fixture(scope="class")
    def metadata_keys_repeat(self):
        return (
            "url",
            "metadata",
            "notes",
        )

    @pytest.fixture(scope="class")
    def metadata_keys_no_repeat(self, metadata_keys_all, metadata_keys_repeat):
        return tuple(
            key
            for key in metadata_keys_all
            if key not in metadata_keys_repeat
        )  # fmt: skip

    @pytest.fixture(scope="class", params=plugins_no_protocols)
    def tokeninfo(self, request):
        with (Path(plugins_path) / f"{request.param}.py").open(encoding="utf-8") as handle:
            tokeninfo = next(tokenize.generate_tokens(handle.readline), None)

        assert type(tokeninfo) is tokenize.TokenInfo, "Parses the first token"
        assert tokeninfo.type == tokenize.STRING, "First token is a string"

        return tokeninfo

    @pytest.fixture(scope="class")
    def metadata_items(self, tokeninfo):
        match = re.search(r"^\"\"\"\n(?P<metadata>.+)\n\"\"\"$", tokeninfo.string, re.DOTALL)
        assert match is not None, "String is a properly formatted long string"

        lines = [
            re.search(r"^\$(?P<key>\w+) (?P<value>\S.+)$", line)
            for line in match.group("metadata").split("\n")
        ]  # fmt: skip
        assert all(lines), "All lines are properly formatted using the '$key value' format"

        return [(match.group("key"), match.group("value")) for match in lines]

    @pytest.fixture(scope="class")
    def metadata_keys(self, metadata_items):
        return tuple(key for key, value in metadata_items)

    @pytest.fixture(scope="class")
    def metadata_dict(self, metadata_keys_no_repeat, metadata_items):
        return {k: v for k, v in metadata_items if k in metadata_keys_no_repeat}

    def test_no_unknown(self, metadata_keys_all, metadata_keys):
        assert not any(True for key in metadata_keys if key not in metadata_keys_all), \
            "No unknown metadata keys are set"  # fmt: skip

    def test_required(self, metadata_keys_required, metadata_keys):
        assert all(True for tag in metadata_keys_required if tag in metadata_keys), \
            "All required metadata keys are set"  # fmt: skip

    def test_order(self, metadata_keys_all, metadata_keys):
        keys = tuple(key for key in metadata_keys_all if key in metadata_keys)
        assert keys == tuple(unique(metadata_keys)), \
            "All metadata keys are defined in order"  # fmt: skip
        assert tuple(reversed(keys)) == tuple(unique(reversed(metadata_keys))), \
            "All repeatable metadata keys are defined in order"  # fmt: skip

    def test_repeat(self, metadata_keys_repeat, metadata_keys, metadata_items):
        items = {key: tuple(v for k, v in metadata_items if k == key) for key in metadata_keys if key in metadata_keys_repeat}
        assert items == {key: tuple(unique(value)) for key, value in items.items()}, \
            "Repeatable keys don't have any duplicates"  # fmt: skip

    def test_no_repeat(self, metadata_keys_no_repeat, metadata_keys):
        keys = tuple(key for key in metadata_keys if key in metadata_keys_no_repeat)
        assert keys == tuple(unique(keys)), "Non-repeatable keys are set at most only once"

    def test_key_url(self, metadata_items):
        assert not any(re_url.match(val) for key, val in metadata_items if key == "url"), \
            "$url metadata values don't start with http:// or https://"  # fmt: skip

    def test_key_type(self, metadata_dict):
        assert metadata_dict.get("type") in PLUGIN_TYPES, \
            "$type metadata has the correct value"  # fmt: skip

    def test_key_metadata(self, metadata_items):
        assert all(re_metadata.match(val) for key, val in metadata_items if key == "metadata"), \
            "$metadata metadata values have the correct format"  # fmt: skip
        indexes = [PLUGIN_METADATA.index(val.split(" ")[0]) for key, val in metadata_items if key == "metadata"]
        assert [PLUGIN_METADATA[i] for i in indexes] == [PLUGIN_METADATA[i] for i in sorted(indexes)], \
            "$metadata metadata values are ordered correctly"  # fmt: skip
