from __future__ import annotations

import os

import uvicorn

from .admin import create_admin
from .config import MIN_ADMIN_PASSWORD_LENGTH, settings, valid_admin_password
from .db import Store


def main() -> None:
    config = settings()
    if not valid_admin_password(config.admin_password):
        raise SystemExit(f"Укажите ADMIN_PASSWORD длиной от {MIN_ADMIN_PASSWORD_LENGTH} символов")
    port = int(os.environ.get("LOCAL_ADMIN_PORT", "8080"))
    store = Store(config.database_path)
    uvicorn.run(create_admin(store, config), host="127.0.0.1", port=port,
                proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
