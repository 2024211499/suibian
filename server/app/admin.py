"""本机管理员初始化：python -m app.admin --username team-admin。"""

import argparse
from getpass import getpass

from pydantic import ValidationError

from app.auth.routes import Credentials
from app.auth.store import AuthStore
from app.config import get_settings


def main():
    parser = argparse.ArgumentParser(description="初始化随变管理员；已有账户不会被覆盖或提权")
    parser.add_argument("--username", required=True, help="3–32 位英文字母、数字或 _.-")
    args = parser.parse_args()
    password = getpass("管理员密码（至少 12 位，不回显）: ")
    if password != getpass("再次输入密码: "):
        parser.exit(1, "两次密码不一致，未修改账户。\n")
    try:
        credentials = Credentials(username=args.username, password=password)
    except ValidationError:
        parser.exit(1, "用户名或密码格式无效，未修改账户。\n")
    store = AuthStore(get_settings().state_dir / "auth.sqlite3")
    try:
        store.bootstrap_admin(credentials.username, credentials.password.get_secret_value())
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print("管理员账户已就绪；已有管理员的原密码保持不变。")


if __name__ == "__main__":
    main()
