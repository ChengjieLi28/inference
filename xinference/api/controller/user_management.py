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
import logging

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from ..database.core import get_db
from ..database.db_orm import User
from ..service.user_service import get_user_service
from .utils import handle_exception_500

logger = logging.getLogger(__name__)


class CreateUserBody(BaseModel):
    username: str
    password: str
    email: str
    role: str


class UpdateUserBody(BaseModel):
    account: str
    username: str
    status: str
    role: str


def convert_orm_to_dict(user: User):
    return {
        "account": user.account,
        "username": user.username,
        "role": user.role.name,
        "email": user.email,
        "status": user.status,
        "last_login_ts": user.last_login_ts,
    }


@handle_exception_500(logger=logger)
def create_user(user: CreateUserBody, db: Session = Depends(get_db)) -> JSONResponse:
    user_service = get_user_service(db)
    existing_user = user_service.get_user_by_name(user.username)
    if existing_user is not None:
        raise ValueError(f"User with name {user.username} already exists.")
    new_user = user_service.create_user(
        user.username, user.password, user.email, user.role
    )
    assert (
        new_user is not None and new_user.username == user.username
    ), "Create user failed."

    return JSONResponse(content=convert_orm_to_dict(new_user))


@handle_exception_500(logger=logger)
def list_users(db: Session = Depends(get_db)) -> JSONResponse:
    user_service = get_user_service(db)
    return JSONResponse(
        content=[convert_orm_to_dict(user) for user in user_service.get_users()]
    )


@handle_exception_500(logger=logger)
def update_user(user: UpdateUserBody, db: Session = Depends(get_db)) -> JSONResponse:
    user_service = get_user_service(db)
    existing_user = user_service.get_user_by_name(user.username)
    if existing_user is None:
        raise ValueError(f"User with name {user.username} does not exist.")
    user_service.update_user(user.account, user.username, user.status, user.role)
    return JSONResponse(content={})
