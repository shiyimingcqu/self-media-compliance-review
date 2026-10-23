"""t-33 共享配置/执行快照冻结契约测试（无 DB/路由/adapter/worker/真实调用）。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _parse_real_flag, build_provider_execution
from app.core.contracts import ProviderExecution, ReviewSnapshot


def make_execution(**over):
    base = {"model_id": "qwen3.8-omni-flash", "endpoint_base": "https://dashscope.aliyuncs.com/api/v1",
            "config_version": "v1"}
    base.update(over)
    return ProviderExecution(**base)


def test_real_flag_fail_closed():
    assert _parse_real_flag("1") and _parse_real_flag("TRUE") and _parse_real_flag(" yes ")
    for bad in (None, "", "0", "false", "no", "maybe", "2"):
        assert _parse_real_flag(bad) is False
    assert Settings().allow_real_provider_calls is False  # 缺省永远关


def test_snapshot_defaults_still_parse_without_execution():
    snap = ReviewSnapshot(mode="demo", rule_version_id="r1", model_config_id="m1", model_config_revision=1)
    assert snap.execution is None  # 历史 demo/real 无此字段照常读，不伪造回填


def test_existing_fields_semantics_unchanged():
    with pytest.raises(ValidationError):
        ReviewSnapshot(mode="real", rule_version_id="r", model_config_id="m", model_config_revision=0)
    snap = ReviewSnapshot(mode="real", rule_version_id="r", model_config_id="m", model_config_revision=2,
                          execution=make_execution())
    assert snap.model_config_id == "m" and snap.model_config_revision == 2
    assert snap.execution is not None and snap.execution.model_id == "qwen3.8-omni-flash"


def test_execution_fields_trimmed_nonempty():
    with pytest.raises(ValidationError):
        make_execution(model_id="   ")
    with pytest.raises(ValidationError):
        make_execution(config_version=" ")
    e = make_execution(model_id="  q  ", config_version=" v2 ",
                       endpoint_base=" https://x.example.com/api ")
    assert e.model_id == "q" and e.config_version == "v2" and e.endpoint_base == "https://x.example.com/api"


@pytest.mark.parametrize("bad", [
    "http://x.example.com/api",              # 非 https
    "https://",                              # 无 host
    "https://localhost/api",                 # 无点分真实 host
    "https://user:sec@example.com/api",      # userinfo 凭据混 URL
    "https://*@token@example.com/api",       # 同上
    "https://x.example.com/api?k=secret",    # query
    "https://x.example.com/api#frag",        # fragment
    "https://x.example.com/a\nb",            # 控制字符
    "https://foo .com/api",                  # host 含空白
    "https://x.example.com:notaport/api",    # 非法端口（parts.port 抛 ValueError）
    "https://x.example.com:70000/api",       # 越界端口
    "https://ex_ample.com/api",              # host 非法字符
])
def test_endpoint_https_hardened(bad):
    secret_markers = ("secret", "sec@", "token", "k=", "#frag", "notaport", "70000", "foo .com", "ex_ample")
    with pytest.raises(ValidationError) as exc:
        make_execution(endpoint_base=bad)
    err = exc.value
    # hide_input_in_errors：框架级 str/repr 也不得回显所给敏感输入
    text = f"{str(err)}|{repr(err)}"
    for m in secret_markers:
        assert m not in text, f"str/repr 泄漏 {m}"
    # 显式传播口径：errors(include_input=False) 同样干净；我方字段级 msg 固定文案
    flat = str(err.errors(include_input=False)) + str(err.errors()[0].get("msg", ""))
    for m in secret_markers:
        assert m not in flat


def test_execution_frozen_and_snapshot_extra_forbid():
    e = make_execution()
    with pytest.raises(ValidationError):  # frozen：mutation 被拒
        e.model_id = "other"
    with pytest.raises(ValidationError):  # extra forbid
        ProviderExecution(**{**e.model_dump(), "api_key": "sk-..."})


def test_builder_copies_freeze_and_fails_closed():
    s = Settings(allow_real_provider_calls=True, provider_model_id="m-x",
                 provider_endpoint_base="https://x.example.com/api", provider_config_version="v9")
    frozen = build_provider_execution(s)
    assert frozen.model_id == "m-x" and frozen.endpoint_base == "https://x.example.com/api"
    # builder 不产生调用/不赋资格：只构建对象；总闸关闭时构建仍可用但准入属另批（此处仅证无副作用）
    for missing in ("provider_model_id", "provider_endpoint_base", "provider_config_version"):
        kwargs = {"provider_model_id": "m", "provider_endpoint_base": "https://x.example.com/api",
                  "provider_config_version": "v"}
        kwargs[missing] = ""
        with pytest.raises(ValueError, match="缺失"):
            build_provider_execution(Settings(**kwargs))


def test_builder_snapshot_immunity_to_later_env_mutation():
    # 构建后修改 Settings（模拟 env 变化）不改已冻结 execution：Settings frozen，构建值已复制
    s = Settings(provider_model_id="m1", provider_endpoint_base="https://x.example.com/api",
                 provider_config_version="v1")
    frozen = build_provider_execution(s)
    s2 = Settings(provider_model_id="m2", provider_endpoint_base="https://y.example.com/api",
                  provider_config_version="v2")
    frozen2 = build_provider_execution(s2)
    assert frozen.model_id == "m1" and frozen2.model_id == "m2"  # 旧快照不受新环境冒改
