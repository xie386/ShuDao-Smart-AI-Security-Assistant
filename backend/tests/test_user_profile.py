"""个人主页测试：schemas 校验 + service 邮箱/用户名逻辑 + 隐患过滤参数透传（纯函数，不依赖 DB/LLM）。

覆盖：
- user_profile_service 的邮箱格式校验 / 用户名格式校验（独立可测，不碰 DB）
- 隐患过滤参数在 router → service 的透传（用 fake Request/Session 验证签名与 where 拼接不报错）
  —— 完整 DB 联调见手动测试指引。
"""
import pytest

from src.schemas.user_profile import (
    AvatarUpdate,
    EmailUpdate,
    PasswordUpdate,
    UsernameUpdate,
)
from src.services import user_profile_service as svc


class TestSchemas:
    def test_username_min(self):
        with pytest.raises(Exception):
            UsernameUpdate(username="a")  # 少于 2 字

    def test_password_min(self):
        with pytest.raises(Exception):
            PasswordUpdate(old_password="x", new_password="123")  # 新密码 <6

    def test_avatar_required(self):
        with pytest.raises(Exception):
            AvatarUpdate()  # avatar 必填

    def test_email_optional(self):
        assert EmailUpdate(email=None).email is None
        assert EmailUpdate(email="a@b.com").email == "a@b.com"


class TestServiceValidation:
    """这些校验是 service 内的纯逻辑，用 monkeypatch 隔离 DB 提交即可验证分支。"""

    def test_email_invalid_rejected(self):
        # 不合法邮箱应抛 422（不碰 DB 提交，仅校验分支）
        class FakeDB:
            def commit(self): pass
            def get(self, *a, **k): pass
            def execute(self, *a, **k):
                class R:
                    def scalar_one_or_none(self): return None
                return R()

        with pytest.raises(Exception) as ei:
            svc.UserProfileService.update_email(FakeDB(), 1, "not-an-email")
        assert "邮箱" in str(ei.value.detail)

    def test_username_too_short(self):
        class FakeDB:
            def commit(self): pass
            def get(self, *a, **k): pass
            def execute(self, *a, **k):
                class R:
                    def scalar_one_or_none(self): return None
                return R()

        with pytest.raises(Exception) as ei:
            svc.UserProfileService.update_username(FakeDB(), 1, "x")
        assert "用户名" in str(ei.value.detail)


class TestHazardFilterSignature:
    """验证 list_hazards 接受 rectified_by / reviewed_by 且能拼出查询（不实际查库）。"""

    def test_signature_accepts_new_params(self):
        from unittest import mock
        from src.services import hazard_service as hz

        calls = {}

        class FakeScalar:
            def scalar_one_or_none(self): return None
        class FakeSession(mock.MagicMock):
            def execute(self, *a, **k):
                return FakeScalar()
            def scalars(self, *a, **k):
                class S:
                    def all(self): return []
                return S()
            def scalar(self, *a, **k): return 0

        # 仅验证方法可调用且新参数被接受（count 查询返回 0，items 空）
        out = hz.HazardService.list_hazards(
            FakeSession(), page=1, page_size=10,
            rectified_by=7, reviewed_by=3,
        )
        assert out.total == 0
        assert out.items == []
