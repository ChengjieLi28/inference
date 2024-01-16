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
import random
import string
import time
from functools import lru_cache
from typing import List

from sqlalchemy.orm import Session

from ..database.db_orm import Secret
from .user_service import get_user_service


class SecretService:
    def __init__(self, db: Session):
        self._db = db
        self._user_service = get_user_service(db)

    @staticmethod
    def _gen_secrets() -> str:
        return "".join(random.sample(string.ascii_letters + string.digits, 8))

    def create_secret(self, name: str, username: str) -> Secret:
        user = self._user_service.get_user_by_name(username)
        if user is None:
            raise ValueError(f"User with name {username} not found")
        secret = Secret(
            name=name,
            secrets=self._gen_secrets(),
            created_ts=int(time.time()),
            user_id=user.id,
        )
        self._db.add(secret)
        self._db.commit()
        self._db.refresh(secret)
        return secret

    def get_secrets(self) -> List[Secret]:
        return self._db.query(Secret).all()

    def delete_secret(self, name: str):
        self._db.query(Secret).filter(Secret.name == name).delete()
        self._db.commit()


@lru_cache
def get_secret_service(db: Session):
    return SecretService(db)
