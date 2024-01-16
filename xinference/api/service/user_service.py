# Copyright 2022-2024 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from enum import Enum
from functools import lru_cache
from typing import List

from sqlalchemy.orm import Session

from ..database.db_orm import User
from .role_service import get_role_service


class UserStatus(Enum):
    enabled = 0
    disabled = 1


class UserService:
    def __init__(self, db: Session):
        self._db = db
        self._role_service = get_role_service(db)

    def create_user(
        self, username: str, password: str, email: str, role_name: str
    ) -> User:
        role = self._role_service.get_role_by_name(role_name)
        if role is None:
            raise ValueError(f"Cannot create user with role name: {role_name}")
        db_user = User(
            account=email,
            username=username,
            password=password,
            email=email,
            status=UserStatus.enabled.name,
            last_login_ts=None,
            role_id=role.id,
        )
        self._db.add(db_user)
        self._db.commit()
        self._db.refresh(db_user)
        return db_user

    def get_users(self) -> List[User]:
        return self._db.query(User).all()

    def get_user_by_name(self, username: str) -> User:
        return self._db.query(User).filter(User.username == username).first()

    def update_user(self, account: str, username: str, status: str, role_name: str):
        role = self._role_service.get_role_by_name(role_name)
        if role is None:
            raise ValueError(f"Cannot update user with role name: {role_name}")

        self._db.query(User).filter(
            User.account == account and User.username == username
        ).update({"status": UserStatus[status].name, "role": role})
        self._db.commit()


@lru_cache
def get_user_service(db: Session):
    return UserService(db)
