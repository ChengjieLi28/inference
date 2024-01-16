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
import time
from functools import lru_cache
from typing import List

from sqlalchemy.orm import Session

from ..database.db_orm import Role


class RoleService:
    def __init__(self, db: Session):
        self._db = db

    def create_role(self, name: str, permissions: str) -> Role:
        role = Role(name=name, permissions=permissions, update_ts=int(time.time()))
        self._db.add(role)
        self._db.commit()
        self._db.refresh(role)
        return role

    def get_roles(self) -> List[Role]:
        return self._db.query(Role).all()

    def get_role_by_name(self, name: str) -> Role:
        return self._db.query(Role).filter(Role.name == name).first()

    def update_role(self, name: str, permissions: str):
        self._db.query(Role).filter(Role.name == name).update(
            {"permissions": permissions, "update_ts": int(time.time())}
        )
        self._db.commit()


@lru_cache
def get_role_service(db: Session):
    return RoleService(db)
