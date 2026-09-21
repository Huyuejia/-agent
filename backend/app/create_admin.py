"""Interactive admin provisioning: python -m app.create_admin."""

from getpass import getpass

from pydantic import TypeAdapter, ValidationError
from pydantic.networks import EmailStr
from sqlalchemy import select

from app.models.user import User
from app.postgres_database import get_postgres_session_factory
from app.security.passwords import hash_password


def main() -> int:
    raw_email = input("管理员邮箱: ").strip()
    try:
        email = str(TypeAdapter(EmailStr).validate_python(raw_email)).lower()
    except ValidationError:
        print("邮箱格式无效")
        return 1

    session = get_postgres_session_factory()()
    try:
        existing = session.scalar(select(User).where(User.normalized_email == email))
        if existing is not None:
            if existing.role == "admin":
                print("该用户已经是管理员")
                return 0
            confirmation = input("该用户已存在，输入 PROMOTE 确认提升为管理员: ")
            if confirmation != "PROMOTE":
                print("已取消")
                return 1
            existing.role = "admin"
            existing.is_active = True
            session.commit()
            print(f"已提升管理员: {existing.email}")
            return 0

        password = getpass("管理员密码（至少 8 个字符）: ")
        if not 8 <= len(password) <= 128:
            print("密码长度必须为 8～128 个字符")
            return 1
        confirmation = getpass("再次输入密码: ")
        if password != confirmation:
            print("两次密码不一致")
            return 1
        user = User(
            email=email,
            normalized_email=email,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        session.add(user)
        session.commit()
        print(f"已创建管理员: {email}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
