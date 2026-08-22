import argparse
import getpass

from sqlalchemy import select

from .database import SessionLocal, create_all
from .models import User, UserRole
from .security import hash_password


def create_admin(username: str | None, display_name: str | None, password: str | None) -> None:
    create_all()
    username = username or input("管理员用户名: ").strip()
    display_name = display_name or input("显示名称: ").strip()
    password = password or getpass.getpass("密码（至少 8 位）: ")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("用户名已存在")
        db.add(
            User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
    print(f"已创建管理员 {username}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin")
    create.add_argument("--username")
    create.add_argument("--display-name")
    create.add_argument("--password")
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.username, args.display_name, args.password)


if __name__ == "__main__":
    main()
