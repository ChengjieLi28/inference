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
import json
import logging
from typing import List, Literal

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from ..database.core import get_db
from ..database.db_orm import Role
from ..service.role_service import get_role_service
from .utils import handle_exception_500

logger = logging.getLogger(__name__)


class Permission(BaseModel):
    models: List[Literal["list", "read", "register", "unregister"]]
    instances: List[Literal["list", "read", "start", "stop"]]
    users: List[Literal["add", "modify", "list", "delete"]]
    roles: List[Literal["add", "modify", "list", "delete"]]
    secrets: List[Literal["add", "list", "delete"]]


class RoleBody(BaseModel):
    role: str
    permissions: Permission


def convert_orm_to_dict(role: Role):
    return {
        "role": role.name,
        "permissions": json.loads(role.permissions),
        "update_ts": role.update_ts,
    }


@handle_exception_500(logger=logger)
def create_role(role_body: RoleBody, db: Session = Depends(get_db)) -> JSONResponse:
    role_service = get_role_service(db)
    new_role = role_service.create_role(role_body.role, role_body.permissions.json())
    return JSONResponse(content=convert_orm_to_dict(new_role))


@handle_exception_500(logger=logger)
def list_roles(db: Session = Depends(get_db)) -> JSONResponse:
    role_service = get_role_service(db)
    return JSONResponse(
        content=[convert_orm_to_dict(role) for role in role_service.get_roles()]
    )


@handle_exception_500(logger=logger)
def update_role(role_body: RoleBody, db: Session = Depends(get_db)) -> JSONResponse:
    role_service = get_role_service(db)
    role_service.update_role(role_body.role, role_body.permissions.json())
    return JSONResponse(content={})
